#pragma once

#include "DeviceConfig.h"

#include <juce_audio_processors_headless/juce_audio_processors_headless.h>

#include <array>
#include <atomic>
#include <memory>
#include <vector>

namespace speakerd
{
    /** The output limits (device.json "limits"), as the audio thread applies them to every
        cycle after the fade: both amplifier channels times `gain`, the tweeter exact zeros if
        `tweeterMuted`, and then every sample clamped to +-`gain`, a ceiling, so a stream above
        full scale or a graph that boosts never gets past it. No member has a default: whoever
        makes an Engine says what its limits are; no limit at all is { 1.0f, false }. */
    struct OutputLimits
    {
        float gain;           // 0 <= gain <= 1
        bool tweeterMuted;
    };

    /** Parameter values on their way to the audio thread (step 3): one slot per hosted
        parameter of one graph, newest value wins, and a lock-free queue of the slots that have a
        new value. The socket thread resolves a parameter to its slot and posts the normalised
        value; the audio thread, at the top of a cycle, before the graph runs, sets every posted
        value on its hosted parameter (AudioProcessorParameter::setValue: for JUCE's LV2 hosting,
        an atomic store and a flag, which the plugin receives as a patch:Set in that very
        processBlock). A slot is in the queue at most once and only while its `queued` flag is
        on: the producer turns the flag on and then queues it, the consumer takes it out of the
        queue (the read finished) and only then turns the flag off. So the queue holds at most
        numSlots entries and always has room, and the newest value is never lost, however long
        the audio thread does not run and however many values arrive while it applies them.

        One producer at a time (the caller serialises them), one consumer: the audio thread. It
        belongs to one graph and is swapped with it (Engine::install). */
    class ParameterInbox
    {
    public:
        explicit ParameterInbox (const std::vector<juce::AudioProcessorParameter*>&);

        int size() const noexcept      { return numSlots; }

        /** Any thread, one at a time: the slot's value is now `normalised`. Lock-free. */
        void post (int slot, float normalised) noexcept;

        /** Audio thread: sets every posted value on its parameter. */
        void applyPending() noexcept;

    private:
        struct Slot
        {
            juce::AudioProcessorParameter* parameter = nullptr;
            std::atomic<float> value { 0.0f };
            std::atomic<bool> queued { false };
        };

        int numSlots;
        std::unique_ptr<Slot[]> slots;
        juce::AbstractFifo fifo;
        std::vector<int> queue;

        JUCE_DECLARE_NON_COPYABLE (ParameterInbox)
    };

    /** The output level of one cycle, as the amplifier gets it (after the fade, the limits and
        the caps): per channel, the largest absolute sample and the sum of the squares. */
    struct LevelRecord
    {
        float wooferPeak = 0.0f, tweeterPeak = 0.0f;
        float wooferSquares = 0.0f, tweeterSquares = 0.0f;
        int samples = 0;
    };

    class TimedNode;

    /** A graph and the inbox of its parameters: what Engine::install swaps in, and hands back. */
    struct RunningGraph
    {
        std::unique_ptr<juce::AudioProcessorGraph> graph;
        std::unique_ptr<ParameterInbox> inbox;    // may be null (the render)
        std::vector<TimedNode*> timedNodes;       // its plugin nodes, owned by the graph, for the timing
    };

    /** What one audio cycle cost, as the audio thread measured it (Engine::process): nanoseconds,
        and the cycle's budget, its length in time (quantum / rate). */
    struct TimingRecord
    {
        static constexpr int maxNodes = 64;        // GraphDocument::maxPluginNodes

        juce::uint32 graphSerial = 0;              // which graph ran (Engine::getGraphSerial)
        juce::uint32 budgetNs = 0;
        juce::uint32 cycleNs = 0;                  // all of Engine::process
        juce::uint32 graphNs = 0;                  // AudioProcessorGraph::processBlock, all blocks of the cycle
        bool graphRan = false;                     // false: the message thread held the graph, or none
        bool positionJump = false;                 // the driver's position did not follow on
        juce::uint16 numNodes = 0;
        std::array<juce::uint32, maxNodes> nodeNs; // per plugin node, in RunningGraph::timedNodes order
    };

    /*  The running graph, and the one place the audio thread enters it.

        Two threads use a graph: the message thread (JUCE's, the main thread) builds, prepares
        and replaces it, and the audio thread (PipeWire's data thread, or the render loop) runs
        it. They hand it over through one atomic, `owner`: the audio thread takes it with a
        compare-exchange at the top of a cycle and gives it back at the end. When the message
        thread holds it, the cycle outputs silence instead of waiting, so the audio thread never
        blocks. The message thread only takes it after the output has faded out, so taking it is
        never heard as a click.

        The fade-out is a handshake, not a flag: park() turns the fade-in request off, then
        increments `parkSerial`. Each cycle reads the serial first and the request second, and
        stores the serial it read in `fadedSerial` only if it began with the fade-in off and
        ended at gain 0. park() waits for its own serial to come back, so it can never act on a
        cycle that ran before it asked.

        What the audio thread does per cycle: it sets the parameter values posted to the running
        graph's inbox, copies the stream in (NaN and infinity become 0) and makes the Mine channel
        from it (left: FL, right: FR, mono: (FL + FR) / 2), calls AudioProcessorGraph::processBlock in blocks of the size the graph is primed
        for, ramps the fade, applies the output limits of device.json (a fixed gain, the tweeter
        muted: constants, fixed when the engine is made), caps the two amplifier channels (NaN
        and infinity become 0, the rest is clamped to the ceiling, +-the limits' gain, at most
        +-1.0) and copies them out. While somebody watches the output level (setLevelWanted), it
        also measures the peak and the sum of the squares of what it copied out, and pushes them
        into a lock-free ring for the monitor thread. Every cycle it records what it cost (the
        cycle, the graph, each plugin node: TimedNode) into another (TimingRecord). No
        allocation, no lock of its own, and no syscall but the timing's clock reads, 4 + 2 per
        plugin node: the vDSO's on most kernels, a short non-blocking syscall on a Cortex-A73
        with the erratum 858921 workaround (nowNs, TimedNode.h; the exception in dsp/CLAUDE.md).

        The locks JUCE takes on the audio thread, and why none of them waits:
        - the graph takes each processor's callback lock around its processBlock; speakerd never
          takes those locks from another thread while the graph runs;
        - the graph's render sequence is handed over under a SpinLock that the audio thread only
          try-locks;
        - JUCE's LV2 hosting, when a plugin echoes a parameter on its atom output (patch:Set),
          sets the hosted parameter from the audio thread (processAtomPort -> PatchSetCallback ->
          setDenormalisedValueWithoutTriggeringUpdate), which takes that parameter's
          listenerLock (juce_AudioProcessorParameter.cpp, sendValueChangedMessageToListeners);
          a StateChanged atom calls updateHostDisplay, which takes the processor's listenerLock.
          The message thread takes the same parameter lock when it sets a value. So once a
          graph runs, nobody sets its parameters on the message thread: parameter changes go to
          the audio thread through the graph's ParameterInbox and are applied there, with
          setValue, which takes no lock. GraphBuilder sets values only on a graph that is not
          running yet.

        Block size. JUCE's graph reallocates its output buffer whenever a call's block size
        differs from the previous call's. The graph is primed once, on the message thread, at
        the quantum the cycles will bring, and from then on it is only ever called with that
        size. A cycle that is a whole multiple of it runs as several blocks. Any other cycle (a
        smaller quantum, or one that is not a multiple) is padded with zeros to whole blocks, its
        real samples fade out, and the output stays silent until service() primes the graph at
        the new size with one silent block and fades back in. A quantum of upwardPrimeRatio or
        more primed blocks that lasts upwardPrimeSeconds is primed anew too, through a normal
        fade-out, so that the graph does not stay at a small block (and its per-call cost) for
        good after a quantum came down and went back up.

        The output gate. The input sink drives the filter on its own, so the graph keeps running
        while the amplifier is away, and PipeWire 0.3.65 keeps handing the filter output buffers
        that nobody reads. Without a gate, the fade would be long over when the amplifier came
        back, and it would hear the stream start at full level. So the PipeWire side closes the
        gate (setOutputGate) whenever the filter is not linked on both sides with every link
        active, and opens it once they all are. While it is closed the plugins keep running and
        the output stays at gain 0; when it opens, the fade-in starts from 0 in the next cycle.
        Lock-free: one atomic, read once per cycle. */
    class Engine
    {
    public:
        /** The output fades in over this time at every start, after every change of graph and
            after a change of sample rate or of quantum, and fades out over it before a change
            and at a stop. */
        static constexpr double fadeSeconds = 0.030;

        /** A new graph runs on this much silence before it is heard. JUCE's LV2 wrapper prepares
            a plugin's DSP when it is instantiated, with its default parameter values, so the
            plugin's own smoothing (loudness glides up to 1 s) starts from the defaults; after
            this, the document's values are in force from the first audible sample. */
        static constexpr double settleSeconds = 1.0;

        /** The largest sample speakerd sends to an amplifier, whatever the limits. */
        static constexpr float outputLimit = 1.0f;

        /** A quantum this many times the primed block size, or more, that lasts this long, is
            primed anew (see above). */
        static constexpr int upwardPrimeRatio = 4;
        static constexpr double upwardPrimeSeconds = 2.0;

        /** The channels of the buffer the graph runs on: its inputs are FL, FR and Mine, its
            outputs the woofer and the tweeter. */
        static constexpr int graphChannels = 3;

        /** The cycles the level ring holds: several seconds at the speaker's quantum, a quarter
            of a second at 32 samples. */
        static constexpr int levelRingSize = 1024;

        /** The cycles the timing ring holds: 10 s at the speaker's quantum, a third of a second at
            32 samples; the monitor thread drains it every 50 ms. */
        static constexpr int timingRingSize = 512;

        /** `limits` are constants for the engine's life (see OutputLimits); `role` says what the
            Mine channel is. */
        Engine (OutputLimits limits, Role role);
        ~Engine();

        //==============================================================================
        // Message thread

        /** Makes `graph` (built and prepared at `sampleRate` for blocks up to `maxBlock`) the
            running graph. It settles the new graph first, on its own buffers, beside the running
            one (the audio thread does not know it yet), primed at `settleBlock`. Then it fades
            the output out if it was playing, swaps the graph in and fades back in (or, with
            fadeIn false, starts at full level, for the render mode): a swap costs the two fades.
            Returns the old graph and its inbox, which the audio thread no longer knows: the caller
            deletes them, on the message thread, once nobody else refers to them. */
        RunningGraph install (RunningGraph next, double sampleRate, int maxBlock, int settleBlock, bool fadeIn = true);

        /** The serial number of the running graph, one more at every install: what a
            TimingRecord's graphSerial refers to. */
        juce::uint32 getGraphSerial() const noexcept   { return graphSerialForReaders.load(); }

        /** What service() did. */
        struct Serviced
        {
            double rate = 0.0;      // the graph was re-prepared at this new sample rate
            int primedBlock = 0;    // the graph was primed at this new quantum
        };

        /** Handles what the audio thread asked for. A cycle at a sample rate the graph is not
            prepared for: re-prepares the graph at the new rate (hosted LV2 plugins are
            re-instantiated, keeping their state), settles it at the cycles' block size and fades
            back in. A cycle that does not fit the primed block size, or a lasting quantum of
            upwardPrimeRatio primed blocks or more: fades out if need be, primes the graph at the
            new size with one silent block and fades back in. Call it often, from a timer. */
        Serviced service();

        /** Closes or opens the output gate (see above). Any thread; lock-free. Open by default,
            for the render; the live node closes it until its links are active. */
        void setOutputGate (bool open) noexcept    { outputGateOpen.store (open, std::memory_order_release); }

        /** Fades the output out and keeps it silent, for a clean stop. Returns after two
            silent cycles more, so that the end of the fade has reached the amplifier before
            the node goes. */
        void fadeOut();

        double getSampleRate() const noexcept      { return preparedRateForReaders.load(); }

        //==============================================================================
        // Statistics, readable from any thread

        juce::uint64 getCycles() const noexcept    { return cycles.load (std::memory_order_relaxed); }
        int getLastBlockSize() const noexcept      { return lastBlockForReaders.load (std::memory_order_relaxed); }   // the last cycle's
        int getPrimedBlockSize() const noexcept    { return primedBlockForReaders.load (std::memory_order_relaxed); } // the graph's
        int getQuantumChanges() const noexcept     { return quantumChanges.load (std::memory_order_relaxed); }
        int getGateOpenings() const noexcept       { return gateOpenings.load (std::memory_order_relaxed); }
        bool isAudible() const noexcept            { return audible.load (std::memory_order_relaxed); }
        int getMaxBlock() const noexcept           { return maxBlockForReaders.load (std::memory_order_relaxed); }

        //==============================================================================
        // The output level, for the monitor thread

        /** Whether the audio thread measures the output level. Off by default: nobody watches. */
        void setLevelWanted (bool wanted) noexcept  { levelWanted.store (wanted, std::memory_order_release); }

        /** One consumer at a time: calls `fn (const TimingRecord&)` for every cycle since the last
            call, oldest first, and returns how many. Every cycle is recorded, watched or not. */
        template <typename Fn>
        int drainTiming (Fn&& fn)
        {
            const auto scope = timingFifo.read (timingFifo.getNumReady());

            for (int i = 0; i < scope.blockSize1; ++i)  fn (timingRing[(size_t) (scope.startIndex1 + i)]);
            for (int i = 0; i < scope.blockSize2; ++i)  fn (timingRing[(size_t) (scope.startIndex2 + i)]);

            return scope.blockSize1 + scope.blockSize2;
        }

        /** Cycles whose record did not fit in the timing ring (the monitor thread was behind). */
        juce::uint64 getTimingDropped() const noexcept  { return timingDropped.load (std::memory_order_relaxed); }

        /** One consumer at a time: calls `fn (const LevelRecord&)` for every cycle measured since
            the last call, oldest first, and returns how many. */
        template <typename Fn>
        int drainLevels (Fn&& fn)
        {
            const auto scope = levelFifo.read (levelFifo.getNumReady());

            for (int i = 0; i < scope.blockSize1; ++i)  fn (levelRing[(size_t) (scope.startIndex1 + i)]);
            for (int i = 0; i < scope.blockSize2; ++i)  fn (levelRing[(size_t) (scope.startIndex2 + i)]);

            return scope.blockSize1 + scope.blockSize2;
        }

        //==============================================================================
        // Audio thread

        /** One cycle. `in` holds FL and FR, `out` the woofer and the tweeter; any of the four may
            be null (silence in, nothing out). `cycleRate` is the rate PipeWire runs at (0 if
            unknown), `position` the driver's position in samples (-1 if unknown): a cycle whose
            position does not follow on from the last one's is counted as a position jump. Never
            blocks or allocates. Its only syscalls are the timing's clock reads where the kernel
            has no vDSO clock (a Cortex-A73 with the erratum 858921 workaround): short and
            non-blocking (nowNs, TimedNode.h). */
        void process (const float* const* in, float* const* out, int numSamples, double cycleRate,
                      juce::int64 position = -1) noexcept;

    private:
        void processCycle (const float* const* in, float* const* out, int numSamples, double cycleRate) noexcept;
        void measureLevel (float* const* out, int numSamples) noexcept;

        enum Owner { nobody = 0, audioThread = 1, messageThread = 2 };

        void park();      // message thread: fade out, then take the graph
        void unpark (bool fadeIn);
        void setRate (double);
        void setPrimedBlock (int);

        /** Runs `graph` on settleSeconds of silence in blocks of `blockSize`, using `scratch`
            (graphChannels channels of at least `blockSize` samples). The last block primes the graph's output buffer at
            `blockSize`. Message thread, on a graph the audio thread is not running. */
        static void settle (juce::AudioProcessorGraph&, double sampleRate, int blockSize,
                            juce::AudioBuffer<float>& scratch, juce::MidiBuffer&);

        // The output limits: constants, only read by the audio thread.
        const float outputGain;       // 0 .. 1
        const float outputCeiling;    // min (outputGain, outputLimit)
        const bool tweeterMuted;
        const Role role;          // what the Mine channel is

        std::atomic<int> owner { nobody };

        // Owned by whoever holds `owner`. preparedRate, maxBlock and primedBlock are only
        // written by the message thread while it holds it, so that thread may read them anytime.
        std::unique_ptr<juce::AudioProcessorGraph> graph;
        std::unique_ptr<ParameterInbox> inbox;
        std::vector<TimedNode*> timedNodes;
        juce::uint32 graphSerial = 0;
        juce::AudioBuffer<float> io;            // graphChannels x (2 * maxBlock): room for a padded cycle
        juce::MidiBuffer midi;
        double preparedRate = 0.0;
        int maxBlock = 0, primedBlock = 1, lastCycleBlock = 0;
        int fadeLength = 1, fadePosition = 0;    // gain = fadePosition / fadeLength
        juce::int64 stableSamples = 0, upwardPrimeAfter = 0;   // samples at a quantum of upwardPrimeRatio blocks or more

        // The audio thread's alone, whoever holds the graph: was the gate open in the last cycle?
        bool gateWasOpen = true;

        // The audio thread's alone: this cycle's timing (filled while it holds the graph), and
        // the last cycle's position.
        TimingRecord cycleTiming;
        juce::int64 lastPosition = -1, lastDuration = 0;

        // Between the threads.
        std::atomic<bool> fadeInRequested { true }, audible { false }, outputGateOpen { true };
        std::atomic<juce::uint32> parkSerial { 0 }, fadedSerial { 0 };
        std::atomic<int> requestedRate { 0 }, requestedBlock { 0 }, requestedPrime { 0 };

        std::atomic<juce::uint64> cycles { 0 };
        std::atomic<int> lastBlockForReaders { 0 }, primedBlockForReaders { 0 }, quantumChanges { 0 }, gateOpenings { 0 };
        std::atomic<double> preparedRateForReaders { 0.0 };
        std::atomic<int> maxBlockForReaders { 0 };
        std::atomic<juce::uint32> graphSerialForReaders { 0 };

        // The timing: the audio thread writes a record per cycle, the monitor thread reads them.
        juce::AbstractFifo timingFifo { timingRingSize };
        std::unique_ptr<TimingRecord[]> timingRing { new TimingRecord[(size_t) timingRingSize] };
        std::atomic<juce::uint64> timingDropped { 0 };

        // The output level: the audio thread writes, the monitor thread reads.
        std::atomic<bool> levelWanted { false };
        juce::AbstractFifo levelFifo { levelRingSize };
        std::array<LevelRecord, levelRingSize> levelRing;

        JUCE_DECLARE_NON_COPYABLE (Engine)
    };
}
