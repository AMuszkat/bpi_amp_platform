#pragma once

#include "PluginProcessor.h"
#include <JuceHeader.h>

//==============================================================================
/**
*/
class PlotComponent  : public juce::AudioProcessorEditor,
                        public juce::ChangeListener
{
public:
    PlotComponent (PluginProcessor&, juce::AudioProcessorValueTreeState& vts);
    ~PlotComponent() override;
    
    //==============================================================================

    //==============================================================================
    void paint (juce::Graphics&) override;
    void resized() override;

    //==============================================================================
    void changeListenerCallback (juce::ChangeBroadcaster* sender) override;
    void updateFrequencyResponses();

private:
    PluginProcessor& pluginProcessor;
    juce::AudioProcessorValueTreeState& valueTreeState;

    Path frequencyResponse;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (PlotComponent)
};