#include "Engine.h"

#include <cmath>

namespace speakerd
{
    Engine::~Engine()
    {
        // The audio thread is gone by now (the PipeWire node is destroyed first). Take the graph
        // anyway, so that a late cycle would output silence rather than touch a deleted graph.
        for (;;)
        {
            int expected = nobody;

            if (owner.compare_exchange_weak (expected, messageThread) || expected == messageThread)
                break;

            juce::Thread::sleep (1);
        }

        graph.reset();
    }

    //==============================================================================
    void Engine::park()
    {
        fadeInRequested.store (false);

        // Wait for the audio thread to fade out. If no cycle runs (no PipeWire node yet, or
        // nothing linked), nothing is playing and there is nothing to wait for.
        const auto start = juce::Time::getMillisecondCounter();
        auto lastProgress = start;
        auto lastCycles = cycles.load();

        while (! faded.load())
        {
            juce::Thread::sleep (2);

            const auto now = juce::Time::getMillisecondCounter();

            if (const auto c = cycles.load(); c != lastCycles)
            {
                lastCycles = c;
                lastProgress = now;
            }

            if (now - lastProgress > 100 || now - start > 1000)
                break;
        }

        // The audio thread holds the graph for at most one cycle at a time.
        for (;;)
        {
            int expected = nobody;

            if (owner.compare_exchange_weak (expected, messageThread, std::memory_order_acquire, std::memory_order_relaxed))
                break;

            juce::Thread::sleep (1);
        }
    }

    void Engine::unpark (bool fadeIn)
    {
        fadePosition = fadeIn ? 0 : fadeLength;
        faded.store (fadePosition == 0);
        fadeInRequested.store (true);
        owner.store (nobody, std::memory_order_release);
    }

    void Engine::setRate (double rate)
    {
        preparedRate = rate;
        preparedRateForReaders.store (rate);
        fadeLength = juce::jmax (1, juce::roundToInt (fadeSeconds * rate));
    }

    void Engine::settle (int blockSize)
    {
        // Runs on the message thread, holding the graph. Besides letting the plugins take their
        // parameter values and finish their smoothing, the last block primes the graph's output
        // buffer at the block size the audio thread will use.
        blockSize = juce::jlimit (1, maxBlock, blockSize);
        const auto total = (juce::int64) std::ceil (settleSeconds * preparedRate);

        for (juce::int64 done = 0; done < total; done += blockSize)
        {
            io.clear();
            juce::AudioBuffer<float> block (io.getArrayOfWritePointers(), 2, blockSize);
            graph->processBlock (block, midi);
            midi.clear();
        }

        lastBlock = blockSize;
        lastBlockForReaders.store (blockSize);
    }

    void Engine::install (std::unique_ptr<juce::AudioProcessorGraph> newGraph, double sampleRate, int maxBlockIn,
                          int settleBlock, bool fadeIn)
    {
        jassert (newGraph != nullptr && maxBlockIn > 0);

        park();

        auto old = std::move (graph);
        graph = std::move (newGraph);
        maxBlock = maxBlockIn;
        io.setSize (2, maxBlock);
        io.clear();
        setRate (sampleRate);
        settle (settleBlock);

        unpark (fadeIn);
        old.reset();
    }

    double Engine::service()
    {
        const auto rate = requestedRate.exchange (0, std::memory_order_acquire);

        // preparedRate is only ever written on this thread, so it can be read here unowned.
        if (rate <= 0 || std::abs (rate - preparedRate) < 0.5)
            return 0.0;

        park();

        if (graph != nullptr)
        {
            const auto block = requestedBlock.load();
            graph->prepareToPlay ((double) rate, maxBlock);
            setRate (rate);
            settle (block > 0 ? block : lastBlock);
        }

        unpark (true);
        return rate;
    }

    void Engine::fadeOut()
    {
        park();     // and keep the graph: from now on every cycle is silent
        fadePosition = 0;
    }

    //==============================================================================
    void Engine::process (const float* const* in, float* const* out, int numSamples, double cycleRate) noexcept
    {
        cycles.fetch_add (1, std::memory_order_relaxed);

        const auto silence = [&]
        {
            if (out != nullptr)
                for (int c = 0; c < 2; ++c)
                    if (out[c] != nullptr)
                        juce::FloatVectorOperations::clear (out[c], numSamples);

            audible.store (false, std::memory_order_relaxed);
        };

        if (numSamples <= 0)
            return;

        int expected = nobody;

        if (! owner.compare_exchange_strong (expected, audioThread, std::memory_order_acquire, std::memory_order_relaxed))
            return silence();   // the message thread is changing the graph, after fading out

        struct Release
        {
            std::atomic<int>& o;
            ~Release() { o.store (nobody, std::memory_order_release); }
        } release { owner };

        // Silent cycles that hold the graph: the output is at zero, so the message thread may
        // take the graph at once.
        const auto silentAndFaded = [&]
        {
            fadePosition = 0;
            faded.store (true);
            silence();
        };

        if (graph == nullptr || numSamples > maxBlock)
            return silentAndFaded();

        if (cycleRate > 0.0 && std::abs (cycleRate - preparedRate) > 0.5)
        {
            // Not prepared for this rate: silent until the message thread re-prepares (service()).
            // The block size comes along, since a new rate usually brings a new quantum.
            requestedBlock.store (numSamples, std::memory_order_relaxed);
            requestedRate.store (juce::roundToInt (cycleRate), std::memory_order_release);
            return silentAndFaded();
        }

        if (numSamples != lastBlock)
        {
            blockSizeChanges.fetch_add (1, std::memory_order_relaxed);
            lastBlock = numSamples;
            lastBlockForReaders.store (numSamples, std::memory_order_relaxed);
        }

        juce::ScopedNoDenormals noDenormals;

        for (int c = 0; c < 2; ++c)
        {
            auto* dst = io.getWritePointer (c);

            if (in != nullptr && in[c] != nullptr)
                juce::FloatVectorOperations::copy (dst, in[c], numSamples);
            else
                juce::FloatVectorOperations::clear (dst, numSamples);
        }

        juce::AudioBuffer<float> block (io.getArrayOfWritePointers(), 2, numSamples);
        graph->processBlock (block, midi);
        midi.clear();

        auto* woofer  = block.getWritePointer (0);
        auto* tweeter = block.getWritePointer (1);
        const bool fadingIn = fadeInRequested.load (std::memory_order_relaxed);

        if (fadingIn && fadePosition >= fadeLength)
        {
            // full level
        }
        else if (! fadingIn && fadePosition <= 0)
        {
            juce::FloatVectorOperations::clear (woofer, numSamples);
            juce::FloatVectorOperations::clear (tweeter, numSamples);
        }
        else
        {
            const auto step = 1.0f / (float) fadeLength;

            for (int i = 0; i < numSamples; ++i)
            {
                fadePosition = juce::jlimit (0, fadeLength, fadePosition + (fadingIn ? 1 : -1));
                const auto gain = (float) fadePosition * step;
                woofer[i]  *= gain;
                tweeter[i] *= gain;
            }
        }

        faded.store (! fadingIn && fadePosition == 0);
        audible.store (fadePosition > 0, std::memory_order_relaxed);

        if (out != nullptr)
        {
            if (out[0] != nullptr)
                juce::FloatVectorOperations::copy (out[0], woofer, numSamples);

            if (out[1] != nullptr)
                juce::FloatVectorOperations::copy (out[1], tweeter, numSamples);
        }
    }
}
