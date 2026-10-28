#pragma once

#include <juce_core/juce_core.h>

#include <functional>
#include <map>
#include <set>
#include <tuple>

namespace bpi
{
    /** The graph document, version 1 (dsp/juce_apps/speakerd/README.md), as the controller sees
        it: a juce::var kept as the hub sent it, so that every key the controller does not know
        travels on unchanged, and its structure, which is what a user edits on the canvas. */
    namespace document
    {
        constexpr juce::uint32 inputNode = 1;    // outputs 0 FL, 1 FR, 2 Mine
        constexpr juce::uint32 outputNode = 2;   // inputs 0 woofer, 1 tweeter
        constexpr juce::uint32 firstPluginNode = 16;

        /** What a structural edit changes: the plugin nodes, their bypass, the connections. */
        struct Structure
        {
            struct Node
            {
                juce::String format, identifier, name;
                bool bypassed = false;

                bool sameKind (const Node& o) const   { return format == o.format && identifier == o.identifier; }
                bool operator== (const Node& o) const { return sameKind (o) && bypassed == o.bypassed; }
                bool operator!= (const Node& o) const { return ! operator== (o); }
            };

            using Connection = std::tuple<juce::uint32, int, juce::uint32, int>;   // src node, channel, dst node, channel

            std::map<juce::uint32, Node> nodes;
            std::set<Connection> connections;

            bool operator== (const Structure& o) const { return nodes == o.nodes && connections == o.connections; }
            bool operator!= (const Structure& o) const { return ! operator== (o); }

            juce::String describeChangeTo (const Structure& after) const;   // "+node 17, -16:0->2:0"
        };

        /** An empty document of revision 0: what the controller shows before the hub's state. */
        juce::var empty();

        juce::var copy (const juce::var& doc);    // a deep copy
        juce::int64 revisionOf (const juce::var& doc);

        Structure structureOf (const juce::var& doc);
        const juce::Array<juce::var>* nodesOf (const juce::var& doc);
        const juce::Array<juce::var>* connectionsOf (const juce::var& doc);
        juce::var findNode (const juce::var& doc, juce::uint32 uid);
        juce::uint32 uidOf (const juce::var& node);

        /** The document a user's edit makes of `accepted`: the change from `before` (the canvas
            as the accepted document left it) to `after` (the canvas now), applied to the
            accepted document, so that nothing the canvas cannot show is lost. `fill` is called
            for every plugin node of the result, to put in its current parameters and canvas
            position. */
        juce::var applyEdit (const juce::var& accepted, const Structure& before, const Structure& after,
                             juce::int64 newRevision, const std::function<void (juce::uint32, juce::DynamicObject&)>& fill);

        juce::String toJson (const juce::var&);   // one line
        juce::String nowIso8601();                // 2026-10-28T09:15:02Z
    }
}
