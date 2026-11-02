#pragma once

#include "PluginProcessor.h"
#include "PlotComponent.h"

//==============================================================================
/**
*/
class FirConvEditor  : public juce::AudioProcessorEditor
{
public:
    FirConvEditor (FirConvProcessor&, juce::AudioProcessorValueTreeState& vts);
    ~FirConvEditor() override;
    
    //==============================================================================
    
    typedef juce::AudioProcessorValueTreeState::SliderAttachment SliderAttachment;

    //==============================================================================
    void paint (juce::Graphics&) override;
    void resized() override;

private:
    // This reference is provided as a quick way for your editor to
    // access the processor object that created it.
    FirConvProcessor& audioProcessor;
    
    juce::AudioProcessorValueTreeState& valueTreeState;
    
    juce::Slider gain_slider;
    juce::Slider freq_slider;
    
    std::unique_ptr<SliderAttachment> gainAttachment;
    std::unique_ptr<SliderAttachment> freqAttachment;

    FirConvPlotComponent plot;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (FirConvEditor)
};
