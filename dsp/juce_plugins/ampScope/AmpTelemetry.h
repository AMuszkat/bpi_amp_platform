#pragma once

#include <juce_core/juce_core.h>
#include <array>
#include <atomic>
#include <vector>

//==============================================================================
/** Reading a MAX98396/MAX98397 amplifier's own measurements out of the kernel.

    NOTHING HERE TOUCHES HARDWARE.  Everything goes through the max98396 codec
    driver, via the two userspace interfaces that driver publishes -- snd_ctl_*
    and snd_pcm_*.  There is no /dev/i2c access and no register poking; if a
    measurement is not reachable through one of those, the answer is to teach the
    driver, not to go around it.

    The two interfaces have distinct jobs here, and only one of them carries data:

      - the CAPTURE STREAM is the ONLY data path.  Voltage sense, current sense,
        the PVDD and VBAT measurement-ADC rails and the die temperature are all
        transmitted in TDM slots at the PCM sample rate, which is what a PCM
        device is for, and it is the only way any of them arrives fast enough to
        be a waveform.  Nothing is ever plotted from anywhere else.

      - the CONTROL INTERFACE is how the amp is CONFIGURED to send that data.
        The driver turns those writes into I2C register writes: closing the IV
        sense path, and saying which TDM slot each data type occupies.  Whatever
        simply has to be on is written once at start-up; the slot assignment is a
        choice, because the slots are a shared budget, so it is left to the user.

    Several amps share one card and one DOUT line -- they arbitrate purely by
    Hi-Z, so two amps must never be given the same slot -- which is why a device
    here is one amplifier, identified by the control-name prefix its device tree
    node was given (sound-name-prefix = "amp1"), not by the card.

    libasound is called directly because JUCE's device layer exposes neither
    interface: it has no notion of mixer controls, and it would not let us pick
    raw TDM slots out of a capture stream.
*/
namespace amp
{

//==============================================================================
/** The quantities the amp can put on the bus, in the order they are listed. */
enum class Channel
{
    vout = 0,   ///< speaker output voltage, V   -- VMON, 16-bit signed
    iout,       ///< speaker output current, A   -- IMON, 16-bit signed
    pvdd,       ///< PVDD supply rail, V         -- Supply1, 9-bit ADC code
    vbat,       ///< VBAT supply rail, V         -- Supply2, 9-bit ADC code
    temp,       ///< die temperature, degC       -- Therm, 9-bit ADC code ('97)
    count
};

constexpr int numChannels = (int) Channel::count;

const char* channelName (Channel);
const char* channelUnit (Channel);

/** The driver's control-name stem for this data type's slot, e.g. "VMON" gives
    "amp1 PCM VMON Slot". */
const char* channelSlotControl (Channel);

/** True when this quantity is a 9-bit measurement-ADC code in a 16-bit field
    rather than a full 16-bit sense-ADC result. */
bool channelIsAdcCode (Channel);

//==============================================================================
/** One amplifier, as the kernel presents it. */
struct Device
{
    int          cardIndex = -1;
    juce::String cardId;                ///< "BPICM4IO"
    juce::String prefix;                ///< "amp1"; empty when the node has no sound-name-prefix
    juce::String i2cName;               ///< "2-0039", when the sysfs entry could be matched
    juce::String captureDevice;         ///< "hw:0,1", the card's first capture PCM (empty if none)

    /** Whether the part has the '97's extra transmit data types: it is the
        presence of the control that says so, not a part number. */
    bool hasThermSlot = false;

    /** How many channels the capture PCM can actually deliver, which is set by
        the CPU DAI's dai-tdm-slot-rx-mask-<lane> -- NOT by the amp, which has 64
        transmit slots, nor by the SoC, which could take the whole frame. A slot
        beyond this is transmitted and simply never picked up, so the slot menu
        has to know the number or it offers choices that cannot work. 0 = unknown. */
    int captureChannels = 0;

    /** Which transmit slot each data type occupies, in the amp's own 8-bit slot
        numbering; -1 = not transmitted. Read from, and written back through, the
        driver's "PCM <x> Slot" controls. */
    std::array<int, (size_t) numChannels> slots { { -1, -1, -1, -1, -1 } };

    int  slot (Channel c) const         { return slots[(size_t) c]; }
    bool isValid() const                { return cardIndex >= 0; }
    bool canCapture() const;

    /** Stable identity across rescans, so a saved session finds its amp again. */
    juce::String key() const            { return cardId + "/" + prefix; }
    juce::String label() const;
};

//==============================================================================
/** A single-producer / single-consumer ring of scope samples.

    The acquisition thread appends frames; the editor reads the most recent
    window. Capacity is a power of two so the wrap is a mask.
*/
class TraceRing
{
public:
    void setCapacity (int frames);
    int  getCapacity() const noexcept               { return capacity; }

    /** Acquisition thread only. */
    void push (const std::array<float, (size_t) numChannels>& frame) noexcept;
    void clear() noexcept;

    /** Total frames ever written -- the scope's time origin. */
    juce::uint64 getWritePos() const noexcept       { return writePos.load (std::memory_order_acquire); }

    /** Reader: frame `pos` counted from the start of time. Callers clamp against
        getWritePos() first. */
    float read (Channel c, juce::uint64 pos) const noexcept;

private:
    std::array<std::vector<float>, (size_t) numChannels> data;
    int capacity = 0, mask = 0;
    std::atomic<juce::uint64> writePos { 0 };
};

//==============================================================================
/** Acquisition and configuration. Owns a thread that reads the capture stream. */
class Telemetry : private juce::Thread
{
public:
    enum class Source
    {
        capture,   ///< the amp's DOUT telemetry -- the real thing
        demo       ///< a synthesised amplifier, for checking the instrument itself
    };

    struct Status
    {
        bool         running = false;
        Source       source  = Source::capture;
        double       sampleRate = 0.0;      ///< frames per second actually achieved
        juce::String message;               ///< what it is doing, or why it is not
        bool         isError = false;
        /** Channels this amp is currently transmitting. */
        std::array<bool, (size_t) numChannels> available { {} };
    };

    Telemetry();
    ~Telemetry() override;

    //--- discovery ------------------------------------------------------------
    /** Rescan the sound cards. Safe from the message thread; blocks briefly. */
    static juce::Array<Device> discover();

    //--- configuration, through the driver's controls --------------------------
    /** Switch on whatever simply has to be on for this amp to transmit: the IV
        sense path (a DAPM switch, so userspace has to close it) and the
        measurement-ADC rails behind the supply data types. Called at start-up
        and whenever the selected amp changes; it is idempotent.
        Returns an empty string on success, or what went wrong. */
    static juce::String applyBootConfiguration (const Device&);

    /** Move one data type to a transmit slot, or to -1 for "not transmitted".
        The driver gives the old slot back to the bus before claiming the new one
        and switches that data type's transmit enable to match. */
    static juce::String setSlot (const Device&, Channel, int ampSlot);

    //--- acquisition ----------------------------------------------------------
    void setDevice (const Device&);
    Device getDevice() const;

    void setSource (Source);
    Source getSource() const noexcept               { return requestedSource; }

    /** PCM capture rate to ask the card for. */
    void setCaptureRate (int hz);
    int  getCaptureRate() const noexcept            { return captureHz.load(); }

    /** The supply and temperature data types are 9-bit measurement-ADC results
        carried in a 16-bit field. The datasheet does not say which end they sit
        at, so this is switchable: true = the 9 bits are the top of the field. */
    void setSupplyMsbAligned (bool b)               { supplyMsbAligned = b; }

    void start();
    void stop();

    Status getStatus() const;
    const TraceRing& getRing() const noexcept       { return ring; }

    /** The record length, in frames. Fixed for the life of the object: the
        acquisition thread is the ring's only writer, and reallocating it while
        the editor reads would pull the storage out from under it. How much TIME
        that is depends on the rate, which is why the scope clamps its window
        against the record rather than the other way round. */
    static constexpr int recordFrames = 1 << 19;    // ~10.9 s at 48 kHz, 12.6 MB

private:
    void run() override;
    bool runCapture();
    bool runDemo();
    void report (const juce::String& message, bool isError);
    void setChannelsAvailable (const juce::Array<Channel>&);
    void setEffectiveSource (Source);

    TraceRing ring;

    mutable juce::CriticalSection lock;
    Device device;
    Status status;

    std::atomic<Source> requestedSource { Source::capture };
    std::atomic<int>    captureHz { 48000 };
    std::atomic<bool>   supplyMsbAligned { true };

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (Telemetry)
};

//==============================================================================
/** How a 16-bit TDM field maps onto a 32-bit capture channel.

    The amp numbers its transmit slots in 8-bit units, so one 32-bit TDM slot
    holds four of them. Only two of the four starting positions are worth
    offering -- the top and bottom halves of the word -- which is also all the
    room there is: two 16-bit data types per channel.
*/
namespace slots
{
    constexpr int perChannel = 4;                       ///< 8-bit amp slots per 32-bit TDM slot

    inline int fromChannelHalf (int tdmChannel, bool lowHalf)
    {
        return tdmChannel * perChannel + (lowHalf ? 2 : 0);
    }

    inline int tdmChannel (int ampSlot)     { return ampSlot / perChannel; }
    inline bool isLowHalf (int ampSlot)     { return (ampSlot % perChannel) >= 2; }

    /** Bit position of the datum inside the 32-bit word, MSB first. */
    inline int shift (int ampSlot)          { return 16 - 8 * (ampSlot % perChannel); }

    /** A 16-bit datum starting in the last 8-bit slot of a word runs off the end
        of it, so a channel-wise read cannot see it. */
    inline bool straddles (int ampSlot)     { return (ampSlot % perChannel) == 3; }

    juce::String name (int ampSlot);        ///< "ch2 hi", "ch2 lo", "off"
}

//==============================================================================
/** Datasheet conversions, shared with the UI so the axis labels agree with the data.

    MAX98397 (Rev 3): speaker voltage ADC 16-bit over +/-30 V, speaker current
    ADC 16-bit over +/-8.3 A, measurement ADC 9-bit with 54.703125 mV per code on
    PVDD, 11.3965 mV on VBAT, and 1 degC per code minus 252 degC on the thermal
    channel.
*/
namespace scale
{
    constexpr float voutFullScale = 30.0f;      // V, peak
    constexpr float ioutFullScale = 8.3f;       // A, peak

    inline float vout (int sample16)    { return (float) sample16 * voutFullScale / 32768.0f; }
    inline float iout (int sample16)    { return (float) sample16 * ioutFullScale / 32768.0f; }
    inline float pvdd (int code9)       { return (float) code9 * 0.054703125f; }
    inline float vbat (int code9)       { return (float) code9 * 0.0113965f; }
    inline float temp (int code9)       { return (float) code9 - 252.0f; }

    /** Convert a raw field for a channel, chosen by what that channel is. */
    float convert (Channel, int raw);
}

} // namespace amp
