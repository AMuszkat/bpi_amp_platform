/*
  ==============================================================================

    This file contains the basic framework code for a JUCE plugin processor.

  ==============================================================================
*/

#include "PluginProcessor.h"
#include "PluginEditor.h"
#include "filters.hpp"  // InvFilter[], HPreLpTaps[], HPreHpSos[] (+ HPreHpNum/Den reference)

//==============================================================================
PluginProcessor::PluginProcessor()
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

PluginProcessor::~PluginProcessor()
{
    
}


//==============================================================================
juce::AudioProcessorValueTreeState::ParameterLayout PluginProcessor::create_param_layout()
{
    juce::AudioProcessorValueTreeState::ParameterLayout layout;

    layout.add(std::make_unique<juce::AudioParameterFloat>("gain", "Gain", juce::NormalisableRange<float>(-60.0f, 0.0f), 0.5f));
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
    // Everything is allocated here. The gain and the biquads work sample by sample and the
    // FIRs in chunks of the prepared size, so a block of any size is processed without
    // allocating; a host that prepares before it has a device (sample rate or block size 0)
    // gets sane values.
    juce::dsp::ProcessSpec spec;
    spec.sampleRate = newSampleRate > 0.0 ? newSampleRate : 48000.0;
    spec.maximumBlockSize = (juce::uint32) (samplesPerBlock > 0 ? samplesPerBlock : 512);
    spec.numChannels = 1; // mono in/out

    // Gain: the "gain" parameter, applied at the next block.
    gain.prepare(spec);
    gain.setGainDecibels(0.0f);
    appliedGainDb = std::numeric_limits<float>::quiet_NaN();

    // Pre-LP FIR (remez low-pass): rolls the correction off above the driver's usable band.
    preLp.prepare (HPreLpTaps, (int) (sizeof(HPreLpTaps) / sizeof(HPreLpTaps[0])), (int) spec.maximumBlockSize);

    // Pre-HP: Butterworth high-pass, applied as a cascade of biquads (float32-stable).
    loadSosCascade(HPreHpSos, (int) (sizeof(HPreHpSos) / sizeof(HPreHpSos[0])), spec);

    // Inverse FIR (least-squares magnitude inverse of the summed driver response).
    inv.prepare (InvFilter, (int) (sizeof(InvFilter) / sizeof(InvFilter[0])), (int) spec.maximumBlockSize);

    reset();
}

void PluginProcessor::loadSosCascade(const float* sos, int numFloats, const juce::dsp::ProcessSpec& spec)
{
    preHpSections.clear();
    const int nSections = numFloats / 6;  // 6 coeffs per biquad: [b0,b1,b2,a0,a1,a2]
    for (int s = 0; s < nSections; ++s)
    {
        const float* c = sos + 6 * s;
        // juce::dsp::IIR::Coefficients' array constructor takes [b0,b1,b2,a0,a1,a2] and
        // normalises by a0 internally; scipy's SOS already have a0 == 1.
        std::array<float, 6> biquad { c[0], c[1], c[2], c[3], c[4], c[5] };
        auto* filt = new juce::dsp::IIR::Filter<float>();
        filt->coefficients = new juce::dsp::IIR::Coefficients<float>(biquad);
        filt->prepare(spec);
        preHpSections.add(filt);
    }
}

void PluginProcessor::reset()
{
    gain.reset();
    preLp.reset();
    for (auto* f : preHpSections)
        f->reset();
    inv.reset();
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

void PluginProcessor::processBlock (juce::AudioBuffer<float>& buffer, juce::MidiBuffer&)
{
    juce::ScopedNoDenormals noDenormals;

    // Audio thread: nothing here allocates, locks or makes a syscall.
    const float gainDb = gainParam->load (std::memory_order_relaxed);

    if (! juce::exactlyEqual (gainDb, appliedGainDb))
    {
        gain.setGainDecibels (gainDb);
        appliedGainDb = gainDb;
    }

    if (buffer.getNumChannels() == 0)
        return;

    // Mono processing: gain -> pre-LP (FIR) -> pre-HP (IIR biquad cascade) -> inverse (FIR),
    // in place on channel 0.
    auto inoutBlock = juce::dsp::AudioBlock<float> (buffer).getSubsetChannelBlock (0, 1);
    juce::dsp::ProcessContextReplacing<float> context (inoutBlock);
    float* const samples = buffer.getWritePointer (0);
    const int numSamples = buffer.getNumSamples();

    gain.process (context);
    preLp.process (samples, samples, numSamples);
    for (auto* f : preHpSections)
        f->process (context);
    inv.process (samples, samples, numSamples);
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
