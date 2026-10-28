#include "SpeakersPanel.h"

namespace bpi
{
    namespace
    {
        constexpr float floorDb = -60.0f;

        float toProportion (float db)
        {
            return juce::jlimit (0.0f, 1.0f, (db - floorDb) / -floorDb);
        }
    }

    SpeakersPanel::SpeakersPanel (Bridge& b) : bridge (b)
    {
        bridge.addListener (this);
        bridge.wantOutputLevels (true);
        startTimerHz (30);
    }

    SpeakersPanel::~SpeakersPanel()
    {
        bridge.wantOutputLevels (false);
        bridge.removeListener (this);
    }

    void SpeakersPanel::timerCallback()
    {
        if (dirty)
        {
            dirty = false;
            repaint();
        }
    }

    void SpeakersPanel::drawBar (juce::Graphics& g, juce::Rectangle<int> r, const juce::String& label, float peakDb, float rmsDb) const
    {
        g.setColour (juce::Colours::white.withAlpha (0.8f));
        g.setFont (juce::FontOptions (12.0f));
        g.drawText (label, r.removeFromLeft (52), juce::Justification::centredLeft);

        auto text = r.removeFromRight (64);
        auto bar = r.reduced (0, 3).toFloat();

        g.setColour (juce::Colours::black.withAlpha (0.5f));
        g.fillRect (bar);

        g.setColour (juce::Colour (0xff2e7d32));
        g.fillRect (bar.withWidth (bar.getWidth() * toProportion (rmsDb)));

        const auto peakX = bar.getX() + bar.getWidth() * toProportion (peakDb);
        g.setColour (peakDb > -3.0f ? juce::Colours::red : juce::Colours::yellow);
        g.fillRect (juce::Rectangle<float> (peakX - 1.0f, bar.getY(), 2.0f, bar.getHeight()));

        g.setColour (juce::Colours::white.withAlpha (0.8f));
        g.setFont (juce::FontOptions (11.0f));
        g.drawText (juce::String (peakDb, 1) + " dB", text, juce::Justification::centredRight);
    }

    void SpeakersPanel::paint (juce::Graphics& g)
    {
        g.fillAll (getLookAndFeel().findColour (juce::ResizableWindow::backgroundColourId).darker (0.4f));

        auto r = getLocalBounds().reduced (8);

        g.setColour (juce::Colours::white);
        g.setFont (juce::FontOptions (15.0f, juce::Font::bold));
        g.drawText ("Speakers", r.removeFromTop (22), juce::Justification::centredLeft);

        g.setFont (juce::FontOptions (12.0f));
        g.setColour (juce::Colours::white.withAlpha (0.7f));
        g.drawText (bridge.isConnected() ? "hub " + bridge.getHubName() + ", revision " + juce::String (bridge.getRevision())
                                         : juce::String ("no hub"),
                    r.removeFromTop (18), juce::Justification::centredLeft);
        r.removeFromTop (6);

        if (bridge.getSpeakers().empty())
        {
            g.drawFittedText ("The hub's state lists the speakers.", r.removeFromTop (40), juce::Justification::topLeft, 3);
            return;
        }

        for (const auto& s : bridge.getSpeakers())
        {
            auto box = r.removeFromTop (96);
            r.removeFromTop (8);

            g.setColour (juce::Colours::black.withAlpha (0.25f));
            g.fillRoundedRectangle (box.toFloat(), 4.0f);
            box.reduce (8, 6);

            auto title = box.removeFromTop (20);
            g.setColour (s.online ? juce::Colours::limegreen : juce::Colours::grey);
            g.fillEllipse (title.removeFromLeft (12).withSizeKeepingCentre (10, 10).toFloat());
            title.removeFromLeft (6);

            g.setColour (juce::Colours::white);
            g.setFont (juce::FontOptions (14.0f, juce::Font::bold));
            g.drawText (s.name + "  (" + s.role + ")", title, juce::Justification::centredLeft);

            g.setFont (juce::FontOptions (12.0f));
            g.setColour (juce::Colours::white.withAlpha (0.75f));
            g.drawText (juce::String (s.online ? "online" : "offline") + ", layer " + (s.layer.isEmpty() ? juce::String ("?") : s.layer),
                        box.removeFromTop (17), juce::Justification::centredLeft);
            g.drawText ("monitor: " + (s.address.isEmpty() ? juce::String ("no address")
                                       : s.monitorRefusal.isNotEmpty() ? s.address + ", refused: " + s.monitorRefusal
                                                                       : s.address + (s.monitorConnected ? ", connected" : ", not connected")),
                        box.removeFromTop (17), juce::Justification::centredLeft);

            drawBar (g, box.removeFromTop (17), "woofer",  s.level[0], s.level[1]);
            drawBar (g, box.removeFromTop (17), "tweeter", s.level[2], s.level[3]);
        }
    }
}
