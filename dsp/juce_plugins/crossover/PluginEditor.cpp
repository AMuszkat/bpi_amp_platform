/*
  ==============================================================================

    This file contains the basic framework code for a JUCE plugin editor.

  ==============================================================================
*/

#include "PluginProcessor.h"
#include "PluginEditor.h"

//==============================================================================
PluginEditor::PluginEditor (PluginProcessor& p, juce::AudioProcessorValueTreeState& vts)
    : AudioProcessorEditor (p), audioProcessor (p), valueTreeState (vts), plot (p, vts)
{
    // Make sure that before the constructor has finished, you've set the
    // editor's size to whatever you need it to be.
    setSize (400, 300);
    setResizable(true, true);
    
    //Attachments
    gainAttachment.reset (new SliderAttachment (valueTreeState, "gain", gain_slider));
    freqAttachment.reset (new SliderAttachment (valueTreeState, "freq", freq_slider));
    
    // these define the parameters of our slider object
    gain_slider.setSliderStyle (juce::Slider::LinearBarVertical);
    gain_slider.setRange(-30.0,0.0);
    gain_slider.setTextBoxStyle (juce::Slider::NoTextBox, false, 90, 0);
    gain_slider.setPopupDisplayEnabled (true, false, this);
    gain_slider.setTextValueSuffix (" Volume");
    gain_slider.setValue(0.0);
    
    
    // these define the parameters of our slider object
    freq_slider.setSliderStyle (juce::Slider::LinearBarVertical);
    freq_slider.setTextBoxStyle (juce::Slider::NoTextBox, false, 90, 0);
    freq_slider.setPopupDisplayEnabled (true, false, this);
    freq_slider.setTextValueSuffix (" Freq.");
    
    // this function adds the slider to the editor
    addAndMakeVisible (&gain_slider);
    addAndMakeVisible (&freq_slider);
    addAndMakeVisible (&plot);
    
}

PluginEditor::~PluginEditor()
{

}

//==============================================================================
void PluginEditor::paint (juce::Graphics& g)
{
    // (Our component is opaque, so we must completely fill the background with a solid colour)
    // fill the whole window white
    g.fillAll (juce::Colours::darkgreen);
     
    // set the current drawing colour to black
    g.setColour (juce::Colours::white);
     
    // set the font size and draw text to the screen
    g.setFont (15.0f);
     
    g.drawFittedText ("Gain+Freq", 0, 0, getWidth(), 30, juce::Justification::centred, 1);
}

void PluginEditor::resized()
{
    // This is generally where you'll want to lay out the positions of any
    // subcomponents in your editor..
    // sets the position and size of the slider with arguments (x, y, width, height)
    gain_slider.setBounds (40, 30, 20, getHeight() - 60);
    freq_slider.setBounds(gain_slider.getX()+50, gain_slider.getY(), gain_slider.getWidth()
                          , getHeight() - 60);

    plot.setBounds(freq_slider.getX()+50, gain_slider.getY(), getWidth()-150, gain_slider.getHeight());
}
