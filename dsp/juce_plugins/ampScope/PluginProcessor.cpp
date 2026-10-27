#include "PluginProcessor.h"
#include "PluginEditor.h"
#include "ScopeView.h"

namespace
{
    juce::String suffixFor (amp::Channel c)
    {
        return juce::String (amp::channelName (c)).toLowerCase();
    }

    /** Sensible starting units/division per quantity, and how far below centre
        the trace sits. A rail belongs near the bottom of the screen; a swing
        belongs on the centre line. */
    struct TraceDefault { float unitsPerDiv, offsetDivs, maxUnitsPerDiv; };

    TraceDefault defaultsFor (amp::Channel c)
    {
        switch (c)
        {
            case amp::Channel::vout: return { 5.0f,   0.0f, 20.0f };
            case amp::Channel::iout: return { 1.0f,   0.0f,  4.0f };
            case amp::Channel::pvdd: return { 4.0f,  -3.0f, 16.0f };
            case amp::Channel::vbat: return { 1.0f,  -3.0f,  4.0f };
            case amp::Channel::temp: return { 20.0f, -3.0f, 60.0f };
            case amp::Channel::count: break;
        }

        return { 1.0f, 0.0f, 10.0f };
    }

    /*
     * Value formatting belongs on the PARAMETER, not on the slider.
     * SliderParameterAttachment installs its own textFromValueFunction in its
     * constructor -- it renders the parameter's text -- so anything the editor
     * sets on the slider beforehand is overwritten and anything it sets
     * afterwards diverges from what the host shows. Putting it here makes the
     * front panel and the host's automation lane read the same.
     */
    juce::String divisionsText (float v, int)   { return juce::String (v, 2) + " div"; }
    juce::String timeText      (float v, int)   { return ScopeStyle::formatTime ((double) v); }
}

juce::String AmpScopeProcessor::showId   (amp::Channel c) { return "show_"   + suffixFor (c); }
juce::String AmpScopeProcessor::scaleId  (amp::Channel c) { return "scale_"  + suffixFor (c); }
juce::String AmpScopeProcessor::offsetId (amp::Channel c) { return "offset_" + suffixFor (c); }

//==============================================================================
juce::AudioProcessorValueTreeState::ParameterLayout AmpScopeProcessor::createParameterLayout()
{
    using namespace juce;

    AudioProcessorValueTreeState::ParameterLayout layout;

    layout.add (std::make_unique<AudioParameterBool> (ParameterID { "run", 1 }, "Run", true));

    // Timebase, as seconds per division: ten divisions make the window, which is
    // how every bench scope labels it.
    layout.add (std::make_unique<AudioParameterFloat> (
        ParameterID { "timePerDiv", 1 }, "Time/div",
        NormalisableRange<float> (10.0e-6f, 1.0f, 0.0f, 0.18f), 2.0e-3f,
        AudioParameterFloatAttributes().withStringFromValueFunction (timeText)));

    layout.add (std::make_unique<AudioParameterChoice> (
        ParameterID { "trigMode", 1 }, "Trigger mode",
        StringArray { "Free run", "Auto", "Normal", "Single" }, 1));

    StringArray channelNames;

    for (int c = 0; c < amp::numChannels; ++c)
        channelNames.add (amp::channelName ((amp::Channel) c));

    layout.add (std::make_unique<AudioParameterChoice> (
        ParameterID { "trigSource", 1 }, "Trigger source", channelNames, (int) amp::Channel::vout));

    layout.add (std::make_unique<AudioParameterChoice> (
        ParameterID { "trigEdge", 1 }, "Trigger edge", StringArray { "Rising", "Falling" }, 0));

    // In divisions from the centre line, exactly like the knob on the front panel:
    // it means the same thing whatever units the source trace is showing.
    layout.add (std::make_unique<AudioParameterFloat> (
        ParameterID { "trigLevel", 1 }, "Trigger level",
        NormalisableRange<float> (-4.0f, 4.0f, 0.01f), 0.0f,
        AudioParameterFloatAttributes().withStringFromValueFunction (divisionsText)));

    //--- acquisition ----------------------------------------------------------
    // The capture stream is the only real data path; Demo synthesises one so the
    // instrument can be checked without hardware.
    layout.add (std::make_unique<AudioParameterChoice> (
        ParameterID { "source", 1 }, "Source",
        StringArray { "Capture", "Demo" }, 0));

    layout.add (std::make_unique<AudioParameterChoice> (
        ParameterID { "captureRate", 1 }, "Capture rate",
        StringArray { "44100", "48000", "88200", "96000", "192000" }, 1));

    layout.add (std::make_unique<AudioParameterChoice> (
        ParameterID { "supplyAlign", 1 }, "Supply alignment",
        StringArray { "MSB", "LSB" }, 0));

    //--- per-trace ------------------------------------------------------------
    for (int c = 0; c < amp::numChannels; ++c)
    {
        const auto channel = (amp::Channel) c;
        const auto name = juce::String (amp::channelName (channel));
        const auto def = defaultsFor (channel);

        layout.add (std::make_unique<AudioParameterBool> (
            ParameterID { showId (channel), 1 }, name + " show",
            channel == amp::Channel::vout || channel == amp::Channel::iout
                                          || channel == amp::Channel::pvdd));

        layout.add (std::make_unique<AudioParameterFloat> (
            ParameterID { scaleId (channel), 1 }, name + " /div",
            NormalisableRange<float> (def.maxUnitsPerDiv / 400.0f, def.maxUnitsPerDiv, 0.0f, 0.4f),
            def.unitsPerDiv,
            AudioParameterFloatAttributes().withStringFromValueFunction (
                [channel] (float v, int) { return ScopeStyle::format (channel, v, 3) + "/div"; })));

        layout.add (std::make_unique<AudioParameterFloat> (
            ParameterID { offsetId (channel), 1 }, name + " offset",
            NormalisableRange<float> (-4.0f, 4.0f, 0.01f), def.offsetDivs,
            AudioParameterFloatAttributes().withStringFromValueFunction (divisionsText)));
    }

    return layout;
}

//==============================================================================
AmpScopeProcessor::AmpScopeProcessor()
    : AudioProcessor (BusesProperties().withInput  ("Input",  juce::AudioChannelSet::mono(), true)
                                       .withOutput ("Output", juce::AudioChannelSet::mono(), true)),
      apvts (*this, nullptr, "PARAMS", createParameterLayout())
{
    refreshDevices();
    applyAcquisitionParameters();
}

AmpScopeProcessor::~AmpScopeProcessor()
{
    telemetry.stop();
}

void AmpScopeProcessor::prepareToPlay (double, int) {}
void AmpScopeProcessor::releaseResources() {}

bool AmpScopeProcessor::isBusesLayoutSupported (const BusesLayout& layouts) const
{
    const auto& in  = layouts.getMainInputChannelSet();
    const auto& out = layouts.getMainOutputChannelSet();

    return in == out && ! out.isDisabled() && out.size() <= 2;
}

void AmpScopeProcessor::processBlock (juce::AudioBuffer<float>& buffer, juce::MidiBuffer&)
{
    juce::ScopedNoDenormals noDenormals;

    // A scope is not in the signal path. Anything the host gave us beyond the
    // input channel count still has to be cleared, or it carries junk.
    for (int ch = getTotalNumInputChannels(); ch < getTotalNumOutputChannels(); ++ch)
        buffer.clear (ch, 0, buffer.getNumSamples());
}

//==============================================================================
juce::Array<amp::Device> AmpScopeProcessor::refreshDevices()
{
    auto found = amp::Telemetry::discover();

    Selection sel;

    {
        const juce::ScopedLock sl (selectionLock);
        knownDevices = found;
        sel = selection;
    }

    // Re-point the acquisition: at the remembered amp if it is still there, at
    // the first one otherwise.
    const amp::Device* chosen = nullptr;

    for (const auto& d : found)
        if (d.key() == sel.deviceKey)
            chosen = &d;

    if (chosen == nullptr && ! found.isEmpty())
        chosen = &found.getReference (0);

    if (chosen != nullptr)
    {
        auto device = *chosen;

        if (sel.captureDevice.isNotEmpty())
            device.captureDevice = sel.captureDevice;

        // Whatever simply has to be on for this amp to transmit gets switched on
        // here, every time we point at it. It is idempotent, and doing it on
        // every refresh means a driver reload or a cold boot needs no ceremony.
        const auto error = amp::Telemetry::applyBootConfiguration (device);

        {
            const juce::ScopedLock sl (selectionLock);
            selection.deviceKey = device.key();
            configurationError = error;
        }

        telemetry.setDevice (device);
        selectionVersion.fetch_add (1);
    }

    return found;
}

void AmpScopeProcessor::setSlotFor (amp::Channel channel, int ampSlot)
{
    const auto device = telemetry.getDevice();
    const auto error = amp::Telemetry::setSlot (device, channel, ampSlot);

    {
        const juce::ScopedLock sl (selectionLock);
        configurationError = error;
    }

    // Read the layout back rather than assuming the write took: the driver may
    // refuse a slot, and the scope demultiplexes on what the amp actually says.
    refreshDevices();
}

juce::String AmpScopeProcessor::getConfigurationError() const
{
    const juce::ScopedLock sl (selectionLock);
    return configurationError;
}

juce::Array<amp::Device> AmpScopeProcessor::getKnownDevices() const
{
    const juce::ScopedLock sl (selectionLock);
    return knownDevices;
}

AmpScopeProcessor::Selection AmpScopeProcessor::getSelection() const
{
    const juce::ScopedLock sl (selectionLock);
    return selection;
}

void AmpScopeProcessor::setSelection (const Selection& s)
{
    {
        const juce::ScopedLock sl (selectionLock);
        selection = s;
    }

    selectionVersion.fetch_add (1);
    refreshDevices();
}

void AmpScopeProcessor::applyAcquisitionParameters()
{
    const auto sourceIndex = (int) apvts.getRawParameterValue ("source")->load();
    const auto captureIndex = (int) apvts.getRawParameterValue ("captureRate")->load();

    static const int captureRates[] { 44100, 48000, 88200, 96000, 192000 };

    telemetry.setCaptureRate (captureRates[juce::jlimit (0, 4, captureIndex)]);
    telemetry.setSupplyMsbAligned ((int) apvts.getRawParameterValue ("supplyAlign")->load() == 0);
    telemetry.setSource (sourceIndex == 1 ? amp::Telemetry::Source::demo
                                          : amp::Telemetry::Source::capture);
}

//==============================================================================
juce::AudioProcessorEditor* AmpScopeProcessor::createEditor()
{
    return new AmpScopeEditor (*this);
}

void AmpScopeProcessor::getStateInformation (juce::MemoryBlock& destData)
{
    auto state = apvts.copyState();

    {
        const juce::ScopedLock sl (selectionLock);

        auto amp = state.getOrCreateChildWithName ("AMP", nullptr);
        amp.setProperty ("device", selection.deviceKey, nullptr);
        amp.setProperty ("capture", selection.captureDevice, nullptr);
    }

    if (auto xml = state.createXml())
        copyXmlToBinary (*xml, destData);
}

void AmpScopeProcessor::setStateInformation (const void* data, int sizeInBytes)
{
    auto xml = getXmlFromBinary (data, sizeInBytes);

    if (xml == nullptr || ! xml->hasTagName (apvts.state.getType()))
        return;

    auto state = juce::ValueTree::fromXml (*xml);

    Selection sel;

    if (auto amp = state.getChildWithName ("AMP"); amp.isValid())
    {
        sel.deviceKey = amp.getProperty ("device").toString();
        sel.captureDevice = amp.getProperty ("capture").toString();
        state.removeChild (amp, nullptr);
    }

    apvts.replaceState (state);

    setSelection (sel);
    applyAcquisitionParameters();
}

//==============================================================================
juce::AudioProcessor* JUCE_CALLTYPE createPluginFilter()
{
    return new AmpScopeProcessor();
}
