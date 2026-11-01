#pragma once

#include "../Plugins/PluginGraph.h"
#include "Auth.h"
#include "GraphDocument.h"
#include "JsonLineLink.h"

#include <juce_osc/juce_osc.h>

#include <array>
#include <map>
#include <optional>
#include <set>

namespace bpi
{
    /** The bridge: the one object through which the controller talks to the speakers
        (docs/protocol.md). It

        - connects to the hub's control link, says hello and builds the canvas from the hub's
          `state`, never from its own older copy;
        - sends every parameter change of every node: over OSC (knobs link), at most 50 a
          second per parameter, while a gesture is active, and the last value of each gesture,
          and any change without a gesture, over the control link;
        - applies the echoes of other controllers' changes, and ignores its own while its
          gesture on that parameter is active (and until the control link's echo of its last
          value arrives);
        - turns a structural edit of the canvas into a proposal of the next revision, keeps it
          when the hub says `applied`, undoes it and says why when `rejected`, and applies the
          `graph` messages of other controllers;
        - connects to each speaker's monitor link, subscribes to the `mon_` streams of the
          plugins whose windows are open (from one speaker each, chosen per node), to the
          output level of every speaker and, while the speakers panel shows it, to every
          speaker's load (`speakerd/timing`, `board/cpu`), and fills the control builds' `mon_`
          parameters;
        - treats a node's bypass only as the document's `bypassed`: a control build's own bypass
          parameter (getBypassParameter()) is never sent as a parameter, never kept in `params`,
          and a change of it is a bypass edit, proposed as a revision.

        Everything runs on the message thread. */
    class Bridge final : private juce::Timer,
                         private juce::ChangeListener,
                         private juce::OSCReceiver::Listener<juce::OSCReceiver::MessageLoopCallback>
    {
    public:
        struct Options
        {
            juce::String hubHost;                // empty: not connected
            int controlPort = 7450, knobsPort = 7451, monitorPort = 7452;
            juce::String name;                   // this controller's origin
            int oscPort = 0;                     // where the hub sends echoes; 0 = any free port
            std::map<juce::String, juce::String> speakerAddresses;   // speaker name -> host
            int monitorRateHz = 30;
            size_t sendQueueLimit = JsonLineLink::defaultMaxQueuedBytes;   // per link, before it reconnects
            bool trace = false;                  // every line in and out on stderr (proofs hidden)
            juce::String secret;                 // the pair's secret: proofs only, never shown
            bool insecure = false;               // believe a hub (or speaker) that has no secret
            juce::String hubName;                // the hub's name to expect; empty: the one it proves
        };

        Bridge (PluginGraph&, Options);
        ~Bridge() override;

        void start();
        const Options& getOptions() const noexcept   { return options; }

        //==============================================================================
        bool isConnected() const;            // the control link is up
        bool isSynced() const noexcept       { return synced; }   // the hub's state arrived
        bool isInsecure() const noexcept     { return controlHandshake.insecure; }   // a hub without a secret, believed by --insecure
        juce::int64 getRevision() const noexcept   { return revision; }
        juce::uint32 getLastSeq() const noexcept   { return lastSeq; }
        int getConnectionCount() const             { return control != nullptr ? control->getConnectionCount() : 0; }
        bool hasPendingProposal() const noexcept   { return pending.has_value(); }
        juce::String getHubName() const            { return hubName; }
        juce::String getStatus() const             { return status; }
        juce::String getRefusal() const            { return lastRefusal; }   // why the hub refused us, if it did
        const juce::var& getAcceptedDocument() const noexcept   { return acceptedDoc; }

        struct Speaker
        {
            juce::String name, role, layer, address;
            bool online = false;
            bool monitorConnected = false;
            juce::String monitorRefusal;    // why the speaker refused this controller, if it did
            std::array<float, 4> level { -120.0f, -120.0f, -120.0f, -120.0f };   // woofer peak, rms, tweeter peak, rms
            double levelTime = -1.0;        // speakerd's clock; < 0: none yet
            int levelCount = 0;

            /** speakerd/timing: what the audio cycles of speakerd's last period cost, as
                fractions of the cycle's length (docs/protocol.md, "Monitoring"). speakerd's
                period is the fastest rate any client asked for, so a value can cover less than
                the time since this controller's previous one: `cycles` and `late` are counted
                over the panel's own interval, from the change in the `*_total` counters. */
            struct Timing
            {
                int count = 0;                  // values received; 0: none (yet, or since the link went)
                double t = 0.0, receivedMs = 0.0;
                double cycleMean = 0.0, cycleMax = 0.0, graphMean = 0.0, graphMax = 0.0;   // speakerd's last period
                juce::int64 cycles = 0, late = 0;   // since the previous value received (or speakerd's last period, below)
                bool fromTotals = false;        // cycles and late are the change in the totals; false: the first
                                                // value since the link came, after a speakerd restart (totals
                                                // went down), or totals not given: speakerd's last period's
                int periodCycles = 0, periodLate = 0, jumps = 0;   // speakerd's last period, as sent
                juce::int64 cyclesTotal = -1, lateTotal = -1, jumpsTotal = -1, droppedTotal = -1;   // -1: not given
                struct NodeLoad { double mean = 0.0, max = 0.0; };
                std::map<juce::uint32, NodeLoad> nodes;   // by the document's uid
                juce::String refusal;           // the speaker refused the subscription: why
            };

            /** board/cpu: the whole board's busy fraction over the last `seconds`, and per core. */
            struct Cpu
            {
                int count = 0;
                double t = 0.0, receivedMs = 0.0;
                double busy = 0.0, seconds = 0.0;
                std::vector<double> cores;
                juce::String refusal;           // an ampd without board/cpu: shown as n/a
            };

            Timing timing;
            Cpu cpu;
        };

        const std::vector<Speaker>& getSpeakers() const noexcept   { return speakers; }

        //==============================================================================
        /** The speaker whose monitors a node's windows show ("L"/"R" in its window). */
        juce::String getMonitorSpeaker (juce::uint32 uid) const;
        void setMonitorSpeaker (juce::uint32 uid, const juce::String& speakerName);

        /** Every speaker's output level, for the speakers panel: counted, so the panel and a
            script can both want it. */
        void wantOutputLevels (bool);

        /** Every speaker's load, for the speakers panel and the canvas: `speakerd/timing` and
            `board/cpu` at loadRateHz, counted like the output levels. */
        void wantSpeakerLoad (bool);
        static constexpr int loadRateHz = 2;
        static constexpr double loadStaleMs = 3000.0;   // no value for this long: shown as stale

        /** The load as the speakers panel shows it, one line each: the audio cycle, the late
            cycles, the board's CPU ("n/a" when the speaker refused board/cpu). */
        juce::String audioLoadText (const Speaker&) const;
        juce::String lateCyclesText (const Speaker&) const;
        juce::String cpuText (const Speaker&) const;

        /** A node's share of each speaker's cycle, as the canvas shows it under the node's name:
            "L 3.1 % / R 3.0 %" (the mean), empty when no speaker has timed it. The tooltip has
            the mean and the max per speaker. */
        juce::String nodeLoadText (juce::uint32 uid) const;
        juce::String nodeLoadTooltip (juce::uint32 uid) const;

        /** A subscription by hand (the test mode). Counted with the windows' own. */
        void subscribe (const juce::String& speakerName, const juce::String& stream);
        void unsubscribe (const juce::String& speakerName, const juce::String& stream);
        juce::StringArray getSubscriptions (const juce::String& speakerName) const;

        struct MonitorValue { juce::var v; double t = 0.0; int count = 0; };
        std::optional<MonitorValue> getMonitorValue (const juce::String& speakerName, const juce::String& stream) const;

        //==============================================================================
        /** What the test mode waits for. */
        struct Echo { juce::var value; juce::uint32 seq = 0; juce::String origin, link; int count = 0; };
        std::optional<Echo> getLastEcho (juce::uint32 uid, const juce::String& id) const;
        int getProposalOutcomes() const noexcept   { return outcomeCount; }
        juce::String getLastOutcome() const        { return lastOutcome; }   // "applied 8" or "rejected 8: <reason>"
        juce::String getRemoteGesture (juce::uint32 uid, const juce::String& id) const;   // who drags it, or empty

        /** The controller's view as one JSON object: what `--script` prints. */
        juce::var describe() const;

        /** A speaker's load as the test mode prints it (`state`, `wait-load`): the numbers in %,
            "n/a" for a stream the speaker refused, and the panel's lines as `text`. */
        juce::var describeLoad (const Speaker&) const;

        //==============================================================================
        struct Listener
        {
            virtual ~Listener() = default;
            virtual void bridgeStatusChanged() {}            // connection, revision, speakers, status
            virtual void outputLevelsChanged() {}
            virtual void speakerLoadChanged() {}             // a timing or CPU value, or a refusal
            virtual void proposalRejected (juce::int64 /*revision*/, const juce::String& /*reason*/) {}
        };

        void addListener (Listener* l)      { listeners.add (l); }
        void removeListener (Listener* l)   { listeners.remove (l); }

        /** A window that shows a node's monitors opened or closed: what a plugin window does,
            and what the test mode does without a window. Counted per node. */
        void nodeMonitorsShown (juce::uint32 uid, bool shown);

        /** A component for a plugin window's header: the L / R monitor selector. */
        juce::Component* createMonitorSelector (juce::uint32 uid);

    private:
        //==============================================================================
        struct ParamKey
        {
            juce::uint32 uid;
            juce::String id;
            bool operator< (const ParamKey& o) const   { return uid != o.uid ? uid < o.uid : id < o.id; }
        };

        struct ParamState
        {
            bool gestureActive = false;
            double ownEchoQuietUntil = 0.0;      // after a gesture: ignore our own OSC echoes until the control echo
            double lastSendMs = -1.0e9;
            std::optional<float> pendingPlain;   // a value waiting for its 50 Hz slot
            bool pendingOverControl = false;
            juce::uint32 lastSeq = 0;
            bool haveSeq = false;
            bool lastSeqFromControl = false;
        };

        class NodeWatcher;
        class MonitorLink;
        class MonitorSelector;

        //==============================================================================
        void connectControl();
        /** One link's handshake (docs/protocol.md, "Authentication"): the server's hello, our
            hello with our nonce and proof, the server's welcome with its proof. Nothing the
            server sends is believed before its proof checked. */
        struct Handshake
        {
            enum class Phase { waitingHello, waitingWelcome, proven, failed };
            Phase phase = Phase::waitingHello;
            juce::String serverRole, serverName, serverNonce, clientNonce;
            bool insecure = false;          // proven by a server with no secret (--insecure)
        };

        /** Our hello to a server's: false (with why) when its nonce is malformed or no random
            nonce can be had. */
        bool answerServerHello (Handshake&, const juce::var& serverHello, const juce::String& expectedRole,
                                const juce::String& expectedName, bool withOscPort, juce::var& ourHello, juce::String& why) const;

        /** The server's welcome checked: true when it proves the pair's secret (or, with
            --insecure, it has none). */
        bool checkWelcome (Handshake&, const juce::var& welcome, juce::String& why) const;

        void failControl (const juce::String& why);
        void bindOscToControlAddress();
        void handleControl (const juce::var&);
        void handleState (const juce::var&);
        void handleGraph (const juce::var&);
        void handleOutcome (const juce::var&, bool applied);
        void handleParamEcho (juce::uint32 uid, const juce::String& id, const juce::var& value,
                              juce::uint32 seq, const juce::String& origin, const juce::String& link);
        void handleGesture (juce::uint32 uid, const juce::String& id, const juce::String& state, const juce::String& origin);

        void applyDocumentToCanvas (const juce::var& doc);
        void canvasMayHaveChanged();
        void propose (const document::Structure& local);
        void undoTo (const juce::var& doc, const juce::String& why);
        void setStatus (const juce::String&);
        void notifyStatus();

        void parameterChanged (juce::uint32 uid, juce::AudioProcessorParameter&, float normalised);
        void gestureChanged (juce::uint32 uid, juce::AudioProcessorParameter&, bool starting);
        void sendParam (juce::uint32 uid, const juce::String& id, juce::AudioProcessorParameter&, double plain, bool overControl);
        void sendControl (const juce::var&);
        void syncWatchers();

        void updateSpeakers (const juce::var& list);
        MonitorLink* monitorFor (const juce::String& speakerName);
        void updateNodeSubscriptions (juce::uint32 uid);
        void updateSpeakerWideSubscriptions();
        void handleMonitorValue (const juce::String& speakerName, const juce::var&);
        void handleMonitorRefusal (const juce::String& speakerName, const juce::String& stream, const juce::String& reason);
        void monitorLinkLost (const juce::String& speakerName);
        Speaker* speakerNamed (const juce::String&);
        bool isFresh (double receivedMs) const   { return nowMs() - receivedMs < loadStaleMs; }

        /** A control build's own bypass parameter moved: maybe a bypass edit (a knob, or
            Node::setBypassed). Looked at once the message loop is idle, like a canvas edit. */
        void bypassParameterChanged();
        void scheduleCanvasCheck();

        void timerCallback() override;
        void changeListenerCallback (juce::ChangeBroadcaster*) override;
        void oscMessageReceived (const juce::OSCMessage&) override;

        static double nowMs()   { return juce::Time::getMillisecondCounterHiRes(); }
        void trace (const juce::String& direction, const juce::String& link, const juce::var&);

        //==============================================================================
        PluginGraph& graph;
        Options options;

        std::unique_ptr<JsonLineLink> control;
        juce::DatagramSocket oscSocket { false };
        juce::OSCReceiver oscIn;
        std::unique_ptr<juce::DatagramSocket> oscOutSocket;   // bound to the control link's local address
        juce::OSCSender oscOut;
        int oscPort = 0;

        bool synced = false;
        Handshake controlHandshake;             // on this control connection
        juce::String oscToken;                  // its OSC token, once the hub proved itself
        int oscDropped = 0;                     // OSC datagrams without our token, ignored
        juce::String refusal;                   // why the link closes, until it does
        juce::String lastRefusal;               // the same, until the hub's state arrives
        juce::String hubName, status;
        juce::int64 revision = 0;
        juce::uint32 lastSeq = 0;
        juce::var acceptedDoc;
        document::Structure acceptedCanvas;     // the canvas as the accepted document left it

        struct Pending
        {
            juce::int64 revision = 0;
            juce::var doc;
            document::Structure canvas;
            bool superseded = false;            // another revision arrived first: the canvas shows that
            double sentMs = 0.0;
        };
        std::optional<Pending> pending;

        int applying = 0;                       // > 0 while the bridge itself changes the canvas or a value
        bool checkScheduled = false;

        std::map<juce::uint32, std::unique_ptr<NodeWatcher>> watchers;
        std::map<ParamKey, ParamState> paramStates;
        std::map<ParamKey, Echo> lastEchoes;
        std::map<ParamKey, juce::String> remoteGestures;
        int outcomeCount = 0;
        juce::String lastOutcome;

        std::vector<Speaker> speakers;
        std::map<juce::String, std::unique_ptr<MonitorLink>> monitors;
        std::map<juce::uint32, juce::String> monitorSpeaker;     // per node
        std::map<juce::uint32, int> openWindows;                 // per node: windows that show monitors
        std::map<juce::uint32, std::pair<juce::String, juce::StringArray>> nodeSubscriptions;  // what each node holds
        std::map<std::pair<juce::String, juce::String>, MonitorValue> monitorValues;   // (speaker, stream)
        /** Streams every speaker gets while something wants them: the output level, the load. */
        struct SpeakerWide
        {
            SpeakerWide (juce::StringArray s, int rate = 0) : streams (std::move (s)), rateHz (rate) {}

            juce::StringArray streams;
            int rateHz = 0;                     // 0: the --monitor-rate
            int wanted = 0;
            std::set<juce::String> subscribed;  // speakers
        };

        SpeakerWide outputLevels { juce::StringArray { "output/level" } };
        SpeakerWide speakerLoad { juce::StringArray { "speakerd/timing", "board/cpu" }, loadRateHz };

        juce::ListenerList<Listener> listeners;

        JUCE_DECLARE_WEAK_REFERENCEABLE (Bridge)
        JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (Bridge)
    };
}
