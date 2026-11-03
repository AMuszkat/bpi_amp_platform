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

        /** The buffer size the document asks for, the optional top-level `quantum`, exactly as
            it is in the document: void when it is absent or null (PipeWire's own default).
            parse() does not check it: what this speaker accepts depends on its PipeWire
            (quantumIn()), and a layer that asks for a size it cannot have still plays. */
        juce::var quantum;

        std::vector<Node> nodes;
        std::vector<Connection> connections;

        /** Parses and checks the document: structure, types, uids, and that every connection
            names nodes that exist (channel counts of plugins are checked when they load). */
        static juce::Result parse (const juce::String& json, GraphDocument& out);

        /** The same, for a document that is already JSON (the `document` of an `apply`). */
        static juce::Result parse (const juce::var& json, GraphDocument& out);

        /** The sizes a document's `quantum` may take at all: powers of two from 64 to 2048. A
            speaker accepts those its PipeWire allows too (PipeWireNode::Clock::quantumRange). */
        static constexpr int minQuantum = 64;
        static constexpr int maxQuantum = 2048;

        /** True, with the size in `out`, when `q` is a JSON integer (or a number without a
            fraction; not a bool, not a string) that is a power of two from `lo` to `hi`. */
        static bool quantumIn (const juce::var& q, int lo, int hi, int& out);

        /** `q` as JSON text, for messages: 256, 100, "256", true, 256.5. */
        static juce::String quantumText (const juce::var& q);
    };
}
