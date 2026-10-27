#pragma once

#include <juce_audio_processors_headless/juce_audio_processors_headless.h>

#include <atomic>
#include <memory>

namespace speakerd
{
    /*  The running graph, and the one place the audio thread enters it.

        Two threads use a graph: the message thread (JUCE's, the main thread) builds, prepares
        and replaces it, and the audio thread (PipeWire's data thread, or the render loop) runs
        it. They hand it over through one atomic, `owner`: the audio thread takes it with a
        compare-exchange at the top of a cycle and gives it back at the end. When the message
        thread holds it, the cycle outputs silence instead of waiting, so the audio thread never
        blocks. The message thread only takes it after fading the output out, so taking it is
        never heard as a click.

        What the audio thread does per cycle, and does not do: it copies the stream in, calls
        AudioProcessorGraph::processBlock, ramps the fade, copies the amplifier channels out. No
        allocation, no lock of its own, no syscall. JUCE's graph takes each processor's callback
        lock around its processBlock; speakerd never takes those locks from another thread while
        the graph runs, so they are never contended and never block. The graph reallocates one
        output buffer when the block size of a call differs from the previous call's; settle()
        primes it at the expected block size on the message thread, so this only happens on the
        audio thread if PipeWire changes its quantum (counted in getBlockSizeChanges()). */
    class Engine
    {
    public:
        /** The output fades in over this time at every start, after every change of graph and
            after a change of sample rate, and fades out over it before a change and at a stop. */
        static constexpr double fadeSeconds = 0.030;

        /** A new graph runs on this much silence before it is heard. JUCE's LV2 wrapper prepares
            a plugin's DSP when it is instantiated, with its default parameter values, so the
            plugin's own smoothing (loudness glides up to 1 s) starts from the defaults; after
            this, the document's values are in force from the first audible sample. */
        static constexpr double settleSeconds = 1.0;

        Engine() = default;
        ~Engine();

        //==============================================================================
        // Message thread

        /** Makes `graph` (built and prepared at `sampleRate` for blocks up to `maxBlock`) the
            running graph: fades the output out if it was playing, swaps the graph in, settles it
            in blocks of `settleBlock`, and fades back in (or, with fadeIn false, starts at full
            level, for the render mode). The old graph is deleted here, on the message thread. */
        void install (std::unique_ptr<juce::AudioProcessorGraph> graph, double sampleRate, int maxBlock,
                      int settleBlock, bool fadeIn = true);

        /** Handles what the audio thread asked for: a cycle at a sample rate the graph is not
            prepared for. Re-prepares the graph at the new rate (hosted LV2 plugins are
            re-instantiated, keeping their state), settles it and fades back in. Call it often,
            from a timer. Returns the new rate, or 0 if nothing was done. */
        double service();

        /** Fades the output out and keeps it silent, for a clean stop. */
        void fadeOut();

        double getSampleRate() const noexcept      { return preparedRateForReaders.load(); }

        //==============================================================================
        // Statistics, readable from any thread

        juce::uint64 getCycles() const noexcept    { return cycles.load (std::memory_order_relaxed); }
        int getLastBlockSize() const noexcept      { return lastBlockForReaders.load (std::memory_order_relaxed); }
        int getBlockSizeChanges() const noexcept   { return blockSizeChanges.load (std::memory_order_relaxed); }
        bool isAudible() const noexcept            { return audible.load (std::memory_order_relaxed); }

        //==============================================================================
        // Audio thread

        /** One cycle. `in` holds FL and FR, `out` the woofer and the tweeter; any of the four may
            be null (silence in, nothing out). `cycleRate` is the rate PipeWire runs at (0 if
            unknown). Never blocks, allocates or makes a syscall. */
        void process (const float* const* in, float* const* out, int numSamples, double cycleRate) noexcept;

    private:
        enum Owner { nobody = 0, audioThread = 1, messageThread = 2 };

        void park();      // message thread: fade out, then take the graph
        void unpark (bool fadeIn);
        void settle (int blockSize);
        void setRate (double);

        std::atomic<int> owner { nobody };

        // Owned by whoever holds `owner`.
        std::unique_ptr<juce::AudioProcessorGraph> graph;
        juce::AudioBuffer<float> io;
        juce::MidiBuffer midi;
        double preparedRate = 0.0;
        int maxBlock = 0, lastBlock = 0;
        int fadeLength = 1, fadePosition = 0;    // gain = fadePosition / fadeLength

        // Between the threads.
        std::atomic<bool> fadeInRequested { true }, faded { true }, audible { false };
        std::atomic<int> requestedRate { 0 }, requestedBlock { 0 };

        std::atomic<juce::uint64> cycles { 0 };
        std::atomic<int> lastBlockForReaders { 0 }, blockSizeChanges { 0 };
        std::atomic<double> preparedRateForReaders { 0.0 };

        JUCE_DECLARE_NON_COPYABLE (Engine)
    };
}
