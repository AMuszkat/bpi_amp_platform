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
        static constexpr juce::uint32 inputNode  = 1;   // outputs: 0 = FL, 1 = FR (2 = Mine, step 3)
        static constexpr juce::uint32 outputNode = 2;   // inputs: 0 = woofer, 1 = tweeter
        static constexpr juce::uint32 firstPluginNode = 16;

        static constexpr int inputChannels  = 2;
        static constexpr int outputChannels = 2;

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
    };
}
