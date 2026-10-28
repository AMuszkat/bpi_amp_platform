#include "Monitor.h"

#include "Control.h"
#include "Engine.h"
#include "LocalLink.h"
#include "Log.h"
#include "TimedNode.h"

#include <cmath>
#include <cstdio>
#include <regex>
#include <vector>

namespace speakerd
{
    namespace
    {
        constexpr auto levelStream = "output/level";
        constexpr auto timingStream = "speakerd/timing";

        juce::String number (double value, const char* format)
        {
            char text[64];
            std::snprintf (text, sizeof text, format, value);
            return text;
        }

        double toDb (double x)
        {
            return x > 0.0 ? juce::jmax (Monitor::floorDb, 20.0 * std::log10 (x)) : Monitor::floorDb;
        }

        juce::String rateText (double hz)  { return number (hz, "%g"); }
    }

    Monitor::Monitor (Engine& e, Control& c, LocalLink& l) : engine (e), control (c), link (l) {}

    Monitor::~Monitor()
    {
        stop();
    }

    void Monitor::start()
    {
        {
            const std::lock_guard<std::mutex> guard (lock);
            stopping = false;
        }

        thread = std::thread ([this] { run(); });
    }

    void Monitor::stop()
    {
        {
            const std::lock_guard<std::mutex> guard (lock);
            stopping = true;
            streams.clear();
            updateLevelWanted();
        }

        changed.notify_all();

        if (thread.joinable())
            thread.join();
    }

    //==============================================================================
    juce::Result Monitor::subscribe (juce::uint64 conn, const juce::String& name, double rateHz)
    {
        Stream stream;

        // Exactly the names ampd accepts (docs/protocol.md).
        static const std::regex nodeStream ("^node/(1[6-9]|[2-9][0-9]|[1-9][0-9]{2,8})/(mon_[A-Za-z0-9_]{1,64})$");
        std::smatch match;
        const auto text = name.toStdString();

        if (name == levelStream)
        {
            stream.level = true;
        }
        else if (name == timingStream)
        {
            stream.timing = true;
        }
        else if (std::regex_match (text, match, nodeStream))
        {
            stream.node = (juce::uint32) std::stoul (match[1].str());
            stream.id = juce::String (match[2].str());
        }
        else
        {
            return juce::Result::fail ("no stream \"" + name + "\": a stream is output/level, speakerd/timing or node/<uid>/<mon_id>, "
                                       "with a plugin uid (16 or more) and a monitor's ID (mon_ and up to 64 of "
                                       "A-Z, a-z, 0-9, _)");
        }

        if (! (rateHz >= minRateHz && rateHz <= maxRateHz))
            return juce::Result::fail ("rate_hz must be from " + rateText (minRateHz) + " to " + rateText (maxRateHz));

        stream.rateHz = rateHz;
        stream.due = Clock::now();
        stream.cyclesAtLastValue = engine.getCycles();
        {
            const std::lock_guard<std::mutex> guard (lock);

            if (conn != connection)
            {
                endAll ({});
                connection = conn;
            }

            if (stream.level && streams.count (name) == 0)
                levelRestart = true;

            if (stream.timing && streams.count (name) == 0)
                timingSinceValue = {};

            if (const auto it = streams.find (name); it != streams.end())
                stream.due = it->second.due;     // a new rate from the next value on

            streams[name] = stream;
            updateLevelWanted();
        }

        log ("monitor: subscribed " + name + " at " + rateText (stream.rateHz) + " Hz");
        changed.notify_all();
        return juce::Result::ok();
    }

    void Monitor::unsubscribe (juce::uint64 conn, const juce::String& name)
    {
        {
            const std::lock_guard<std::mutex> guard (lock);

            if (conn != connection || streams.erase (name) == 0)
                return;

            updateLevelWanted();
        }

        log ("monitor: unsubscribed " + name);
        changed.notify_all();
    }

    void Monitor::clear (juce::uint64 conn)
    {
        {
            const std::lock_guard<std::mutex> guard (lock);

            if (conn != connection)
                return;

            endAll ("the client went");
            connection = 0;
        }

        changed.notify_all();
    }

    void Monitor::endAll (const juce::String& why)
    {
        if (! streams.empty() && why.isNotEmpty())
            log ("monitor: " + why + ": " + juce::String ((int) streams.size()) + " subscription(s) ended");

        streams.clear();
        updateLevelWanted();
    }

    void Monitor::updateLevelWanted()
    {
        engine.setLevelWanted (streams.count (levelStream) > 0);
    }

    //==============================================================================
    void Monitor::drainLevels()
    {
        if (levelRestart)
        {
            // What the ring holds is from before this subscription.
            engine.drainLevels ([] (const LevelRecord&) {});
            wooferPeak = tweeterPeak = 0.0f;
            wooferSquares = tweeterSquares = 0.0;
            levelSamples = 0;
            levelRestart = false;
            return;
        }

        engine.drainLevels ([this] (const LevelRecord& r)
        {
            wooferPeak = juce::jmax (wooferPeak, r.wooferPeak);
            tweeterPeak = juce::jmax (tweeterPeak, r.tweeterPeak);
            wooferSquares += r.wooferSquares;
            tweeterSquares += r.tweeterSquares;
            levelSamples += r.samples;
        });
    }

    juce::String Monitor::levelLine (const juce::String& t)
    {
        const auto n = (double) levelSamples;
        const auto v = "[" + number (toDb (wooferPeak), "%.2f") + "," + number (toDb (std::sqrt (wooferSquares / n)), "%.2f")
                     + "," + number (toDb (tweeterPeak), "%.2f") + "," + number (toDb (std::sqrt (tweeterSquares / n)), "%.2f") + "]";

        wooferPeak = tweeterPeak = 0.0f;
        wooferSquares = tweeterSquares = 0.0;
        levelSamples = 0;

        return "{\"op\":\"value\",\"stream\":\"output/level\",\"t\":" + t + ",\"v\":" + v + "}";
    }

    juce::var Monitor::timingValue (const TimingStats& stats) const
    {
        auto v = stats.toVar();

        if (auto* o = v.getDynamicObject())
        {
            o->setProperty ("cycles_total", cyclesTotal);
            o->setProperty ("late_total", lateTotal);
            o->setProperty ("jumps_total", jumpsTotal);
            o->setProperty ("dropped_total", (juce::int64) engine.getTimingDropped());
        }

        return v;
    }

    juce::var Monitor::timingStatus()
    {
        const std::lock_guard<std::mutex> guard (lock);
        TimingStats minute;

        for (const auto& second : lastMinute)
            minute.merge (second);

        auto v = timingValue (minute);

        if (auto* o = v.getDynamicObject())
            o->setProperty ("seconds", (int) lastMinute.size());

        return v;
    }

    void Monitor::drainTiming (Clock::time_point now)
    {
        const bool watched = streams.count (timingStream) > 0;

        engine.drainTiming ([&] (const TimingRecord& r)
        {
            // The names of the graph that ran, from Control, once per graph. Until Control has
            // them (the first cycles of a new graph), the nodes are left out.
            if (r.graphRan && (! namesKnown || r.graphSerial != namesSerial))
            {
                namesKnown = control.timedNodes (r.graphSerial, names);
                namesSerial = r.graphSerial;
            }

            const auto* known = r.graphRan && namesKnown && r.graphSerial == namesSerial ? &names : nullptr;
            timingThisSecond.add (r, known);

            if (watched)
                timingSinceValue.add (r, known);

            ++cyclesTotal;
            lateTotal += r.cycleNs > r.budgetNs ? 1 : 0;
            jumpsTotal += r.positionJump ? 1 : 0;
        });

        if (now - secondStarted < std::chrono::seconds (1))
            return;

        // A second is over: kept for the last minute, said when something went late.
        const auto& second = timingThisSecond;

        if (second.late > 0 || second.jumps > 0)
            log ("timing: in the last second " + juce::String (second.late) + " late cycle(s), the longest at "
                 + juce::String (100.0 * second.cycle.max, 1) + " % of its cycle; " + juce::String (second.jumps)
                 + " position jump(s)");

        lastMinute.push_back (second);

        while ((int) lastMinute.size() > timingLogSeconds)
            lastMinute.pop_front();

        timingThisSecond = {};
        secondStarted = now;

        if (now - lastSummary >= std::chrono::seconds (timingLogSeconds))
        {
            TimingStats minute;

            for (const auto& s : lastMinute)
                minute.merge (s);

            log ("timing: last " + juce::String ((int) lastMinute.size()) + " s: " + minute.describe());
            lastSummary = now;
        }
    }

    void Monitor::run()
    {
        // What one read of the monitored clock costs, here: a few tens of nanoseconds from the
        // vDSO; about a microsecond means a syscall on the audio thread (a Cortex-A73 whose kernel
        // works around ARM erratum 858921), the exception dsp/CLAUDE.md names.
        {
            constexpr int reads = 100000;
            const auto start = nowNs();
            juce::int64 sink = 0;

            for (int i = 0; i < reads; ++i)
                sink += nowNs() & 1;

            const auto perRead = (double) (nowNs() - start) / reads;
            log ("timing: a clock read costs " + juce::String (perRead, 1) + " ns" + (sink < 0 ? " " : ""));
        }

        std::unique_lock<std::mutex> guard (lock);
        secondStarted = lastSummary = Clock::now();

        while (! stopping)
        {
            const auto now = Clock::now();
            drainTiming (now);

            const bool level = streams.count (levelStream) > 0;

            if (level)
                drainLevels();

            const auto t = number (std::chrono::duration<double> (now.time_since_epoch()).count(), "%.6f");
            std::vector<juce::String> lines;
            auto next = now + std::chrono::duration_cast<Clock::duration> (std::chrono::duration<double> (timingDrainSeconds));

            for (auto& [name, stream] : streams)
            {
                if (stream.due <= now)
                {
                    const auto period = std::chrono::duration_cast<Clock::duration> (std::chrono::duration<double> (1.0 / stream.rateHz));
                    stream.due += period;

                    if (stream.due <= now)
                        stream.due = now + period;    // behind: no catching up

                    if (stream.level)
                    {
                        if (levelSamples > 0)
                            lines.push_back (levelLine (t));
                    }
                    else if (stream.timing)
                    {
                        // Nothing when no audio cycle ran in the period.
                        if (timingSinceValue.cycles > 0)
                            lines.push_back ("{\"op\":\"value\",\"stream\":\"speakerd/timing\",\"t\":" + t + ",\"v\":"
                                             + juce::JSON::toString (timingValue (timingSinceValue), true) + "}");

                        timingSinceValue = {};
                    }
                    else if (double value = 0.0; engine.getCycles() != stream.cyclesAtLastValue
                                                 && control.readMonitor (stream.node, stream.id, value))
                    {
                        // Only once an audio cycle ran since the last value: the plugin's monitor
                        // only moves while it processes, and the period otherwise measured nothing.
                        stream.cyclesAtLastValue = engine.getCycles();
                        lines.push_back ("{\"op\":\"value\",\"stream\":\"" + name + "\",\"t\":" + t + ",\"v\":"
                                         + number (value, "%.7g") + "}");
                    }
                }

                next = juce::jmin (next, stream.due);
            }

            if (level)
                next = juce::jmin (next, now + std::chrono::duration_cast<Clock::duration> (std::chrono::duration<double> (levelDrainSeconds)));

            for (const auto& line : lines)
                link.sendValue (connection, line);

            changed.wait_until (guard, next);
        }
    }
}
