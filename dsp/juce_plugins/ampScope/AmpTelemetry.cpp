#include "AmpTelemetry.h"

#include <alsa/asoundlib.h>
#include <cstring>

namespace amp
{

//==============================================================================
const char* channelName (Channel c)
{
    switch (c)
    {
        case Channel::vout:  return "Vout";
        case Channel::iout:  return "Iout";
        case Channel::pvdd:  return "PVDD";
        case Channel::vbat:  return "VBAT";
        case Channel::temp:  return "Temp";
        case Channel::count: break;
    }
    return "";
}

namespace slots
{
    juce::String name (int ampSlot)
    {
        if (ampSlot < 0)
            return "off";

        return "ch" + juce::String (tdmChannel (ampSlot))
                 + (straddles (ampSlot) ? " +3" : isLowHalf (ampSlot) ? " lo" : " hi");
    }
}

const char* channelUnit (Channel c)
{
    switch (c)
    {
        case Channel::vout:
        case Channel::pvdd:
        case Channel::vbat:  return "V";
        case Channel::iout:  return "A";
        case Channel::temp:  return "C";
        case Channel::count: break;
    }
    return "";
}

/* The driver names its slot controls after the amp's data types, not after the
   quantity: Supply1 IS PVDD and Supply2 IS VBAT, per the transmit enables. */
const char* channelSlotControl (Channel c)
{
    switch (c)
    {
        case Channel::vout:  return "VMON";
        case Channel::iout:  return "IMON";
        case Channel::pvdd:  return "Supply1";
        case Channel::vbat:  return "Supply2";
        case Channel::temp:  return "Therm";
        case Channel::count: break;
    }
    return "";
}

bool channelIsAdcCode (Channel c)
{
    return c == Channel::pvdd || c == Channel::vbat || c == Channel::temp;
}

namespace scale
{
    float convert (Channel c, int raw)
    {
        switch (c)
        {
            case Channel::vout:  return vout (raw);
            case Channel::iout:  return iout (raw);
            case Channel::pvdd:  return pvdd (raw);
            case Channel::vbat:  return vbat (raw);
            case Channel::temp:  return temp (raw);
            case Channel::count: break;
        }
        return 0.0f;
    }
}

//==============================================================================
// Small helpers. Kept file-local: nothing outside this translation unit should
// need to know that ALSA is what is underneath.
namespace
{

juce::String cardTag (int index, const juce::String& id)
{
    return "hw:" + juce::String (index) + (id.isNotEmpty() ? " " + id : juce::String());
}

/** The driver prefixes every control with the node's sound-name-prefix. */
juce::String qualify (const juce::String& prefix, const juce::String& name)
{
    return prefix.isEmpty() ? name : prefix + " " + name;
}

bool readIntControl (snd_ctl_t* ctl, const juce::String& name, long& out)
{
    snd_ctl_elem_id_t* id;
    snd_ctl_elem_value_t* value;
    snd_ctl_elem_id_alloca (&id);
    snd_ctl_elem_value_alloca (&value);

    snd_ctl_elem_id_set_interface (id, SND_CTL_ELEM_IFACE_MIXER);
    snd_ctl_elem_id_set_name (id, name.toRawUTF8());
    snd_ctl_elem_value_set_id (value, id);

    if (snd_ctl_elem_read (ctl, value) < 0)
        return false;

    out = snd_ctl_elem_value_get_integer (value, 0);
    return true;
}

bool writeIntControl (snd_ctl_t* ctl, const juce::String& name, long value, int& err)
{
    snd_ctl_elem_id_t* id;
    snd_ctl_elem_value_t* v;
    snd_ctl_elem_id_alloca (&id);
    snd_ctl_elem_value_alloca (&v);

    snd_ctl_elem_id_set_interface (id, SND_CTL_ELEM_IFACE_MIXER);
    snd_ctl_elem_id_set_name (id, name.toRawUTF8());
    snd_ctl_elem_value_set_id (v, id);

    // Read first: snd_ctl_elem_write wants a fully populated value, and this
    // also tells us whether the control exists at all.
    if ((err = snd_ctl_elem_read (ctl, v)) < 0)
        return false;

    if (snd_ctl_elem_value_get_integer (v, 0) == value)
        return true;

    snd_ctl_elem_value_set_integer (v, 0, value);
    err = snd_ctl_elem_write (ctl, v);
    return err >= 0;
}

/** "PCM <x> Slot" reports 64 for a data type that was never given a slot. */
int readSlotControl (snd_ctl_t* ctl, const juce::String& prefix, const char* what)
{
    long v = 0;

    if (! readIntControl (ctl, qualify (prefix, juce::String ("PCM ") + what + " Slot"), v))
        return -1;

    return (v >= 0 && v < 64) ? (int) v : -1;
}

juce::StringArray listControlNames (snd_ctl_t* ctl)
{
    juce::StringArray names;

    snd_ctl_elem_list_t* list;
    snd_ctl_elem_list_alloca (&list);

    if (snd_ctl_elem_list (ctl, list) < 0)
        return names;

    const unsigned int count = snd_ctl_elem_list_get_count (list);

    if (count == 0 || snd_ctl_elem_list_alloc_space (list, count) < 0)
        return names;

    if (snd_ctl_elem_list (ctl, list) >= 0)
        for (unsigned int i = 0; i < snd_ctl_elem_list_get_used (list); ++i)
            names.add (juce::String::fromUTF8 (snd_ctl_elem_list_get_name (list, i)));

    snd_ctl_elem_list_free_space (list);
    return names;
}

/** How wide the capture stream is. Opened and closed without starting it: the
    TDM interface constrains the channel count from the lane's rx mask, so this
    is the authoritative answer to "which TDM slots can be read at all". */
int captureChannelCount (const juce::String& device)
{
    if (device.isEmpty())
        return 0;

    snd_pcm_t* pcm = nullptr;

    if (snd_pcm_open (&pcm, device.toRawUTF8(), SND_PCM_STREAM_CAPTURE,
                      SND_PCM_NONBLOCK) < 0)
        return 0;

    snd_pcm_hw_params_t* hw;
    snd_pcm_hw_params_alloca (&hw);

    unsigned int maxChannels = 0;

    if (snd_pcm_hw_params_any (pcm, hw) >= 0)
        snd_pcm_hw_params_get_channels_max (hw, &maxChannels);

    snd_pcm_close (pcm);
    return (int) maxChannels;
}

/** The card's first PCM that can capture, as an ALSA device string. */
juce::String findCaptureDevice (snd_ctl_t* ctl, int cardIndex)
{
    snd_pcm_info_t* info;
    snd_pcm_info_alloca (&info);

    int device = -1;

    while (snd_ctl_pcm_next_device (ctl, &device) >= 0 && device >= 0)
    {
        snd_pcm_info_set_device (info, (unsigned int) device);
        snd_pcm_info_set_subdevice (info, 0);
        snd_pcm_info_set_stream (info, SND_PCM_STREAM_CAPTURE);

        if (snd_ctl_pcm_info (ctl, info) >= 0)
            return "hw:" + juce::String (cardIndex) + "," + juce::String (device);
    }

    return {};
}

/** Match an amp to its I2C address through sysfs, so the picker can say which
    physical part it is and not just "amp1". The codec's DT node carries the same
    sound-name-prefix the ALSA controls are named after. */
juce::String findI2cName (const juce::String& prefix)
{
    if (prefix.isEmpty())
        return {};

    const juce::File root ("/sys/bus/i2c/drivers/max98396");

    if (! root.isDirectory())
        return {};

    for (const auto& entry : juce::RangedDirectoryIterator (root, false, "*",
                                                            juce::File::findDirectories))
    {
        const auto node = entry.getFile().getChildFile ("of_node/sound-name-prefix");

        if (node.existsAsFile() && node.loadFileAsString().trim() == prefix)
            return entry.getFile().getFileName();
    }

    return {};
}

} // anonymous namespace

bool Device::canCapture() const
{
    if (captureDevice.isEmpty())
        return false;

    for (auto s : slots)
        if (s >= 0)
            return true;

    return false;
}

juce::String Device::label() const
{
    juce::String s = prefix.isNotEmpty() ? prefix : juce::String ("codec");

    if (i2cName.isNotEmpty())
        s << " (" << i2cName << ")";

    s << "  " << cardTag (cardIndex, cardId);
    return s;
}

//==============================================================================
void TraceRing::setCapacity (int frames)
{
    int wanted = juce::nextPowerOfTwo (juce::jmax (1024, frames));

    if (wanted == capacity)
        return;

    for (auto& d : data)
    {
        d.assign ((size_t) wanted, 0.0f);
        d.shrink_to_fit();
    }

    capacity = wanted;
    mask = wanted - 1;
    writePos.store (0, std::memory_order_release);
}

void TraceRing::clear() noexcept
{
    for (auto& d : data)
        std::fill (d.begin(), d.end(), 0.0f);

    writePos.store (0, std::memory_order_release);
}

void TraceRing::push (const std::array<float, (size_t) numChannels>& frame) noexcept
{
    if (capacity == 0)
        return;

    const auto pos = writePos.load (std::memory_order_relaxed);
    const size_t index = (size_t) (pos & (juce::uint64) mask);

    for (int c = 0; c < numChannels; ++c)
        data[(size_t) c][index] = frame[(size_t) c];

    writePos.store (pos + 1, std::memory_order_release);
}

float TraceRing::read (Channel c, juce::uint64 pos) const noexcept
{
    if (capacity == 0)
        return 0.0f;

    return data[(size_t) c][(size_t) (pos & (juce::uint64) mask)];
}

//==============================================================================
/*
 * Configuration goes out the same way the measurements come in: through the
 * driver. These write mixer controls; the driver turns them into I2C register
 * writes. Nothing here knows or cares what register any of it lands in.
 */
juce::String Telemetry::applyBootConfiguration (const Device& d)
{
    if (! d.isValid())
        return "No amplifier selected.";

    const juce::String hw = "hw:" + juce::String (d.cardIndex);
    snd_ctl_t* ctl = nullptr;

    if (const int err = snd_ctl_open (&ctl, hw.toRawUTF8(), 0); err < 0)
        return "Cannot open " + hw + ": " + snd_strerror (err);

    // "VI Sense Switch" is a DAPM switch in front of the whole transmit path, so
    // nothing powers up until userspace closes it -- this is the one write
    // without which the amp stays silent however its slots are set. The two
    // measurement-ADC rails are what the Supply1/Supply2 data types carry, so
    // they have to be running for those slots to hold anything but zero.
    static const char* const alwaysOn[]
    {
        "VI Sense Switch",
        "Meas ADC PVDD Switch",
        "Meas ADC VBAT Switch",
    };

    juce::StringArray failed;

    for (auto* name : alwaysOn)
    {
        int err = 0;

        if (! writeIntControl (ctl, qualify (d.prefix, name), 1, err))
            failed.add (juce::String (name) + " (" + snd_strerror (err) + ")");
    }

    snd_ctl_close (ctl);

    if (failed.isEmpty())
        return {};

    return "Could not switch on " + failed.joinIntoString (", ")
             + (failed.size() == 1 ? "" : "")
             + ". Is the driver current, and is this user in the audio group?";
}

juce::String Telemetry::setSlot (const Device& d, Channel c, int ampSlot)
{
    if (! d.isValid())
        return "No amplifier selected.";

    const juce::String hw = "hw:" + juce::String (d.cardIndex);
    snd_ctl_t* ctl = nullptr;

    if (const int err = snd_ctl_open (&ctl, hw.toRawUTF8(), 0); err < 0)
        return "Cannot open " + hw + ": " + snd_strerror (err);

    const auto name = qualify (d.prefix, juce::String ("PCM ")
                                           + channelSlotControl (c) + " Slot");
    int err = 0;
    const bool ok = writeIntControl (ctl, name, ampSlot < 0 ? 64 : ampSlot, err);

    snd_ctl_close (ctl);

    if (ok)
        return {};

    return "Cannot set " + name + ": " + snd_strerror (err);
}

//==============================================================================
Telemetry::Telemetry()  : juce::Thread ("amp telemetry")
{
    ring.setCapacity (recordFrames);
}

Telemetry::~Telemetry()
{
    stop();
}

juce::Array<Device> Telemetry::discover()
{
    juce::Array<Device> found;
    int card = -1;

    while (snd_card_next (&card) >= 0 && card >= 0)
    {
        const juce::String hw = "hw:" + juce::String (card);

        snd_ctl_t* ctl = nullptr;

        if (snd_ctl_open (&ctl, hw.toRawUTF8(), 0) < 0)
            continue;

        snd_ctl_card_info_t* info;
        snd_ctl_card_info_alloca (&info);

        juce::String cardId;

        if (snd_ctl_card_info (ctl, info) >= 0)
            cardId = juce::String::fromUTF8 (snd_ctl_card_info_get_id (info));

        const auto names = listControlNames (ctl);
        const auto capture = findCaptureDevice (ctl, card);
        const int captureWidth = captureChannelCount (capture);

        // "ADC PVDD" is the marker: every max98396/7 the driver bound to has one,
        // and its prefix is what separates one amp from the next on a shared card.
        for (const auto& name : names)
        {
            if (! name.endsWith ("ADC PVDD"))
                continue;

            Device d;
            d.cardIndex = card;
            d.cardId = cardId;
            d.prefix = name.dropLastCharacters (juce::String ("ADC PVDD").length()).trim();
            d.captureDevice = capture;
            d.i2cName = findI2cName (d.prefix);

            d.hasThermSlot = names.contains (qualify (d.prefix, "PCM Therm Slot"));
            d.captureChannels = captureWidth;

            for (int i = 0; i < numChannels; ++i)
                d.slots[(size_t) i] = readSlotControl (ctl, d.prefix,
                                                       channelSlotControl ((Channel) i));

            found.add (d);
        }

        snd_ctl_close (ctl);
    }

    return found;
}

void Telemetry::setDevice (const Device& d)
{
    const bool wasRunning = isThreadRunning();
    stop();

    {
        const juce::ScopedLock sl (lock);
        device = d;
    }

    if (wasRunning)
        start();
}

Device Telemetry::getDevice() const
{
    const juce::ScopedLock sl (lock);
    return device;
}

void Telemetry::setSource (Source s)
{
    if (requestedSource.exchange (s) == s)
        return;

    if (isThreadRunning())
    {
        stop();
        start();
    }
}

void Telemetry::setCaptureRate (int hz)   { captureHz.store (juce::jlimit (8000, 192000, hz)); }

void Telemetry::start()
{
    if (isThreadRunning())
        return;

    ring.clear();
    startThread (juce::Thread::Priority::normal);
}

void Telemetry::stop()
{
    signalThreadShouldExit();
    stopThread (2000);

    const juce::ScopedLock sl (lock);
    status.running = false;
}

Telemetry::Status Telemetry::getStatus() const
{
    const juce::ScopedLock sl (lock);
    return status;
}

void Telemetry::report (const juce::String& message, bool isError)
{
    const juce::ScopedLock sl (lock);
    status.message = message;
    status.isError = isError;
}

void Telemetry::setChannelsAvailable (const juce::Array<Channel>& channels)
{
    const juce::ScopedLock sl (lock);
    status.available.fill (false);

    for (auto c : channels)
        status.available[(size_t) c] = true;
}

void Telemetry::setEffectiveSource (Source s)
{
    const juce::ScopedLock sl (lock);
    status.source = s;
}

void Telemetry::run()
{
    {
        const juce::ScopedLock sl (lock);
        status.running = true;
        status.sampleRate = 0.0;
    }

    if (requestedSource.load() == Source::demo)
        runDemo();
    else
        runCapture();

    const juce::ScopedLock sl (lock);
    status.running = false;
}

//==============================================================================
bool Telemetry::runCapture()
{
    setEffectiveSource (Source::capture);

    const auto d = getDevice();

    if (! d.isValid())
    {
        report ("No amplifier selected.", true);
        return false;
    }

    if (d.captureDevice.isEmpty())
    {
        report ("this card has no capture PCM, so the SoC is not set up to receive "
                "the amps' DOUT", true);
        return false;
    }

    // Where each data type sits in the frame. The amp numbers transmit slots in
    // 8-bit units; a 32-bit TDM slot therefore holds four of them, and a 16-bit
    // datum starting at amp slot s lands in channel s/4, shifted left by
    // 16 - 8 * (s % 4) within that word. s % 4 == 3 would straddle into the next
    // channel, which the hardware allows but a channel-wise read cannot see.
    // Where each data type sits in the frame, read from the amp itself rather
    // than assumed: the slot table came from the driver's own slot controls.
    struct Tap { Channel channel; int channelIndex; int shift; bool isAdcCode; };

    juce::Array<Tap> taps;
    juce::StringArray complaints;
    int neededChannels = 0;

    for (int i = 0; i < numChannels; ++i)
    {
        const auto c = (Channel) i;
        const int slot = d.slot (c);

        if (slot < 0)
            continue;

        if (slots::straddles (slot))
        {
            complaints.add (juce::String (channelName (c)) + " is at amp slot "
                              + juce::String (slot) + ", which straddles two 32-bit "
                              "channels");
            continue;
        }

        taps.add ({ c, slots::tdmChannel (slot), slots::shift (slot), channelIsAdcCode (c) });
        neededChannels = juce::jmax (neededChannels, slots::tdmChannel (slot) + 1);
    }

    if (taps.isEmpty())
    {
        report ("no transmit slots are assigned to this amp -- set adi,vmon-slot-no / "
                "adi,imon-slot-no and adi,telemetry-enable in the device tree"
                  + (complaints.isEmpty() ? juce::String()
                                          : " (" + complaints.joinIntoString ("; ") + ")"),
                true);
        return false;
    }

    snd_pcm_t* pcm = nullptr;

    if (const int err = snd_pcm_open (&pcm, d.captureDevice.toRawUTF8(),
                                      SND_PCM_STREAM_CAPTURE, 0); err < 0)
    {
        report ("cannot open " + d.captureDevice + ": " + snd_strerror (err)
                  + (err == -EBUSY ? ", something else (PipeWire?) holds it" : ""),
                true);
        return false;
    }

    snd_pcm_hw_params_t* hw;
    snd_pcm_hw_params_alloca (&hw);
    snd_pcm_hw_params_any (pcm, hw);

    unsigned int rate = (unsigned int) captureHz.load();
    unsigned int channels = (unsigned int) neededChannels;
    snd_pcm_uframes_t period = 0;
    int dir = 0;

    const bool configured =
           snd_pcm_hw_params_set_access (pcm, hw, SND_PCM_ACCESS_RW_INTERLEAVED) >= 0
        && snd_pcm_hw_params_set_format (pcm, hw, SND_PCM_FORMAT_S32_LE) >= 0
        && snd_pcm_hw_params_set_channels_near (pcm, hw, &channels) >= 0
        && snd_pcm_hw_params_set_rate_near (pcm, hw, &rate, &dir) >= 0;

    if (! configured)
    {
        snd_pcm_close (pcm);
        report ("the capture PCM will not accept 32-bit interleaved frames", true);
        return false;
    }

    period = (snd_pcm_uframes_t) juce::jmax (64, (int) rate / 200);        // ~5 ms
    snd_pcm_hw_params_set_period_size_near (pcm, hw, &period, &dir);

    if (const int err = snd_pcm_hw_params (pcm, hw); err < 0)
    {
        snd_pcm_close (pcm);
        report (juce::String ("cannot configure the capture PCM: ") + snd_strerror (err), true);
        return false;
    }

    if ((int) channels < neededChannels)
    {
        const auto got = juce::String ((int) channels);
        snd_pcm_close (pcm);
        report ("the capture stream carries only " + got + " channels but this amp's slots "
                "reach ch" + juce::String (neededChannels - 1)
                  + " -- the amp transmits it, the SoC just is not picking those slots up. "
                    "Widen dai-tdm-slot-rx-mask-<lane> in the device tree", true);
        return false;
    }

    {
        juce::Array<Channel> present;

        for (const auto& t : taps)
            present.add (t.channel);

        setChannelsAvailable (present);

        const juce::ScopedLock sl (lock);
        status.sampleRate = rate;
    }

    ring.clear();

    juce::StringArray what;

    for (const auto& t : taps)
        what.add (juce::String (channelName (t.channel)) + "="
                    + slots::name (d.slot (t.channel)).replace (" ", ""));

    report ("Capturing " + d.captureDevice + " at " + juce::String (rate) + " Hz, "
              + juce::String ((int) channels) + " channels: " + what.joinIntoString (" ")
              + (complaints.isEmpty() ? juce::String() : "  [" + complaints.joinIntoString ("; ") + "]"),
            false);

    const int blockFrames = (int) period;
    std::vector<juce::int32> buffer ((size_t) blockFrames * channels);

    if (snd_pcm_prepare (pcm) < 0)
    {
        snd_pcm_close (pcm);
        report ("cannot prepare the capture PCM", true);
        return false;
    }

    std::array<float, (size_t) numChannels> frame {};

    while (! threadShouldExit())
    {
        const snd_pcm_sframes_t got = snd_pcm_readi (pcm, buffer.data(),
                                                     (snd_pcm_uframes_t) blockFrames);

        if (got < 0)
        {
            if (snd_pcm_recover (pcm, (int) got, 1) < 0)
            {
                report (juce::String ("Capture stopped: ") + snd_strerror ((int) got), true);
                break;
            }

            continue;
        }

        const bool msbAligned = supplyMsbAligned.load();

        for (snd_pcm_sframes_t f = 0; f < got; ++f)
        {
            const juce::int32* const words = buffer.data() + (size_t) f * channels;

            for (const auto& t : taps)
            {
                const auto datum = (juce::int16) ((words[t.channelIndex] >> t.shift) & 0xFFFF);

                // A 9-bit measurement-ADC result inside a 16-bit field; the
                // sense ADCs fill theirs and are signed.
                const int raw = t.isAdcCode
                                    ? (msbAligned ? (((int) (juce::uint16) datum >> 7) & 0x1FF)
                                                  : ((int) (juce::uint16) datum & 0x1FF))
                                    : (int) datum;

                frame[(size_t) t.channel] = scale::convert (t.channel, raw);
            }

            ring.push (frame);
        }
    }

    snd_pcm_close (pcm);
    return true;
}


//==============================================================================
/*
 * A synthesised amplifier: a 100 Hz tone into 4 ohms, with the supply sagging
 * on the current peaks the way a real rail does, and the die warming up.
 *
 * This is not a toy. Until the board's DOUT is wired back to the SoC there is no
 * way at all to see whether the instrument itself -- the timebase, the trigger,
 * the per-trace scaling, the decimation -- behaves, and a scope that has never
 * drawn a known waveform is a scope nobody should trust. It is also what the
 * off-tree render harness drives.
 */
bool Telemetry::runDemo()
{
    setEffectiveSource (Source::demo);

    const double rate = (double) captureHz.load();

    setChannelsAvailable ({ Channel::vout, Channel::iout, Channel::pvdd, Channel::temp });

    {
        const juce::ScopedLock sl (lock);
        status.sampleRate = rate;
    }

    ring.clear();

    report ("Simulated amplifier: 100 Hz into 4 ohm, 24 V rail. No hardware is being read.", false);

    constexpr double toneHz = 100.0;
    constexpr double peakVolts = 12.0;
    constexpr double loadOhms = 4.0;

    const int blockFrames = juce::jmax (32, (int) (rate / 200.0));      // ~5 ms
    const double blockMs = 1000.0 * (double) blockFrames / rate;

    double phase = 0.0;
    double sag = 0.0;
    double celsius = 38.0;
    double next = juce::Time::getMillisecondCounterHiRes();

    std::array<float, (size_t) numChannels> frame {};

    while (! threadShouldExit())
    {
        for (int i = 0; i < blockFrames; ++i)
        {
            const double v = peakVolts * std::sin (phase);
            const double a = v / loadOhms;

            // A first-order rail droop driven by the current draw, plus the
            // 2 x tone ripple a rectified supply actually shows.
            const double draw = std::abs (a) * 0.35 + 0.25 * std::sin (2.0 * phase);
            sag += (draw - sag) * 0.002;

            frame[(size_t) Channel::vout] = (float) v;
            frame[(size_t) Channel::iout] = (float) a;
            frame[(size_t) Channel::pvdd] = (float) (24.0 - sag);
            frame[(size_t) Channel::temp] = (float) celsius;

            ring.push (frame);

            phase += 2.0 * juce::MathConstants<double>::pi * toneHz / rate;

            if (phase >= 2.0 * juce::MathConstants<double>::pi)
                phase -= 2.0 * juce::MathConstants<double>::pi;

            celsius += (56.0 - celsius) * 2.0e-7;
        }

        next += blockMs;
        const double now = juce::Time::getMillisecondCounterHiRes();

        if (next < now)
            next = now;

        wait (juce::jmax (1, juce::roundToInt (next - now)));
    }

    return true;
}

} // namespace amp
