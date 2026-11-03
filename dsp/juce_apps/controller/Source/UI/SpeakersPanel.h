#pragma once

#include "../Bridge/Bridge.h"

namespace bpi
{
    /** The speakers beside the canvas: each one's name, role, whether it is online, the state
        layer it plays, its monitor link, its output level bar (`output/level`: woofer and
        tweeter, peak and RMS) and its load (`speakerd/timing`: the audio cycle's mean and max
        and the late cycles; `board/cpu`: the whole board's CPU, n/a from an ampd without it),
        from the hub's state and each speaker's monitor link. It subscribes to those streams
        only while it is showing (not while its window is minimised or hidden).

        At the top, the "Buffer size" selector: the document's `quantum`, which both speakers
        run (Bridge::getBufferSizeChoices()); choosing one proposes a revision. Each speaker's
        box shows the buffer size it runs, and the cycle's length at its rate. */
    class SpeakersPanel final : public juce::Component,
                                private Bridge::Listener,
                                private juce::Timer
    {
    public:
        explicit SpeakersPanel (Bridge&);
        ~SpeakersPanel() override;

        void paint (juce::Graphics&) override;
        void resized() override;

    private:
        void bridgeStatusChanged() override   { updateBufferSize(); repaint(); }
        void proposalRejected (juce::int64, const juce::String&) override   { updateBufferSize(); }
        void updateBufferSize();
        void bufferSizeChosen();
        void outputLevelsChanged() override   { dirty = true; }
        void speakerLoadChanged() override    { dirty = true; }
        void timerCallback() override;
        void visibilityChanged() override     { updateSubscriptions(); }
        void updateSubscriptions();

        void drawBar (juce::Graphics&, juce::Rectangle<int>, const juce::String& label, float peakDb, float rmsDb) const;

        Bridge& bridge;
        juce::Label bufferSizeLabel { {}, "Buffer size" };
        juce::ComboBox bufferSize;
        juce::Array<int> shownSizes;          // the selector's items, as last built
        juce::String shownDefaultLabel;
        bool updatingBufferSize = false;
        bool dirty = false;
        bool subscribed = false;            // to every speaker's output level and load
        int loadRepaintTicks = 0;
    };
}
