#pragma once

#include "PluginProcessor.h"
#include "PlotComponent.h"
#include <JuceHeader.h>

//==============================================================================
/**
*/
class PluginEditor  : public juce::AudioProcessorEditor
{
public:
    PluginEditor (PluginProcessor&, juce::AudioProcessorValueTreeState& vts);
    ~PluginEditor() override;
    
    //==============================================================================
    
    typedef juce::AudioProcessorValueTreeState::SliderAttachment SliderAttachment;

    //==============================================================================
    void paint (juce::Graphics&) override;
    void resized() override;

private:
    // This reference is provided as a quick way for your editor to
    // access the processor object that created it.
    PluginProcessor& audioProcessor;
    
    juce::AudioProcessorValueTreeState& valueTreeState;
    
    juce::Slider gain_slider;
    juce::Slider freq_slider;
    
    std::unique_ptr<SliderAttachment> gainAttachment;
    std::unique_ptr<SliderAttachment> freqAttachment;

    PlotComponent plot;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (PluginEditor)
};
