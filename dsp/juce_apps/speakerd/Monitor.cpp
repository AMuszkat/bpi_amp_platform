#include "Monitor.h"

#include "Control.h"
#include "Engine.h"
#include "LocalLink.h"
#include "Log.h"

#include <cmath>
#include <cstdio>
#include <vector>

namespace speakerd
{
    namespace
    {
        constexpr auto levelStream = "output/level";

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

        if (name == levelStream)
        {
            stream.level = true;
        }
        else
        {
            const auto parts = juce::StringArray::fromTokens (name, "/", {});
            const auto validId = [] (const juce::String& id)
            {
                return id.startsWith ("mon_") && id.length() > 4
                    && id.containsOnly ("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.");
            };

            if (parts.size() != 3 || parts[0] != "node" || parts[1].isEmpty() || ! parts[1].containsOnly ("0123456789")
                || parts[1].length() > 10 || parts[1].getLargeIntValue() > 0xffffffffLL || ! validId (parts[2]))
                return juce::Result::fail ("no stream \"" + name + "\": a stream is output/level or node/<uid>/<mon_id> "
                                           "(a monitor's ID starts with mon_)");

            stream.node = (juce::uint32) parts[1].getLargeIntValue();
            stream.id = parts[2];
        }

        stream.rateHz = juce::jmin (maxRateHz, rateHz);
        stream.due = Clock::now();
        {
            const std::lock_guard<std::mutex> guard (lock);

            if (conn != connection)
            {
                endAll ({});
                connection = conn;
            }

            if (stream.level && streams.count (name) == 0)
                levelRestart = true;

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

    void Monitor::run()
    {
        std::unique_lock<std::mutex> guard (lock);

        while (! stopping)
        {
            if (streams.empty())
            {
                changed.wait (guard);
                continue;
            }

            const auto now = Clock::now();
            const bool level = streams.count (levelStream) > 0;

            if (level)
                drainLevels();

            const auto t = number (std::chrono::duration<double> (now.time_since_epoch()).count(), "%.6f");
            std::vector<juce::String> lines;
            auto next = now + std::chrono::hours (1);

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
                    else if (double value = 0.0; control.readMonitor (stream.node, stream.id, value))
                    {
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
