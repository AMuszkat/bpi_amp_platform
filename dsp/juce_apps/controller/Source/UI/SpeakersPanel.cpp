#include "SpeakersPanel.h"

namespace bpi
{
    namespace
    {
        constexpr float floorDb = -60.0f;
        constexpr int defaultItemId = 1;          // the selector's "Default"; a size's id is the size
        constexpr int headerHeight = 22 + 18 + 6;
        constexpr int speakerBoxHeight = 172;

        float toProportion (float db)
        {
            return juce::jlimit (0.0f, 1.0f, (db - floorDb) / -floorDb);
        }
    }

    SpeakersPanel::SpeakersPanel (Bridge& b) : bridge (b)
    {
        bufferSizeLabel.setFont (juce::FontOptions (12.0f));
        bufferSizeLabel.setColour (juce::Label::textColourId, juce::Colours::white.withAlpha (0.85f));
        addAndMakeVisible (bufferSizeLabel);

        bufferSize.setTextWhenNothingSelected ("?");
        bufferSize.onChange = [this] { bufferSizeChosen(); };
        addAndMakeVisible (bufferSize);

        bridge.addListener (this);
        updateBufferSize();
        startTimerHz (30);
    }

    SpeakersPanel::~SpeakersPanel()
    {
        if (subscribed)
        {
            bridge.wantOutputLevels (false);
            bridge.wantSpeakerLoad (false);
        }

        bridge.removeListener (this);
    }

    void SpeakersPanel::updateSubscriptions()
    {
        // Only what is on screen is produced (docs/remote-plugin-host.md, section 5).
        if (const auto showing = isShowing(); showing != subscribed)
        {
            subscribed = showing;
            bridge.wantOutputLevels (showing);
            bridge.wantSpeakerLoad (showing);
            repaint();
        }
    }

    void SpeakersPanel::resized()
    {
        auto r = getLocalBounds().reduced (8).withTrimmedTop (headerHeight).removeFromTop (24);
        bufferSizeLabel.setBounds (r.removeFromLeft (80));
        bufferSize.setBounds (r);
    }

    void SpeakersPanel::updateBufferSize()
    {
        // From the bridge every time it says something changed: the hub's state (the speakers'
        // ranges), a proposal sent, applied or undone.
        const auto choices = bridge.getBufferSizeChoices();
        const juce::ScopedValueSetter<bool> svs (updatingBufferSize, true);

        auto sizes = choices.sizes;
        if (choices.selected && *choices.selected > defaultItemId && ! sizes.contains (*choices.selected))
            sizes.addUsingDefaultSort (*choices.selected);   // the document's, even outside the range: shown as it is

        // Rebuilt only when the items change, so that an open list stays open.
        if (sizes != shownSizes || choices.defaultLabel != shownDefaultLabel)
        {
            bufferSize.clear (juce::dontSendNotification);
            bufferSize.addItem (choices.defaultLabel, defaultItemId);

            for (auto q : sizes)
                bufferSize.addItem (juce::String (q) + (choices.sizes.contains (q) ? "" : " (outside the speakers' range)"), q);

            shownSizes = sizes;
            shownDefaultLabel = choices.defaultLabel;
        }

        bufferSize.setSelectedId (choices.selected ? *choices.selected : defaultItemId, juce::dontSendNotification);
        bufferSize.setEnabled (choices.enabled && ! bridge.hasPendingProposal());

        const auto tip = ! choices.enabled ? "The buffer size cannot be chosen: " + choices.why
                       : bridge.hasPendingProposal() ? juce::String ("A revision is waiting for the hub's answer")
                       : juce::String ("The PipeWire buffer (quantum) both speakers run, in samples: a revision of the document. "
                                       "Default: no size in the document, the PipeWire config's own");
        bufferSize.setTooltip (tip);
        bufferSizeLabel.setTooltip (tip);
    }

    void SpeakersPanel::bufferSizeChosen()
    {
        if (updatingBufferSize)
            return;

        const auto id = bufferSize.getSelectedId();
        const auto choices = bridge.getBufferSizeChoices();
        const std::optional<int> chosen = id == defaultItemId ? std::nullopt : std::optional<int> (id);

        if (id == 0 || chosen == choices.selected)
            return;

        // Proposed like any edit; a rejection puts the selector back (updateBufferSize from the
        // bridge's undo) and shows the reason (the main window's message box, the status bar).
        juce::String why;
        bridge.proposeBufferSize (chosen, why);
        updateBufferSize();
    }

    void SpeakersPanel::timerCallback()
    {
        updateSubscriptions();   // isShowing() changes with no call when the window is minimised

        // Once a second anyway, so that a load that stopped coming shows as stale.
        if (dirty || ++loadRepaintTicks >= 30)
        {
            dirty = false;
            loadRepaintTicks = 0;
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

        // The buffer size selector is a child (resized()); under it, why it cannot be used, or
        // what it is set to.
        r.removeFromTop (24);
        {
            const auto choices = bridge.getBufferSizeChoices();
            g.setFont (juce::FontOptions (11.0f));
            g.setColour (choices.enabled ? juce::Colours::white.withAlpha (0.6f) : juce::Colours::orange);
            g.drawFittedText (choices.enabled ? juce::String ("both speakers, as one revision") : choices.why,
                              r.removeFromTop (18), juce::Justification::centredLeft, 1);
            r.removeFromTop (8);
        }

        if (bridge.getSpeakers().empty())
        {
            g.drawFittedText ("The hub's state lists the speakers.", r.removeFromTop (40), juce::Justification::topLeft, 3);
            return;
        }

        for (const auto& s : bridge.getSpeakers())
        {
            auto box = r.removeFromTop (speakerBoxHeight);
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
            box.removeFromTop (4);

            // The load: the audio cycle (speakerd/timing) and the whole board (board/cpu).
            const auto late = s.timing.count > 0 && s.timing.late > 0;
            g.setFont (juce::FontOptions (12.0f));
            g.setColour (juce::Colours::white.withAlpha (0.75f));
            g.drawText (bridge.audioLoadText (s), box.removeFromTop (17), juce::Justification::centredLeft);
            g.setColour (late ? juce::Colours::orange : juce::Colours::white.withAlpha (0.75f));
            g.drawText (bridge.lateCyclesText (s), box.removeFromTop (17), juce::Justification::centredLeft);
            g.setColour (juce::Colours::white.withAlpha (0.75f));
            g.drawText (bridge.cpuText (s), box.removeFromTop (17), juce::Justification::centredLeft);
            g.drawText (bridge.bufferText (s), box.removeFromTop (17), juce::Justification::centredLeft);
        }
    }
}
