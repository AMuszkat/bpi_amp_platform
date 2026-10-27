#pragma once

#include <juce_audio_processors/juce_audio_processors.h>
#include <juce_dsp/juce_dsp.h>

#include <array>

#if (MSVC)
#include "ipps.h"
#endif

//==============================================================================
/**
*/
class PluginProcessor  : public juce::AudioProcessor,
                                     private juce::AudioProcessorValueTreeState::Listener,
                                     public juce::ChangeBroadcaster
{
public:
    //==============================================================================
    PluginProcessor();
    ~PluginProcessor() override;

    //==============================================================================
    void prepareToPlay (double sampleRate, int samplesPerBlock) override;
    void reset () override;
    void releaseResources() override;

   #ifndef JucePlugin_PreferredChannelConfigurations
    bool isBusesLayoutSupported (const BusesLayout& layouts) const override;
   #endif

    void processBlock (juce::AudioBuffer<float>&, juce::MidiBuffer&) override;

    //==============================================================================
    juce::AudioProcessorEditor* createEditor() override;
    bool hasEditor() const override;

    //==============================================================================
    const juce::String getName() const override;

    bool acceptsMidi() const override;
    bool producesMidi() const override;
    bool isMidiEffect() const override;
    double getTailLengthSeconds() const override;

    //==============================================================================
    int getNumPrograms() override;
    int getCurrentProgram() override;
    void setCurrentProgram (int index) override;
    const juce::String getProgramName (int index) override;
    void changeProgramName (int index, const juce::String& newName) override;

    //==============================================================================
    void getStateInformation (juce::MemoryBlock& destData) override;
    void setStateInformation (const void* data, int sizeInBytes) override;
    
    //==============================================================================
    void createFrequencyPlot(juce::Path& p, const std::vector<double>& freqs,  const std::vector<double>& mags, const juce::Rectangle<int>& bounds);
    const std::vector<double>& getMagnitudes ();
    const std::vector<double>& getFrequencies ();
    void createLogFrequencyVector(std::vector<double>& frequencyVector, int numPoints, double minFreq, double maxFreq);

    
private:
    //==============================================================================
    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (PluginProcessor)
    
    //void valueTreePropertyChanged (juce::ValueTree&, const juce::Identifier&) override;
    void parameterChanged (const juce::String &parameterID, float newValue) override;
    std::atomic<bool> requiresUpdate { true };
    
    juce::AudioProcessorValueTreeState apvts;
    juce::AudioProcessorValueTreeState::ParameterLayout create_param_layout();

    // Magnitude-compensation chain (mono), applied in this order per block:
    //   gain -> pre-LP (FIR) -> pre-HP (IIR biquad cascade) -> inverse (FIR)
    // The coefficients come from filters.hpp, produced by the Python pipeline / dsp_gui.
    // The pre-HP is a Butterworth high-pass: a single high-order direct-form IIR is numerically
    // unstable in float32 at its low cutoff (poles cluster near z=1 -> the recursion overflows),
    // so it is exported as second-order sections (HPreHpSos) and run here as a cascade of biquads.
    juce::dsp::Gain<float>        gain;
    juce::dsp::FIR::Filter<float> preLp;   // HPreLpTaps  (remez low-pass, HF band-limit)
    juce::OwnedArray<juce::dsp::IIR::Filter<float>> preHpSections;  // HPreHpSos (LF band-limit)
    juce::dsp::FIR::Filter<float> inv;     // InvFilter   (least-squares magnitude inverse)

    // Build the pre-HP biquad cascade from a flat SOS array laid out 6 floats per section
    // ([b0,b1,b2,a0,a1,a2], the layout scipy.signal.butter(..., output='sos') emits).
    void loadSosCascade (const float* sos, int numFloats, const juce::dsp::ProcessSpec& spec);

    void updatePlot();
    std::vector<double> frequencies;
    std::vector<double> magnitudes;
    double sampleRate = 0;
    double mapLog10(double x, double inMin, double inMax, double outMin, double outMax);
};
