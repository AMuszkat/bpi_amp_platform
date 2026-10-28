#include "ScriptRunner.h"

#include <iostream>

namespace bpi
{
    namespace
    {
        using NodeID = juce::AudioProcessorGraph::NodeID;
        constexpr double defaultTimeoutSeconds = 5.0;
        constexpr double proposalTimeoutSeconds = 20.0;

        juce::var parseValue (const juce::String& word)
        {
            if (word == "true")  return true;
            if (word == "false") return false;

            if (word.containsOnly ("0123456789+-.eE") && word.containsAnyOf ("0123456789"))
                return word.getDoubleValue();

            return word.unquoted();
        }

        bool sameValue (const juce::var& a, const juce::var& b)
        {
            const auto isNumber = [] (const juce::var& v) { return v.isInt() || v.isInt64() || v.isDouble() || v.isBool(); };

            if (isNumber (a) && isNumber (b))
            {
                const auto x = (double) a, y = (double) b;
                return std::abs (x - y) <= 1.0e-3 * juce::jmax (1.0, std::abs (x), std::abs (y));
            }

            return a.toString() == b.toString();
        }

        juce::var object (std::initializer_list<std::pair<const char*, juce::var>> properties)
        {
            auto* o = new juce::DynamicObject();
            for (const auto& [k, v] : properties)
                o->setProperty (k, v);
            return juce::var (o);
        }
    }

    ScriptRunner::ScriptRunner (Bridge& b, PluginGraph& g, juce::StringArray l, Hooks h, std::function<void (int)> done)
        : bridge (b), graph (g), lines (std::move (l)), hooks (std::move (h)), onDone (std::move (done))
    {
        int number = 0;

        for (const auto& raw : lines)
        {
            const auto line = raw.upToFirstOccurrenceOf ("#", false, false).trim();

            if (line.isEmpty())
                continue;

            Step s;
            s.line = line;
            s.words = juce::StringArray::fromTokens (line, " \t", "\"");
            s.words.removeEmptyStrings();
            s.number = ++number;
            steps.push_back (s);
        }
    }

    ScriptRunner::~ScriptRunner()   { stopTimer(); }

    juce::StringArray ScriptRunner::readScript (const juce::File& file, juce::String& error)
    {
        if (! file.existsAsFile())
        {
            error = "no script " + file.getFullPathName();
            return {};
        }

        juce::StringArray result;
        result.addLines (file.loadFileAsString());
        return result;
    }

    void ScriptRunner::start()
    {
        startTimer (10);
    }

    void ScriptRunner::print (const juce::var& v)
    {
        std::cout << document::toJson (v) << std::endl;
    }

    juce::uint32 ScriptRunner::uidArg (const juce::String& word) const
    {
        if (word == "$new")
            return lastAdded;

        return (juce::uint32) word.getLargeIntValue();
    }

    bool ScriptRunner::pinArg (const juce::String& word, juce::uint32& node, int& channel) const
    {
        if (! word.containsChar (':'))
            return false;

        node = uidArg (word.upToFirstOccurrenceOf (":", false, false));
        channel = word.fromFirstOccurrenceOf (":", false, false).getIntValue();
        return node != 0;
    }

    double ScriptRunner::timeoutArg (int index, double defaultSeconds) const
    {
        const auto& words = steps[current].words;
        return index < words.size() ? words[index].getDoubleValue() : defaultSeconds;
    }

    void ScriptRunner::timerCallback()
    {
        if (current >= steps.size())
        {
            stopTimer();
            print (object ({ { "result", failures == 0 ? "pass" : "fail" }, { "steps", (int) steps.size() }, { "failed", failures } }));

            if (onDone != nullptr)
                onDone (failures == 0 ? 0 : 1);
            return;
        }

        auto& step = steps[current];
        const auto first = ! started;
        const auto now = juce::Time::getMillisecondCounterHiRes();

        if (lastTickMs > 0.0)
            maxTickGapMs = juce::jmax (maxTickGapMs, now - lastTickMs);

        lastTickMs = now;

        if (first)
        {
            started = true;
            maxTickGapMs = 0.0;
            startedMs = juce::Time::getMillisecondCounterHiRes();
            deadlineMs = startedMs + defaultTimeoutSeconds * 1000.0;
            waitDetail = juce::var();
        }

        if (run (step, first))
            return;   // finishStep() was called

        if (juce::Time::getMillisecondCounterHiRes() > deadlineMs)
            finishStep (false, waitDetail, "timed out");
    }

    void ScriptRunner::finishStep (bool ok, const juce::var& extra, const juce::String& error)
    {
        const auto& step = steps[current];
        auto* o = new juce::DynamicObject();
        o->setProperty ("step", step.number);
        o->setProperty ("cmd", step.words[0]);
        o->setProperty ("line", step.line);
        o->setProperty ("ok", ok);
        o->setProperty ("ms", juce::roundToInt (juce::Time::getMillisecondCounterHiRes() - startedMs));
        o->setProperty ("max_tick_ms", juce::roundToInt (maxTickGapMs));

        if (error.isNotEmpty())
            o->setProperty ("error", error);

        if (auto* e = extra.getDynamicObject())
            for (const auto& p : e->getProperties())
                o->setProperty (p.name, p.value);

        print (juce::var (o));

        started = false;
        ++current;

        if (! ok)
        {
            ++failures;
            current = steps.size();   // stop at the first failure
        }
    }

    bool ScriptRunner::run (Step& step, bool first)
    {
        const auto& w = step.words;
        const auto cmd = w[0];
        const auto arg = [&w] (int i) { return w[i]; };
        const auto need = [&] (int n)
        {
            if (w.size() > n)
                return true;

            finishStep (false, {}, "needs " + juce::String (n) + " argument(s)");
            return false;
        };

        const auto setTimeout = [&] (int index, double seconds)
        {
            if (first)
                deadlineMs = startedMs + timeoutArg (index, seconds) * 1000.0;
        };

        const auto controlParam = [&] (juce::uint32 uid, const juce::String& id) -> juce::AudioProcessorParameter*
        {
            if (auto* n = graph.graph.getNodeForId (NodeID { uid }))
                if (n->properties["kind"].toString() == nodeKind::control)
                    return params::find (*n->getProcessor(), id);

            return nullptr;
        };

        const auto edit = [&] (bool done, const juce::String& error, const juce::var& extra = {})
        {
            finishStep (done, extra, done ? juce::String() : error);
            return true;
        };

        //==============================================================================
        if (cmd == "wait-synced" || cmd == "connect")
        {
            setTimeout (1, 10.0);
            waitDetail = object ({ { "status", bridge.getStatus() } });

            if (bridge.isSynced())
                return edit (true, {}, object ({ { "revision", bridge.getRevision() }, { "hub", bridge.getHubName() } }));

            return false;
        }

        if (cmd == "state" || cmd == "print-state")
        {
            finishStep (true, object ({ { "state", bridge.describe() } }));
            return true;
        }

        if (cmd == "print")
        {
            finishStep (true, object ({ { "text", step.line.fromFirstOccurrenceOf (" ", false, false) } }));
            return true;
        }

        if (cmd == "sleep")
        {
            if (! need (1)) return true;
            if (juce::Time::getMillisecondCounterHiRes() - startedMs >= arg (1).getDoubleValue())
                return edit (true, {});
            deadlineMs = startedMs + arg (1).getDoubleValue() + 60000.0;
            return false;
        }

        if (cmd == "set")
        {
            if (! need (3)) return true;
            auto* p = controlParam (uidArg (arg (1)), arg (2));

            if (p == nullptr)
                return edit (false, "no parameter " + arg (2) + " on a control node " + arg (1));

            if (! bridge.isSynced())
                return edit (false, "not synced with the hub");

            const auto normalised = params::normalisedFor (*p, parseValue (arg (3)));

            if (! normalised)
                return edit (false, arg (3) + " does not fit " + arg (2));

            p->setValueNotifyingHost (*normalised);
            return edit (true, {}, object ({ { "value", params::toJson (*p, params::plainValue (*p)) } }));
        }

        if (cmd == "drag")
        {
            // drag <uid> <id> <from> <to> [steps] [ms]: a gesture, the way a slider makes one.
            if (! need (4)) return true;
            auto* p = controlParam (uidArg (arg (1)), arg (2));

            if (p == nullptr)
                return edit (false, "no parameter " + arg (2) + " on a control node " + arg (1));

            const auto numSteps = juce::jmax (1, w.size() > 5 ? arg (5).getIntValue() : 20);
            const auto durationMs = w.size() > 6 ? arg (6).getDoubleValue() : 500.0;
            const auto from = arg (3).getDoubleValue(), to = arg (4).getDoubleValue();
            const auto elapsed = juce::Time::getMillisecondCounterHiRes() - startedMs;

            if (first)
            {
                deadlineMs = startedMs + durationMs + 10000.0;
                p->beginChangeGesture();
                waitDetail = 0;
            }

            const auto due = juce::jmin (numSteps, (int) (elapsed / (durationMs / numSteps)) + 1);
            int sent = (int) waitDetail;

            while (sent < due)
            {
                ++sent;
                const auto value = from + (to - from) * sent / numSteps;
                if (const auto normalised = params::normalisedFor (*p, value))
                    p->setValueNotifyingHost (*normalised);
            }

            waitDetail = sent;

            if (sent >= numSteps)
            {
                p->endChangeGesture();
                return edit (true, {}, object ({ { "values", numSteps }, { "value", params::toJson (*p, params::plainValue (*p)) } }));
            }

            return false;
        }

        if (cmd == "sweep")
        {
            // sweep <uid> <seconds>: every parameter of a node, every tick, without a gesture
            // (a preset morph): as much as the control link carries, 50 a second per parameter.
            if (! need (2)) return true;
            auto* n = graph.graph.getNodeForId (NodeID { uidArg (arg (1)) });

            if (n == nullptr || n->properties["kind"].toString() != nodeKind::control)
                return edit (false, "no control node " + arg (1));

            const auto durationMs = arg (2).getDoubleValue() * 1000.0;

            if (first)
            {
                deadlineMs = startedMs + durationMs + 10000.0;
                sweepTicks = sweepValues = 0;
            }

            ++sweepTicks;

            for (auto* p : n->getProcessor()->getParameters())
            {
                if (params::isMonitor (params::idOf (*p)))
                    continue;

                p->setValueNotifyingHost ((sweepTicks % 2) != 0 ? 0.25f : 0.75f);
                ++sweepValues;
            }

            if (juce::Time::getMillisecondCounterHiRes() - startedMs < durationMs)
                return false;

            return edit (true, {}, object ({ { "ticks", sweepTicks }, { "values", sweepValues } }));
        }

        if (cmd == "wait-echo")
        {
            if (! need (3)) return true;
            setTimeout (4, defaultTimeoutSeconds);
            const auto want = parseValue (arg (3));

            if (const auto echo = bridge.getLastEcho (uidArg (arg (1)), arg (2)))
            {
                waitDetail = object ({ { "last", echo->value }, { "seq", (juce::int64) echo->seq }, { "origin", echo->origin } });

                if (sameValue (echo->value, want))
                    return edit (true, {}, object ({ { "value", echo->value }, { "seq", (juce::int64) echo->seq },
                                                     { "origin", echo->origin }, { "link", echo->link }, { "echoes", echo->count } }));
            }

            return false;
        }

        if (cmd == "wait-param" || cmd == "wait-monitor")
        {
            if (! need (3)) return true;
            setTimeout (4, defaultTimeoutSeconds);
            auto* p = controlParam (uidArg (arg (1)), arg (2));

            if (p == nullptr)
                return edit (false, "no parameter " + arg (2) + " on a control node " + arg (1));

            const auto value = params::toJson (*p, params::plainValue (*p));
            waitDetail = object ({ { "value", value } });

            if (sameValue (value, parseValue (arg (3))))
                return edit (true, {}, object ({ { "value", value } }));

            return false;
        }

        //==============================================================================
        if (cmd == "add")
        {
            if (! need (1)) return true;
            auto* type = ControlPlugins::forName (arg (1));

            if (type == nullptr)
                return edit (false, "no control build " + arg (1) + " in this controller");

            outcomesBefore = bridge.getProposalOutcomes();
            const juce::Point<double> pos { w.size() > 3 ? arg (2).getDoubleValue() : 0.5,
                                            w.size() > 3 ? arg (3).getDoubleValue() : 0.5 };
            lastAdded = graph.addPlugin (*type, pos).uid;
            return edit (lastAdded != 0, "could not add " + arg (1), object ({ { "uid", (int) lastAdded } }));
        }

        if (cmd == "remove")
        {
            if (! need (1)) return true;
            const auto uid = uidArg (arg (1));

            if (uid < document::firstPluginNode || graph.graph.getNodeForId (NodeID { uid }) == nullptr)
                return edit (false, "no plugin node " + arg (1));

            outcomesBefore = bridge.getProposalOutcomes();
            graph.graph.removeNode (NodeID { uid });
            return edit (true, {});
        }

        if (cmd == "connect-pins" || cmd == "disconnect")
        {
            if (! need (2)) return true;
            juce::uint32 src = 0, dst = 0;
            int srcChannel = 0, dstChannel = 0;

            if (! pinArg (arg (1), src, srcChannel) || ! pinArg (arg (2), dst, dstChannel))
                return edit (false, "pins are <node>:<channel>");

            const juce::AudioProcessorGraph::Connection c { { NodeID { src }, srcChannel }, { NodeID { dst }, dstChannel } };
            outcomesBefore = bridge.getProposalOutcomes();
            const auto done = cmd == "disconnect" ? graph.graph.removeConnection (c) : graph.graph.addConnection (c);
            return edit (done, cmd == "disconnect" ? "no such connection" : "the graph refuses that connection");
        }

        if (cmd == "bypass")
        {
            if (! need (2)) return true;
            outcomesBefore = bridge.getProposalOutcomes();
            graph.setBypassed (NodeID { uidArg (arg (1)) }, arg (2) == "on");
            return edit (true, {});
        }

        if (cmd == "expect-applied" || cmd == "expect-rejected")
        {
            const auto wantApplied = cmd == "expect-applied";
            setTimeout (wantApplied ? 1 : 99, proposalTimeoutSeconds);
            waitDetail = object ({ { "status", bridge.getStatus() } });

            if (bridge.getProposalOutcomes() <= outcomesBefore)
                return false;

            const auto outcome = bridge.getLastOutcome();
            auto ok = outcome.startsWith (wantApplied ? "applied" : "rejected");

            if (! wantApplied && w.size() > 1)
                ok = ok && outcome.contains (step.line.fromFirstOccurrenceOf (" ", false, false).trim().unquoted());

            return edit (ok, "the hub's answer was: " + outcome,
                         object ({ { "outcome", outcome }, { "revision", bridge.getRevision() }, { "status", bridge.getStatus() } }));
        }

        if (cmd == "expect-revision")
        {
            if (! need (1)) return true;
            setTimeout (2, defaultTimeoutSeconds);
            waitDetail = object ({ { "revision", bridge.getRevision() } });
            return bridge.getRevision() == arg (1).getLargeIntValue()
                       ? edit (true, {}, object ({ { "revision", bridge.getRevision() } }))
                       : false;
        }

        if (cmd == "expect-node" || cmd == "expect-no-node")
        {
            if (! need (1)) return true;
            setTimeout (2, defaultTimeoutSeconds);
            const auto present = graph.graph.getNodeForId (NodeID { uidArg (arg (1)) }) != nullptr;
            return present == (cmd == "expect-node") ? edit (true, {}) : false;
        }

        if (cmd == "expect-connection" || cmd == "expect-no-connection")
        {
            if (! need (2)) return true;
            setTimeout (3, defaultTimeoutSeconds);
            juce::uint32 src = 0, dst = 0;
            int srcChannel = 0, dstChannel = 0;

            if (! pinArg (arg (1), src, srcChannel) || ! pinArg (arg (2), dst, dstChannel))
                return edit (false, "pins are <node>:<channel>");

            const auto present = graph.graph.isConnected ({ { NodeID { src }, srcChannel }, { NodeID { dst }, dstChannel } });
            return present == (cmd == "expect-connection") ? edit (true, {}) : false;
        }

        //==============================================================================
        if (cmd == "open" || cmd == "close")
        {
            if (! need (1)) return true;
            const auto uid = uidArg (arg (1));
            const auto open = cmd == "open";
            std::optional<juce::Point<int>> position;

            if (open && w.size() > 3)
                position = juce::Point<int> (arg (2).getIntValue(), arg (3).getIntValue());

            const auto window = hooks.showWindow != nullptr && hooks.showWindow (uid, open, position);

            if (! window)
                bridge.nodeMonitorsShown (uid, open);   // no GUI: what the window would do

            return edit (true, {}, object ({ { "window", window } }));
        }

        if (cmd == "main-window")
        {
            if (! need (4)) return true;
            const juce::Rectangle<int> r (arg (1).getIntValue(), arg (2).getIntValue(), arg (3).getIntValue(), arg (4).getIntValue());
            return edit (hooks.placeMainWindow != nullptr && hooks.placeMainWindow (r), "no GUI (run with --gui)");
        }

        if (cmd == "monitor-speaker")
        {
            if (! need (2)) return true;
            bridge.setMonitorSpeaker (uidArg (arg (1)), arg (2));
            return edit (true, {});
        }

        if (cmd == "subscribe" || cmd == "unsubscribe")
        {
            if (! need (2)) return true;

            if (cmd == "subscribe")
                bridge.subscribe (arg (1), arg (2));
            else
                bridge.unsubscribe (arg (1), arg (2));

            return edit (true, {}, object ({ { "subscriptions", bridge.getSubscriptions (arg (1)).joinIntoString (" ") } }));
        }

        if (cmd == "output-levels")
        {
            if (! need (1)) return true;
            bridge.wantOutputLevels (arg (1) == "on");
            return edit (true, {});
        }

        if (cmd == "wait-value")
        {
            if (! need (2)) return true;
            setTimeout (3, defaultTimeoutSeconds);

            if (first)
            {
                const auto v = bridge.getMonitorValue (arg (1), arg (2));
                waitDetail = v ? v->count : 0;   // a value newer than this command
            }

            if (const auto v = bridge.getMonitorValue (arg (1), arg (2)); v && v->count > (int) waitDetail)
                return edit (true, {}, object ({ { "v", v->v }, { "t", v->t }, { "values", v->count } }));

            return false;
        }

        if (cmd == "expect-speaker")
        {
            if (! need (2)) return true;
            setTimeout (3, defaultTimeoutSeconds);

            for (const auto& s : bridge.getSpeakers())
                if (s.name == arg (1) && s.online == (arg (2) == "online"))
                    return edit (true, {}, object ({ { "role", s.role }, { "layer", s.layer } }));

            return false;
        }

        if (cmd == "screenshot")
        {
            if (! need (1)) return true;
            const juce::File file (juce::File::getCurrentWorkingDirectory().getChildFile (arg (1)));
            const auto done = hooks.screenshot != nullptr && hooks.screenshot (file);
            return edit (done, "no GUI (run with --gui)", object ({ { "file", file.getFullPathName() } }));
        }

        finishStep (false, {}, "unknown command");
        return true;
    }
}
