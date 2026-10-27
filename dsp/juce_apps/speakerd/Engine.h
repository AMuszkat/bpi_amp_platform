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
        blocks. The message thread only takes it after the output has faded out, so taking it is
        never heard as a click.

        The fade-out is a handshake, not a flag: park() turns the fade-in request off, then
        increments `parkSerial`. Each cycle reads the serial first and the request second, and
        stores the serial it read in `fadedSerial` only if it began with the fade-in off and
        ended at gain 0. park() waits for its own serial to come back, so it can never act on a
        cycle that ran before it asked.

        What the audio thread does per cycle: it copies the stream in (NaN and infinity become
        0), calls AudioProcessorGraph::processBlock in blocks of the size the graph is primed
        for, ramps the fade, caps the two amplifier channels (NaN and infinity become 0, the rest
        is clamped to +-1.0) and copies them out. No allocation, no lock of its own, no syscall.

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
          graph runs, nobody sets its parameters on the message thread: in step 3, parameter
          changes go to the audio thread through the command queue and are applied there. In
          step 2 values are only set on a graph that is not running yet (GraphBuilder).

        Block size. JUCE's graph reallocates its output buffer whenever a call's block size
        differs from the previous call's. The graph is primed once, on the message thread, at
        the quantum the cycles will bring, and from then on it is only ever called with that
        size. A cycle that is a whole multiple of it runs as several blocks. Any other cycle (a
        smaller quantum, or one that is not a multiple) is padded with zeros to whole blocks, its
        real samples fade out, and the output stays silent until service() primes the graph at
        the new size with one silent block and fades back in. */
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

        /** The largest sample speakerd sends to an amplifier. */
        static constexpr float outputLimit = 1.0f;

        Engine() = default;
        ~Engine();

        //==============================================================================
        // Message thread

        /** Makes `graph` (built and prepared at `sampleRate` for blocks up to `maxBlock`) the
            running graph. It settles the new graph first, on its own buffers, beside the running
            one (the audio thread does not know it yet), primed at `settleBlock`. Then it fades
            the output out if it was playing, swaps the graph in and fades back in (or, with
            fadeIn false, starts at full level, for the render mode): a swap costs the two fades.
            The old graph is deleted here, on the message thread. */
        void install (std::unique_ptr<juce::AudioProcessorGraph> graph, double sampleRate, int maxBlock,
                      int settleBlock, bool fadeIn = true);

        /** What service() did. */
        struct Serviced
        {
            double rate = 0.0;      // the graph was re-prepared at this new sample rate
            int primedBlock = 0;    // the graph was primed at this new quantum
        };

        /** Handles what the audio thread asked for. A cycle at a sample rate the graph is not
            prepared for: re-prepares the graph at the new rate (hosted LV2 plugins are
            re-instantiated, keeping their state), settles it and fades back in. A cycle that
            does not fit the primed block size: primes the graph at the new size with one silent
            block and fades back in. Call it often, from a timer. */
        Serviced service();

        /** Fades the output out and keeps it silent, for a clean stop. */
        void fadeOut();

        double getSampleRate() const noexcept      { return preparedRateForReaders.load(); }

        //==============================================================================
        // Statistics, readable from any thread

        juce::uint64 getCycles() const noexcept    { return cycles.load (std::memory_order_relaxed); }
        int getLastBlockSize() const noexcept      { return lastBlockForReaders.load (std::memory_order_relaxed); }   // the last cycle's
        int getPrimedBlockSize() const noexcept    { return primedBlockForReaders.load (std::memory_order_relaxed); } // the graph's
        int getQuantumChanges() const noexcept     { return quantumChanges.load (std::memory_order_relaxed); }
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
        void setRate (double);
        void setPrimedBlock (int);

        /** Runs `graph` on settleSeconds of silence in blocks of `blockSize`, using `scratch`
            (at least `blockSize` samples). The last block primes the graph's output buffer at
            `blockSize`. Message thread, on a graph the audio thread is not running. */
        static void settle (juce::AudioProcessorGraph&, double sampleRate, int blockSize,
                            juce::AudioBuffer<float>& scratch, juce::MidiBuffer&);

        std::atomic<int> owner { nobody };

        // Owned by whoever holds `owner`. preparedRate, maxBlock and primedBlock are only
        // written by the message thread while it holds it, so that thread may read them anytime.
        std::unique_ptr<juce::AudioProcessorGraph> graph;
        juce::AudioBuffer<float> io;            // 2 x (2 * maxBlock): room for a padded cycle
        juce::MidiBuffer midi;
        double preparedRate = 0.0;
        int maxBlock = 0, primedBlock = 1, lastCycleBlock = 0;
        int fadeLength = 1, fadePosition = 0;    // gain = fadePosition / fadeLength

        // Between the threads.
        std::atomic<bool> fadeInRequested { true }, audible { false };
        std::atomic<juce::uint32> parkSerial { 0 }, fadedSerial { 0 };
        std::atomic<int> requestedRate { 0 }, requestedBlock { 0 }, requestedPrime { 0 };

        std::atomic<juce::uint64> cycles { 0 };
        std::atomic<int> lastBlockForReaders { 0 }, primedBlockForReaders { 0 }, quantumChanges { 0 };
        std::atomic<double> preparedRateForReaders { 0.0 };

        JUCE_DECLARE_NON_COPYABLE (Engine)
    };
}
