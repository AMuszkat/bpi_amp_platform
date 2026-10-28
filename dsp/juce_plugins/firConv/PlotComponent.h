#pragma once

#include "PluginProcessor.h"
#include <JuceHeader.h>

//==============================================================================
/**
*/
class PlotComponent  : public juce::AudioProcessorEditor,
                       private juce::Timer
{
public:
    PlotComponent (PluginProcessor&, juce::AudioProcessorValueTreeState& vts);
    ~PlotComponent() override;
    
    //==============================================================================

    //==============================================================================
    void paint (juce::Graphics&) override;
    void resized() override;

    //==============================================================================
    void updateFrequencyResponses();

private:
    PluginProcessor& pluginProcessor;
    juce::AudioProcessorValueTreeState& valueTreeState;

    // The plot follows the parameters by polling them on the message thread. (It used to be
    // told by the processor, from parameterChanged(), which a host may call on the audio
    // thread, and posting a change message from there allocates and locks.)
    void timerCallback() override;
    std::atomic<float>* gainValue = nullptr;
    std::atomic<float>* freqValue = nullptr;
    float plottedGain = std::numeric_limits<float>::quiet_NaN();
    float plottedFreq = std::numeric_limits<float>::quiet_NaN();

    Path frequencyResponse;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (PlotComponent)
};