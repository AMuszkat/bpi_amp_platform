#pragma once

#include "GraphBuilder.h"

namespace speakerd
{
    /** The state layer speakerd plays from (docs/remote-plugin-host.md, section 8, rule 1). */
    struct LoadedLayer
    {
        juce::String name;          // "current", "last-known-good", "factory" or "passthrough"
        juce::String fileName;      // graph.json, graph.lkg.json, factory.json; empty for passthrough
        juce::int64 revision = -1;  // the document's revision; -1 for passthrough
        BuiltGraph built;

        juce::String describe() const;   // "current (graph.json), revision 3" or "passthrough"
    };

    /** The layers in the order they are tried. */
    const juce::StringArray& layerNames();   // current, last-known-good, factory, passthrough

    /** Tries graph.json, graph.lkg.json and factory.json in the state directory, in that order,
        starting at `firstLayer` (one of layerNames()), and returns the first that loads
        completely: it parses, and every plugin in it is found, instantiated and takes its state
        and parameter values. Says on stderr why each layer before it failed. If none loads,
        returns passthrough, loudly. Message thread. */
    LoadedLayer loadFirstWorkingLayer (const juce::File& stateDir, GraphBuilder&, Role,
                                       double sampleRate, int maxBlock,
                                       const juce::String& firstLayer = "current");
}
