/*
  ==============================================================================

    This file contains the basic framework code for a JUCE plugin processor.

  ==============================================================================
*/

#include "PluginProcessor.h"
#include "PluginEditor.h"
#include "crossover_coefs.hpp" // provides lp[] and hp[] FIR coefficient arrays
#include <cstring>

//==============================================================================
PluginProcessor::PluginProcessor()
#ifndef JucePlugin_PreferredChannelConfigurations
    : AudioProcessor (BusesProperties()
                     #if ! JucePlugin_IsMidiEffect
                      #if ! JucePlugin_IsSynth
                   .withInput  ("Input",  juce::AudioChannelSet::mono(), true)
                      #endif
                       .withOutput ("Output", juce::AudioChannelSet::stereo(), true)
                     #endif
                       )
#endif
,apvts(*this, nullptr, "PARAMETERS", create_param_layout())
{
    apvts.addParameterListener("gain",this);
    apvts.addParameterListener("freq",this);

    createLogFrequencyVector(frequencies, 1000, 1.0, 20000.0);
    magnitudes.resize (frequencies.size());
}

PluginProcessor::~PluginProcessor()
{
    
}


//==============================================================================
juce::AudioProcessorValueTreeState::ParameterLayout PluginProcessor::create_param_layout()
{
    juce::AudioProcessorValueTreeState::ParameterLayout layout;

    layout.add(std::make_unique<juce::AudioParameterFloat>("gain", "Gain", juce::NormalisableRange<float>(-30.0f, 0.0f), 0.5f));
    layout.add(std::make_unique<juce::AudioParameterFloat>("freq", "Freq", juce::NormalisableRange<float>(0.1f, 20000.0f, 0.5f, 0.15f), 0.25f));

    return layout;
}
//==============================================================================
const juce::String PluginProcessor::getName() const
{
    return JucePlugin_Name;
}

bool PluginProcessor::acceptsMidi() const
{
   #if JucePlugin_WantsMidiInput
    return true;
   #else
    return false;
   #endif
}

bool PluginProcessor::producesMidi() const
{
   #if JucePlugin_ProducesMidiOutput
    return true;
   #else
    return false;
   #endif
}

bool PluginProcessor::isMidiEffect() const
{
   #if JucePlugin_IsMidiEffect
    return true;
   #else
    return false;
   #endif
}

double PluginProcessor::getTailLengthSeconds() const
{
    return 0.0;
}

int PluginProcessor::getNumPrograms()
{
    return 1;   // NB: some hosts don't cope very well if you tell them there are 0 programs,
                // so this should be at least 1, even if you're not really implementing programs.
}

int PluginProcessor::getCurrentProgram()
{
    return 0;
}

void PluginProcessor::setCurrentProgram (int index)
{
}

const juce::String PluginProcessor::getProgramName (int index)
{
    return {};
}

void PluginProcessor::changeProgramName (int index, const juce::String& newName)
{
}

//==============================================================================
void PluginProcessor::prepareToPlay (double sampleRate, int samplesPerBlock)
{
    // Use this method as the place to do any pre-playback
    // initialisation that you need..
    juce::dsp::ProcessSpec spec;
    spec.sampleRate = sampleRate;
    spec.maximumBlockSize = samplesPerBlock;
    // Process internally as mono; we'll route to two outputs later
    spec.numChannels = 1;
    this->sampleRate = sampleRate;
    
    // Allocate temporary mono buffers for LP/HP outputs
    lpBuffer.setSize(1, samplesPerBlock);
    hpBuffer.setSize(1, samplesPerBlock);

    // Prepare LP chain
    lpChain.prepare(spec);
    auto& lpGain = lpChain.get<lpGainIndex>();
    auto& lpFilter = lpChain.get<lpFilterIndex>();
    lpGain.setGainDecibels(0.0f);
    {
        using Coeffs = juce::dsp::FIR::Coefficients<float>;
        lpFilter.coefficients = Coeffs::Ptr(new Coeffs(lp, (int)(sizeof(lp)/sizeof(lp[0])))); // FIR::Filter<>::CoefficientsPtr
    }

    // Prepare HP chain
    hpChain.prepare(spec);
    auto& hpGain = hpChain.get<hpGainIndex>();
    auto& hpFilter = hpChain.get<hpFilterIndex>();
    hpGain.setGainDecibels(0.0f);
    {
        using Coeffs = juce::dsp::FIR::Coefficients<float>;
        hpFilter.coefficients = Coeffs::Ptr(new Coeffs(hp, (int)(sizeof(hp)/sizeof(hp[0])))); // FIR::Filter<>::CoefficientsPtr
    }

    reset();
}

void PluginProcessor::reset()
{
    lpChain.reset();
    hpChain.reset();
}

void PluginProcessor::releaseResources()
{
    // When playback stops, you can use this as an opportunity to free up any
    // spare memory, etc.
}

// void PluginProcessor::valueTreePropertyChanged (juce::ValueTree&, const juce::Identifier&)
// {
//     requiresUpdate.store (true);
//     DBG("prop changed");
// }

void PluginProcessor::parameterChanged (const juce::String &parameterID, float newValue)
{
    if (parameterID == "gain")
    {
        DBG("gain changes");
    lpChain.get<lpGainIndex>().setGainDecibels(newValue);
    hpChain.get<hpGainIndex>().setGainDecibels(newValue);
    }
    // if (parameterID == "freq")
    // {
    //     DBG("freq changes");
    //     auto& iirfilter = processorChain.get<1>();
    //     *iirfilter.state = juce::dsp::IIR::ArrayCoefficients<float>::makeHighPass  (getSampleRate(), newValue);
    // }

    updatePlot();
}

#ifndef JucePlugin_PreferredChannelConfigurations
bool PluginProcessor::isBusesLayoutSupported (const BusesLayout& layouts) const
{
  #if JucePlugin_IsMidiEffect
    juce::ignoreUnused (layouts);
    return true;
  #else
    // Force exactly two output channels (stereo)
    if (layouts.getMainOutputChannelSet() != juce::AudioChannelSet::stereo())
        return false;

   #if ! JucePlugin_IsSynth
    // For non-synths, require one input channel (mono)
    if (layouts.getMainInputChannelSet() != juce::AudioChannelSet::mono())
        return false;
   #endif

    return true;
  #endif
}
#endif

void PluginProcessor::processBlock (juce::AudioBuffer<float>& buffer, juce::MidiBuffer& midiMessages)
{
    juce::ScopedNoDenormals noDenormals;
    
    // if (requiresUpdate.load())
    //     update();

    const int inChs  = getTotalNumInputChannels();
    const int outChs = getTotalNumOutputChannels();

    // Ensure temp buffers sized
    lpBuffer.setSize(1, buffer.getNumSamples(), false, false, true);
    hpBuffer.setSize(1, buffer.getNumSamples(), false, false, true);

    // Copy mono input (channel 0) to both temp buffers' single channel
    const float* inData = buffer.getReadPointer(0);
    lpBuffer.copyFrom(0, 0, inData, buffer.getNumSamples());
    hpBuffer.copyFrom(0, 0, inData, buffer.getNumSamples());

    // Process LP
    auto lpBlock = juce::dsp::AudioBlock<float>(lpBuffer).getSubsetChannelBlock(0, (size_t) 1);
    lpChain.process(juce::dsp::ProcessContextReplacing<float>(lpBlock));
    // Process HP
    auto hpBlock = juce::dsp::AudioBlock<float>(hpBuffer).getSubsetChannelBlock(0, (size_t) 1);
    hpChain.process(juce::dsp::ProcessContextReplacing<float>(hpBlock));

    // Route: output channel 0 = LP, output channel 1 = HP.
    // If multiple input channels, average them into each output.
    const int N = buffer.getNumSamples();
    if (outChs >= 1)
    {
        float* outLP = buffer.getWritePointer(0);
        const float* lpData = lpBuffer.getReadPointer(0);
        std::memcpy(outLP, lpData, sizeof(float) * (size_t) N);
    }
    if (outChs >= 2)
    {
        float* outHP = buffer.getWritePointer(1);
        const float* hpData = hpBuffer.getReadPointer(0);
        std::memcpy(outHP, hpData, sizeof(float) * (size_t) N);
    }
    // Clear any remaining output channels beyond the first two
    for (int ch = 2; ch < outChs; ++ch)
        buffer.clear(ch, 0, N);
}

//==============================================================================
bool PluginProcessor::hasEditor() const
{
    return true; // (change this to false if you choose to not supply an editor)
}

juce::AudioProcessorEditor* PluginProcessor::createEditor()
{
    return new PluginEditor (*this, apvts);
}

//==============================================================================
void PluginProcessor::getStateInformation (juce::MemoryBlock& destData)
{
    copyXmlToBinary (*apvts.copyState().createXml(), destData);
}

void PluginProcessor::setStateInformation (const void* data, int sizeInBytes)
{
    apvts.replaceState (juce::ValueTree::fromXml (*getXmlFromBinary (data, sizeInBytes)));
}

//==============================================================================
// This creates new instances of the plugin..
juce::AudioProcessor* JUCE_CALLTYPE createPluginFilter()
{
    return new PluginProcessor();
}

void PluginProcessor::updatePlot()
{
    // To compute magnitude response for current FIR filters, you could use:
    // processorChain.get<firLpFiltIndex>().state->getMagnitudeForFrequencyArray(...)
    // and similarly for hp. Combine or display individually as needed.
 sendChangeMessage();
}

void PluginProcessor::createFrequencyPlot(juce::Path& p, const std::vector<double>& freqs,  const std::vector<double>& mags, const juce::Rectangle<int>& bounds)
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

const std::vector<double>& PluginProcessor::getMagnitudes ()
{
    return magnitudes;
}
const std::vector<double>& PluginProcessor::getFrequencies ()
{
    return frequencies;
}
void PluginProcessor::createLogFrequencyVector(std::vector<double>& frequencyVector, int numPoints, double minFreq, double maxFreq)
{
    frequencyVector.clear(); // Clear any existing content
    frequencyVector.reserve(numPoints);

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
double PluginProcessor::mapLog10(double x, double inMin, double inMax, double outMin, double outMax)
{
    return jmap(std::log10(x), std::log10(inMin), std::log10(inMax), outMin, outMax);
}
