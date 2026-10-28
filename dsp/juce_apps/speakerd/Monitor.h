#pragma once

#include <juce_core/juce_core.h>

#include <chrono>
#include <condition_variable>
#include <map>
#include <mutex>
#include <thread>

namespace speakerd
{
    class Control;
    class Engine;
    class LocalLink;

    /*  The monitor thread (docs/protocol.md, "Monitoring"): a normal-priority thread that
        samples the streams the local link's client subscribed to, each at its own rate (at most
        maxRateHz), and sends their values as `value` lines. Nothing is sampled, measured or sent
        for a stream nobody subscribed to.

        - output/level: [woofer_peak, woofer_rms, tweeter_peak, tweeter_rms] in dBFS, of what the
          amplifier got since the last value (after the fade, the limits and the caps). The audio
          thread measures each cycle while this stream is subscribed (Engine::setLevelWanted)
          and pushes the cycle's peaks and sums of squares into a lock-free ring; this thread
          drains the ring at least every levelDrainSeconds and adds them up. RMS is the plain
          root mean square: a full-scale sine is 0 dBFS peak and -3.01 dBFS RMS. Silence, and
          anything below -120 dBFS, is -120. A period in which no cycle ran sends nothing.
        - node/<uid>/<mon_id>: the value a hosted plugin last reported for its monitor
          parameter, in its own units. Sent as a number. A stream the running graph does not have
          (no such node or parameter) sends nothing, until a revision has it.

        `t` is speakerd's monotonic clock (CLOCK_MONOTONIC), in seconds. Subscriptions belong to
        the connection that made them, and end with it. */
    class Monitor
    {
    public:
        static constexpr double defaultRateHz = 30.0;
        static constexpr double maxRateHz = 60.0;
        static constexpr double levelDrainSeconds = 0.05;
        static constexpr double floorDb = -120.0;

        Monitor (Engine&, Control&, LocalLink&);
        ~Monitor();

        void start();
        void stop();

        /** Link thread. Fails, with the reason, for a stream name that is neither output/level
            nor node/<uid>/mon_<...>. Subscribing again changes the rate. */
        juce::Result subscribe (juce::uint64 connection, const juce::String& stream, double rateHz);
        void unsubscribe (juce::uint64 connection, const juce::String& stream);

        /** The connection went: every subscription it made ends. */
        void clear (juce::uint64 connection);

    private:
        using Clock = std::chrono::steady_clock;

        struct Stream
        {
            bool level = false;
            juce::uint32 node = 0;
            juce::String id;
            double rateHz = defaultRateHz;
            Clock::time_point due;
        };

        void run();
        void updateLevelWanted();               // under the lock
        void drainLevels();                     // monitor thread, under the lock
        juce::String levelLine (const juce::String& t);
        void endAll (const juce::String& why);  // under the lock

        Engine& engine;
        Control& control;
        LocalLink& link;

        std::mutex lock;
        std::condition_variable changed;
        std::map<juce::String, Stream> streams;
        juce::uint64 connection = 0;
        bool stopping = false;
        bool levelRestart = false;              // a new level subscription: drop what the ring holds
        std::thread thread;

        // The output level since the last value: the monitor thread's alone.
        float wooferPeak = 0.0f, tweeterPeak = 0.0f;
        double wooferSquares = 0.0, tweeterSquares = 0.0;
        juce::int64 levelSamples = 0;

        JUCE_DECLARE_NON_COPYABLE (Monitor)
    };
}
