#pragma once

#include "Engine.h"

#include <map>
#include <utility>
#include <vector>

namespace speakerd
{
    /*  The timing of a run of audio cycles, from the Engine's TimingRecords (README, Timing):
        what each plugin node, the whole graph and the whole cycle took, as a fraction of the
        cycle's budget (its length in time, quantum / rate), mean and max; the cycles that took
        longer than their budget (late); and the cycles whose driver position did not follow on
        from the last one's (position jumps: a skipped cycle, or another driver). Built on the
        monitor thread, never on the audio thread. */
    struct TimingStats
    {
        using NodeNames = std::vector<std::pair<juce::uint32, juce::String>>;   // uid, name

        struct Share
        {
            double ns = 0.0, budgetNs = 0.0, max = 0.0;

            void add (double ns, double budgetNs);
            void merge (const Share&);
            double mean() const noexcept    { return budgetNs > 0.0 ? ns / budgetNs : 0.0; }
        };

        struct Node
        {
            juce::String name;
            Share share;
        };

        juce::int64 cycles = 0, late = 0, jumps = 0;
        Share cycle, graph;
        std::map<juce::uint32, Node> nodes;

        /** One cycle. `names` are its graph's plugin nodes in the record's order, or null when
            they are not known (the nodes are then left out; the rest is counted). */
        void add (const TimingRecord&, const NodeNames* names);
        void merge (const TimingStats&);

        /** {"cycles":..,"late":..,"jumps":..,"cycle":{"mean","max"},"graph":{"mean","max"},
            "nodes":[{"uid","name","mean","max"}]}: fractions of the cycle, 4 significant digits. */
        juce::var toVar() const;

        /** For the log: "node 16 (loudness_headless) mean 1.2 % max 3.4 %; graph mean 1.3 % max
            3.6 %; cycle mean 1.5 % max 3.9 %; 2813 cycles, 0 late, 0 position jumps". */
        juce::String describe() const;
    };
}
