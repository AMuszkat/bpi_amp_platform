#pragma once

#include "../Bridge/Bridge.h"

namespace bpi
{
    /** The speakers beside the canvas: each one's name, role, whether it is online, the state
        layer it plays, its monitor link, and its output level bar (`output/level`: woofer and
        tweeter, peak and RMS), from the hub's state and each speaker's monitor link. */
    class SpeakersPanel final : public juce::Component,
                                private Bridge::Listener,
                                private juce::Timer
    {
    public:
        explicit SpeakersPanel (Bridge&);
        ~SpeakersPanel() override;

        void paint (juce::Graphics&) override;

    private:
        void bridgeStatusChanged() override   { repaint(); }
        void outputLevelsChanged() override   { dirty = true; }
        void timerCallback() override;

        void drawBar (juce::Graphics&, juce::Rectangle<int>, const juce::String& label, float peakDb, float rmsDb) const;

        Bridge& bridge;
        bool dirty = false;
    };
}
