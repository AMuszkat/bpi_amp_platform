#pragma once

#include <juce_audio_processors/juce_audio_processors.h>
#include <juce_dsp/juce_dsp.h>

#include "BlockFir.h"   // dsp/juce_plugins/shared/blockfir (SHARED in CMakeLists.txt)

#include <limits>
#include <vector>

#if (MSVC)
#include "ipps.h"
#endif

//==============================================================================
/**
*/
class PluginProcessor  : public juce::AudioProcessor
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

    using juce::AudioProcessor::processBlock;          // keep the double overload visible
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
    
    juce::AudioProcessorValueTreeState apvts;
    juce::AudioProcessorValueTreeState::ParameterLayout create_param_layout();

    // The gain is read on the audio thread at the start of every block. Nothing of this
    // plugin runs when a parameter changes: a host may change one on the audio thread, and
    // the editor's plot polls the parameters from its own timer (PlotComponent).
    std::atomic<float>* gainParam = nullptr;
    float appliedGainDb = std::numeric_limits<float>::quiet_NaN();   // audio thread only

    // Magnitude-compensation chain (mono), applied in this order per block:
    //   gain -> pre-LP (FIR) -> pre-HP (IIR biquad cascade) -> inverse (FIR)
    // The coefficients come from filters.hpp, produced by the Python pipeline / dsp_gui.
    // The pre-HP is a Butterworth high-pass: a single high-order direct-form IIR is numerically
    // unstable in float32 at its low cutoff (poles cluster near z=1 -> the recursion overflows),
    // so it is exported as second-order sections (HPreHpSos) and run here as a cascade of biquads.
    // The FIRs are the shared block FIR (BlockFir.h), which processes a block longer than the
    // prepared size in chunks of it.
    juce::dsp::Gain<float>        gain;
    speaker::BlockFir             preLp;   // HPreLpTaps  (remez low-pass, HF band-limit)
    speaker::BlockFir             inv;     // InvFilter   (least-squares magnitude inverse)

    // The pre-HP (HPreHpSos, the LF band-limit): its second-order sections in Transposed Direct
    // Form II, as juce::dsp::IIR::Filter runs them, but in double. Its poles sit close to
    // z = 1, so the rounding of its state passes through about 100 dB of gain at 10-25 Hz: in
    // float32 that was the chain's largest error, and every build realised it differently
    // (README.md). The coefficients are HPreHpSos's float values, promoted.
    struct Biquad
    {
        double b0, b1, b2, a1, a2;   // divided by a0 (scipy's sections have a0 == 1)
        double s1, s2;               // the state
    };
    std::vector<Biquad> preHp;       // sized in prepareToPlay

    // Build the pre-HP cascade from a flat SOS array laid out 6 floats per section
    // ([b0,b1,b2,a0,a1,a2], the layout scipy.signal.butter(..., output='sos') emits).
    void loadSosCascade (const float* sos, int numFloats);
    // The pre-HP in place on `numSamples` floats: each sample promoted to double, through every
    // section, rounded to float once.
    void processPreHp (float* samples, int numSamples) noexcept;

    std::vector<double> frequencies;
    std::vector<double> magnitudes;
    double mapLog10(double x, double inMin, double inMax, double outMin, double outMax);
};
