#include "PluginProcessor.h"

#if ! SPEAKER_HEADLESS
 #include "PluginEditor.h"
#endif

namespace
{
    /** Default contour sets, quietest first.

        Every set starts FLAT: all gains 0 dB, so the plugin does nothing until contours are
        dialled in. Only the thresholds (where each set takes over) and the shelf frequency /
        Q starting points are pre-set. The Fletcher-Munson shape this is for -- the ear losing
        low-frequency sensitivity as level drops, so the quietest setting wants the most bass
        lift and the reference setting stays flat -- is now the user's to draw on the pad.
    */
    struct SettingDefaults
    {
        float thresholdDb;
        float gain1, freq1, q1;
        float gain2, freq2, q2;
    };

    constexpr SettingDefaults defaultSettings[LoudnessIDs::numSettings] =
    {
        { -60.0f, 0.0f, 60.0f, 0.7f,  0.0f, 160.0f, 0.7f },   // very quiet
        { -40.0f, 0.0f, 60.0f, 0.7f,  0.0f, 160.0f, 0.7f },
        { -25.0f, 0.0f, 60.0f, 0.7f,  0.0f, 160.0f, 0.7f },
        { -12.0f, 0.0f, 60.0f, 0.7f,  0.0f, 160.0f, 0.7f },   // reference
    };
}

//==============================================================================
LoudnessProcessor::LoudnessProcessor()
#ifndef JucePlugin_PreferredChannelConfigurations
     : AudioProcessor (BusesProperties()
                        #if ! JucePlugin_IsMidiEffect
                         #if ! JucePlugin_IsSynth
                           .withInput  ("Input",  juce::AudioChannelSet::mono(), true)
                         #endif
                           .withOutput ("Output", juce::AudioChannelSet::mono(), true)
                        #endif
                       ),
#endif
       apvts (*this, nullptr, "PARAMETERS", createParameterLayout())
{
    for (int s = 0; s < LoudnessIDs::numSettings; ++s)
    {
        thresholdParam[(size_t) s] = apvts.getRawParameterValue (LoudnessIDs::threshold (s));

        for (int b = 0; b < LoudnessIDs::numBands; ++b)
        {
            gainParam[(size_t) s][(size_t) b] = apvts.getRawParameterValue (LoudnessIDs::band (s, b, "gain"));
            freqParam[(size_t) s][(size_t) b] = apvts.getRawParameterValue (LoudnessIDs::band (s, b, "freq"));
            qParam[(size_t) s][(size_t) b]    = apvts.getRawParameterValue (LoudnessIDs::band (s, b, "q"));
        }
    }

    modeParam       = apvts.getRawParameterValue (LoudnessIDs::mode);
    attackParam     = apvts.getRawParameterValue (LoudnessIDs::attack);
    releaseParam    = apvts.getRawParameterValue (LoudnessIDs::release);
    hysteresisParam = apvts.getRawParameterValue (LoudnessIDs::hysteresis);
    glideParam      = apvts.getRawParameterValue (LoudnessIDs::glide);
    detectorParam   = apvts.getRawParameterValue (LoudnessIDs::detector);
    bypassParam     = apvts.getRawParameterValue (LoudnessIDs::bypass);
}

LoudnessProcessor::~LoudnessProcessor() = default;

//==============================================================================
juce::AudioProcessorValueTreeState::ParameterLayout LoudnessProcessor::createParameterLayout()
{
    juce::AudioProcessorValueTreeState::ParameterLayout layout;

    // Shelf frequency: skewed so the useful 40-200 Hz region gets most of the travel.
    const juce::NormalisableRange<float> freqRange { 20.0f, 2000.0f, 0.1f, 0.30f };
    const juce::NormalisableRange<float> gainRange { -24.0f, 24.0f, 0.1f };
    const juce::NormalisableRange<float> qRange    { 0.1f, 4.0f, 0.01f, 0.6f };
    const juce::NormalisableRange<float> thrRange  { -90.0f, 0.0f, 0.1f };

    for (int s = 0; s < LoudnessIDs::numSettings; ++s)
    {
        const auto& d = defaultSettings[s];
        const auto tag = juce::String (s + 1);

        layout.add (std::make_unique<juce::AudioParameterFloat> (
            juce::ParameterID { LoudnessIDs::threshold (s), 1 },
            "Set " + tag + " Threshold", thrRange, d.thresholdDb,
            juce::AudioParameterFloatAttributes().withLabel ("dBFS")));

        const float gains[] { d.gain1, d.gain2 };
        const float freqs[] { d.freq1, d.freq2 };
        const float qs[]    { d.q1,    d.q2    };

        for (int b = 0; b < LoudnessIDs::numBands; ++b)
        {
            const auto name = "Set " + tag + " EQ" + juce::String (b + 1) + " ";

            layout.add (std::make_unique<juce::AudioParameterFloat> (
                juce::ParameterID { LoudnessIDs::band (s, b, "gain"), 1 },
                name + "Gain", gainRange, gains[b],
                juce::AudioParameterFloatAttributes().withLabel ("dB")));

            layout.add (std::make_unique<juce::AudioParameterFloat> (
                juce::ParameterID { LoudnessIDs::band (s, b, "freq"), 1 },
                name + "Freq", freqRange, freqs[b],
                juce::AudioParameterFloatAttributes().withLabel ("Hz")));

            layout.add (std::make_unique<juce::AudioParameterFloat> (
                juce::ParameterID { LoudnessIDs::band (s, b, "q"), 1 },
                name + "Q", qRange, qs[b]));
        }
    }

    layout.add (std::make_unique<juce::AudioParameterChoice> (
        juce::ParameterID { LoudnessIDs::mode, 1 }, "Mode",
        juce::StringArray { "Auto", "Force 1", "Force 2", "Force 3", "Force 4" }, 0));

    layout.add (std::make_unique<juce::AudioParameterChoice> (
        juce::ParameterID { LoudnessIDs::detector, 1 }, "Detector",
        juce::StringArray { "RMS", "Peak" }, 0));

    // VU-like defaults: slow enough that the contour does not pump on transients.
    layout.add (std::make_unique<juce::AudioParameterFloat> (
        juce::ParameterID { LoudnessIDs::attack, 1 }, "Detector Attack",
        juce::NormalisableRange<float> { 1.0f, 1000.0f, 0.1f, 0.4f }, 100.0f,
        juce::AudioParameterFloatAttributes().withLabel ("ms")));

    layout.add (std::make_unique<juce::AudioParameterFloat> (
        juce::ParameterID { LoudnessIDs::release, 1 }, "Detector Release",
        juce::NormalisableRange<float> { 1.0f, 3000.0f, 0.1f, 0.4f }, 300.0f,
        juce::AudioParameterFloatAttributes().withLabel ("ms")));

    layout.add (std::make_unique<juce::AudioParameterFloat> (
        juce::ParameterID { LoudnessIDs::hysteresis, 1 }, "Hysteresis",
        juce::NormalisableRange<float> { 0.0f, 12.0f, 0.1f }, 3.0f,
        juce::AudioParameterFloatAttributes().withLabel ("dB")));

    layout.add (std::make_unique<juce::AudioParameterFloat> (
        juce::ParameterID { LoudnessIDs::glide, 1 }, "Glide",
        juce::NormalisableRange<float> { 1.0f, 1000.0f, 1.0f, 0.5f }, 120.0f,
        juce::AudioParameterFloatAttributes().withLabel ("ms")));

    layout.add (std::make_unique<juce::AudioParameterBool> (
        juce::ParameterID { LoudnessIDs::bypass, 1 }, "Bypass", false));

    return layout;
}

#if SPEAKER_CONTROL
//==============================================================================
// The control build is a remote control: its editor drives these parameters, and the speaker
// running the headless build does the processing. It never processes audio itself, so the
// DSP sources (LoudnessDsp.cpp) are not compiled in and the audio passes through untouched.
void LoudnessProcessor::prepareToPlay (double, int) {}
void LoudnessProcessor::reset() {}
void LoudnessProcessor::processBlock (juce::AudioBuffer<float>&, juce::MidiBuffer&) {}
#endif

void LoudnessProcessor::releaseResources() {}

//==============================================================================
#ifndef JucePlugin_PreferredChannelConfigurations
bool LoudnessProcessor::isBusesLayoutSupported (const BusesLayout& layouts) const
{
  #if JucePlugin_IsMidiEffect
    juce::ignoreUnused (layouts);
    return true;
  #else
    if (layouts.getMainOutputChannelSet() != juce::AudioChannelSet::mono())
        return false;

   #if ! JucePlugin_IsSynth
    if (layouts.getMainOutputChannelSet() != layouts.getMainInputChannelSet())
        return false;
   #endif

    return true;
  #endif
}
#endif

void LoudnessProcessor::getLiveBandParams (int bandIndex, float& gainDb, float& freqHz, float& q) const noexcept
{
    jassert (juce::isPositiveAndBelow (bandIndex, LoudnessIDs::numBands));
    gainDb = liveGain[(size_t) bandIndex].load (std::memory_order_relaxed);
    freqHz = liveFreq[(size_t) bandIndex].load (std::memory_order_relaxed);
    q      = liveQ[(size_t) bandIndex].load (std::memory_order_relaxed);
}

//==============================================================================
#if SPEAKER_HEADLESS
// The headless build runs on the speaker, where nobody opens a window.
bool LoudnessProcessor::hasEditor() const                                   { return false; }
juce::AudioProcessorEditor* LoudnessProcessor::createEditor()               { return nullptr; }
#else
bool LoudnessProcessor::hasEditor() const                                   { return true; }
juce::AudioProcessorEditor* LoudnessProcessor::createEditor()               { return new LoudnessEditor (*this); }
#endif
const juce::String LoudnessProcessor::getName() const                       { return JucePlugin_Name; }
bool LoudnessProcessor::isMidiEffect() const                                { return false; }
double LoudnessProcessor::getTailLengthSeconds() const                      { return 0.0; }
int LoudnessProcessor::getNumPrograms()                                     { return 1; }
int LoudnessProcessor::getCurrentProgram()                                  { return 0; }
void LoudnessProcessor::setCurrentProgram (int)                             {}
const juce::String LoudnessProcessor::getProgramName (int)                  { return {}; }
void LoudnessProcessor::changeProgramName (int, const juce::String&)        {}

bool LoudnessProcessor::acceptsMidi() const
{
   #if JucePlugin_WantsMidiInput
    return true;
   #else
    return false;
   #endif
}

bool LoudnessProcessor::producesMidi() const
{
   #if JucePlugin_ProducesMidiOutput
    return true;
   #else
    return false;
   #endif
}

//==============================================================================
namespace
{
    const juce::Identifier viewTag ("VIEW");
    const juce::Identifier minHzId ("minHz"), maxHzId ("maxHz"), bottomDbId ("bottomDb"), topDbId ("topDb");
}

LoudnessProcessor::ViewLimits LoudnessProcessor::ViewLimits::sanitised() const noexcept
{
    ViewLimits v = *this;
    const ViewLimits defaults;

    v.minHz = juce::jlimit (lowestHz, highestHz, std::isfinite (v.minHz) ? v.minHz : defaults.minHz);
    v.maxHz = juce::jlimit (lowestHz, highestHz, std::isfinite (v.maxHz) ? v.maxHz : defaults.maxHz);

    if (v.maxHz < v.minHz * minRatio)
    {
        v.minHz = defaults.minHz;
        v.maxHz = defaults.maxHz;
    }

    v.bottomDb = juce::jlimit (lowestDb, highestDb, std::isfinite (v.bottomDb) ? v.bottomDb : defaults.bottomDb);
    v.topDb    = juce::jlimit (lowestDb, highestDb, std::isfinite (v.topDb) ? v.topDb : defaults.topDb);

    if (v.topDb < v.bottomDb + minSpanDb)
    {
        v.bottomDb = defaults.bottomDb;
        v.topDb    = defaults.topDb;
    }

    return v;
}

LoudnessProcessor::ViewLimits LoudnessProcessor::getViewLimits() const noexcept
{
    const juce::SpinLock::ScopedLockType lock (viewLock);
    return viewLimits;
}

void LoudnessProcessor::setViewLimits (const ViewLimits& newLimits) noexcept
{
    {
        const juce::SpinLock::ScopedLockType lock (viewLock);
        viewLimits = newLimits.sanitised();
    }

    viewVersion.fetch_add (1, std::memory_order_release);
}

void LoudnessProcessor::getStateInformation (juce::MemoryBlock& destData)
{
    // copyState() is a deep copy, so adding the view to it cannot race the live tree.
    auto state = apvts.copyState();
    const auto view = getViewLimits();

    juce::ValueTree viewTree (viewTag);
    viewTree.setProperty (minHzId, view.minHz, nullptr);
    viewTree.setProperty (maxHzId, view.maxHz, nullptr);
    viewTree.setProperty (bottomDbId, view.bottomDb, nullptr);
    viewTree.setProperty (topDbId, view.topDb, nullptr);
    state.appendChild (viewTree, nullptr);

    if (auto xml = state.createXml())
        copyXmlToBinary (*xml, destData);
}

void LoudnessProcessor::setStateInformation (const void* data, int sizeInBytes)
{
    if (auto xml = getXmlFromBinary (data, sizeInBytes))
    {
        auto state = juce::ValueTree::fromXml (*xml);

        // Sessions saved before the view existed simply keep the default view.
        if (auto viewTree = state.getChildWithName (viewTag); viewTree.isValid())
        {
            const ViewLimits defaults;
            ViewLimits view;
            view.minHz    = (float) viewTree.getProperty (minHzId, defaults.minHz);
            view.maxHz    = (float) viewTree.getProperty (maxHzId, defaults.maxHz);
            view.bottomDb = (float) viewTree.getProperty (bottomDbId, defaults.bottomDb);
            view.topDb    = (float) viewTree.getProperty (topDbId, defaults.topDb);
            setViewLimits (view);

            state.removeChild (viewTree, nullptr);  // not APVTS state; keep it out of the tree
        }

        apvts.replaceState (state);
    }
}

//==============================================================================
juce::AudioProcessor* JUCE_CALLTYPE createPluginFilter()
{
    return new LoudnessProcessor();
}
