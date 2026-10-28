#include "GraphBuilder.h"

#include "LV2State.h"
#include "Samples.h"

#include <cmath>
#include <map>

namespace speakerd
{
    namespace
    {
        using Graph  = juce::AudioProcessorGraph;
        using NodeID = Graph::NodeID;
        using IO     = Graph::AudioGraphIOProcessor;
        constexpr auto deferred = Graph::UpdateKind::none;   // one rebuild, in prepareToPlay

        /** Node uid of the passthrough processor, from the reserved range. */
        constexpr juce::uint32 passthroughNode = 3;

        /** The parameter ID a plugin declared. JUCE's LV2 hosting reports it as a URI under the
            plugin's own URI, which differs between builds; the ID is the part after the last
            '#', '/' or ':' (the same rule as plugin-check). */
        juce::String bareId (const juce::String& id)
        {
            const auto cut = juce::jmax (id.lastIndexOfChar ('#'), id.lastIndexOfChar ('/'), id.lastIndexOfChar (':'));
            return cut >= 0 ? id.substring (cut + 1) : id;
        }

        std::unique_ptr<Graph> makeGraph (double sampleRate, int maxBlock)
        {
            auto graph = std::make_unique<Graph>();

            // The stream in (FL, FR, and Mine, which the Engine makes from them by the speaker's
            // role) and the amplifier out (woofer, tweeter). The I/O nodes take their channel
            // counts from the graph, so this comes first.
            graph->setPlayConfigDetails (GraphDocument::inputChannels, GraphDocument::outputChannels, sampleRate, maxBlock);
            graph->addNode (std::make_unique<IO> (IO::audioInputNode),  NodeID { GraphDocument::inputNode },  deferred);
            graph->addNode (std::make_unique<IO> (IO::audioOutputNode), NodeID { GraphDocument::outputNode }, deferred);
            return graph;
        }

        juce::String channelsOf (Graph& graph, juce::uint32 uid)
        {
            if (auto* node = graph.getNodeForId (NodeID { uid }))
                return "node " + juce::String (uid) + " has " + juce::String (node->getProcessor()->getTotalNumInputChannels())
                     + " input(s) and " + juce::String (node->getProcessor()->getTotalNumOutputChannels()) + " output(s)";

            return "no node " + juce::String (uid);
        }

        //==============================================================================
        /** Passthrough: the speaker's own channel (Mine) to the woofer, at a fixed, low level. */
        class PassthroughProcessor final : public juce::AudioProcessor
        {
        public:
            explicit PassthroughProcessor (float gainIn)
                : AudioProcessor (BusesProperties().withInput  ("Mine",   juce::AudioChannelSet::mono(), true)
                                                   .withOutput ("Woofer", juce::AudioChannelSet::mono(), true)),
                  gain (gainIn)
            {}

            const juce::String getName() const override        { return "passthrough"; }
            void prepareToPlay (double, int) override          {}
            void releaseResources() override                   {}

            using AudioProcessor::processBlock;   // the double-precision one stays the default

            void processBlock (juce::AudioBuffer<float>& buffer, juce::MidiBuffer&) override
            {
                const auto n = buffer.getNumSamples();
                auto* woofer = buffer.getWritePointer (0);   // in place: Mine in, the woofer out

                juce::FloatVectorOperations::multiply (woofer, gain, n);

                // The cap is absolute: a stream above full scale (a player's volume above 1)
                // still reaches the woofer at -20 dBFS at most, and NaN or infinity as silence.
                samples::cap (woofer, n, gain);
            }

            double getTailLengthSeconds() const override                 { return 0.0; }
            bool acceptsMidi() const override                            { return false; }
            bool producesMidi() const override                           { return false; }
            juce::AudioProcessorEditor* createEditor() override          { return nullptr; }
            bool hasEditor() const override                              { return false; }
            int getNumPrograms() override                                { return 1; }
            int getCurrentProgram() override                             { return 0; }
            void setCurrentProgram (int) override                        {}
            const juce::String getProgramName (int) override             { return {}; }
            void changeProgramName (int, const juce::String&) override   {}
            void getStateInformation (juce::MemoryBlock&) override       {}
            void setStateInformation (const void*, int) override         {}

        private:
            const float gain;
        };
    }

    //==============================================================================
    std::vector<HostedParameter> describeParameters (juce::AudioProcessor& instance)
    {
        std::vector<HostedParameter> result;

        for (auto* p : instance.getParameters())
        {
            auto* hosted = dynamic_cast<juce::HostedAudioProcessorParameter*> (p);

            if (hosted == nullptr)
                continue;

            HostedParameter d;
            d.parameter = p;
            d.id = bareId (hosted->getParameterID());

            // JUCE's LV2 hosting gives an enumeration (a choice or a switch) its labels; for any
            // other parameter this is empty.
            d.choices = p->isDiscrete() ? p->getAllValueStrings() : juce::StringArray();
            d.isBoolean = p->isBoolean();
            d.lowest  = p->getText (0.0f, 64).getDoubleValue();
            d.highest = p->getText (1.0f, 64).getDoubleValue();

            if (! d.choices.isEmpty())
            {
                // A choice's own values are its indices.
                d.lowest = 0.0;
                d.highest = d.choices.size() - 1;
            }

            result.push_back (std::move (d));
        }

        return result;
    }

    juce::Result toNormalised (const HostedParameter& d, const juce::var& value, float& normalised,
                               juce::StringArray& warnings, const juce::String& where)
    {
        auto& p = *d.parameter;
        const auto& choices = d.choices;
        const auto choiceValue = [&] (int index)
        {
            return choices.size() > 1 ? (float) index / (float) (choices.size() - 1) : 0.0f;
        };
        const auto choiceList = [&] { return "\"" + choices.joinIntoString ("\", \"") + "\""; };

        if (value.isBool())
        {
            if (! d.isBoolean)
                return juce::Result::fail (where + ": true/false is only for a switch; give a number");

            normalised = (bool) value ? 1.0f : 0.0f;
            return juce::Result::ok();
        }

        double number = 0.0;

        if (value.isString())
        {
            const auto text = value.toString().trim();

            if (const auto index = choices.indexOf (text); index >= 0)
            {
                normalised = choiceValue (index);
                return juce::Result::ok();
            }

            if (! text.containsOnly ("0123456789+-.eE") || ! text.containsAnyOf ("0123456789"))
                return juce::Result::fail (where + ": \"" + text + "\" is not "
                                           + (choices.isEmpty() ? juce::String ("a number")
                                                                : "one of " + choiceList()));

            number = text.getDoubleValue();
        }
        else if (value.isInt() || value.isInt64() || value.isDouble())
        {
            number = (double) value;
        }
        else
        {
            return juce::Result::fail (where + ": the value must be a number, a bool or a string");
        }

        if (! std::isfinite (number))
            return juce::Result::fail (where + ": not a finite number");

        if (! choices.isEmpty())
        {
            const auto index = juce::roundToInt (number);

            if (std::abs (number - index) > 1.0e-6 || ! juce::isPositiveAndBelow (index, choices.size()))
                return juce::Result::fail (where + ": a choice takes an index from 0 to "
                                           + juce::String (choices.size() - 1) + ", or one of " + choiceList());

            normalised = choiceValue (index);
            return juce::Result::ok();
        }

        // A continuous parameter: the hosted parameter maps the plugin's own range linearly,
        // so its text conversion turns the plain value into its normalised value (clamped).
        normalised = p.getValueForText (juce::String (number, 9));

        const auto lowest = d.lowest, highest = d.highest;

        if (lowest < highest && (number < lowest - 1.0e-6 * (highest - lowest) || number > highest + 1.0e-6 * (highest - lowest)))
            warnings.add (where + ": " + juce::String (number) + " is outside " + juce::String (lowest)
                          + " .. " + juce::String (highest) + ", clamped");

        return juce::Result::ok();
    }

    //==============================================================================
    juce::Result setParameters (juce::AudioProcessor& instance, const std::vector<std::pair<juce::String, juce::var>>& params,
                                const juce::String& where, const juce::String& pluginName, juce::StringArray& warnings)
    {
        const auto described = describeParameters (instance);
        std::map<juce::String, const HostedParameter*> byId;
        for (const auto& d : described)
            byId[d.id] = &d;

        for (const auto& [id, value] : params)
        {
            const auto it = byId.find (id);

            if (it == byId.end())
                return juce::Result::fail (where + ": " + pluginName + " has no parameter \"" + id + "\"");

            if (isMonitorId (id))
                return juce::Result::fail (where + ": " + id + " is a monitor (mon_): it is read-only, and never in a document");

            float normalised = 0.0f;
            if (auto r = toNormalised (*it->second, value, normalised, warnings, where + ": " + id); r.failed())
                return r;

            it->second->parameter->setValueNotifyingHost (normalised);
        }

        return juce::Result::ok();
    }

    juce::Result GraphBuilder::build (const GraphDocument& doc, double sampleRate, int maxBlock, BuiltGraph& out)
    {
        out = {};
        auto graph = makeGraph (sampleRate, maxBlock);

        // 1. Every plugin, found by its identifier (for LV2, the URI) and instantiated.
        std::vector<std::pair<const GraphDocument::Node*, juce::String>> loaded;   // node, plugin name

        for (const auto& node : doc.nodes)
        {
            const auto where = node.describe();

            juce::AudioPluginFormat* format = nullptr;
            for (auto* f : formats.getFormats())
                if (f->getName() == node.format)
                    format = f;

            if (format == nullptr)
                return juce::Result::fail (where + ": plugin format \"" + node.format + "\" is not hosted (speakerd hosts LV2)");

            juce::OwnedArray<juce::PluginDescription> found;
            format->findAllTypesForFile (found, node.identifier);

            if (found.isEmpty())
                return juce::Result::fail (where + ": no " + node.format + " plugin " + node.identifier
                                           + " (looked in " + format->getDefaultLocationsToSearch().toStringWithSeparator (", ")
                                           + " and the --lv2-bundle paths)");

            juce::String error;
            auto instance = formats.createPluginInstance (*found.getFirst(), sampleRate, maxBlock, error);

            if (instance == nullptr)
                return juce::Result::fail (where + ": " + node.identifier + " did not instantiate: " + error);

            instance->enableAllBuses();

            if (graph->addNode (std::move (instance), NodeID { node.uid }, deferred) == nullptr)
                return juce::Result::fail (where + ": uid " + juce::String (node.uid) + " is taken");

            loaded.emplace_back (&node, found.getFirst()->name);
            ++out.pluginNodes;
        }

        // 2. The connections, per channel.
        bool reachesAmplifier = false;

        for (const auto& c : doc.connections)
        {
            const Graph::Connection connection { { NodeID { c.srcNode }, c.srcChannel }, { NodeID { c.dstNode }, c.dstChannel } };

            if (! graph->addConnection (connection, deferred))
                return juce::Result::fail ("connection " + c.describe() + " is not possible: "
                                           + channelsOf (*graph, c.srcNode) + ", " + channelsOf (*graph, c.dstNode));

            reachesAmplifier = reachesAmplifier || c.dstNode == GraphDocument::outputNode;
        }

        // JUCE's graph accepts a loop and renders it, giving silence to the connection that
        // closes it (its render sequence finds no buffer for that source). No document means
        // that, and AudioPluginHost does not stop it either, so a loop fails the layer here.
        for (const auto& [node, pluginName] : loaded)
            if (graph->isAnInputTo (NodeID { node->uid }, NodeID { node->uid }))
                return juce::Result::fail (node->describe() + " feeds itself: the graph has a loop");

        if (! reachesAmplifier)
            out.warnings.add ("no connection reaches the output node: the amplifier gets silence");

        // 3. Prepare. JUCE's LV2 hosting re-instantiates a plugin in prepareToPlay, carrying its
        //    LV2 state across but not a control-port value that was set and not yet processed (a
        //    bypass, or any parameter of a plugin that uses control ports). So the state and the
        //    values are applied after this, to the instances that will run.
        graph->prepareToPlay (sampleRate, maxBlock);

        // 4. Each node's own state first, then its parameter values on top of it: the truth is the
        //    document plus the parameter values.
        for (const auto& [node, pluginName] : loaded)
        {
            const auto where = node->describe();
            auto* graphNode = graph->getNodeForId (NodeID { node->uid });
            auto& instance = *graphNode->getProcessor();

            if (node->state.has_value())
            {
                auto* plugin = dynamic_cast<juce::AudioPluginInstance*> (&instance);

                if (plugin == nullptr)
                    return juce::Result::fail (where + ": state: not a hosted plugin");

                if (auto r = lv2state::restore (*plugin, *node->state); r.failed())
                    return juce::Result::fail (where + ": state: " + r.getErrorMessage());
            }

            // A hosted LV2 plugin receives these on its atom port in its next processBlock;
            // Engine::settle runs those blocks before anything is heard.
            if (auto r = setParameters (instance, node->params, where, pluginName, out.warnings); r.failed())
                return r;

            if (node->bypassed)
                graphNode->setBypassed (true);
        }

        out.graph = std::move (graph);
        return juce::Result::ok();
    }

    BuiltGraph GraphBuilder::buildPassthrough (double sampleRate, int maxBlock)
    {
        BuiltGraph out;
        auto graph = makeGraph (sampleRate, maxBlock);

        const auto gain = juce::Decibels::decibelsToGain (passthroughGainDb);
        graph->addNode (std::make_unique<PassthroughProcessor> (gain), NodeID { passthroughNode }, deferred);

        const NodeID in { GraphDocument::inputNode }, pass { passthroughNode }, amp { GraphDocument::outputNode };
        graph->addConnection ({ { in, GraphDocument::mineChannel }, { pass, 0 } }, deferred);
        graph->addConnection ({ { pass, 0 }, { amp, 0 } }, deferred);   // the woofer; the tweeter stays silent

        graph->prepareToPlay (sampleRate, maxBlock);
        out.graph = std::move (graph);
        return out;
    }
}
