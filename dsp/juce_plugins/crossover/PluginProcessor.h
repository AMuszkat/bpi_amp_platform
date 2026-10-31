#pragma once

#include <juce_audio_processors/juce_audio_processors.h>
#include <juce_dsp/juce_dsp.h>

#include "BlockFir.h"   // dsp/juce_plugins/shared/blockfir (SHARED in CMakeLists.txt)

#include <limits>

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
    
    // The two bands, each its gain and then its FIR, the shared block FIR (BlockFir.h).
    juce::dsp::Gain<float> lpGain, hpGain;
    speaker::BlockFir      lpFir, hpFir;

    // Temporary buffers to hold chain outputs prior to routing/mixing, sized in prepareToPlay
    // for the block size the host announced. A larger block is processed in chunks of that
    // size, so processBlock never resizes (allocates) them.
    juce::AudioBuffer<float> lpBuffer;
    juce::AudioBuffer<float> hpBuffer;
    void processChunk (juce::AudioBuffer<float>& buffer, int start, int numSamples) noexcept;

    std::vector<double> frequencies;
    std::vector<double> magnitudes;
    double mapLog10(double x, double inMin, double inMax, double outMin, double outMax);
};
