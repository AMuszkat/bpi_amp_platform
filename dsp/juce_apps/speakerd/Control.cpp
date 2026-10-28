#include "Control.h"

#include "Log.h"
#include "Monitor.h"

#include <cmath>
#include <limits>

namespace speakerd
{
    namespace
    {
        juce::var object (std::initializer_list<std::pair<const char*, juce::var>> properties)
        {
            auto* o = new juce::DynamicObject();

            for (const auto& [name, value] : properties)
                o->setProperty (name, value);

            return juce::var (o);
        }

        bool isInteger (const juce::var& v)    { return v.isInt() || v.isInt64(); }

        /** "is a's seq newer than b's?" for a 32-bit counter that wraps. */
        bool isNewer (juce::uint32 a, juce::uint32 b)  { return (juce::int32) (a - b) > 0; }
    }

    Control::Control (Engine& e, GraphBuilder& b, const juce::String& versionText, TweeterRule rule)
        : engine (e), builder (b), version (versionText), tweeterRule (rule)
    {
    }

    Control::~Control() = default;

    //==============================================================================
    Control::Tables Control::describeGraph (juce::AudioProcessorGraph& graph)
    {
        Tables t;

        for (auto* node : graph.getNodes())
        {
            const auto uid = node->nodeID.uid;

            if (uid < GraphDocument::firstPluginNode)
                continue;

            auto* processor = &hostedProcessor (*node->getProcessor());
            const auto name = processor->getName();
            t.nodes[uid] = name;

            for (auto& d : describeParameters (*processor))
            {
                Entry entry;
                entry.slot = (int) t.slots.size();
                entry.nodeName = name;
                t.slots.push_back (d.parameter);
                const auto id = d.id;
                entry.parameter = std::move (d);
                t.entries[{ uid, id }] = std::move (entry);
            }
        }

        return t;
    }

    RunningGraph Control::swapIn (BuiltGraph&& built, Tables&& next, double sampleRate, int maxBlock, int settleBlock,
                                  juce::int64 newRevision, const juce::String& newLayer, const juce::String& newLayerFile,
                                  juce::uint64 replayFrom)
    {
        auto ownedInbox = std::make_unique<ParameterInbox> (next.slots);
        auto* newInbox = ownedInbox.get();

        // Settled beside the running graph, then swapped in under the fade. From here the audio
        // thread runs the new graph and reads the new inbox; values posted to the old one in
        // between are replayed below.
        std::vector<std::pair<juce::uint32, juce::String>> names;
        for (auto* node : built.timedNodes)
            names.emplace_back (node->getUid(), node->getInstance().getName());

        auto old = engine.install ({ std::move (built.graph), std::move (ownedInbox), std::move (built.timedNodes) },
                                   sampleRate, maxBlock, settleBlock);

        const std::lock_guard<std::mutex> guard (lock);
        timedSerial = engine.getGraphSerial();
        timedNames = std::move (names);
        tables = std::move (next);
        inbox = newInbox;
        revision = newRevision;
        layer = newLayer;
        layerFile = newLayerFile;

        // The parameters that arrived after the apply that built this graph are newer than its
        // document: they went to the old graph, and go to this one too, where it has them.
        for (const auto& [key, r] : sinceApply)
        {
            if (r.arrival < replayFrom)
                continue;

            int slot = 0;
            float normalised = 0.0f;

            if (resolve (tables, key.first, key.second, r.value, slot, normalised).wasOk())
                inbox->post (slot, normalised);
        }

        return old;
    }

    void Control::installLayer (LoadedLayer&& loaded, double sampleRate, int maxBlock, int settleBlock)
    {
        auto t = describeGraph (*loaded.built.graph);
        const auto rev = juce::jmax ((juce::int64) 0, loaded.revision);
        auto old = swapIn (std::move (loaded.built), std::move (t), sampleRate, maxBlock, settleBlock,
                           rev, loaded.name, loaded.fileName, std::numeric_limits<juce::uint64>::max());
        old = {};
    }

    juce::String Control::describe() const
    {
        const std::lock_guard<std::mutex> guard (lock);

        if (layerFile.isNotEmpty())
            return layer + " (" + layerFile + "), revision " + juce::String (revision);

        if (layer == "passthrough")
            return layer;

        return "revision " + juce::String (revision) + " (applied)";
    }

    juce::var Control::helloObject (const juce::String& op) const
    {
        return object ({ { "op", op },
                         { "role", "speakerd" },
                         { "version", version },
                         { "revision", revision },
                         { "layer", layer },
                         { "protocol", protocolVersion } });
    }

    bool Control::timedNodes (juce::uint32 serial, std::vector<std::pair<juce::uint32, juce::String>>& out) const
    {
        const std::lock_guard<std::mutex> guard (lock);

        if (serial != timedSerial)
            return false;

        out = timedNames;
        return true;
    }

    bool Control::readMonitor (juce::uint32 node, const juce::String& id, double& value) const
    {
        if (! isMonitorId (id))
            return false;

        const std::lock_guard<std::mutex> guard (lock);
        const auto it = tables.entries.find ({ node, id });

        if (it == tables.entries.end())
            return false;

        // JUCE's LV2 hosting keeps the value the plugin last reported (a patch:Set on its atom
        // output) in an atomic: reading it takes no lock.
        const auto& p = it->second.parameter;
        value = p.plainValue (p.parameter->getValue());
        return std::isfinite (value);
    }

    //==============================================================================
    void Control::answer (juce::uint64 connection, const juce::var& message)
    {
        if (link != nullptr)
            link->send (connection, juce::JSON::toString (message, true));
    }

    void Control::connected (juce::uint64 connection)
    {
        juce::var hello;
        {
            const std::lock_guard<std::mutex> guard (lock);

            // A new client is a new ampd, whose hub may count seq from anywhere: what the last
            // one sent says nothing about it.
            lastSeq.clear();
            hello = helloObject ("hello");
        }

        answer (connection, hello);
    }

    void Control::disconnected (juce::uint64 connection)
    {
        if (monitor != nullptr)
            monitor->clear (connection);
    }

    void Control::received (juce::uint64 connection, const juce::var& message)
    {
        const auto op = message["op"].toString();

        if (op == "param")
            handleParam (connection, message);
        else if (op == "apply")
            handleApply (connection, message);
        else if (op == "status")
        {
            juce::var status;
            {
                const std::lock_guard<std::mutex> guard (lock);
                status = helloObject ("status");
            }

            // Not under this lock: the monitor thread takes its own, then this one.
            if (monitor != nullptr)
                if (auto* o = status.getDynamicObject())
                    o->setProperty ("timing", monitor->timingStatus());

            answer (connection, status);
        }
        else if (op == "subscribe")
            handleSubscription (connection, message, true);
        else if (op == "unsubscribe")
            handleSubscription (connection, message, false);

        // Anything else (gesture, hello, an op of a newer protocol) is ignored.
    }

    //==============================================================================
    juce::Result Control::resolve (const Tables& t, juce::uint32 node, const juce::String& id, const juce::var& value,
                                   int& slot, float& normalised) const
    {
        const auto nodeIt = t.nodes.find (node);

        if (nodeIt == t.nodes.end())
            return juce::Result::fail ("no node " + juce::String (node) + " in revision " + juce::String (revision));

        const auto where = "node " + juce::String (node) + " (" + nodeIt->second + ")";
        const auto it = t.entries.find ({ node, id });

        if (it == t.entries.end())
            return juce::Result::fail (where + " has no parameter \"" + id + "\"");

        juce::StringArray warnings;   // a value outside the range is clamped, as in a document

        if (auto r = toNormalised (it->second.parameter, value, normalised, warnings, where + ": " + id); r.failed())
            return r;

        slot = it->second.slot;
        return juce::Result::ok();
    }

    void Control::handleParam (juce::uint64 connection, const juce::var& message)
    {
        const auto& nodeVar = message["node"];
        const auto& idVar = message["id"];
        const auto& value = message["value"];
        const auto& seqVar = message["seq"];

        const auto refuse = [&] (const juce::String& reason)
        {
            answer (connection, object ({ { "op", "error" }, { "about", "param" }, { "node", nodeVar }, { "id", idVar },
                                          { "reason", reason } }));
        };

        if (! isInteger (nodeVar) || (juce::int64) nodeVar < 1 || (juce::int64) nodeVar > 0xffffffffLL)
            return refuse ("node must be a node uid (an integer)");

        if (! idVar.isString() || idVar.toString().isEmpty())
            return refuse ("id must be a parameter ID (a string)");

        if (value.isVoid() || value.isUndefined())
            return refuse ("value is missing");

        juce::int64 seq = 0;

        if (! seqVar.isVoid())
        {
            if (! isInteger (seqVar) || (juce::int64) seqVar < 0 || (juce::int64) seqVar > 0xffffffffLL)
                return refuse ("seq must be an integer from 0 to 4294967295");

            seq = (juce::int64) seqVar;
        }

        const auto node = (juce::uint32) (juce::int64) nodeVar;
        const auto id = idVar.toString();

        if (isMonitorId (id))
            return refuse (id + " is a monitor (mon_): it is read-only");

        juce::String error;
        {
            const std::lock_guard<std::mutex> guard (lock);
            int slot = 0;
            float normalised = 0.0f;

            if (auto r = resolve (tables, node, id, value, slot, normalised); r.failed())
            {
                error = r.getErrorMessage();
            }
            else
            {
                // seq 0 always applies (a speaker's own ampd); any other only when it is newer
                // than the last one applied to this parameter (UDP may reorder).
                if (seq != 0)
                {
                    const Key key { node, id };
                    const auto it = lastSeq.find (key);

                    if (it != lastSeq.end() && ! isNewer ((juce::uint32) seq, it->second))
                        return;

                    lastSeq[key] = (juce::uint32) seq;
                }

                if (inbox != nullptr)
                    inbox->post (slot, normalised);

                if (building || ! waiting.empty())
                    sinceApply[{ node, id }] = { value, arrivals++ };
            }
        }

        if (error.isNotEmpty())
            refuse (error);
    }

    void Control::handleApply (juce::uint64 connection, const juce::var& message)
    {
        const auto& revisionVar = message["revision"];
        const auto& document = message["document"];

        const auto refuse = [&] (const juce::String& reason)
        {
            log ((isInteger (revisionVar) ? "apply: revision " + revisionVar.toString() + ": " : juce::String ("apply: "))
                 + "FAILED: " + reason);

            if (isInteger (revisionVar))
                answer (connection, object ({ { "op", "failed" }, { "revision", revisionVar }, { "reason", reason } }));
            else
                answer (connection, object ({ { "op", "failed" }, { "reason", reason } }));
        };

        if (! isInteger (revisionVar) || (juce::int64) revisionVar < 0)
            return refuse ("revision must be an integer, 0 or more");

        if (! document.isObject())
            return refuse ("document must be a graph document (an object)");

        const auto rev = (juce::int64) revisionVar;
        size_t ahead = 0;
        {
            const std::lock_guard<std::mutex> guard (lock);
            ahead = waiting.size();

            if ((int) ahead < maxWaitingApplies)
                waiting.push_back ({ connection, rev, document, arrivals });
        }

        if ((int) ahead >= maxWaitingApplies)
            return refuse (juce::String ((int) ahead) + " revisions are waiting already");

        if (ahead > 0)
            log ("apply: revision " + juce::String (rev) + ": waiting behind " + juce::String ((int) ahead) + " other revision(s)");
    }

    void Control::handleSubscription (juce::uint64 connection, const juce::var& message, bool subscribe)
    {
        const auto& streamVar = message["stream"];

        const auto refuse = [&] (const juce::String& reason)
        {
            answer (connection, object ({ { "op", "error" }, { "about", subscribe ? "subscribe" : "unsubscribe" },
                                          { "stream", streamVar }, { "reason", reason } }));
        };

        if (! streamVar.isString() || streamVar.toString().isEmpty())
            return refuse ("stream must be a stream name: output/level or node/<uid>/<mon_id>");

        if (monitor == nullptr)
            return;

        if (! subscribe)
            return monitor->unsubscribe (connection, streamVar.toString());

        double rate = Monitor::defaultRateHz;
        const auto& rateVar = message["rate_hz"];

        if (! rateVar.isVoid())
        {
            if (! (rateVar.isInt() || rateVar.isInt64() || rateVar.isDouble()) || ! std::isfinite ((double) rateVar)
                || (double) rateVar < Monitor::minRateHz || (double) rateVar > Monitor::maxRateHz)
                return refuse ("rate_hz must be a number from " + juce::String (Monitor::minRateHz) + " to "
                               + juce::String (Monitor::maxRateHz));

            rate = (double) rateVar;
        }

        if (auto r = monitor->subscribe (connection, streamVar.toString(), rate); r.failed())
            refuse (r.getErrorMessage());
    }

    //==============================================================================
    void Control::service()
    {
        Waiting job;
        {
            const std::lock_guard<std::mutex> guard (lock);

            if (building || waiting.empty())
                return;

            job = waiting.front();
            building = true;
        }

        apply (job);
        finishApply();
    }

    void Control::finishApply()
    {
        const std::lock_guard<std::mutex> guard (lock);
        building = false;
        waiting.pop_front();

        // Keep only the parameters a waiting apply will replay.
        if (waiting.empty())
        {
            sinceApply.clear();
        }
        else
        {
            const auto keepFrom = waiting.front().replayFrom;

            for (auto it = sinceApply.begin(); it != sinceApply.end();)
                it = it->second.arrival < keepFrom ? sinceApply.erase (it) : std::next (it);
        }
    }

    void Control::apply (const Waiting& job)
    {
        const auto prefix = "apply: revision " + juce::String (job.revision) + ": ";
        const auto failed = [&] (const juce::String& reason)
        {
            log (prefix + "FAILED: " + reason + "; " + describe() + " plays on");
            answer (job.connection, object ({ { "op", "failed" }, { "revision", job.revision }, { "reason", reason } }));
        };

        log (prefix + "building");

        // The apply's revision is the revision. The document may leave it out (it is filled in),
        // or say the same; a document that says another one is refused.
        auto documentJson = job.document.clone();

        if (auto* o = documentJson.getDynamicObject(); o != nullptr)
        {
            const auto& own = o->getProperty ("revision");

            if (own.isVoid())
                o->setProperty ("revision", job.revision);
            else if (! isInteger (own) || (juce::int64) own != job.revision)
                return failed ("the document says revision " + own.toString() + ", the apply " + juce::String (job.revision));
        }

        GraphDocument doc;

        if (auto r = GraphDocument::parse (documentJson, doc); r.failed())
            return failed (r.getErrorMessage());

        // A tweeter that plays only ever gets a crossover's high band.
        juce::StringArray ruleWarnings;
        if (auto r = tweeterRule.check (doc, ruleWarnings); r.failed())
            return failed (r.getErrorMessage());

        for (const auto& warning : ruleWarnings)
            log (prefix + "warning: " + warning);

        const auto rate = engine.getSampleRate();
        const auto maxBlock = engine.getMaxBlock();
        const auto settleBlock = engine.getPrimedBlockSize();

        if (rate <= 0.0 || maxBlock <= 0)
            return failed ("speakerd has no running graph to replace");

        BuiltGraph built;

        try
        {
            if (auto r = builder.build (doc, rate, maxBlock, built); r.failed())
                return failed (r.getErrorMessage());
        }
        catch (const std::exception& e)
        {
            return failed (juce::String ("building the graph threw: ") + e.what());
        }

        for (const auto& warning : built.warnings)
            log (prefix + "warning: " + warning);

        const auto pluginNodes = built.pluginNodes;
        auto t = describeGraph (*built.graph);
        auto old = swapIn (std::move (built), std::move (t), rate, maxBlock, settleBlock, doc.revision, "current", {},
                           job.replayFrom);
        const auto cyclesBefore = engine.getCycles();

        // "applied" once the new graph plays: the audio thread has run a cycle of it (unless no
        // cycle runs at all: nothing is linked, and it plays as soon as something is).
        for (const auto start = juce::Time::getMillisecondCounter();
             engine.getCycles() < cyclesBefore + 2 && juce::Time::getMillisecondCounter() - start < 200;)
            juce::Thread::sleep (1);

        log (prefix + "applied, " + juce::String (pluginNodes) + " plugin node(s)");
        answer (job.connection, object ({ { "op", "applied" }, { "revision", job.revision } }));

        old = {};   // the old graph, which nothing refers to any more
    }
}
