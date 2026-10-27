/*
  ==============================================================================

    This file contains the basic framework code for a JUCE plugin editor.

  ==============================================================================
*/

#include "PluginProcessor.h"
#include "PlotComponent.h"

//==============================================================================
PlotComponent::PlotComponent (PluginProcessor& p, juce::AudioProcessorValueTreeState& vts)
    : AudioProcessorEditor (p), pluginProcessor (p), valueTreeState (vts)
{
    // Make sure that before the constructor has finished, you've set the
    // editor's size to whatever you need it to be.
    setSize (100, 100);
    pluginProcessor.addChangeListener (this);
}

PlotComponent::~PlotComponent()
{
  pluginProcessor.removeChangeListener (this);
}

//==============================================================================
void PlotComponent::paint (juce::Graphics& g)
{
    // (Our component is opaque, so we must completely fill the background with a solid colour)
    // fill the whole window white


    juce::Colour gradCol1 = juce::Colour::fromRGB(102, 0, 67);
    juce::Colour gradCol2 = juce::Colour::fromRGB(24, 13, 31);
    juce::Colour gradCol3 = juce::Colour::fromRGB(36, 37, 64);
    juce::Colour gradCol4 = juce::Colour::fromRGB(22, 49, 104);

    float x1 = static_cast<float>(getWidth())*0.0f;
    float x2 = static_cast<float>(getWidth())*0.34f;
    float x3 = static_cast<float>(getWidth())*0.73f;
    float x4 = static_cast<float>(getWidth())*1.0f;
    
    
    juce::ColourGradient grad1 = juce::ColourGradient::horizontal(gradCol1, x1, gradCol2, x2);
    juce::ColourGradient grad2 = juce::ColourGradient::horizontal(gradCol2, x2, gradCol3, x3);
    juce::ColourGradient grad3 = juce::ColourGradient::horizontal(gradCol3, x3, gradCol4, x4);
    
    
    g.setFillType(grad1);
    g.fillRect(0.0f, 0.0f, x2, static_cast<float>(getHeight()));
    g.setFillType(grad2);
    g.fillRect(x2, 0.0f, x3-x2, static_cast<float>(getHeight()));
    g.setFillType(grad3);
    g.fillRect(x3, 0.0f, x4-x3, static_cast<float>(getHeight()));
     
    // set the font size and draw text to the screen
    g.setFont (15.0f);
     
    g.drawFittedText ("Gain+Freq", 0, 0, getWidth(), 30, juce::Justification::centred, 1);

    g.setColour(juce::Colours::white);
    g.strokePath (frequencyResponse, PathStrokeType (2.5f));
}

void PlotComponent::resized()
{
}
void PlotComponent::changeListenerCallback(juce::ChangeBroadcaster* sender)
{
    updateFrequencyResponses();
    repaint();
}
void PlotComponent::updateFrequencyResponses ()
{
    //auto pixelsPerDouble = 2.0f * getParentHeight() / juce::Decibels::decibelsToGain (20.0f);

    frequencyResponse.clear();
    pluginProcessor.createFrequencyPlot (frequencyResponse, pluginProcessor.getFrequencies(),  pluginProcessor.getMagnitudes(), getBounds());
}