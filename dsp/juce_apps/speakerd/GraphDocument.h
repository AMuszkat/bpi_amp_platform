#pragma once

#include <juce_core/juce_core.h>

#include <optional>
#include <utility>
#include <vector>

namespace speakerd
{
    /** The graph document, version 1: what a state layer holds, and what the hub will send.

        It is AudioPluginHost's .filtergraph (nodes with uids, the plugin by format and
        identifier, the plugin's state, connections per channel between node uids) as JSON,
        with a revision number and each node's parameter values keyed by parameter ID. The
        input and output nodes are implicit, with reserved uids. README.md has the format and an
        example. */
    struct GraphDocument
    {
        /** Reserved node uids. 1 to 15 are kept for nodes speakerd provides itself. */
        static constexpr juce::uint32 inputNode  = 1;   // outputs: 0 = FL, 1 = FR, 2 = Mine
        static constexpr juce::uint32 outputNode = 2;   // inputs: 0 = woofer, 1 = tweeter
        static constexpr juce::uint32 firstPluginNode = 16;

        /** Bounds on a document: what a speaker's graph may hold, and what speakerd reads. A
            document is at most maxDocumentBytes of text: a layer file, and an apply, whose line on
            the local link is at most as long. The states are base64 in it, 4 characters for every
            3 bytes, so their total is capped where it still fits with room to spare: 16 MiB of
            states is 21.3 MiB of base64, and the cap of one state (lv2state::maxStateBytes) is
            the same 16 MiB. A larger cap could never be reached through 32 MiB of text. */
        static constexpr int maxPluginNodes = 64;
        static constexpr int maxConnections = 512;
        static constexpr juce::int64 maxDocumentBytes = 32 * 1024 * 1024;
        static constexpr size_t maxStateBytesTotal = 16u * 1024u * 1024u;
        static_assert ((juce::int64) (maxStateBytesTotal + 2) / 3 * 4 < maxDocumentBytes,
                       "the states' cap must fit in a document as base64");

        static constexpr int inputChannels  = 3;   // FL, FR, and Mine: the speaker's own channel, by its role
        static constexpr int outputChannels = 2;
        static constexpr int mineChannel    = 2;
        static constexpr int streamChannels = 2;   // what a player plays: FL, FR

        struct Node
        {
            juce::uint32 uid = 0;
            juce::String format;        // PluginDescription::pluginFormatName, "LV2"
            juce::String identifier;    // PluginDescription::fileOrIdentifier: for LV2, the URI
            juce::String name;          // optional, for messages

            /** Parameter values in the plugin's own units, by parameter ID, in document order.
                A number, a bool, or a string (the text of a choice). */
            std::vector<std::pair<juce::String, juce::var>> params;

            /** The plugin's own state, as its getStateInformation() returns it. Optional. */
            std::optional<juce::MemoryBlock> state;

            bool bypassed = false;

            juce::String describe() const;   // "node 16 (loudness)"
        };

        struct Connection
        {
            juce::uint32 srcNode = 0;
            int srcChannel = 0;
            juce::uint32 dstNode = 0;
            int dstChannel = 0;

            juce::String describe() const;   // "16:0 -> 2:1"
        };

        juce::int64 revision = 0;
        std::vector<Node> nodes;
        std::vector<Connection> connections;

        /** Parses and checks the document: structure, types, uids, and that every connection
            names nodes that exist (channel counts of plugins are checked when they load). */
        static juce::Result parse (const juce::String& json, GraphDocument& out);

        /** The same, for a document that is already JSON (the `document` of an `apply`). */
        static juce::Result parse (const juce::var& json, GraphDocument& out);

        /** A crossover that filters, as firmware/speakerd/setup-helper.py's is_crossover says: the
            last segment of the plugin identifier starts with "crossover" (any case), does not end
            with "_control" (a control build has no DSP), and the node is not bypassed. */
        static bool isCrossover (const Node&);

        /** The crossover's high band: the only output that may feed the tweeter. */
        static constexpr int crossoverHighBand = 1;

        /** The outputs ("<node>:<channel>", sorted as text) through which the stream reaches the
            tweeter (output node channel 1) other than through a crossover's high band; empty when
            there are none. Exactly the graph walk of firmware/speakerd/setup-helper.py (graph):
            from the input node, along every output of every node reached except a crossover's
            high band, the low band included; an output that feeds the tweeter and was walked
            is unsafe. */
        juce::StringArray tweeterFeedsWithoutCrossover() const;
    };

    /** The rule that protects a tweeter that plays (device.json's limits say "tweeter": "on"):
        a document in which anything but a crossover's high band reaches the tweeter does not
        load, as a layer at boot or as a revision from the local link. With the tweeter muted it
        does not apply. --allow-tweeter-without-crossover turns the refusal into a warning, for
        the virtual pair's measurements. */
    struct TweeterRule
    {
        bool tweeterOn = false;
        bool allowWithoutCrossover = false;

        /** Fails with the reason, or adds a warning when allowed. */
        juce::Result check (const GraphDocument&, juce::StringArray& warnings) const;
    };
}
