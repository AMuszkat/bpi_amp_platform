#pragma once

#include "Engine.h"
#include "GraphBuilder.h"
#include "LocalLink.h"
#include "StateLayers.h"

#include <deque>
#include <functional>
#include <map>
#include <mutex>

namespace speakerd
{
    class Monitor;

    /*  What arrives over the local link, and the graph it acts on (docs/protocol.md).

        - `param`: the link thread looks the parameter up in the running graph's table, checks
          `seq`, turns the value into the hosted parameter's normalised value, and posts it to
          the graph's ParameterInbox; the audio thread sets it at the top of its next cycle.
          Nothing is answered unless the node or the ID is unknown, the ID is a monitor's, or
          the value is not one the parameter takes: {"op":"error","about":"param",...}.
        - `apply`: queued, in order, for the message thread, which builds the document as a new
          graph beside the running one and swaps it in under the fade (Engine::install). The
          answer is {"op":"applied","revision":N} once the new graph plays, or
          {"op":"failed","revision":N,"reason":...} with the old graph still playing. An apply
          that arrives while another is being built waits for it (at most maxWaitingApplies;
          beyond that it fails at once).
        - `status`: answered with the hello's fields; also sent unasked (notifyStatus()) when the
          rate or the quantum the cycles run at changes.
        - the document's `quantum`: an apply whose quantum this speaker's PipeWire does not allow
          fails before anything is built; once a document is in force (the boot layer, an
          applied revision), its quantum is set as PipeWire's clock.force-quantum (0 for none).
        - `subscribe`, `unsubscribe`: handed to the Monitor.

        One lock guards the running graph's tables, the seq of every parameter, the revision and
        the apply queue. The link thread, the monitor thread and the message thread take it
        briefly; the audio thread never does. A graph is deleted only after its tables have been
        replaced under the lock, so nothing refers to its parameters any more. */
    class Control final : public LocalLink::Handler
    {
    public:
        static constexpr int maxWaitingApplies = 8;
        static constexpr int protocolVersion = 1;

        Control (Engine&, GraphBuilder&, const juce::String& versionText);
        ~Control() override;

        void setLink (LocalLink* l)        { link = l; }
        void setMonitor (Monitor* m)       { monitor = m; }

        /** What this speaker's PipeWire allows for a document's `quantum`, and how to force it.
            Set once, before installLayer() and before the link starts. */
        struct QuantumSettings
        {
            int lo = 64, hi = 2048;          // the accepted powers of two (PipeWireNode::Clock::quantumRange)
            int defaultQuantum = 0;          // default.clock.quantum
            std::function<bool (int)> force; // PipeWireNode::setForceQuantum; empty: nothing is forced
        };

        void setQuantumSettings (QuantumSettings q)  { quantumSettings = std::move (q); }

        /** Message thread: sends the connected client, if it has been sent its hello, an unasked
            `status` with the hello's fields. The live mode's timer calls it when the rate or the running quantum
            changed. */
        void notifyStatus();

        /** Message thread: installs the layer speakerd boots from, and makes it the running
            graph of the tables. */
        void installLayer (LoadedLayer&&, double sampleRate, int maxBlock, int settleBlock);

        /** Message thread, often (the live mode's timer): builds and installs the next waiting
            apply, if any. Blocks while it builds and settles (about a second). */
        void service();

        /** "current (graph.json), revision 3", "passthrough", or "revision 8 (applied)". */
        juce::String describe() const;

        /** The monitor thread: the plain value of a node's monitor parameter (mon_...), if the
            running graph has it. */
        bool readMonitor (juce::uint32 node, const juce::String& id, double& value) const;

        // LocalLink::Handler
        void connected (juce::uint64) override;
        void received (juce::uint64, const juce::var&) override;
        void disconnected (juce::uint64) override;

    private:
        struct Entry
        {
            HostedParameter parameter;
            int slot = 0;
            juce::String nodeName;
        };

        using Key = std::pair<juce::uint32, juce::String>;

        struct Tables
        {
            std::map<Key, Entry> entries;
            std::map<juce::uint32, juce::String> nodes;          // uid -> name, for messages
            std::vector<juce::AudioProcessorParameter*> slots;
        };

        struct Waiting
        {
            juce::uint64 connection = 0;
            juce::int64 revision = 0;
            juce::var document;
            juce::uint64 replayFrom = 0;    // the params that arrived after this apply
        };

        /** The newest value of a parameter that arrived while revisions wait, and when. */
        struct Received
        {
            juce::var value;
            juce::uint64 arrival = 0;
        };

    public:
        /** The plugin nodes of graph `serial` (Engine::getGraphSerial), in the order of its
            TimingRecords: uid and name. False when `serial` is not the running graph's. */
        bool timedNodes (juce::uint32 serial, std::vector<std::pair<juce::uint32, juce::String>>& out) const;

    private:

        static Tables describeGraph (juce::AudioProcessorGraph&);

        /** Swaps `next` in, publishes its tables and replays the parameters that arrived after
            the apply that built it; returns the old graph, which nobody refers to any more. */
        RunningGraph swapIn (BuiltGraph&&, Tables&&, double sampleRate, int maxBlock, int settleBlock,
                             juce::int64 revision, const juce::String& layer, const juce::String& layerFile,
                             juce::uint64 replayFrom);

        void apply (const Waiting&);

        /** The document `revision` is in force: its quantum becomes clock.force-quantum, or 0
            (none, or a boot layer's that is outside the range). Logs it. Message thread. */
        void forceQuantumOf (juce::int64 revision, const juce::var& quantum);

        /** "quantum <v>: not a power of two from <lo> to <hi> (...)" when an apply's quantum is
            not one this speaker accepts; empty when it is, or when there is none. */
        juce::String refuseQuantum (const juce::var& quantum) const;

        void finishApply();
        void answer (juce::uint64 connection, const juce::var&);
        void handleParam (juce::uint64, const juce::var&);
        void handleApply (juce::uint64, const juce::var&);
        void handleSubscription (juce::uint64, const juce::var&, bool subscribe);

        /** The parameter, its value, as the audio thread will take it; or the reason it cannot.
            Under the lock. */
        juce::Result resolve (const Tables&, juce::uint32 node, const juce::String& id, const juce::var& value,
                              int& slot, float& normalised) const;

        juce::var helloObject (const juce::String& op) const;   // under the lock

        Engine& engine;
        GraphBuilder& builder;
        const juce::String version;
        LocalLink* link = nullptr;
        Monitor* monitor = nullptr;
        QuantumSettings quantumSettings;

        mutable std::mutex lock;
        Tables tables;
        ParameterInbox* inbox = nullptr;                 // the running graph's: the Engine owns it
        juce::int64 revision = 0;
        juce::String layer, layerFile;
        std::map<Key, juce::uint32> lastSeq;             // per parameter, since the client connected
        juce::uint64 greeted = 0;                        // the connection that was sent its hello, or 0
        std::deque<Waiting> waiting;
        bool building = false;
        // The parameters that arrived since the oldest waiting apply, newest value per parameter:
        // at most one entry per parameter, however many arrive while a revision builds.
        std::map<Key, Received> sinceApply;
        juce::uint64 arrivals = 0;                       // params counted since speakerd started

        // The running graph's plugin nodes, for the timing.
        juce::uint32 timedSerial = 0;
        std::vector<std::pair<juce::uint32, juce::String>> timedNames;

        JUCE_DECLARE_NON_COPYABLE (Control)
    };
}
