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
    gainParam = apvts.getRawParameterValue ("gain");

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

void PluginProcessor::setCurrentProgram (int)
{
}

const juce::String PluginProcessor::getProgramName (int)
{
    return {};
}

void PluginProcessor::changeProgramName (int, const juce::String&)
{
}

//==============================================================================
void PluginProcessor::prepareToPlay (double newSampleRate, int samplesPerBlock)
{
    // A host may prepare before it has an audio device (JUCE's LV2 wrapper instantiates with a
    // block size of 0); a later block of any size is still handled, in chunks.
    const int preparedBlock = samplesPerBlock > 0 ? samplesPerBlock : 512;

    juce::dsp::ProcessSpec spec;
    spec.sampleRate = newSampleRate > 0.0 ? newSampleRate : 48000.0;
    spec.maximumBlockSize = (juce::uint32) preparedBlock;
    // Process internally as mono; we'll route to two outputs later
    spec.numChannels = 1;

    // Allocate temporary mono buffers for LP/HP outputs: here, never in processBlock.
    lpBuffer.setSize(1, preparedBlock);
    hpBuffer.setSize(1, preparedBlock);

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

    // The gain parameter is applied again at the next block, whatever it is now.
    appliedGainDb = std::numeric_limits<float>::quiet_NaN();

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

void PluginProcessor::processBlock (juce::AudioBuffer<float>& buffer, juce::MidiBuffer&)
{
    juce::ScopedNoDenormals noDenormals;

    // Audio thread: nothing here allocates, locks or makes a syscall.
    const float gainDb = gainParam->load (std::memory_order_relaxed);

    if (! juce::exactlyEqual (gainDb, appliedGainDb))
    {
        lpChain.get<lpGainIndex>().setGainDecibels (gainDb);
        hpChain.get<hpGainIndex>().setGainDecibels (gainDb);
        appliedGainDb = gainDb;
    }

    const int numSamples = buffer.getNumSamples();

    if (buffer.getNumChannels() == 0 || lpBuffer.getNumSamples() == 0)
    {
        buffer.clear();
        return;
    }

    // A block larger than the one prepared is processed in prepared-size chunks.
    const int chunk = lpBuffer.getNumSamples();

    for (int start = 0; start < numSamples; start += chunk)
        processChunk (buffer, start, juce::jmin (chunk, numSamples - start));

    // Clear any remaining output channels beyond the first two
    for (int ch = 2; ch < getTotalNumOutputChannels(); ++ch)
        buffer.clear (ch, 0, numSamples);
}

void PluginProcessor::processChunk (juce::AudioBuffer<float>& buffer, int start, int numSamples) noexcept
{
    const auto n = (size_t) numSamples;

    // Copy mono input (channel 0) to both temp buffers' single channel
    const float* inData = buffer.getReadPointer (0, start);
    lpBuffer.copyFrom (0, 0, inData, numSamples);
    hpBuffer.copyFrom (0, 0, inData, numSamples);

    // Process LP and HP over the first numSamples of their buffers
    auto lpBlock = juce::dsp::AudioBlock<float> (lpBuffer).getSubsetChannelBlock (0, 1).getSubBlock (0, n);
    lpChain.process (juce::dsp::ProcessContextReplacing<float> (lpBlock));
    auto hpBlock = juce::dsp::AudioBlock<float> (hpBuffer).getSubsetChannelBlock (0, 1).getSubBlock (0, n);
    hpChain.process (juce::dsp::ProcessContextReplacing<float> (hpBlock));

    // Route: output channel 0 = LP, output channel 1 = HP.
    const int outChs = getTotalNumOutputChannels();

    if (outChs >= 1)
        std::memcpy (buffer.getWritePointer (0, start), lpBuffer.getReadPointer (0), sizeof (float) * n);

    if (outChs >= 2 && buffer.getNumChannels() >= 2)
        std::memcpy (buffer.getWritePointer (1, start), hpBuffer.getReadPointer (0), sizeof (float) * n);
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
    if (auto xml = apvts.copyState().createXml())
        copyXmlToBinary (*xml, destData);
}

void PluginProcessor::setStateInformation (const void* data, int sizeInBytes)
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
    return new PluginProcessor();
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
double PluginProcessor::mapLog10(double x, double inMin, double inMax, double outMin, double outMax)
{
    return jmap(std::log10(x), std::log10(inMin), std::log10(inMax), outMin, outMax);
}
