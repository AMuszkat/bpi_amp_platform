#pragma once

#include "DeviceConfig.h"
#include "GraphDocument.h"
#include "PluginBundles.h"
#include "TimedNode.h"

#include <juce_audio_processors_headless/juce_audio_processors_headless.h>

#include <memory>

namespace speakerd
{
    /** The passthrough layer's level: the speaker's own channel reaches the woofer 20 dB down,
        and is clamped there, so nothing in passthrough is ever louder than -20 dBFS, even a
        stream above full scale. */
    constexpr float passthroughGainDb = -20.0f;

    /** A graph ready to run: built, every plugin loaded and given its state and parameter
        values, and prepared. It has not processed anything yet (see Engine::settle). */
    struct BuiltGraph
    {
        std::unique_ptr<juce::AudioProcessorGraph> graph;
        std::vector<TimedNode*> timedNodes;     // the plugin nodes, owned by the graph
        int pluginNodes = 0;
        juce::StringArray warnings;
    };

    /** A monitor is a read-only parameter whose ID starts with this (docs/protocol.md): never
        set, never in a document. */
    inline bool isMonitorId (const juce::String& id)    { return id.startsWith ("mon_"); }

    /** A hosted parameter, and what it takes to turn a value in the plugin's own units into its
        normalised value and back, read once, while the plugin is not running. After that,
        toNormalised() only calls the parameter's getValueForText, which reads constants. */
    struct HostedParameter
    {
        juce::AudioProcessorParameter* parameter = nullptr;
        juce::String id;              // the ID the plugin declared (the part after its URI)
        juce::StringArray choices;    // a choice's (or a switch's) labels; empty for anything else
        bool isBoolean = false;
        double lowest = 0.0, highest = 1.0;   // the plugin's own values at 0 and at 1

        /** The plugin's own value for a normalised one (JUCE's LV2 hosting maps the plugin's
            range linearly). */
        double plainValue (float normalised) const noexcept   { return lowest + (double) normalised * (highest - lowest); }
    };

    /** Every hosted parameter of a plugin, in its order. Message thread, before it runs. */
    std::vector<HostedParameter> describeParameters (juce::AudioProcessor&);

    /** A value in the plugin's own units, as in a document (a number, a choice's text, a
        switch's true/false), as the parameter's normalised value. A value outside the range is
        clamped, with a warning added. Any thread, for a parameter described above. */
    juce::Result toNormalised (const HostedParameter&, const juce::var& value, float& normalised,
                               juce::StringArray& warnings, const juce::String& where);

    /** Sets a hosted plugin's parameters from a document's values (`params` of a node: the
        plugin's own units, by parameter ID), as a layer does. Fails on an unknown parameter ID,
        a monitor's (mon_), or a value the parameter cannot take; a value outside the range is
        clamped, with a warning. A hosted LV2 plugin takes them in its next processBlock calls.
        Message thread, on a plugin that is not running. */
    juce::Result setParameters (juce::AudioProcessor&, const std::vector<std::pair<juce::String, juce::var>>& params,
                                const juce::String& where, const juce::String& pluginName, juce::StringArray& warnings);

    /** Turns a graph document into a running AudioProcessorGraph, the way AudioPluginHost's
        PluginGraph::restoreFromXml does: the input node and the output node at their reserved
        uids, each plugin node at its own uid, then the connections. */
    class GraphBuilder
    {
    public:
        /** With `bundles`, every plugin node it instantiates is logged with its build identity
            (README.md, Log lines: "plugin node <uid>: ..."). */
        explicit GraphBuilder (juce::AudioPluginFormatManager& formatsIn, PluginBundles* bundlesIn = nullptr)
            : formats (formatsIn), bundles (bundlesIn) {}

        /** Builds and prepares the graph a document describes, at this sample rate and largest
            block. Fails, with the reason, unless every plugin is found and instantiated, takes
            its state and every parameter value, every connection is possible, and no node
            feeds itself (a loop). Each plugin node's identity line is logged as soon as its
            plugin is instantiated, so also for a document that fails after it; it never fails
            the build. */
        juce::Result build (const GraphDocument&, double sampleRate, int maxBlock, BuiltGraph&);

        /** The last layer, passthrough: no plugin at all. The input node's Mine channel, the
            speaker's own channel (left: FL, right: FR, mono: (FL + FR) / 2, made by the Engine
            from device.json's role), goes to the woofer at passthroughGainDb, clamped to that
            level and with NaN and infinity as 0, and the tweeter gets nothing. */
        BuiltGraph buildPassthrough (double sampleRate, int maxBlock);

    private:
        juce::AudioPluginFormatManager& formats;
        PluginBundles* bundles = nullptr;
    };
}
