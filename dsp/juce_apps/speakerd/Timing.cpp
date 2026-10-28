#include "Timing.h"

#include <cmath>
#include <cstdio>

namespace speakerd
{
    namespace
    {
        /** A fraction for JSON: 4 significant digits, as a number. */
        juce::var fraction (double x)
        {
            if (! std::isfinite (x) || x <= 0.0)
                return 0.0;

            char text[32];
            std::snprintf (text, sizeof text, "%.4g", x);
            return std::atof (text);
        }

        juce::String percent (double x)
        {
            char text[32];
            std::snprintf (text, sizeof text, "%.1f %%", 100.0 * x);
            return text;
        }

        juce::var share (const TimingStats::Share& s)
        {
            auto* o = new juce::DynamicObject();
            o->setProperty ("mean", fraction (s.mean()));
            o->setProperty ("max", fraction (s.max));
            return juce::var (o);
        }
    }

    void TimingStats::Share::add (double nsIn, double budgetNsIn)
    {
        ns += nsIn;
        budgetNs += budgetNsIn;

        if (budgetNsIn > 0.0)
            max = juce::jmax (max, nsIn / budgetNsIn);
    }

    void TimingStats::Share::merge (const Share& other)
    {
        ns += other.ns;
        budgetNs += other.budgetNs;
        max = juce::jmax (max, other.max);
    }

    void TimingStats::add (const TimingRecord& r, const NodeNames* names)
    {
        const auto budget = (double) r.budgetNs;

        ++cycles;
        cycle.add ((double) r.cycleNs, budget);

        if (r.cycleNs > r.budgetNs)
            ++late;

        if (r.positionJump)
            ++jumps;

        if (! r.graphRan)
            return;

        graph.add ((double) r.graphNs, budget);

        if (names == nullptr || names->size() != (size_t) r.numNodes)
            return;

        for (size_t i = 0; i < names->size(); ++i)
        {
            auto& node = nodes[(*names)[i].first];
            node.name = (*names)[i].second;
            node.share.add ((double) r.nodeNs[i], budget);
        }
    }

    void TimingStats::merge (const TimingStats& other)
    {
        cycles += other.cycles;
        late += other.late;
        jumps += other.jumps;
        cycle.merge (other.cycle);
        graph.merge (other.graph);

        for (const auto& [uid, node] : other.nodes)
        {
            auto& mine = nodes[uid];
            mine.name = node.name;
            mine.share.merge (node.share);
        }
    }

    juce::var TimingStats::toVar() const
    {
        auto* o = new juce::DynamicObject();
        o->setProperty ("cycles", cycles);
        o->setProperty ("late", late);
        o->setProperty ("jumps", jumps);
        o->setProperty ("cycle", share (cycle));
        o->setProperty ("graph", share (graph));

        juce::Array<juce::var> list;

        for (const auto& [uid, node] : nodes)
        {
            auto* n = new juce::DynamicObject();
            n->setProperty ("uid", (juce::int64) uid);
            n->setProperty ("name", node.name);
            n->setProperty ("mean", fraction (node.share.mean()));
            n->setProperty ("max", fraction (node.share.max));
            list.add (juce::var (n));
        }

        o->setProperty ("nodes", list);
        return juce::var (o);
    }

    juce::String TimingStats::describe() const
    {
        juce::StringArray parts;

        for (const auto& [uid, node] : nodes)
            parts.add ("node " + juce::String (uid) + " (" + node.name + ") mean " + percent (node.share.mean())
                       + " max " + percent (node.share.max));

        parts.add ("graph mean " + percent (graph.mean()) + " max " + percent (graph.max));
        parts.add ("cycle mean " + percent (cycle.mean()) + " max " + percent (cycle.max));
        parts.add (juce::String (cycles) + " cycles, " + juce::String (late) + " late, " + juce::String (jumps)
                   + " position jump(s)");
        return parts.joinIntoString ("; ");
    }
}
