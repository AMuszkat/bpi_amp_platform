#include "PipeWireNode.h"

#include "Engine.h"
#include "Log.h"

#include <pipewire/filter.h>
#include <pipewire/pipewire.h>

#include <algorithm>
#include <atomic>
#include <cerrno>
#include <cstring>
#include <map>
#include <vector>

namespace speakerd
{
    namespace
    {
        constexpr const char* filterInputs[]  { "input_FL", "input_FR" };
        constexpr const char* filterChannels[] { "FL", "FR" };
        constexpr const char* filterOutputs[] { "output_woofer", "output_tweeter" };
        constexpr const char* sinkMonitors[]  { "monitor_FL", "monitor_FR" };

        juce::String lookup (const spa_dict* props, const char* key)
        {
            if (props == nullptr)
                return {};

            const auto* value = spa_dict_lookup (props, key);
            return value != nullptr ? juce::String::fromUTF8 (value) : juce::String();
        }

        juce::uint32 lookupId (const spa_dict* props, const char* key)
        {
            const auto text = lookup (props, key);
            return text.isEmpty() ? SPA_ID_INVALID : (juce::uint32) text.getLargeIntValue();
        }

        juce::String errorText (int res)
        {
            return juce::String::fromUTF8 (std::strerror (res < 0 ? -res : res));
        }

        struct PortData { int unused = 0; };   // pw_filter hands one of these back per port
    }

    //==============================================================================
    struct PipeWireNode::Impl
    {
        Impl (Engine& e, const juce::String& name, const DeviceConfig& d)
            : engine (e), sinkName (name), filterName (name + "-dsp"), device (d) {}

        Engine& engine;
        const juce::String sinkName, filterName;
        const DeviceConfig device;

        pw_thread_loop* loop = nullptr;
        pw_context* context = nullptr;
        pw_core* core = nullptr;
        pw_registry* registry = nullptr;
        pw_filter* filter = nullptr;
        pw_proxy* ownSink = nullptr;     // the input sink, when speakerd made it
        spa_hook coreListener {}, registryListener {}, filterListener {};

        void* inputs[2] {};
        void* outputs[2] {};

        int syncSeq = 0;
        bool synced = false;

        // Linking waits for the registry to settle: a registry change asks for one round trip
        // (pw_core_sync), and the links are updated when it comes back. PipeWire sends every
        // change of one event (a node going away: its links, then its ports, then the node) before
        // it answers a sync it reads afterwards, so speakerd never links to a port that is
        // already going away.
        int settleSeq = -1;
        bool settlePending = false;
        bool linkingStopped = false;

        /** Errors PipeWire reports that are not a lost connection: logged at most once per
            this many milliseconds. */
        static constexpr juce::uint32 errorLogInterval = 10000;
        juce::uint32 errorLogged = 0;
        bool anyErrorLogged = false;
        Clock clock;

        // The registry, as far as linking needs it. PipeWire loop thread only.
        struct Port { juce::uint32 node = SPA_ID_INVALID; bool isOutput = false; juce::String name; };
        std::map<juce::uint32, juce::String> nodes;                               // id -> node.name
        std::map<juce::uint32, Port> ports;                                       // id -> port
        std::map<juce::uint32, std::pair<juce::uint32, juce::uint32>> links;      // id -> (output port, input port)

        struct OwnLink
        {
            Impl* owner = nullptr;
            pw_proxy* proxy = nullptr;
            spa_hook listener {};
            juce::uint32 outputPort = SPA_ID_INVALID, inputPort = SPA_ID_INVALID;
            juce::String description;     // "speakerd-dsp:output_woofer -> amplifier:playback_FL"
            bool failed = false;
            bool seen = false;    // its link has been in the registry
        };
        std::vector<std::unique_ptr<OwnLink>> ownLinks;

        /** A link that keeps failing is made again on every registry change; its failure is
            logged at most once per this many milliseconds, per pair of ports. */
        static constexpr juce::uint32 linkFailureLogInterval = 10000;
        std::map<std::pair<juce::uint32, juce::uint32>, juce::uint32> linkFailureLogged;   // ports -> when

        juce::uint32 ownNode = SPA_ID_INVALID;

        /** One side of the filter: two links, logged together. */
        struct Side
        {
            const char* what;            // "input sink", "amplifier"
            bool linked = false, announcedWaiting = false;
        };
        Side inputSide { "input sink" }, outputSide { "amplifier" };

        std::atomic<bool> failed { false }, linked { false };
        std::atomic<juce::uint64> linkCount { 0 };

        //==============================================================================
        static void onCoreInfo (void* data, const pw_core_info* info)
        {
            auto& self = *static_cast<Impl*> (data);

            if (info == nullptr || info->props == nullptr)
                return;

            if (const auto rate = lookup (info->props, "default.clock.rate").getIntValue(); rate > 0)
                self.clock.rate = rate;

            if (const auto quantum = lookup (info->props, "default.clock.quantum").getIntValue(); quantum > 0)
                self.clock.quantum = quantum;

            if (const auto limit = lookup (info->props, "default.clock.quantum-limit").getIntValue(); limit > 0)
                self.clock.quantumLimit = limit;
        }

        static void onCoreDone (void* data, uint32_t id, int seq)
        {
            auto& self = *static_cast<Impl*> (data);

            if (id != PW_ID_CORE)
                return;

            if (seq == self.syncSeq)
            {
                self.synced = true;
                pw_thread_loop_signal (self.loop, false);
            }

            if (self.settlePending && seq == self.settleSeq)
            {
                self.settlePending = false;
                self.updateLinks();
            }
        }

        static void onCoreError (void* data, uint32_t id, int, int res, const char* message)
        {
            auto& self = *static_cast<Impl*> (data);
            const auto text = juce::String::fromUTF8 (message != nullptr ? message : "") + " (" + errorText (res) + ")";

            // Errors on other objects (a link that could not be made) reach their own proxy.
            if (id != PW_ID_CORE)
                return;

            // Only a broken connection is fatal: the client library reports it on the core with
            // -EPIPE, which is also what pw_stream and pw_filter take as a lost connection.
            // PipeWire also reports on the core an error about one of speakerd's objects, for
            // instance the destroy of a link the server had already dropped ("unknown resource
            // <id>"), when a link and its peer go away together. That is not fatal.
            if (res != -EPIPE)
            {
                const auto now = juce::Time::getMillisecondCounter();

                if (! self.anyErrorLogged || now - self.errorLogged >= errorLogInterval)
                {
                    self.anyErrorLogged = true;
                    self.errorLogged = now;
                    log ("pipewire: error, carrying on: " + text);
                }

                return;
            }

            log ("pipewire: connection error: " + text);
            self.failed = true;
            pw_thread_loop_signal (self.loop, false);
        }

        /** Updates the links once the registry has settled (see settleSeq). */
        void requestLinkUpdate()
        {
            if (core == nullptr || settlePending)
                return;

            const auto seq = pw_core_sync (core, PW_ID_CORE, 0);

            if (seq < 0)
                return updateLinks();

            settleSeq = seq;
            settlePending = true;
        }

        static void onGlobal (void* data, uint32_t id, uint32_t, const char* type, uint32_t, const spa_dict* props)
        {
            auto& self = *static_cast<Impl*> (data);

            if (std::strcmp (type, PW_TYPE_INTERFACE_Node) == 0)
                self.nodes[id] = lookup (props, PW_KEY_NODE_NAME);
            else if (std::strcmp (type, PW_TYPE_INTERFACE_Port) == 0)
                self.ports[id] = { lookupId (props, PW_KEY_NODE_ID), lookup (props, PW_KEY_PORT_DIRECTION) == "out",
                                   lookup (props, PW_KEY_PORT_NAME) };
            else if (std::strcmp (type, PW_TYPE_INTERFACE_Link) == 0)
                self.links[id] = { lookupId (props, PW_KEY_LINK_OUTPUT_PORT), lookupId (props, PW_KEY_LINK_INPUT_PORT) };
            else
                return;

            self.requestLinkUpdate();
        }

        static void onGlobalRemove (void* data, uint32_t id)
        {
            auto& self = *static_cast<Impl*> (data);
            self.nodes.erase (id);
            self.ports.erase (id);
            self.links.erase (id);
            self.requestLinkUpdate();
        }

        static void onFilterState (void* data, pw_filter_state, pw_filter_state state, const char* error)
        {
            auto& self = *static_cast<Impl*> (data);

            switch (state)
            {
                case PW_FILTER_STATE_ERROR:
                    log ("pipewire: node '" + self.filterName + "' failed: "
                         + juce::String::fromUTF8 (error != nullptr ? error : "unknown error"));
                    self.failed = true;
                    break;

                case PW_FILTER_STATE_PAUSED:
                case PW_FILTER_STATE_STREAMING:
                    if (self.ownNode == SPA_ID_INVALID && pw_filter_get_node_id (self.filter) != SPA_ID_INVALID)
                    {
                        self.ownNode = pw_filter_get_node_id (self.filter);
                        log ("pipewire: node '" + self.filterName + "' registered as id " + juce::String (self.ownNode));
                    }

                    self.requestLinkUpdate();
                    break;

                case PW_FILTER_STATE_UNCONNECTED:
                case PW_FILTER_STATE_CONNECTING:
                    break;
            }
        }

        /** The real-time part: PipeWire's data thread, once per cycle. */
        static void onProcess (void* data, spa_io_position* position)
        {
            auto& self = *static_cast<Impl*> (data);

            if (position == nullptr)
                return;

            const auto n = (uint32_t) position->clock.duration;
            const auto rate = position->clock.rate.denom > 0 ? (double) position->clock.rate.denom
                                                             / (double) juce::jmax (1u, position->clock.rate.num)
                                                             : 0.0;

            const float* in[2];
            float* out[2];

            for (int c = 0; c < 2; ++c)
            {
                in[c]  = static_cast<const float*> (pw_filter_get_dsp_buffer (self.inputs[c], n));
                out[c] = static_cast<float*> (pw_filter_get_dsp_buffer (self.outputs[c], n));
            }

            self.engine.process (in, out, (int) n, rate);
        }

        static void onLinkError (void* data, int, int res, const char* message)
        {
            auto& link = *static_cast<OwnLink*> (data);
            link.failed = true;

            const auto now = juce::Time::getMillisecondCounter();
            const auto key = std::make_pair (link.outputPort, link.inputPort);
            auto& self = *link.owner;

            if (const auto it = self.linkFailureLogged.find (key);
                it != self.linkFailureLogged.end() && now - it->second < linkFailureLogInterval)
                return;

            self.linkFailureLogged[key] = now;
            log ("pipewire: a link failed: " + juce::String::fromUTF8 (message != nullptr ? message : "")
                 + " (" + errorText (res) + "): " + link.description);
        }

        static void onSinkError (void* data, int, int res, const char* message)
        {
            auto& self = *static_cast<Impl*> (data);
            log ("pipewire: the input sink '" + self.sinkName + "' failed: "
                 + juce::String::fromUTF8 (message != nullptr ? message : "") + " (" + errorText (res) + ")");
            self.failed = true;
        }

        //==============================================================================
        juce::uint32 findNode (const juce::String& name) const
        {
            for (const auto& [id, nodeName] : nodes)
                if (nodeName == name && id != ownNode)
                    return id;

            return SPA_ID_INVALID;
        }

        juce::uint32 findPort (juce::uint32 node, bool isOutput, const juce::String& name) const
        {
            if (node == SPA_ID_INVALID)
                return SPA_ID_INVALID;

            for (const auto& [id, port] : ports)
                if (port.node == node && port.isOutput == isOutput && port.name == name)
                    return id;

            return SPA_ID_INVALID;
        }

        bool linkExists (juce::uint32 outputPort, juce::uint32 inputPort) const
        {
            for (const auto& [id, link] : links)
                if (link.first == outputPort && link.second == inputPort)
                    return true;

            return false;
        }

        /** Makes the two links of one side if all four ports exist, logs the side's changes, and
            returns whether both links exist. */
        bool linkSide (Side& side, const juce::String& peerName, juce::uint32 outNode, const juce::uint32 (&from)[2],
                       juce::uint32 inNode, const juce::uint32 (&to)[2], const juce::String& description)
        {
            const bool complete = from[0] != SPA_ID_INVALID && from[1] != SPA_ID_INVALID
                               && to[0] != SPA_ID_INVALID && to[1] != SPA_ID_INVALID;

            if (complete)
                for (int i = 0; i < 2; ++i)
                    if (! linkExists (from[i], to[i]) && ! isPending (from[i], to[i]))
                        createLink (outNode, from[i], inNode, to[i]);

            const bool nowLinked = complete && linkExists (from[0], to[0]) && linkExists (from[1], to[1]);

            if (nowLinked && ! side.linked)
            {
                log ("linked to " + juce::String (side.what) + " '" + peerName + "' (" + description + ")");
                side.announcedWaiting = false;
            }
            else if (! nowLinked && side.linked)
            {
                log ("unlinked from " + juce::String (side.what) + " '" + peerName + "': linking again as soon as it is back");
            }
            else if (! nowLinked && ! complete && ! side.announcedWaiting)
            {
                log ("waiting for " + juce::String (side.what) + " '" + peerName + "' and its ports (" + description + ")");
                side.announcedWaiting = true;
            }

            side.linked = nowLinked;
            return nowLinked;
        }

        bool isPending (juce::uint32 outputPort, juce::uint32 inputPort) const
        {
            for (const auto& link : ownLinks)
                if (link->outputPort == outputPort && link->inputPort == inputPort)
                    return true;

            return false;
        }

        /** Runs on every registry change: drops links that failed, whose ports went away or that
            someone removed, and makes the missing ones. */
        void updateLinks()
        {
            if (filter == nullptr || ownNode == SPA_ID_INVALID || linkingStopped)
                return;

            for (auto it = ownLinks.begin(); it != ownLinks.end();)
            {
                auto& link = **it;
                const auto exists = linkExists (link.outputPort, link.inputPort);
                link.seen = link.seen || exists;

                if (link.failed || (link.seen && ! exists)
                    || ports.count (link.outputPort) == 0 || ports.count (link.inputPort) == 0)
                {
                    spa_hook_remove (&(*it)->listener);
                    pw_proxy_destroy (link.proxy);
                    it = ownLinks.erase (it);
                }
                else
                {
                    ++it;
                }
            }

            const juce::uint32 ins[]  { findPort (ownNode, false, filterInputs[0]), findPort (ownNode, false, filterInputs[1]) };
            const juce::uint32 outs[] { findPort (ownNode, true, filterOutputs[0]), findPort (ownNode, true, filterOutputs[1]) };

            if (std::find (std::begin (ins), std::end (ins), SPA_ID_INVALID) != std::end (ins)
                || std::find (std::begin (outs), std::end (outs), SPA_ID_INVALID) != std::end (outs))
                return;   // the filter's own ports are not all registered yet

            const auto sink = findNode (sinkName);
            const juce::uint32 monitors[] { findPort (sink, true, sinkMonitors[0]), findPort (sink, true, sinkMonitors[1]) };
            const auto amplifier = findNode (device.amplifierNode);
            const juce::uint32 amp[]       { findPort (amplifier, false, device.wooferPort), findPort (amplifier, false, device.tweeterPort) };

            const auto inputLinked = linkSide (inputSide, sinkName, sink, monitors, ownNode, ins,
                                               juce::String (sinkMonitors[0]) + " -> " + filterInputs[0] + ", "
                                               + sinkMonitors[1] + " -> " + filterInputs[1]);

            const auto outputLinked = linkSide (outputSide, device.amplifierNode, ownNode, outs, amplifier, amp,
                                                juce::String (filterOutputs[0]) + " -> " + device.wooferPort + ", "
                                                + filterOutputs[1] + " -> " + device.tweeterPort);

            const auto both = inputLinked && outputLinked;

            if (both && ! linked.load())
                linkCount.fetch_add (1);

            linked = both;
        }

        juce::String describePort (juce::uint32 port) const
        {
            const auto p = ports.find (port);

            if (p == ports.end())
                return "port " + juce::String (port);

            const auto n = nodes.find (p->second.node);
            return (n != nodes.end() ? n->second : juce::String ("node ") + juce::String (p->second.node)) + ":" + p->second.name;
        }

        void createLink (juce::uint32 outputNode, juce::uint32 outputPort, juce::uint32 inputNode, juce::uint32 inputPort)
        {
            auto* props = pw_properties_new (nullptr, nullptr);
            pw_properties_setf (props, PW_KEY_LINK_OUTPUT_NODE, "%u", outputNode);
            pw_properties_setf (props, PW_KEY_LINK_OUTPUT_PORT, "%u", outputPort);
            pw_properties_setf (props, PW_KEY_LINK_INPUT_NODE, "%u", inputNode);
            pw_properties_setf (props, PW_KEY_LINK_INPUT_PORT, "%u", inputPort);
            pw_properties_set (props, PW_KEY_OBJECT_LINGER, "false");   // the link goes when speakerd goes

            auto* proxy = static_cast<pw_proxy*> (pw_core_create_object (core, "link-factory", PW_TYPE_INTERFACE_Link,
                                                                         PW_VERSION_LINK, &props->dict, 0));
            pw_properties_free (props);

            if (proxy == nullptr)
            {
                log ("pipewire: could not create a link: " + errorText (errno));
                return;
            }

            auto link = std::make_unique<OwnLink>();
            link->owner = this;
            link->proxy = proxy;
            link->outputPort = outputPort;
            link->inputPort = inputPort;
            link->description = describePort (outputPort) + " -> " + describePort (inputPort);
            pw_proxy_add_listener (proxy, &link->listener, &linkEvents, link.get());
            ownLinks.push_back (std::move (link));
        }

        //==============================================================================
        static pw_core_events makeCoreEvents()
        {
            pw_core_events e {};
            e.version = PW_VERSION_CORE_EVENTS;
            e.info = onCoreInfo;
            e.done = onCoreDone;
            e.error = onCoreError;
            return e;
        }

        static pw_registry_events makeRegistryEvents()
        {
            pw_registry_events e {};
            e.version = PW_VERSION_REGISTRY_EVENTS;
            e.global = onGlobal;
            e.global_remove = onGlobalRemove;
            return e;
        }

        static pw_filter_events makeFilterEvents()
        {
            pw_filter_events e {};
            e.version = PW_VERSION_FILTER_EVENTS;
            e.state_changed = onFilterState;
            e.process = onProcess;
            return e;
        }

        static pw_proxy_events makeProxyEvents (void (*onError) (void*, int, int, const char*))
        {
            pw_proxy_events e {};
            e.version = PW_VERSION_PROXY_EVENTS;
            e.error = onError;
            return e;
        }

        static inline const pw_core_events coreEvents         = makeCoreEvents();
        static inline const pw_registry_events registryEvents = makeRegistryEvents();
        static inline const pw_filter_events filterEvents     = makeFilterEvents();
        static inline const pw_proxy_events linkEvents        = makeProxyEvents (onLinkError);
        static inline const pw_proxy_events sinkEvents        = makeProxyEvents (onSinkError);
        spa_hook sinkListener {};
    };

    //==============================================================================
    PipeWireNode::PipeWireNode (Engine& engine, const juce::String& name, const DeviceConfig& device)
        : impl (std::make_unique<Impl> (engine, name, device))
    {
        pw_init (nullptr, nullptr);
    }

    PipeWireNode::~PipeWireNode()
    {
        stop();
        pw_deinit();
    }

    juce::Result PipeWireNode::connect (Clock& clock)
    {
        auto& d = *impl;

        if (d.loop == nullptr)
        {
            d.loop = pw_thread_loop_new ("speakerd-pw", nullptr);
            d.context = d.loop != nullptr ? pw_context_new (pw_thread_loop_get_loop (d.loop), nullptr, 0) : nullptr;

            if (d.context == nullptr || pw_thread_loop_start (d.loop) < 0)
            {
                // Both go, so that the next try starts clean. The loop is not running here.
                if (d.context != nullptr)
                    pw_context_destroy (d.context);

                if (d.loop != nullptr)
                    pw_thread_loop_destroy (d.loop);

                d.context = nullptr;
                d.loop = nullptr;
                return juce::Result::fail ("cannot start the PipeWire client loop");
            }
        }

        pw_thread_loop_lock (d.loop);

        d.core = pw_context_connect (d.context, nullptr, 0);

        if (d.core == nullptr)
        {
            const auto error = errorText (errno);
            pw_thread_loop_unlock (d.loop);
            return juce::Result::fail ("cannot connect to PipeWire: " + error);
        }

        d.failed = false;
        d.synced = false;
        d.settlePending = false;
        d.linkingStopped = false;
        pw_core_add_listener (d.core, &d.coreListener, &Impl::coreEvents, &d);
        d.registry = pw_core_get_registry (d.core, PW_VERSION_REGISTRY, 0);
        pw_registry_add_listener (d.registry, &d.registryListener, &Impl::registryEvents, &d);

        // The core info and the registry arrive on connecting; one round trip makes sure they
        // are both here.
        d.syncSeq = pw_core_sync (d.core, PW_ID_CORE, 0);

        for (int waited = 0; ! d.synced && ! d.failed && waited < 5; ++waited)
            pw_thread_loop_timed_wait (d.loop, 1);

        const bool ok = d.synced && ! d.failed;
        clock = d.clock;
        pw_thread_loop_unlock (d.loop);

        if (! ok)
        {
            stop();
            return juce::Result::fail ("PipeWire did not answer");
        }

        return juce::Result::ok();
    }

    juce::Result PipeWireNode::start()
    {
        auto& d = *impl;
        pw_thread_loop_lock (d.loop);

        // The input sink players play into: one that is already there (made by the speaker's
        // PipeWire configuration, or left by an earlier speakerd), or a new one. A new one
        // lingers: it stays in PipeWire when speakerd stops, so that a stream playing into it
        // stays there, and the amplifier gets silence, until the next speakerd adopts it.
        if (d.findNode (d.sinkName) != SPA_ID_INVALID)
        {
            log ("pipewire: input sink '" + d.sinkName + "' exists: using it");
        }
        else
        {
            auto* props = pw_properties_new ("factory.name",            "support.null-audio-sink",
                                             PW_KEY_NODE_NAME,          d.sinkName.toRawUTF8(),
                                             PW_KEY_NODE_DESCRIPTION,   "speakerd input",
                                             PW_KEY_MEDIA_CLASS,        "Audio/Sink",
                                             "audio.position",          "[ FL FR ]",
                                             // What the monitor carries is exactly what was played.
                                             "monitor.channel-volumes", "false",
                                             PW_KEY_OBJECT_LINGER,      "true",
                                             nullptr);

            d.ownSink = static_cast<pw_proxy*> (pw_core_create_object (d.core, "adapter", PW_TYPE_INTERFACE_Node,
                                                                       PW_VERSION_NODE, &props->dict, 0));
            pw_properties_free (props);

            if (d.ownSink == nullptr)
            {
                pw_thread_loop_unlock (d.loop);
                return juce::Result::fail ("cannot create the input sink '" + d.sinkName + "': " + errorText (errno));
            }

            pw_proxy_add_listener (d.ownSink, &d.sinkListener, &Impl::sinkEvents, &d);
            log ("pipewire: created the input sink '" + d.sinkName + "'");
        }

        // The filter has no media class: the session manager leaves it alone, speakerd links it.
        // It asks for the speaker's quantum, so that a player asking for more latency does not
        // change the block size the graph runs at.
        auto* props = pw_properties_new (PW_KEY_MEDIA_TYPE,       "Audio",
                                         PW_KEY_MEDIA_CATEGORY,   "Filter",
                                         PW_KEY_MEDIA_ROLE,       "DSP",
                                         PW_KEY_NODE_NAME,        d.filterName.toRawUTF8(),
                                         PW_KEY_NODE_DESCRIPTION, "speakerd: headless plugin host",
                                         nullptr);
        pw_properties_setf (props, PW_KEY_NODE_LATENCY, "%d/%d", d.clock.quantum, juce::roundToInt (d.clock.rate));

        d.filter = pw_filter_new (d.core, d.filterName.toRawUTF8(), props);

        if (d.filter == nullptr)
        {
            pw_thread_loop_unlock (d.loop);
            return juce::Result::fail ("cannot create the PipeWire node: " + errorText (errno));
        }

        pw_filter_add_listener (d.filter, &d.filterListener, &Impl::filterEvents, &d);

        for (int c = 0; c < 2; ++c)
        {
            d.inputs[c] = pw_filter_add_port (d.filter, PW_DIRECTION_INPUT, PW_FILTER_PORT_FLAG_MAP_BUFFERS, sizeof (PortData),
                                              pw_properties_new (PW_KEY_FORMAT_DSP, "32 bit float mono audio",
                                                                 PW_KEY_PORT_NAME, filterInputs[c],
                                                                 PW_KEY_AUDIO_CHANNEL, filterChannels[c],
                                                                 nullptr),
                                              nullptr, 0);

            d.outputs[c] = pw_filter_add_port (d.filter, PW_DIRECTION_OUTPUT, PW_FILTER_PORT_FLAG_MAP_BUFFERS, sizeof (PortData),
                                               pw_properties_new (PW_KEY_FORMAT_DSP, "32 bit float mono audio",
                                                                  PW_KEY_PORT_NAME, filterOutputs[c],
                                                                  nullptr),
                                               nullptr, 0);
        }

        const auto result = pw_filter_connect (d.filter, PW_FILTER_FLAG_RT_PROCESS, nullptr, 0);
        pw_thread_loop_unlock (d.loop);

        if (result < 0 || d.inputs[0] == nullptr || d.inputs[1] == nullptr || d.outputs[0] == nullptr || d.outputs[1] == nullptr)
            return juce::Result::fail ("cannot connect the PipeWire node: " + errorText (result));

        return juce::Result::ok();
    }

    void PipeWireNode::stopLinking()
    {
        auto& d = *impl;

        if (d.loop == nullptr)
            return;

        pw_thread_loop_lock (d.loop);
        d.linkingStopped = true;
        pw_thread_loop_unlock (d.loop);
    }

    void PipeWireNode::stop()
    {
        auto& d = *impl;

        if (d.loop == nullptr)
            return;

        pw_thread_loop_lock (d.loop);

        for (auto& link : d.ownLinks)
        {
            spa_hook_remove (&link->listener);
            pw_proxy_destroy (link->proxy);
        }

        d.ownLinks.clear();

        if (d.filter != nullptr)
        {
            spa_hook_remove (&d.filterListener);
            pw_filter_destroy (d.filter);
            d.filter = nullptr;
        }

        if (d.ownSink != nullptr)
        {
            // Only the local proxy: the sink itself lingers in PipeWire.
            spa_hook_remove (&d.sinkListener);
            pw_proxy_destroy (d.ownSink);
            d.ownSink = nullptr;
        }

        if (d.registry != nullptr)
        {
            spa_hook_remove (&d.registryListener);
            pw_proxy_destroy (reinterpret_cast<pw_proxy*> (d.registry));
            d.registry = nullptr;
        }

        if (d.core != nullptr)
        {
            spa_hook_remove (&d.coreListener);
            pw_core_disconnect (d.core);
            d.core = nullptr;
        }

        pw_thread_loop_unlock (d.loop);
        pw_thread_loop_stop (d.loop);

        if (d.context != nullptr)
            pw_context_destroy (d.context);

        pw_thread_loop_destroy (d.loop);
        d.context = nullptr;
        d.loop = nullptr;
        d.linked = false;
    }

    bool PipeWireNode::hasFailed() const noexcept            { return impl->failed.load(); }
    bool PipeWireNode::isLinked() const noexcept             { return impl->linked.load(); }
    juce::uint64 PipeWireNode::getLinkCount() const noexcept { return impl->linkCount.load(); }
}
