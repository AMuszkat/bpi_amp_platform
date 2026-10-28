#include "Bridge.h"

#include <iostream>

namespace bpi
{
    namespace
    {
        using Node = juce::AudioProcessorGraph::Node;
        using NodeID = juce::AudioProcessorGraph::NodeID;

        constexpr double paramIntervalMs = 1000.0 / 50.0;    // 50 messages a second per parameter
        constexpr double ownEchoQuietMs = 2000.0;            // after a gesture, at most this long
        constexpr double proposalTimeoutMs = 15000.0;        // the hub's 10 s, and some

        juce::var object (std::initializer_list<std::pair<const char*, juce::var>> properties)
        {
            auto* o = new juce::DynamicObject();
            for (const auto& [k, v] : properties)
                o->setProperty (k, v);
            return juce::var (o);
        }

        bool isInteger (const juce::var& v)   { return v.isInt() || v.isInt64(); }

        /** Serial number arithmetic: is `a` newer than `b`, for a 32-bit counter that wraps. */
        bool seqNewer (juce::uint32 a, juce::uint32 b)   { return (juce::int32) (a - b) > 0; }

        juce::String roleLabel (const juce::String& role, const juce::String& name)
        {
            if (role == "left")  return "L";
            if (role == "right") return "R";
            if (role == "mono")  return "M";
            return name;
        }
    }

    //==============================================================================
    /** Listens to every parameter of one node's control build. */
    class Bridge::NodeWatcher final : private juce::AudioProcessorParameter::Listener
    {
    public:
        NodeWatcher (Bridge& b, Node::Ptr n) : bridge (b), node (std::move (n))
        {
            for (auto* p : node->getProcessor()->getParameters())
                p->addListener (this);
        }

        ~NodeWatcher() override
        {
            for (auto* p : node->getProcessor()->getParameters())
                p->removeListener (this);
        }

        const Node::Ptr& getNode() const   { return node; }

    private:
        void parameterValueChanged (int index, float value) override
        {
            if (auto* p = node->getProcessor()->getParameters()[index])
                bridge.parameterChanged (node->nodeID.uid, *p, value);
        }

        void parameterGestureChanged (int index, bool starting) override
        {
            if (auto* p = node->getProcessor()->getParameters()[index])
                bridge.gestureChanged (node->nodeID.uid, *p, starting);
        }

        Bridge& bridge;
        const Node::Ptr node;
    };

    //==============================================================================
    /** One speaker's monitor link: its subscriptions, counted, and the values it sends. */
    class Bridge::MonitorLink
    {
    public:
        MonitorLink (Bridge& b, const juce::String& speakerName, const juce::String& host, int port)
            : bridge (b), speaker (speakerName), link ("monitor " + speakerName, host, port)
        {
            link.onConnected = [this]
            {
                for (const auto& [stream, count] : counts)
                    send (subscribeMessage (stream));

                bridge.notifyStatus();
            };

            link.onDisconnected = [this] (const juce::String&) { bridge.notifyStatus(); };

            link.onMessage = [this] (const juce::var& m)
            {
                bridge.trace ("<", "monitor " + speaker, m);

                if (m["op"].toString() == "value")
                    bridge.handleMonitorValue (speaker, m);
            };

            link.start();
        }

        void subscribe (const juce::String& stream)
        {
            if (++counts[stream] == 1)
                send (subscribeMessage (stream));
        }

        void unsubscribe (const juce::String& stream)
        {
            const auto it = counts.find (stream);

            if (it == counts.end())
                return;

            if (--it->second <= 0)
            {
                counts.erase (it);
                send (object ({ { "op", "unsubscribe" }, { "stream", stream } }));
            }
        }

        juce::StringArray streams() const
        {
            juce::StringArray s;
            for (const auto& [stream, count] : counts)
                s.add (stream);
            return s;
        }

        bool isConnected() const   { return link.isConnected(); }
        juce::String getAddress() const   { return link.getAddress(); }

    private:
        juce::var subscribeMessage (const juce::String& stream) const
        {
            return object ({ { "op", "subscribe" }, { "stream", stream }, { "rate_hz", bridge.options.monitorRateHz } });
        }

        void send (const juce::var& m)
        {
            if (link.send (m))
                bridge.trace (">", "monitor " + speaker, m);
        }

        Bridge& bridge;
        const juce::String speaker;
        JsonLineLink link;
        std::map<juce::String, int> counts;
    };

    //==============================================================================
    /** The header of a plugin window: which speaker's monitors it shows. */
    class Bridge::MonitorSelector final : public juce::Component,
                                          private Bridge::Listener,
                                          private juce::Timer
    {
    public:
        MonitorSelector (Bridge& b, juce::uint32 nodeUid) : bridge (b), uid (nodeUid)
        {
            label.setText ("Monitor from", juce::dontSendNotification);
            label.setJustificationType (juce::Justification::centredRight);
            addAndMakeVisible (label);
            addAndMakeVisible (info);
            bridge.addListener (this);
            rebuild();
            startTimerHz (10);
        }

        ~MonitorSelector() override   { bridge.removeListener (this); }

        void paint (juce::Graphics& g) override
        {
            g.fillAll (getLookAndFeel().findColour (juce::ResizableWindow::backgroundColourId).darker (0.3f));
        }

        void resized() override
        {
            auto r = getLocalBounds().reduced (4, 3);
            label.setBounds (r.removeFromLeft (100));
            r.removeFromLeft (6);

            for (auto* b : buttons)
                b->setBounds (r.removeFromLeft (36).reduced (1, 0));

            r.removeFromLeft (8);
            info.setBounds (r);
        }

    private:
        void bridgeStatusChanged() override   { rebuild(); }
        void timerCallback() override         { rebuild(); }

        /** The node's monitors as text: "mon_level -20.5", for editors that do not show them. */
        juce::String monitorValues() const
        {
            juce::StringArray values;

            if (auto* n = bridge.graph.graph.getNodeForId (NodeID { uid }))
                for (auto* p : n->getProcessor()->getParameters())
                    if (const auto id = params::idOf (*p); params::isMonitor (id))
                        values.add (id + " " + juce::String (params::plainValue (*p), 1) + " " + p->getLabel());

            return values.joinIntoString (", ");
        }

        void rebuild()
        {
            juce::StringArray names;
            for (const auto& s : bridge.getSpeakers())
                names.add (s.name);

            if (names != shownNames)
            {
                buttons.clear();
                shownNames = names;

                for (const auto& s : bridge.getSpeakers())
                {
                    auto* button = buttons.add (new juce::TextButton (roleLabel (s.role, s.name)));
                    button->setClickingTogglesState (true);
                    button->setColour (juce::TextButton::buttonOnColourId, juce::Colour (0xff2f6aa3));
                    button->setRadioGroupId (1);
                    button->setTooltip ("Show the monitors of speaker " + s.name);
                    button->onClick = [this, name = s.name] { bridge.setMonitorSpeaker (uid, name); };
                    addAndMakeVisible (button);
                }

                resized();
            }

            const auto current = bridge.getMonitorSpeaker (uid);

            for (int i = 0; i < buttons.size(); ++i)
                buttons[i]->setToggleState (shownNames[i] == current, juce::dontSendNotification);

            juce::String text = current.isEmpty() ? "no speaker" : current;

            for (const auto& s : bridge.getSpeakers())
                if (s.name == current)
                    text << (s.monitorConnected ? "" : s.address.isEmpty() ? ", no address" : ", not connected")
                         << (s.online ? "" : ", offline");

            if (const auto values = monitorValues(); values.isNotEmpty())
                text << "   " << values;

            if (text != info.getText())
                info.setText (text, juce::dontSendNotification);
        }

        Bridge& bridge;
        const juce::uint32 uid;
        juce::Label label, info;
        juce::OwnedArray<juce::TextButton> buttons;
        juce::StringArray shownNames;
    };

    //==============================================================================
    Bridge::Bridge (PluginGraph& g, Options o) : graph (g), options (std::move (o))
    {
        acceptedDoc = document::empty();
        acceptedCanvas = graph.structure();
        graph.graph.addChangeListener (this);

        graph.onWindowOpened = [this] (Node& node, PluginWindow& w)
        {
            if (w.type != PluginWindow::Type::debug)
                nodeMonitorsShown (node.nodeID.uid, true);
        };

        graph.onWindowClosing = [this] (Node& node, PluginWindow& w)
        {
            if (w.type != PluginWindow::Type::debug)
                nodeMonitorsShown (node.nodeID.uid, false);
        };

        graph.makeWindowHeader = [this] (Node& node) { return createMonitorSelector (node.nodeID.uid); };
    }

    Bridge::~Bridge()
    {
        stopTimer();
        graph.closeAnyOpenPluginWindows();
        graph.onWindowOpened = nullptr;
        graph.onWindowClosing = nullptr;
        graph.makeWindowHeader = nullptr;
        graph.graph.removeChangeListener (this);
        oscIn.removeListener (this);
        oscIn.disconnect();
        watchers.clear();
        monitors.clear();
        control.reset();
    }

    void Bridge::start()
    {
        // Echoes from the hub arrive here (the hello's osc_port).
        if (oscSocket.bindToPort (options.oscPort))
        {
            oscPort = oscSocket.getBoundPort();
            oscIn.connectToSocket (oscSocket);
            oscIn.addListener (this);
        }
        else
        {
            setStatus ("cannot listen for OSC on UDP port " + juce::String (options.oscPort));
        }

        startTimer (5);

        if (options.hubHost.isNotEmpty())
            connectControl();
        else
            setStatus ("no hub: give --hub or set it in Hub > Connect");
    }

    void Bridge::connectControl()
    {
        oscOut.connect (options.hubHost, options.knobsPort);

        control = std::make_unique<JsonLineLink> ("control", options.hubHost, options.controlPort);

        control->onConnected = [this]
        {
            sendControl (object ({ { "op", "hello" }, { "role", "controller" }, { "name", options.name },
                                   { "osc_port", oscPort }, { "protocol", 1 } }));
            setStatus ("connected to " + control->getAddress() + ", waiting for the hub's state");
        };

        control->onDisconnected = [this] (const juce::String& why)
        {
            synced = false;
            pending.reset();
            setStatus ("hub " + control->getAddress() + ": " + why + "; reconnecting");
        };

        control->onMessage = [this] (const juce::var& m) { trace ("<", "control", m); handleControl (m); };

        setStatus ("connecting to " + control->getAddress());
        control->start();
    }

    bool Bridge::isConnected() const   { return control != nullptr && control->isConnected(); }

    void Bridge::trace (const juce::String& direction, const juce::String& link, const juce::var& m)
    {
        if (options.trace)
            std::cerr << "[bridge] " << direction << " " << link << " " << document::toJson (m).substring (0, 400) << std::endl;
    }

    void Bridge::sendControl (const juce::var& m)
    {
        if (control != nullptr && control->send (m))
            trace (">", "control", m);
    }

    void Bridge::setStatus (const juce::String& s)
    {
        status = s;
        notifyStatus();
    }

    void Bridge::notifyStatus()
    {
        for (auto& s : speakers)
            if (auto* m = monitorFor (s.name))
                s.monitorConnected = m->isConnected();

        listeners.call ([] (Listener& l) { l.bridgeStatusChanged(); });
    }

    //==============================================================================
    void Bridge::handleControl (const juce::var& m)
    {
        const auto op = m["op"].toString();

        if (op == "hello")
        {
            hubName = m["name"].toString();
            notifyStatus();
        }
        else if (op == "state")
        {
            handleState (m);
        }
        else if (op == "graph")
        {
            handleGraph (m);
        }
        else if (op == "applied" || op == "rejected")
        {
            handleOutcome (m, op == "applied");
        }
        else if (op == "param")
        {
            if (isInteger (m["node"]))
                handleParamEcho ((juce::uint32) (juce::int64) m["node"], m["id"].toString(), m["value"],
                                 isInteger (m["seq"]) ? (juce::uint32) (juce::int64) m["seq"] : 0u,
                                 m["origin"].toString(), "control");
        }
        else if (op == "gesture")
        {
            if (isInteger (m["node"]))
                handleGesture ((juce::uint32) (juce::int64) m["node"], m["id"].toString(), m["state"].toString(), m["origin"].toString());
        }
        else if (op == "error")
        {
            setStatus ("hub: " + m["about"].toString() + " " + m["node"].toString() + " " + m["id"].toString()
                       + ": " + m["reason"].toString());
        }
        // Anything else is a newer peer's: ignored (docs/protocol.md, "Framing").
    }

    void Bridge::handleState (const juce::var& m)
    {
        const auto& doc = m["document"];
        acceptedDoc = doc.isObject() ? document::copy (doc) : document::empty();
        revision = isInteger (m["revision"]) ? (juce::int64) m["revision"] : document::revisionOf (acceptedDoc);
        lastSeq = isInteger (m["seq"]) ? (juce::uint32) (juce::int64) m["seq"] : 0u;
        pending.reset();

        // A new state is a new start for the per-parameter sequence numbers (the hub may have
        // restarted, and its counter with it).
        for (auto& [key, st] : paramStates)
            st.haveSeq = false;

        applyDocumentToCanvas (acceptedDoc);
        updateSpeakers (m["speakers"]);

        remoteGestures.clear();
        if (auto* gestures = m["gestures"].getArray())
            for (const auto& gesture : *gestures)
                if (isInteger (gesture["node"]) && gesture["origin"].toString() != options.name
                    && gesture["state"].toString() != "end")
                    remoteGestures[{ (juce::uint32) (juce::int64) gesture["node"], gesture["id"].toString() }] = gesture["origin"].toString();

        synced = true;
        setStatus ("revision " + juce::String (revision) + " from hub " + hubName);
    }

    void Bridge::handleGraph (const juce::var& m)
    {
        const auto rev = isInteger (m["revision"]) ? (juce::int64) m["revision"] : 0;
        const auto& doc = m["document"];

        if (! synced || rev <= revision || ! doc.isObject())
            return;

        const auto ours = pending.has_value() && rev == pending->revision && m["origin"].toString() == options.name;

        acceptedDoc = document::copy (doc);
        revision = rev;

        if (ours)
        {
            pending.reset();
            ++outcomeCount;
            lastOutcome = "applied " + juce::String (rev);
        }
        else if (pending.has_value())
        {
            pending->superseded = true;
        }

        applyDocumentToCanvas (acceptedDoc);
        setStatus ("revision " + juce::String (rev) + (ours ? " applied" : " from " + m["origin"].toString()));
    }

    void Bridge::handleOutcome (const juce::var& m, bool applied)
    {
        const auto rev = isInteger (m["revision"]) ? (juce::int64) m["revision"] : 0;

        if (! pending.has_value() || rev != pending->revision)
            return;   // not ours, or the `graph` message already settled it

        const auto superseded = pending->superseded;
        ++outcomeCount;

        if (applied)
        {
            if (! superseded)
            {
                acceptedDoc = pending->doc;
                acceptedCanvas = pending->canvas;
                revision = rev;
            }

            pending.reset();
            lastOutcome = "applied " + juce::String (rev);
            setStatus ("revision " + juce::String (rev) + " applied");
            return;
        }

        const auto reason = m["reason"].toString();
        pending.reset();
        lastOutcome = "rejected " + juce::String (rev) + ": " + reason;

        if (! superseded)
            undoTo (acceptedDoc, "revision " + juce::String (rev) + " rejected: " + reason + ". The edit was undone.");
        else
            setStatus ("revision " + juce::String (rev) + " rejected: " + reason);

        listeners.call ([&] (Listener& l) { l.proposalRejected (rev, reason); });
    }

    //==============================================================================
    void Bridge::applyDocumentToCanvas (const juce::var& doc)
    {
        juce::StringArray problems;

        {
            const juce::ScopedValueSetter<int> svs (applying, applying + 1);
            problems = graph.applyDocument (doc, [this] (juce::uint32 uid, const juce::String& id)
            {
                const auto it = paramStates.find ({ uid, id });
                return it != paramStates.end() && it->second.gestureActive;
            });
        }

        acceptedCanvas = graph.structure();
        syncWatchers();

        for (const auto& [uid, count] : std::map<juce::uint32, int> (openWindows))
            updateNodeSubscriptions (uid);

        for (const auto& p : problems)
            std::cerr << "[bridge] " << p << std::endl;
    }

    void Bridge::undoTo (const juce::var& doc, const juce::String& why)
    {
        {
            const juce::ScopedValueSetter<int> svs (applying, applying + 1);
            graph.applyDocument (doc, nullptr, false);
        }

        syncWatchers();
        setStatus (why);
    }

    void Bridge::changeListenerCallback (juce::ChangeBroadcaster*)
    {
        // The graph says it changed: maybe a user's edit. Look once the message loop is idle,
        // so that one gesture of several changes ("Disconnect all pins") is one proposal.
        if (! checkScheduled)
        {
            checkScheduled = true;
            juce::MessageManager::callAsync ([safe = juce::WeakReference<Bridge> (this)]
            {
                if (safe != nullptr)
                {
                    safe->checkScheduled = false;
                    safe->canvasMayHaveChanged();
                }
            });
        }
    }

    void Bridge::canvasMayHaveChanged()
    {
        if (applying > 0)
            return;

        syncWatchers();
        const auto local = graph.structure();

        if (! synced)
        {
            if (local != acceptedCanvas)
                undoTo (acceptedDoc, "Not connected to the hub: the graph follows the hub's state, so the edit was undone.");
            return;
        }

        if (pending.has_value())
        {
            const auto& target = pending->superseded ? acceptedCanvas : pending->canvas;

            if (local != target)
                undoTo (pending->superseded ? acceptedDoc : pending->doc,
                        "Revision " + juce::String (pending->revision) + " is waiting for the hub's answer: edit again after it.");
            return;
        }

        if (local != acceptedCanvas)
            propose (local);
    }

    void Bridge::propose (const document::Structure& local)
    {
        const auto newRevision = revision + 1;
        const auto change = acceptedCanvas.describeChangeTo (local);

        auto doc = document::applyEdit (acceptedDoc, acceptedCanvas, local, newRevision,
                                        [this] (juce::uint32 uid, juce::DynamicObject& node)
        {
            if (auto* n = graph.graph.getNodeForId (NodeID { uid }))
            {
                if (n->properties["kind"].toString() == nodeKind::control)
                    node.setProperty ("params", graph.currentParams (n->nodeID));

                const auto pos = graph.getNodePosition (n->nodeID);
                node.setProperty ("ui", object ({ { "x", pos.x }, { "y", pos.y } }));
            }
        });

        Pending p;
        p.revision = newRevision;
        p.doc = doc;
        p.canvas = local;
        p.sentMs = nowMs();

        const auto message = object ({ { "op", "graph" }, { "revision", newRevision }, { "base", revision },
                                       { "time", document::nowIso8601() }, { "origin", options.name },
                                       { "document", doc } });

        if (control == nullptr || ! control->send (message))
        {
            undoTo (acceptedDoc, "Could not send the edit to the hub: undone.");
            return;
        }

        trace (">", "control", message);
        pending = std::move (p);
        setStatus ("proposed revision " + juce::String (newRevision) + ": " + change);
    }

    //==============================================================================
    void Bridge::syncWatchers()
    {
        for (auto it = watchers.begin(); it != watchers.end();)
        {
            auto* n = graph.graph.getNodeForId (NodeID { it->first });
            it = (n == nullptr || n != it->second->getNode().get()) ? watchers.erase (it) : std::next (it);
        }

        for (auto* n : graph.graph.getNodes())
            if (n->properties["kind"].toString() == nodeKind::control && watchers.count (n->nodeID.uid) == 0)
                watchers[n->nodeID.uid] = std::make_unique<NodeWatcher> (*this, n);

        for (auto it = paramStates.begin(); it != paramStates.end();)
            it = watchers.count (it->first.uid) == 0 ? paramStates.erase (it) : std::next (it);

        for (auto it = nodeSubscriptions.begin(); it != nodeSubscriptions.end();)
        {
            const auto uid = it->first;
            ++it;
            if (watchers.count (uid) == 0)
                updateNodeSubscriptions (uid);
        }
    }

    void Bridge::parameterChanged (juce::uint32 uid, juce::AudioProcessorParameter& p, float normalised)
    {
        if (applying > 0 || ! synced)
            return;   // our own setting of a value; or no hub state yet, which overwrites it anyway

        const auto id = params::idOf (p);

        if (id.isEmpty() || params::isMonitor (id))
            return;

        auto& st = paramStates[{ uid, id }];
        const auto plain = params::plainValue (p, normalised);
        const auto overControl = ! st.gestureActive;

        if (nowMs() - st.lastSendMs >= paramIntervalMs)
        {
            st.pendingPlain.reset();
            st.lastSendMs = nowMs();
            sendParam (uid, id, p, plain, overControl);
        }
        else
        {
            st.pendingPlain = (float) plain;
            st.pendingOverControl = overControl;
        }
    }

    void Bridge::gestureChanged (juce::uint32 uid, juce::AudioProcessorParameter& p, bool starting)
    {
        if (applying > 0 || ! synced)
            return;

        const auto id = params::idOf (p);

        if (id.isEmpty() || params::isMonitor (id))
            return;

        auto& st = paramStates[{ uid, id }];
        st.gestureActive = starting;

        if (! starting)
        {
            // The last value of the gesture, reliably: UDP may have lost it.
            st.pendingPlain.reset();
            st.lastSendMs = nowMs();
            st.ownEchoQuietUntil = nowMs() + ownEchoQuietMs;
            sendParam (uid, id, p, params::plainValue (p), true);
        }

        sendControl (object ({ { "op", "gesture" }, { "node", (int) uid }, { "id", id },
                               { "state", starting ? "begin" : "end" }, { "origin", options.name } }));
    }

    void Bridge::sendParam (juce::uint32 uid, const juce::String& id, juce::AudioProcessorParameter& p, double plain, bool overControl)
    {
        if (overControl)
        {
            sendControl (object ({ { "op", "param" }, { "node", (int) uid }, { "id", id },
                                   { "value", params::toJson (p, plain) }, { "seq", 0 }, { "origin", options.name } }));
            return;
        }

        juce::OSCMessage m ("/bpi/param");
        m.addInt32 ((juce::int32) uid);
        m.addString (id);
        m.addFloat32 ((float) plain);
        m.addInt32 (0);
        m.addString (options.name);

        if (oscOut.send (m) && options.trace)
            std::cerr << "[bridge] > knobs /bpi/param " << (int) uid << " " << id << " " << plain << std::endl;
    }

    void Bridge::handleParamEcho (juce::uint32 uid, const juce::String& id, const juce::var& value,
                                  juce::uint32 seq, const juce::String& origin, const juce::String& link)
    {
        const ParamKey key { uid, id };
        auto& echo = lastEchoes[key];
        echo.value = value;
        echo.seq = seq;
        echo.origin = origin;
        echo.link = link;
        ++echo.count;

        if (seq != 0 && seqNewer (seq, lastSeq))
            lastSeq = seq;

        if (params::isMonitor (id))
            return;

        auto* node = graph.graph.getNodeForId (NodeID { uid });

        if (node == nullptr || node->properties["kind"].toString() != nodeKind::control)
            return;

        auto* p = params::find (*node->getProcessor(), id);

        if (p == nullptr)
            return;

        auto& st = paramStates[key];

        // The control link's echo of our own last value: our own changes are through.
        if (origin == options.name && link == "control" && ! st.gestureActive)
            st.ownEchoQuietUntil = 0.0;

        if (seq != 0)
        {
            if (st.haveSeq && ! seqNewer (seq, st.lastSeq))
                return;   // UDP reordered it, or the other link already brought it

            st.lastSeq = seq;
            st.haveSeq = true;
        }

        if (origin == options.name && (st.gestureActive || nowMs() < st.ownEchoQuietUntil))
            return;

        if (const auto normalised = params::normalisedFor (*p, value))
        {
            if (std::abs (p->getValue() - *normalised) > 1.0e-7f)
            {
                const juce::ScopedValueSetter<int> svs (applying, applying + 1);
                p->setValueNotifyingHost (*normalised);
            }
        }
    }

    void Bridge::handleGesture (juce::uint32 uid, const juce::String& id, const juce::String& state, const juce::String& origin)
    {
        if (origin == options.name)
            return;

        if (state == "begin")
            remoteGestures[{ uid, id }] = origin;
        else
            remoteGestures.erase ({ uid, id });

        notifyStatus();
    }

    void Bridge::oscMessageReceived (const juce::OSCMessage& m)
    {
        const auto address = m.getAddressPattern().toString();

        const auto number = [&m] (int i) -> std::optional<double>
        {
            if (i >= m.size()) return std::nullopt;
            if (m[i].isInt32())   return (double) m[i].getInt32();
            if (m[i].isFloat32()) return (double) m[i].getFloat32();
            return std::nullopt;
        };

        const auto text = [&m] (int i) -> juce::String
        {
            return i < m.size() && m[i].isString() ? m[i].getString() : juce::String();
        };

        if (address == "/bpi/param" && m.size() >= 5)
        {
            const auto node = number (0), value = number (2), seq = number (3);

            if (node && value && seq)
            {
                if (options.trace)
                    std::cerr << "[bridge] < knobs /bpi/param " << (int) *node << " " << text (1) << " " << *value
                              << " seq " << (juce::uint32) (juce::int64) *seq << " " << text (4) << std::endl;

                handleParamEcho ((juce::uint32) *node, text (1), *value, (juce::uint32) (juce::int64) *seq, text (4), "knobs");
            }
        }
        else if (address == "/bpi/gesture" && m.size() >= 4)
        {
            if (const auto node = number (0))
                handleGesture ((juce::uint32) *node, text (1), text (2), text (3));
        }
    }

    void Bridge::timerCallback()
    {
        const auto now = nowMs();

        for (auto& [key, st] : paramStates)
        {
            if (! st.pendingPlain.has_value() || now - st.lastSendMs < paramIntervalMs)
                continue;

            if (auto* n = graph.graph.getNodeForId (NodeID { key.uid }))
                if (auto* p = params::find (*n->getProcessor(), key.id))
                    sendParam (key.uid, key.id, *p, *st.pendingPlain, st.pendingOverControl || ! st.gestureActive);

            st.pendingPlain.reset();
            st.lastSendMs = now;
        }

        if (pending.has_value() && now - pending->sentMs > proposalTimeoutMs)
        {
            const auto rev = pending->revision;
            const auto superseded = pending->superseded;
            pending.reset();
            ++outcomeCount;
            lastOutcome = "rejected " + juce::String (rev) + ": no answer from the hub";

            if (! superseded)
                undoTo (acceptedDoc, "Revision " + juce::String (rev) + ": no answer from the hub. The edit was undone.");

            listeners.call ([&] (Listener& l) { l.proposalRejected (rev, "no answer from the hub"); });
        }
    }

    //==============================================================================
    void Bridge::updateSpeakers (const juce::var& list)
    {
        std::vector<Speaker> updated;

        if (auto* array = list.getArray())
        {
            for (const auto& entry : *array)
            {
                Speaker s;
                s.name = entry["name"].toString();

                if (s.name.isEmpty())
                    continue;

                s.role = entry["role"].toString();
                s.layer = entry["layer"].toString();
                s.online = entry["online"].isBool() ? (bool) entry["online"] : false;

                for (const auto& old : speakers)
                    if (old.name == s.name)
                    {
                        s.level = old.level;
                        s.levelTime = old.levelTime;
                        s.levelCount = old.levelCount;
                    }

                // Where its monitor link is: the command line or settings, a newer hub's
                // `address`, or, for the hub's own speaker, the hub's address.
                if (const auto it = options.speakerAddresses.find (s.name); it != options.speakerAddresses.end())
                    s.address = it->second;
                else if (entry["address"].isString())
                    s.address = entry["address"].toString();
                else if (s.name == hubName)
                    s.address = options.hubHost;

                updated.push_back (s);
            }
        }

        speakers = std::move (updated);

        for (const auto& s : speakers)
            if (s.address.isNotEmpty() && monitors.count (s.name) == 0)
                monitors[s.name] = std::make_unique<MonitorLink> (*this, s.name, s.address, options.monitorPort);

        for (const auto& [uid, count] : std::map<juce::uint32, int> (openWindows))
            updateNodeSubscriptions (uid);

        updateOutputLevelSubscriptions();
        notifyStatus();
    }

    Bridge::MonitorLink* Bridge::monitorFor (const juce::String& speakerName)
    {
        const auto it = monitors.find (speakerName);
        return it != monitors.end() ? it->second.get() : nullptr;
    }

    juce::String Bridge::getMonitorSpeaker (juce::uint32 uid) const
    {
        const auto known = [this] (const juce::String& name)
        {
            return std::any_of (speakers.begin(), speakers.end(), [&] (const Speaker& s) { return s.name == name; });
        };

        if (const auto it = monitorSpeaker.find (uid); it != monitorSpeaker.end() && known (it->second))
            return it->second;

        for (const auto& s : speakers)
            if (s.role == "left")
                return s.name;

        return speakers.empty() ? juce::String() : speakers.front().name;
    }

    void Bridge::setMonitorSpeaker (juce::uint32 uid, const juce::String& speakerName)
    {
        monitorSpeaker[uid] = speakerName;
        updateNodeSubscriptions (uid);
        notifyStatus();
    }

    void Bridge::updateNodeSubscriptions (juce::uint32 uid)
    {
        juce::String speaker;
        juce::StringArray streams;

        if (openWindows.count (uid) > 0)
        {
            if (auto* n = graph.graph.getNodeForId (NodeID { uid }); n != nullptr && n->properties["kind"].toString() == nodeKind::control)
            {
                speaker = getMonitorSpeaker (uid);

                for (auto* p : n->getProcessor()->getParameters())
                    if (const auto id = params::idOf (*p); params::isMonitor (id))
                        streams.add ("node/" + juce::String (uid) + "/" + id);
            }
        }

        auto& held = nodeSubscriptions[uid];

        if (held.first == speaker && held.second == streams)
        {
            if (speaker.isEmpty())
                nodeSubscriptions.erase (uid);
            return;
        }

        if (auto* m = monitorFor (held.first))
            for (const auto& s : held.second)
                m->unsubscribe (s);

        if (speaker.isEmpty() || streams.isEmpty())
        {
            nodeSubscriptions.erase (uid);
            return;
        }

        held = { speaker, streams };

        if (auto* m = monitorFor (speaker))
            for (const auto& s : streams)
                m->subscribe (s);
        else
            held = { {}, {} };   // no link to that speaker yet: retried when the speakers change
    }

    void Bridge::nodeMonitorsShown (juce::uint32 uid, bool shown)
    {
        if (shown)
            ++openWindows[uid];
        else if (--openWindows[uid] <= 0)
            openWindows.erase (uid);

        updateNodeSubscriptions (uid);
    }

    void Bridge::wantOutputLevels (bool want)
    {
        outputLevelWanted += want ? 1 : -1;
        updateOutputLevelSubscriptions();
    }

    void Bridge::updateOutputLevelSubscriptions()
    {
        for (auto& [name, link] : monitors)
        {
            const auto wanted = outputLevelWanted > 0
                                && std::any_of (speakers.begin(), speakers.end(), [&, n = name] (const Speaker& s) { return s.name == n; });
            const auto has = outputLevelSubscribed.count (name) > 0;

            if (wanted && ! has)
            {
                link->subscribe ("output/level");
                outputLevelSubscribed.insert (name);
            }
            else if (! wanted && has)
            {
                link->unsubscribe ("output/level");
                outputLevelSubscribed.erase (name);
            }
        }
    }

    void Bridge::subscribe (const juce::String& speakerName, const juce::String& stream)
    {
        if (auto* m = monitorFor (speakerName))
            m->subscribe (stream);
    }

    void Bridge::unsubscribe (const juce::String& speakerName, const juce::String& stream)
    {
        if (auto* m = monitorFor (speakerName))
            m->unsubscribe (stream);
    }

    juce::StringArray Bridge::getSubscriptions (const juce::String& speakerName) const
    {
        const auto it = monitors.find (speakerName);
        return it != monitors.end() ? it->second->streams() : juce::StringArray();
    }

    std::optional<Bridge::MonitorValue> Bridge::getMonitorValue (const juce::String& speakerName, const juce::String& stream) const
    {
        const auto it = monitorValues.find ({ speakerName, stream });

        if (it == monitorValues.end())
            return std::nullopt;

        return it->second;
    }

    void Bridge::handleMonitorValue (const juce::String& speakerName, const juce::var& m)
    {
        const auto stream = m["stream"].toString();
        const auto& v = m["v"];
        const auto t = (double) m["t"];

        auto& stored = monitorValues[{ speakerName, stream }];
        stored.v = v;
        stored.t = t;
        ++stored.count;

        if (stream == "output/level")
        {
            if (auto* values = v.getArray(); values != nullptr && values->size() >= 4)
            {
                for (auto& s : speakers)
                {
                    if (s.name != speakerName)
                        continue;

                    for (int i = 0; i < 4; ++i)
                        s.level[(size_t) i] = (float) (double) values->getReference (i);

                    s.levelTime = t;
                    ++s.levelCount;
                }

                listeners.call ([] (Listener& l) { l.outputLevelsChanged(); });
            }

            return;
        }

        // node/<uid>/<mon_id>: the value of the node's monitor, when this is the speaker its
        // windows show.
        const auto parts = juce::StringArray::fromTokens (stream, "/", "");

        if (parts.size() != 3 || parts[0] != "node" || ! params::isMonitor (parts[2]))
            return;

        const auto uid = (juce::uint32) parts[1].getLargeIntValue();

        if (getMonitorSpeaker (uid) != speakerName)
            return;

        if (auto* n = graph.graph.getNodeForId (NodeID { uid }); n != nullptr && n->properties["kind"].toString() == nodeKind::control)
        {
            if (auto* p = params::find (*n->getProcessor(), parts[2]))
            {
                if (const auto normalised = params::normalisedFor (*p, v))
                {
                    const juce::ScopedValueSetter<int> svs (applying, applying + 1);
                    p->setValueNotifyingHost (*normalised);
                }
            }
        }
    }

    //==============================================================================
    std::optional<Bridge::Echo> Bridge::getLastEcho (juce::uint32 uid, const juce::String& id) const
    {
        const auto it = lastEchoes.find ({ uid, id });

        if (it == lastEchoes.end())
            return std::nullopt;

        return it->second;
    }

    juce::String Bridge::getRemoteGesture (juce::uint32 uid, const juce::String& id) const
    {
        const auto it = remoteGestures.find ({ uid, id });
        return it != remoteGestures.end() ? it->second : juce::String();
    }

    juce::Component* Bridge::createMonitorSelector (juce::uint32 uid)
    {
        return new MonitorSelector (*this, uid);
    }

    juce::var Bridge::describe() const
    {
        juce::Array<juce::var> nodes, connections, speakerList, gestures;

        for (auto* n : graph.graph.getNodes())
        {
            auto* o = new juce::DynamicObject();
            o->setProperty ("uid", (int) n->nodeID.uid);
            o->setProperty ("kind", n->properties["kind"]);

            if (PluginGraph::isPluginNode (*n))
            {
                o->setProperty ("name", n->properties["name"]);
                o->setProperty ("identifier", n->properties["identifier"]);
                o->setProperty ("bypassed", n->isBypassed());
            }

            if (n->properties["kind"].toString() == nodeKind::control)
            {
                o->setProperty ("params", graph.currentParams (n->nodeID));

                auto* monitorObject = new juce::DynamicObject();
                for (auto* p : n->getProcessor()->getParameters())
                    if (const auto id = params::idOf (*p); params::isMonitor (id))
                        monitorObject->setProperty (id, params::toJson (*p, params::plainValue (*p)));

                o->setProperty ("monitors", juce::var (monitorObject));
                o->setProperty ("monitor_speaker", getMonitorSpeaker (n->nodeID.uid));
            }

            nodes.add (juce::var (o));
        }

        for (const auto& c : graph.graph.getConnections())
            connections.add (juce::Array<juce::var> { (int) c.source.nodeID.uid, c.source.channelIndex,
                                                      (int) c.destination.nodeID.uid, c.destination.channelIndex });

        for (const auto& s : speakers)
        {
            juce::Array<juce::var> level;
            for (auto x : s.level)
                level.add ((double) x);

            juce::Array<juce::var> subs;
            for (const auto& stream : getSubscriptions (s.name))
                subs.add (stream);

            speakerList.add (object ({ { "name", s.name }, { "role", s.role }, { "online", s.online }, { "layer", s.layer },
                                       { "address", s.address }, { "monitor_connected", s.monitorConnected },
                                       { "level", level }, { "level_count", s.levelCount }, { "subscriptions", subs } }));
        }

        for (const auto& [key, origin] : remoteGestures)
            gestures.add (object ({ { "node", (int) key.uid }, { "id", key.id }, { "origin", origin } }));

        return object ({ { "connected", isConnected() }, { "synced", synced }, { "hub", hubName },
                         { "revision", revision }, { "seq", (juce::int64) lastSeq },
                         { "pending", pending.has_value() ? juce::var (pending->revision) : juce::var() },
                         { "outcome", lastOutcome }, { "status", status },
                         { "nodes", nodes }, { "connections", connections }, { "speakers", speakerList },
                         { "gestures", gestures } });
    }
}
