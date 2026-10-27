#pragma once

#include "DeviceConfig.h"
#include "GraphDocument.h"

#include <juce_audio_processors_headless/juce_audio_processors_headless.h>

#include <memory>

namespace speakerd
{
    /** The passthrough layer's level: the speaker's own channel reaches the woofer 20 dB down,
        so nothing in passthrough is ever louder than -20 dBFS. */
    constexpr float passthroughGainDb = -20.0f;

    /** A graph ready to run: built, every plugin loaded and given its state and parameter
        values, and prepared. It has not processed anything yet (see Engine::settle). */
    struct BuiltGraph
    {
        std::unique_ptr<juce::AudioProcessorGraph> graph;
        int pluginNodes = 0;
        juce::StringArray warnings;
    };

    /** Turns a graph document into a running AudioProcessorGraph, the way AudioPluginHost's
        PluginGraph::restoreFromXml does: the input node and the output node at their reserved
        uids, each plugin node at its own uid, then the connections. */
    class GraphBuilder
    {
    public:
        explicit GraphBuilder (juce::AudioPluginFormatManager& formatsIn) : formats (formatsIn) {}

        /** Builds and prepares the graph a document describes, at this sample rate and largest
            block. Fails, with the reason, unless every plugin is found and instantiated, takes
            its state and every parameter value, and every connection is possible. */
        juce::Result build (const GraphDocument&, double sampleRate, int maxBlock, BuiltGraph&);

        /** The last layer, passthrough: no plugin at all. The speaker's own channel (left: FL,
            right: FR, mono: (FL + FR) / 2) goes to the woofer at passthroughGainDb, and the
            tweeter gets nothing. */
        BuiltGraph buildPassthrough (Role, double sampleRate, int maxBlock);

    private:
        juce::AudioPluginFormatManager& formats;
    };
}
