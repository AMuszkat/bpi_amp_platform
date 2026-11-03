#include "Bridge.h"

#include <cmath>
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

        juce::var orNull (int v)   { return v > 0 ? juce::var (v) : juce::var(); }

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
            {
                // The plugin's own bypass is the node's bypass: the document's `bypassed`, a
                // structural edit, never a parameter (whether Node::setBypassed or a knob moved it).
                if (params::isBypass (*node->getProcessor(), *p))
                    bridge.bypassParameterChanged();
                else
                    bridge.parameterChanged (node->nodeID.uid, *p, value);
            }
        }

        void parameterGestureChanged (int index, bool starting) override
        {
            if (auto* p = node->getProcessor()->getParameters()[index])
                if (! params::isBypass (*node->getProcessor(), *p))
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
            : bridge (b), speaker (speakerName), link ("monitor " + speakerName, host, port, b.options.sendQueueLimit)
        {
            // The speaker speaks first; nothing is subscribed, and no value believed, before
            // its welcome proves it knows the pair's secret.
            link.onConnected = [this] { handshake = {}; refused.clear(); bridge.notifyStatus(); };

            link.onDisconnected = [this] (const juce::String&)
            {
                handshake = {};
                refused.clear();
                bridge.monitorLinkLost (speaker);
                bridge.notifyStatus();
            };

            link.onMessage = [this] (const juce::var& m)
            {
                bridge.trace ("<", "monitor " + speaker, m);
                const auto op = m["op"].toString();
                juce::String why;

                if (op == "error" && m["about"].toString() == "hello")
                {
                    fail ("refused this controller: " + m["reason"].toString(), false);
                }
                else if (op == "error" && m["about"].toString() == "subscribe" && proven())
                {
                    // The speaker (or its speakerd) cannot give that stream: ampd has dropped the
                    // subscription. It stays counted here and is asked for again on the next
                    // connection (an upgraded ampd may have it); until then, not again.
                    const auto stream = m["stream"].toString();

                    if (counts.count (stream) > 0)
                    {
                        refused.insert (stream);
                        bridge.handleMonitorRefusal (speaker, stream, m["reason"].toString());
                    }
                }
                else if (handshake.phase == Handshake::Phase::waitingHello)
                {
                    juce::var hello;

                    if (op != "hello")
                        fail ("sent " + op + " before its hello", true);
                    else if (! bridge.answerServerHello (handshake, m, "speaker", speaker, false, hello, why))
                        fail (why, true);
                    else
                        send (hello);
                }
                else if (handshake.phase == Handshake::Phase::waitingWelcome)
                {
                    if (op != "welcome")
                    {
                        fail ("sent " + op + " before proving itself", true);
                    }
                    else if (! bridge.checkWelcome (handshake, m, why))
                    {
                        fail (why, true);
                    }
                    else
                    {
                        refusal.clear();

                        for (const auto& [stream, rates] : counts)
                            send (subscribeMessage (stream, rateOf (stream)));

                        bridge.notifyStatus();
                    }
                }
                else if (handshake.phase == Handshake::Phase::proven && op == "value")
                {
                    bridge.handleMonitorValue (speaker, m);
                }
            };

            link.start();
        }

        /** Counted: each holder asks at its own rate, and the stream goes at the highest of
            them (a second subscribe of a stream only changes its rate, docs/protocol.md). */
        void subscribe (const juce::String& stream, int rateHz)
        {
            const auto before = rateOf (stream);
            counts[stream].insert (rateHz);

            if (proven() && refused.count (stream) == 0 && rateOf (stream) != before)
                send (subscribeMessage (stream, rateOf (stream)));
        }

        void unsubscribe (const juce::String& stream, int rateHz)
        {
            const auto it = counts.find (stream);

            if (it == counts.end())
                return;

            const auto before = rateOf (stream);
            const auto one = it->second.find (rateHz);
            it->second.erase (one != it->second.end() ? one : it->second.begin());

            if (it->second.empty())
            {
                counts.erase (it);

                if (proven() && refused.count (stream) == 0)
                    send (object ({ { "op", "unsubscribe" }, { "stream", stream } }));

                refused.erase (stream);
            }
            else if (proven() && refused.count (stream) == 0 && rateOf (stream) != before)
            {
                send (subscribeMessage (stream, rateOf (stream)));
            }
        }

        juce::StringArray streams() const
        {
            juce::StringArray s;
            for (const auto& [stream, rates] : counts)
                s.add (stream);
            return s;
        }

        /** The rate a stream is asked for: the highest any holder wants; 0 when none. */
        int rateOf (const juce::String& stream) const
        {
            const auto it = counts.find (stream);
            return it == counts.end() || it->second.empty() ? 0 : *it->second.rbegin();
        }

        bool isConnected() const   { return link.isConnected() && proven(); }
        juce::String getRefusal() const   { return refusal; }
        juce::String getAddress() const   { return link.getAddress(); }

    private:
        bool proven() const   { return handshake.phase == Handshake::Phase::proven; }

        /** The speaker did not prove itself, or refused us: say why, believe nothing more on
            this connection, and close it when it is ours to close. */
        void fail (const juce::String& why, bool close)
        {
            handshake.phase = Handshake::Phase::failed;
            refusal = why;
            std::cerr << "[bridge] speaker " << speaker << ": " << why << "; trying again in 5 s" << std::endl;

            if (close)
                link.dropConnection (why, JsonLineLink::refusalBackOffMs);

            bridge.notifyStatus();
        }

        static juce::var subscribeMessage (const juce::String& stream, int rateHz)
        {
            return object ({ { "op", "subscribe" }, { "stream", stream }, { "rate_hz", rateHz } });
        }

        void send (const juce::var& m)
        {
            if (link.send (m))
                bridge.trace (">", "monitor " + speaker, m);
        }

        Bridge& bridge;
        const juce::String speaker;
        JsonLineLink link;
        std::map<juce::String, std::multiset<int>> counts;   // stream -> the rate of each holder
        std::set<juce::String> refused;                      // refused on this connection
        Handshake handshake;
        juce::String refusal;
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

    bool Bridge::answerServerHello (Handshake& h, const juce::var& serverHello, const juce::String& expectedRole,
                                    const juce::String& expectedName, bool withOscPort, juce::var& ourHello, juce::String& why) const
    {
        h.serverRole = serverHello["role"].toString();
        h.serverName = serverHello["name"].toString();
        h.serverNonce = serverHello["nonce"].toString();

        if (h.serverRole != expectedRole)
        {
            why = "said it is a " + h.serverRole.quoted() + ", not a " + expectedRole;
            return false;
        }

        // The name its proof will cover must be the one expected: a speaker of the pair could
        // otherwise answer for its partner.
        if (expectedName.isNotEmpty() && h.serverName != expectedName)
        {
            why = "is named " + h.serverName.quoted() + ", not " + expectedName.quoted() + " as expected";
            return false;
        }

        if (! auth::isNonce (h.serverNonce))
        {
            why = "sent a malformed nonce";
            return false;
        }

        h.clientNonce = auth::randomNonce();

        if (h.clientNonce.isEmpty())
        {
            why = "no random nonce: the system's random source did not answer";
            return false;
        }

        ourHello = object ({ { "op", "hello" }, { "role", "controller" }, { "name", options.name } });
        auto* o = ourHello.getDynamicObject();

        if (withOscPort)
            o->setProperty ("osc_port", oscPort);

        o->setProperty ("protocol", 1);
        o->setProperty ("nonce", h.clientNonce);

        // Without a secret there is no proof to give ("auth": null): only a server that has
        // none either accepts that, and only --insecure believes such a server.
        o->setProperty ("auth", options.secret.isNotEmpty()
                                    ? juce::var (auth::clientProof (options.secret, h.serverNonce, h.clientNonce, "controller", options.name))
                                    : juce::var());

        h.phase = Handshake::Phase::waitingWelcome;
        return true;
    }

    bool Bridge::checkWelcome (Handshake& h, const juce::var& welcome, juce::String& why) const
    {
        const auto& given = welcome["auth"];

        if (given.isVoid() || given.isUndefined())
        {
            if (! options.insecure)
            {
                why = "has no secret (its welcome carries no proof): connect with --insecure only to a pair that has not been installed yet";
                return false;
            }

            h.insecure = true;
            h.phase = Handshake::Phase::proven;
            return true;
        }

        if (options.secret.isEmpty())
        {
            why = "proves a secret this controller does not have: give the pair's secret file";
            return false;
        }

        const auto expected = auth::serverProof (options.secret, h.serverNonce, h.clientNonce, h.serverRole, h.serverName);

        if (! given.isString() || ! auth::sameProof (given.toString(), expected))
        {
            why = "did not prove the pair's secret (a wrong welcome proof): not believed";
            return false;
        }

        h.phase = Handshake::Phase::proven;
        return true;
    }

    void Bridge::failControl (const juce::String& why)
    {
        controlHandshake.phase = Handshake::Phase::failed;
        refusal = why;
        lastRefusal = why;
        synced = false;
        setStatus ("The hub " + why + ". Trying again in 5 s");

        if (control != nullptr)
            control->dropConnection (why, JsonLineLink::refusalBackOffMs);
    }

    void Bridge::bindOscToControlAddress()
    {
        // The hub takes OSC only from the address of an authenticated control connection: send
        // it from that connection's own local address, whatever the routes say.
        oscOut.disconnect();
        oscOutSocket = std::make_unique<juce::DatagramSocket> (false);
        const auto local = control != nullptr ? control->getLocalAddress() : juce::String();

        if (local.isNotEmpty() && oscOutSocket->bindToPort (0, local))
            oscOut.connectToSocket (*oscOutSocket, options.hubHost, options.knobsPort);
        else
            oscOut.connect (options.hubHost, options.knobsPort);
    }

    void Bridge::connectControl()
    {
        control = std::make_unique<JsonLineLink> ("control", options.hubHost, options.controlPort, options.sendQueueLimit);

        // The hub speaks first (docs/protocol.md, "Authentication"): its hello brings the nonce.
        control->onConnected = [this]
        {
            controlHandshake = {};
            oscToken.clear();
            refusal.clear();
            setStatus ("connected to " + control->getAddress() + ", waiting for the hub's hello");
        };

        control->onDisconnected = [this] (const juce::String& why)
        {
            synced = false;
            pending.reset();
            controlHandshake = {};
            oscToken.clear();

            if (refusal.isNotEmpty())
                setStatus ("The hub " + refusal + ". Check the pair's secret (Hub > Connect, or --secret-file); trying again in 5 s");
            else
                setStatus ("hub " + control->getAddress() + ": " + why + "; reconnecting");

            std::cerr << "[bridge] control link " << control->getAddress() << ": "
                      << (refusal.isNotEmpty() ? "refused: " + refusal : why) << "; reconnecting" << std::endl;
            refusal.clear();
        };

        control->onMessage = [this] (const juce::var& m) { trace ("<", "control", m); handleControl (m); };

        setStatus ("connecting to " + control->getAddress());
        control->start();
    }

    bool Bridge::isConnected() const   { return control != nullptr && control->isConnected(); }

    void Bridge::trace (const juce::String& direction, const juce::String& link, const juce::var& m)
    {
        if (! options.trace)
            return;

        auto shown = m;

        if (m.hasProperty ("auth"))   // a proof is no secret, but it stays out of logs all the same
        {
            shown = document::copy (m);
            shown.getDynamicObject()->setProperty ("auth", "(proof)");
        }

        std::cerr << "[bridge] " << direction << " " << link << " " << document::toJson (shown).substring (0, 400) << std::endl;
    }

    void Bridge::sendControl (const juce::var& m)
    {
        if (control != nullptr && control->send (m))
            trace (">", "control", m);
    }

    void Bridge::setStatus (const juce::String& s)
    {
        // Loudly, for as long as it lasts: a hub without a secret proves nothing.
        status = controlHandshake.insecure && ! s.startsWith ("INSECURE") ? "INSECURE (the hub has no secret): " + s : s;
        notifyStatus();
    }

    void Bridge::notifyStatus()
    {
        for (auto& s : speakers)
            if (auto* m = monitorFor (s.name))
            {
                s.monitorConnected = m->isConnected();
                s.monitorRefusal = m->getRefusal();
            }

        listeners.call ([] (Listener& l) { l.bridgeStatusChanged(); });
    }

    //==============================================================================
    void Bridge::handleControl (const juce::var& m)
    {
        const auto op = m["op"].toString();
        using Phase = Handshake::Phase;

        if (op == "error" && m["about"].toString() == "hello")
        {
            refusal = "refused this controller: " + (m["reason"].toString().isNotEmpty() ? m["reason"].toString() : juce::String ("no reason given"));
            lastRefusal = refusal;
            controlHandshake.phase = Phase::failed;
            setStatus ("The hub " + refusal + ". Check the pair's secret");
            return;
        }

        if (controlHandshake.phase == Phase::failed)
            return;   // nothing more is believed on this connection

        if (controlHandshake.phase == Phase::waitingHello)
        {
            juce::var hello;
            juce::String why;

            if (op != "hello")
                return failControl ("sent " + op + " before its hello");

            if (! answerServerHello (controlHandshake, m, "hub", options.hubName, true, hello, why))
                return failControl (why);

            hubName = controlHandshake.serverName;

            // A hub hello is a new start for the per-parameter seq (clarification 6).
            for (auto& [key, st] : paramStates)
                st.haveSeq = false;

            sendControl (hello);
            setStatus ("connected to " + control->getAddress() + ", waiting for the proof of hub "
                       + hubName.quoted() + (options.hubName.isEmpty() ? " (no hub name configured)" : ""));
            return;
        }

        if (controlHandshake.phase == Phase::waitingWelcome)
        {
            juce::String why;

            if (op != "welcome")
                return failControl ("sent " + op + " before proving itself");

            if (! checkWelcome (controlHandshake, m, why))
                return failControl (why);

            // The OSC token of this connection; none from a hub without a secret.
            oscToken = controlHandshake.insecure ? juce::String()
                                                 : auth::oscToken (options.secret, controlHandshake.serverNonce, controlHandshake.clientNonce);
            bindOscToControlAddress();

            if (controlHandshake.insecure)
                std::cerr << "[bridge] INSECURE: the hub has no secret; believed because of --insecure" << std::endl;

            setStatus (controlHandshake.insecure ? "believed without a proof (--insecure), waiting for its state"
                                                 : "the hub proved itself, waiting for its state");
            return;
        }

        if (op == "hello" || op == "welcome")
        {
            return;   // the handshake is over
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
        auto stateDoc = doc.isObject() ? document::copy (doc) : document::empty();
        const auto stateRevision = isInteger (m["revision"]) ? (juce::int64) m["revision"] : document::revisionOf (stateDoc);
        lastSeq = isInteger (m["seq"]) ? (juce::uint32) (juce::int64) m["seq"] : 0u;

        // The hub sends its whole state again whenever a speaker's fields change (online, the
        // buffer size it runs: docs/protocol.md), also while a proposal of ours is waiting; an
        // apply that changes the buffer size makes one at once. The proposal stays waiting for
        // its answer: with the same revision as before, the canvas keeps showing it; with
        // another, the canvas shows the hub's and the proposal is superseded. A new connection
        // has no proposal (onDisconnected dropped it).
        const auto keepCanvas = synced && pending.has_value() && ! pending->superseded && stateRevision == revision;

        if (pending.has_value() && ! keepCanvas)
            pending->superseded = true;

        if (! keepCanvas)
        {
            acceptedDoc = std::move (stateDoc);
            revision = stateRevision;
        }

        // A new state is a new start for the per-parameter sequence numbers (the hub may have
        // restarted, and its counter with it).
        for (auto& [key, st] : paramStates)
            st.haveSeq = false;

        lastRefusal.clear();

        if (! keepCanvas)
            applyDocumentToCanvas (acceptedDoc);

        updateSpeakers (m["speakers"]);

        remoteGestures.clear();
        if (auto* gestures = m["gestures"].getArray())
            for (const auto& gesture : *gestures)
                if (isInteger (gesture["node"]) && gesture["origin"].toString() != options.name
                    && gesture["state"].toString() != "end")
                    remoteGestures[{ (juce::uint32) (juce::int64) gesture["node"], gesture["id"].toString() }] = gesture["origin"].toString();

        synced = true;

        if (! keepCanvas)
            setStatus ("revision " + juce::String (revision) + " from hub " + hubName);
        else
            notifyStatus();
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
        scheduleCanvasCheck();
    }

    void Bridge::bypassParameterChanged()
    {
        // Our own mirror of a document sets it (Node::setBypassed under `applying`): no edit.
        // Otherwise the canvas's bypass moved (Node::isBypassed() reads the parameter), and the
        // check proposes it as the document's `bypassed`, or undoes it when it cannot.
        if (applying == 0)
            scheduleCanvasCheck();
    }

    void Bridge::scheduleCanvasCheck()
    {
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

    bool Bridge::propose (const document::Structure& local, std::optional<std::optional<int>> quantum)
    {
        const auto newRevision = revision + 1;
        auto change = acceptedCanvas.describeChangeTo (local);

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

        // The buffer size: the document's own field, so it goes to both speakers or neither.
        if (quantum.has_value())
        {
            if (auto* root = doc.getDynamicObject())
            {
                if (quantum->has_value())
                    root->setProperty ("quantum", **quantum);
                else
                    root->removeProperty ("quantum");
            }

            const auto what = "buffer size " + (quantum->has_value() ? juce::String (**quantum) : juce::String ("default"));
            change = change.isEmpty() ? what : change + ", " + what;
        }

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
            return false;
        }

        trace (">", "control", message);
        pending = std::move (p);
        setStatus ("proposed revision " + juce::String (newRevision) + ": " + change);
        return true;
    }

    const juce::var& Bridge::shownDocument() const
    {
        return pending.has_value() && ! pending->superseded ? pending->doc : acceptedDoc;
    }

    bool Bridge::proposeBufferSize (std::optional<int> quantum, juce::String& why)
    {
        if (! synced || ! isConnected())
            why = "not synced with the hub";
        else if (pending.has_value())
            why = "revision " + juce::String (pending->revision) + " is waiting for the hub's answer";
        else if (applying > 0)
            why = "the canvas is being changed";

        if (why.isNotEmpty())
        {
            setStatus ("Buffer size not changed: " + why + ".");
            return false;
        }

        if (! propose (graph.structure(), quantum))
        {
            why = "could not send the edit to the hub";
            return false;
        }

        return true;
    }

    Bridge::BufferSizeChoices Bridge::getBufferSizeChoices() const
    {
        BufferSizeChoices c;
        auto lo = document::minQuantum, hi = document::maxQuantum;
        juce::Array<int> defaults;

        for (const auto& s : speakers)
        {
            if (! s.online)
                continue;

            if (! s.hasQuantumRange())
            {
                if (c.why.isEmpty())
                    c.why = s.name + ": its speakerd cannot set the buffer size (no quantum_range)";
                continue;
            }

            lo = juce::jmax (lo, s.quantumLo);
            hi = juce::jmin (hi, s.quantumHi);

            if (s.quantumDefault > 0)
                defaults.addIfNotAlreadyThere (s.quantumDefault);
        }

        for (auto q = document::minQuantum; q <= document::maxQuantum; q *= 2)
            if (q >= lo && q <= hi)
                c.sizes.add (q);

        juce::StringArray defaultText;
        for (auto d : defaults)
            defaultText.add (juce::String (d));

        c.defaultLabel = "Default" + (defaultText.isEmpty() ? juce::String() : " (" + defaultText.joinIntoString (" / ") + ")");
        c.selected = document::quantumOf (shownDocument());

        if (! synced)
            c.why = "no state from the hub yet";
        else if (c.why.isEmpty() && c.sizes.isEmpty())
            c.why = "the speakers have no buffer size in common (" + juce::String (lo) + " to " + juce::String (hi) + ")";

        c.enabled = c.why.isEmpty();
        return c;
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

        if (controlHandshake.phase != Handshake::Phase::proven)
            return;

        juce::OSCMessage m ("/bpi/param");   // ,isfiss: node, id, value, seq, origin, token
        m.addInt32 ((juce::int32) uid);
        m.addString (id);
        m.addFloat32 ((float) plain);
        m.addInt32 (0);
        m.addString (options.name);
        m.addString (oscToken);

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

        // The plugin's own bypass follows the document's `bypassed` only: a `param` of it (an
        // older controller's) would leave the canvas and the document disagreeing.
        if (p == nullptr || params::isBypass (*node->getProcessor(), *p))
            return;

        auto& st = paramStates[key];

        // The control link's echo of our own last value: our own changes are through.
        if (origin == options.name && link == "control" && ! st.gestureActive)
            st.ownEchoQuietUntil = 0.0;

        if (seq != 0)
        {
            // Older: UDP reordered it. The same seq again: the other link's copy, kept only when
            // it is the control link's exact value after OSC's 32-bit float (docs/protocol.md,
            // clarification 26).
            const auto sameAgainExact = st.haveSeq && seq == st.lastSeq && link == "control" && ! st.lastSeqFromControl;

            if (st.haveSeq && ! seqNewer (seq, st.lastSeq) && ! sameAgainExact)
                return;

            st.lastSeq = seq;
            st.haveSeq = true;
            st.lastSeqFromControl = link == "control";
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

        // Only echoes stamped with this connection's token (docs/protocol.md, "Authentication").
        const auto tokenAt = address == "/bpi/param" ? 5 : 4;

        if (controlHandshake.phase != Handshake::Phase::proven || m.size() <= tokenAt || ! m[tokenAt].isString()
            || ! auth::sameProof (m[tokenAt].getString() + "#", oscToken + "#"))
        {
            ++oscDropped;
            return;
        }

        if (address == "/bpi/param" && m.size() >= 6)
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
        else if (address == "/bpi/gesture" && m.size() >= 5)
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

                // The buffer size (null or missing: unknown, an older hub or speakerd).
                const auto positive = [] (const juce::var& v)
                {
                    return isInteger (v) && (juce::int64) v > 0 && (juce::int64) v <= 1 << 24 ? (int) (juce::int64) v : 0;
                };

                s.rate = positive (entry["rate"]);
                s.quantum = positive (entry["quantum"]);
                s.quantumDefault = positive (entry["quantum_default"]);

                if (auto* range = entry["quantum_range"].getArray(); range != nullptr && range->size() == 2)
                    if (const auto a = positive ((*range)[0]), b = positive ((*range)[1]); a > 0 && b >= a)
                        std::tie (s.quantumLo, s.quantumHi) = std::make_pair (a, b);

                for (const auto& old : speakers)
                    if (old.name == s.name)
                    {
                        s.level = old.level;
                        s.levelTime = old.levelTime;
                        s.levelCount = old.levelCount;
                        s.timing = old.timing;
                        s.cpu = old.cpu;
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

        updateSpeakerWideSubscriptions();
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
                m->unsubscribe (s, options.monitorRateHz);

        if (speaker.isEmpty() || streams.isEmpty())
        {
            nodeSubscriptions.erase (uid);
            return;
        }

        held = { speaker, streams };

        if (auto* m = monitorFor (speaker))
            for (const auto& s : streams)
                m->subscribe (s, options.monitorRateHz);
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
        outputLevels.wanted += want ? 1 : -1;
        updateSpeakerWideSubscriptions();
    }

    void Bridge::wantSpeakerLoad (bool want)
    {
        speakerLoad.wanted += want ? 1 : -1;
        updateSpeakerWideSubscriptions();
    }

    void Bridge::updateSpeakerWideSubscriptions()
    {
        for (auto* set : { &outputLevels, &speakerLoad })
        {
            const auto rate = set->rateHz > 0 ? set->rateHz : options.monitorRateHz;

            for (auto& [name, link] : monitors)
            {
                const auto wanted = set->wanted > 0
                                    && std::any_of (speakers.begin(), speakers.end(), [&, n = name] (const Speaker& s) { return s.name == n; });
                const auto has = set->subscribed.count (name) > 0;

                if (wanted && ! has)
                {
                    for (const auto& stream : set->streams)
                        link->subscribe (stream, rate);
                    set->subscribed.insert (name);
                }
                else if (! wanted && has)
                {
                    for (const auto& stream : set->streams)
                        link->unsubscribe (stream, rate);
                    set->subscribed.erase (name);
                }
            }
        }
    }

    void Bridge::subscribe (const juce::String& speakerName, const juce::String& stream)
    {
        if (auto* m = monitorFor (speakerName))
            m->subscribe (stream, options.monitorRateHz);
    }

    void Bridge::unsubscribe (const juce::String& speakerName, const juce::String& stream)
    {
        if (auto* m = monitorFor (speakerName))
            m->unsubscribe (stream, options.monitorRateHz);
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

        if (stream == "speakerd/timing" || stream == "board/cpu")
        {
            auto* s = speakerNamed (speakerName);

            if (s == nullptr || ! v.isObject())
                return;

            const auto number = [] (const juce::var& x, double fallback)
            {
                return (x.isDouble() || x.isInt() || x.isInt64()) && std::isfinite ((double) x) ? (double) x : fallback;
            };

            if (stream == "speakerd/timing")
            {
                auto& timing = s->timing;
                // The totals of the previous value, if one came on this link (a link that went
                // reset the timing, so a reconnect starts again from the first value).
                const auto hadValue = timing.count > 0;
                const auto previousCycles = timing.cyclesTotal, previousLate = timing.lateTotal;
                timing.t = t;
                timing.receivedMs = nowMs();
                timing.refusal.clear();
                timing.cycleMean = number (v["cycle"]["mean"], 0.0);
                timing.cycleMax = number (v["cycle"]["max"], 0.0);
                timing.graphMean = number (v["graph"]["mean"], 0.0);
                timing.graphMax = number (v["graph"]["max"], 0.0);
                timing.periodCycles = (int) number (v["cycles"], 0.0);
                timing.periodLate = (int) number (v["late"], 0.0);
                timing.jumps = (int) number (v["jumps"], 0.0);
                timing.cyclesTotal = (juce::int64) number (v["cycles_total"], -1.0);
                timing.lateTotal = (juce::int64) number (v["late_total"], -1.0);

                // speakerd sends at the fastest rate any client asked for and ampd passes this
                // controller every few of its values, so the late cycles of the periods between
                // are only in the totals (firmware/CLAUDE.md). Totals that went down are a
                // speakerd that restarted: its last period is all there is.
                const auto deltaCycles = timing.cyclesTotal - previousCycles;
                const auto deltaLate = timing.lateTotal - previousLate;
                timing.fromTotals = hadValue && previousCycles >= 0 && previousLate >= 0
                                     && timing.cyclesTotal >= 0 && timing.lateTotal >= 0
                                     && deltaCycles >= 0 && deltaLate >= 0 && deltaLate <= deltaCycles;
                timing.cycles = timing.fromTotals ? deltaCycles : (juce::int64) timing.periodCycles;
                timing.late = timing.fromTotals ? deltaLate : (juce::int64) timing.periodLate;
                timing.jumpsTotal = (juce::int64) number (v["jumps_total"], -1.0);
                timing.droppedTotal = (juce::int64) number (v["dropped_total"], -1.0);
                timing.nodes.clear();

                if (auto* nodes = v["nodes"].getArray())
                    for (const auto& n : *nodes)
                        if (isInteger (n["uid"]))
                            timing.nodes[(juce::uint32) (juce::int64) n["uid"]] = { number (n["mean"], 0.0), number (n["max"], 0.0) };

                ++timing.count;
            }
            else
            {
                auto& cpu = s->cpu;
                cpu.t = t;
                cpu.receivedMs = nowMs();
                cpu.refusal.clear();
                cpu.busy = juce::jlimit (0.0, 1.0, number (v["busy"], 0.0));
                cpu.seconds = number (v["seconds"], 0.0);
                cpu.cores.clear();

                if (auto* cores = v["cores"].getArray())
                    for (const auto& c : *cores)
                        cpu.cores.push_back (juce::jlimit (0.0, 1.0, number (c, 0.0)));

                ++cpu.count;
            }

            listeners.call ([] (Listener& l) { l.speakerLoadChanged(); });
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

    void Bridge::handleMonitorRefusal (const juce::String& speakerName, const juce::String& stream, const juce::String& reason)
    {
        std::cerr << "[bridge] speaker " << speakerName << " refused " << stream << ": " << reason << std::endl;

        if (auto* s = speakerNamed (speakerName))
        {
            const auto why = reason.isNotEmpty() ? reason : juce::String ("refused");

            if (stream == "board/cpu")
            {
                s->cpu = {};
                s->cpu.refusal = why;
            }
            else if (stream == "speakerd/timing")
            {
                s->timing = {};
                s->timing.refusal = why;
            }
            else
            {
                return;
            }

            listeners.call ([] (Listener& l) { l.speakerLoadChanged(); });
        }
    }

    void Bridge::monitorLinkLost (const juce::String& speakerName)
    {
        // Nothing of a link that went is shown as if it were now; the next connection asks again.
        if (auto* s = speakerNamed (speakerName))
        {
            s->timing = {};
            s->cpu = {};
            listeners.call ([] (Listener& l) { l.speakerLoadChanged(); });
        }
    }

    Bridge::Speaker* Bridge::speakerNamed (const juce::String& name)
    {
        for (auto& s : speakers)
            if (s.name == name)
                return &s;

        return nullptr;
    }

    //==============================================================================
    namespace
    {
        juce::String percent (double fraction)
        {
            return juce::String (fraction * 100.0, 1) + " %";
        }
    }

    juce::String Bridge::audioLoadText (const Speaker& s) const
    {
        const auto& timing = s.timing;
        juce::String text ("audio cycle: ");

        if (timing.refusal.isNotEmpty())
            return text + "n/a";

        if (timing.count == 0)
            return text + (s.monitorConnected ? "waiting" : "-");

        return text + percent (timing.cycleMean) + " mean, " + percent (timing.cycleMax) + " max"
                    + (isFresh (timing.receivedMs) ? "" : " (stale)");
    }

    juce::String Bridge::lateCyclesText (const Speaker& s) const
    {
        const auto& timing = s.timing;

        if (timing.count == 0)
            return "late cycles: -";

        auto text = "late cycles: " + juce::String (timing.late) + " of " + juce::String (timing.cycles);

        if (timing.lateTotal >= 0)
            text << ", " << juce::String (timing.lateTotal) << " since start";

        return text;
    }

    juce::String Bridge::cpuText (const Speaker& s) const
    {
        const auto& cpu = s.cpu;
        juce::String text ("board CPU: ");

        if (cpu.refusal.isNotEmpty())
            return text + "n/a";

        if (cpu.count == 0)
            return text + (s.monitorConnected ? "waiting" : "-");

        text << percent (cpu.busy);

        if (! cpu.cores.empty())
        {
            juce::StringArray cores;
            for (auto c : cpu.cores)
                cores.add (juce::String (juce::roundToInt (c * 100.0)));
            text << " (cores " << cores.joinIntoString (" ") << ")";
        }

        return text + (isFresh (cpu.receivedMs) ? "" : " (stale)");
    }

    juce::String Bridge::bufferText (const Speaker& s) const
    {
        if (s.quantum <= 0)
            return s.hasQuantumRange() || s.rate > 0 ? "buffer: not running" : "buffer: unknown";

        if (s.rate <= 0)
            return "buffer: " + juce::String (s.quantum) + " samples";

        return "buffer: " + juce::String (s.quantum) + " samples, " + juce::String (s.quantum * 1000.0 / s.rate, 1)
             + " ms at " + juce::String (s.rate) + " Hz";
    }

    juce::String Bridge::nodeLoadText (juce::uint32 uid) const
    {
        juce::StringArray parts;

        for (const auto& s : speakers)
        {
            if (s.timing.count == 0)
                continue;

            const auto it = s.timing.nodes.find (uid);
            parts.add (roleLabel (s.role, s.name) + " " + (it != s.timing.nodes.end() ? percent (it->second.mean) : juce::String ("-")));
        }

        return parts.joinIntoString (" / ");
    }

    juce::String Bridge::nodeLoadTooltip (juce::uint32 uid) const
    {
        juce::StringArray parts;

        for (const auto& s : speakers)
        {
            if (s.timing.count == 0)
                continue;

            const auto it = s.timing.nodes.find (uid);
            parts.add ("speaker " + s.name + ": " + (it != s.timing.nodes.end()
                                                         ? percent (it->second.mean) + " mean, " + percent (it->second.max) + " max"
                                                         : juce::String ("not timed")));
        }

        return parts.isEmpty() ? juce::String() : "Share of the audio cycle, " + parts.joinIntoString ("; ");
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

    juce::var Bridge::describeLoad (const Speaker& s) const
    {
        const auto rounded = [] (double fraction) { return std::round (fraction * 1000.0) / 10.0; };   // % to 0.1
        juce::var timing, cpu;

        if (s.timing.count > 0)
        {
            auto* nodeObject = new juce::DynamicObject();
            for (const auto& [uid, load] : s.timing.nodes)
                nodeObject->setProperty (juce::String (uid), object ({ { "mean_pct", rounded (load.mean) }, { "max_pct", rounded (load.max) } }));

            timing = object ({ { "cycle_mean_pct", rounded (s.timing.cycleMean) }, { "cycle_max_pct", rounded (s.timing.cycleMax) },
                               { "graph_mean_pct", rounded (s.timing.graphMean) }, { "graph_max_pct", rounded (s.timing.graphMax) },
                               { "cycles", s.timing.cycles }, { "late", s.timing.late }, { "from_totals", s.timing.fromTotals },
                               { "period_cycles", s.timing.periodCycles }, { "period_late", s.timing.periodLate },
                               { "jumps", s.timing.jumps },
                               { "late_total", s.timing.lateTotal }, { "cycles_total", s.timing.cyclesTotal },
                               { "nodes", juce::var (nodeObject) }, { "values", s.timing.count } });
        }
        else if (s.timing.refusal.isNotEmpty())
        {
            timing = "n/a";
        }

        if (s.cpu.count > 0)
        {
            juce::Array<juce::var> cores;
            for (auto c : s.cpu.cores)
                cores.add (rounded (c));

            cpu = object ({ { "busy_pct", rounded (s.cpu.busy) }, { "cores_pct", cores }, { "seconds", s.cpu.seconds },
                            { "values", s.cpu.count } });
        }
        else if (s.cpu.refusal.isNotEmpty())
        {
            cpu = "n/a";
        }

        return object ({ { "timing", timing }, { "timing_refusal", s.timing.refusal },
                         { "cpu", cpu }, { "cpu_refusal", s.cpu.refusal },
                         { "text", juce::Array<juce::var> { audioLoadText (s), lateCyclesText (s), cpuText (s) } } });
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

            if (PluginGraph::isPluginNode (*n))
                o->setProperty ("load", nodeLoadText (n->nodeID.uid));

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
                                       { "monitor_refusal", s.monitorRefusal },
                                       { "level", level }, { "level_count", s.levelCount }, { "subscriptions", subs },
                                       { "load", describeLoad (s) },
                                       { "rate", orNull (s.rate) }, { "quantum", orNull (s.quantum) },
                                       { "quantum_range", s.hasQuantumRange() ? juce::var (juce::Array<juce::var> { s.quantumLo, s.quantumHi }) : juce::var() },
                                       { "quantum_default", orNull (s.quantumDefault) },
                                       { "buffer", bufferText (s) } }));
        }

        const auto choices = getBufferSizeChoices();
        juce::Array<juce::var> sizes;
        for (auto q : choices.sizes)
            sizes.add (q);

        const auto bufferSize = object ({ { "choices", sizes }, { "default_label", choices.defaultLabel },
                                          { "selected", choices.selected ? juce::var (*choices.selected) : juce::var() },
                                          { "enabled", choices.enabled }, { "why", choices.why } });

        for (const auto& [key, origin] : remoteGestures)
            gestures.add (object ({ { "node", (int) key.uid }, { "id", key.id }, { "origin", origin } }));

        return object ({ { "connected", isConnected() }, { "connections_made", getConnectionCount() },
                         { "refused", lastRefusal }, { "insecure", controlHandshake.insecure },
                         { "proven", controlHandshake.phase == Handshake::Phase::proven }, { "osc_dropped", oscDropped },
                         { "queued_bytes", control != nullptr ? (juce::int64) control->getQueuedBytes() : (juce::int64) 0 },
                         { "synced", synced }, { "hub", hubName },
                         { "revision", revision }, { "seq", (juce::int64) lastSeq },
                         { "quantum", acceptedDoc.isObject() && acceptedDoc.getDynamicObject()->hasProperty ("quantum") ? acceptedDoc["quantum"] : juce::var() },
                         { "buffer_size", bufferSize },
                         { "pending", pending.has_value() ? juce::var (pending->revision) : juce::var() },
                         { "outcome", lastOutcome }, { "status", status },
                         { "nodes", nodes }, { "connections", connections }, { "speakers", speakerList },
                         { "gestures", gestures } });
    }
}
