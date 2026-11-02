/*
  ==============================================================================

    This file contains the basic framework code for a JUCE plugin processor.

    The parameters, the state and the plot helpers: compiled into every build (complete,
    control, headless). The audio processing is in FirConvDsp.cpp, which the control build
    leaves out (DSP in CMakeLists.txt); the editor in PluginEditor.cpp and PlotComponent.cpp,
    which the headless build leaves out (EDITOR).

  ==============================================================================
*/

#include "PluginProcessor.h"

#if ! SPEAKER_HEADLESS
 #include "PluginEditor.h"
#endif

#include <cmath>

//==============================================================================
FirConvProcessor::FirConvProcessor()
#ifndef JucePlugin_PreferredChannelConfigurations
         : AudioProcessor (BusesProperties()
                                         #if ! JucePlugin_IsMidiEffect
                                            #if ! JucePlugin_IsSynth
                                             .withInput  ("Input",  juce::AudioChannelSet::mono(), true)
                                            #endif
                                             .withOutput ("Output", juce::AudioChannelSet::mono(), true)
                                         #endif
                                             )
#endif
,apvts(*this, nullptr, "PARAMETERS", create_param_layout())
{
    gainParam = apvts.getRawParameterValue ("gain");

    createLogFrequencyVector(frequencies, 1000, 1.0, 20000.0);
    magnitudes.resize (frequencies.size());
}

FirConvProcessor::~FirConvProcessor()
{
    
}


//==============================================================================
juce::AudioProcessorValueTreeState::ParameterLayout FirConvProcessor::create_param_layout()
{
    juce::AudioProcessorValueTreeState::ParameterLayout layout;

    // The default is 0 dB. It was written 0.5f up to 0.0.3, a value outside the range that every
    // host and the APVTS already read as 0 dB (the range's end), so the layout is unchanged.
    layout.add(std::make_unique<juce::AudioParameterFloat>("gain", "Gain", juce::NormalisableRange<float>(-60.0f, 0.0f), 0.0f));
    layout.add(std::make_unique<juce::AudioParameterFloat>("freq", "Freq", juce::NormalisableRange<float>(0.1f, 20000.0f, 0.5f, 0.15f), 0.25f));

    return layout;
}
//==============================================================================
const juce::String FirConvProcessor::getName() const
{
    return JucePlugin_Name;
}

bool FirConvProcessor::acceptsMidi() const
{
   #if JucePlugin_WantsMidiInput
    return true;
   #else
    return false;
   #endif
}

bool FirConvProcessor::producesMidi() const
{
   #if JucePlugin_ProducesMidiOutput
    return true;
   #else
    return false;
   #endif
}

bool FirConvProcessor::isMidiEffect() const
{
   #if JucePlugin_IsMidiEffect
    return true;
   #else
    return false;
   #endif
}

double FirConvProcessor::getTailLengthSeconds() const
{
    return 0.0;
}

int FirConvProcessor::getNumPrograms()
{
    return 1;   // NB: some hosts don't cope very well if you tell them there are 0 programs,
                // so this should be at least 1, even if you're not really implementing programs.
}

int FirConvProcessor::getCurrentProgram()
{
    return 0;
}

void FirConvProcessor::setCurrentProgram (int)
{
}

const juce::String FirConvProcessor::getProgramName (int)
{
    return {};
}

void FirConvProcessor::changeProgramName (int, const juce::String&)
{
}

//==============================================================================
#if SPEAKER_CONTROL
// The control build is a remote control: its editor drives the parameters, and the speaker
// running the headless build does the processing. It never processes audio itself, so the DSP
// (FirConvDsp.cpp, the coefficient header with it) is not compiled in, and the audio passes
// through untouched, bypassed or not.
void FirConvProcessor::prepareToPlay (double, int) {}
void FirConvProcessor::reset() {}
void FirConvProcessor::processBlock (juce::AudioBuffer<float>&, juce::MidiBuffer&) {}
void FirConvProcessor::processBlockBypassed (juce::AudioBuffer<float>&, juce::MidiBuffer&) {}
#endif

void FirConvProcessor::releaseResources()
{
    // When playback stops, you can use this as an opportunity to free up any
    // spare memory, etc.
}

#ifndef JucePlugin_PreferredChannelConfigurations
bool FirConvProcessor::isBusesLayoutSupported (const BusesLayout& layouts) const
{
  #if JucePlugin_IsMidiEffect
    juce::ignoreUnused (layouts);
    return true;
  #else
    // Support mono only
    if (layouts.getMainOutputChannelSet() != juce::AudioChannelSet::mono())
        return false;

    // This checks if the input layout matches the output layout
   #if ! JucePlugin_IsSynth
    if (layouts.getMainOutputChannelSet() != layouts.getMainInputChannelSet())
        return false;
   #endif

    return true;
  #endif
}
#endif

//==============================================================================
#if SPEAKER_HEADLESS
// The headless build runs on the speaker, where nobody opens a window.
bool FirConvProcessor::hasEditor() const
{
    return false;
}

juce::AudioProcessorEditor* FirConvProcessor::createEditor()
{
    return nullptr;
}
#else
bool FirConvProcessor::hasEditor() const
{
    return true;
}

juce::AudioProcessorEditor* FirConvProcessor::createEditor()
{
    return new FirConvEditor (*this, apvts);
}
#endif

//==============================================================================
void FirConvProcessor::getStateInformation (juce::MemoryBlock& destData)
{
    if (auto xml = apvts.copyState().createXml())
        copyXmlToBinary (*xml, destData);
}

void FirConvProcessor::setStateInformation (const void* data, int sizeInBytes)
{
    // A state that is not ours, or not a state at all, is ignored: getXmlFromBinary() returns
    // nullptr for it, and dereferencing that crashed the host.
    auto xml = getXmlFromBinary (data, sizeInBytes);

    if (xml != nullptr && xml->hasTagName (apvts.state.getType()))
        apvts.replaceState (juce::ValueTree::fromXml (*xml));
}

//==============================================================================
// This creates new instances of the plugin..
juce::AudioProcessor* JUCE_CALLTYPE createPluginFilter()
{
    return new FirConvProcessor();
}

//==============================================================================
// The plot helpers the editor's FirConvPlotComponent calls: in every build that has an editor, the
// control build included, which has no DSP to read the response from.
void FirConvProcessor::createFrequencyPlot(juce::Path& p, const std::vector<double>& freqs,  const std::vector<double>& mags, const juce::Rectangle<int>& bounds)
{
    for (size_t i = 0; i < freqs.size(); ++i)
        {            
            float freq = static_cast<float>(freqs[i]);
            float x = mapLog10(freq, 1.0f, 20000.f, 0.0f, static_cast<float>(bounds.getWidth()));
            
            float db_scale = 100.0f;
            float mag = static_cast<float>(mags[i]);
            float decibels = juce::Decibels::gainToDecibels(mag);
            //decibels = jlimit(-db_scale, db_scale, decibels);
            float y = juce::jmap(decibels, -db_scale, db_scale, static_cast<float>(bounds.getHeight()), 0.0f);
            
            if (i == 0)
                p.startNewSubPath(x, y);
            else
                p.lineTo(x, y);
        }
}

const std::vector<double>& FirConvProcessor::getMagnitudes ()
{
    return magnitudes;
}
const std::vector<double>& FirConvProcessor::getFrequencies ()
{
    return frequencies;
}
void FirConvProcessor::createLogFrequencyVector(std::vector<double>& frequencyVector, int numPoints, double minFreq, double maxFreq)
{
    frequencyVector.clear(); // Clear any existing content
    frequencyVector.reserve((size_t) numPoints);

    // Calculate the logarithmic spacing between frequencies
    float logMinFreq = std::log(minFreq);
    float logMaxFreq = std::log(maxFreq);
    float logSpacing = (logMaxFreq - logMinFreq) / (numPoints - 1);

    // Populate the vector with frequencies
    for (int i = 0; i < numPoints; ++i)
    {
        float logFrequency = logMinFreq + i * logSpacing;
        float frequency = std::exp(logFrequency);
        frequencyVector.push_back(frequency);
    }
}
double FirConvProcessor::mapLog10(double x, double inMin, double inMax, double outMin, double outMax)
{
    return juce::jmap(std::log10(x), std::log10(inMin), std::log10(inMax), outMin, outMax);
}
