#include "Engine.h"

#include "Samples.h"
#include "TimedNode.h"

#include <cmath>

namespace speakerd
{
    namespace
    {
        float validGain (float gain) noexcept
        {
            return samples::isFinite (gain) ? juce::jlimit (0.0f, 1.0f, gain) : 0.0f;
        }
    }

    //==============================================================================
    ParameterInbox::ParameterInbox (const std::vector<juce::AudioProcessorParameter*>& parameters)
        : numSlots ((int) parameters.size()),
          slots (std::make_unique<Slot[]> (parameters.size())),
          fifo (numSlots + 1),                  // an AbstractFifo holds one item fewer than its size
          queue ((size_t) numSlots + 1, 0)
    {
        for (int i = 0; i < numSlots; ++i)
            slots[(size_t) i].parameter = parameters[(size_t) i];
    }

    void ParameterInbox::post (int slot, float normalised) noexcept
    {
        if (! juce::isPositiveAndBelow (slot, numSlots))
            return;

        auto& s = slots[(size_t) slot];
        s.value.store (normalised, std::memory_order_relaxed);

        // In the queue at most once: whoever turns `queued` on puts the slot in, and the
        // consumer takes it out of the queue before it turns `queued` off. So the queue never
        // holds more than numSlots entries and always has room. A value stored after the
        // consumer turned `queued` off is either read by it or queued again: the newest value
        // is never lost.
        if (! s.queued.exchange (true, std::memory_order_acq_rel))
        {
            bool written = false;
            {
                const auto scope = fifo.write (1);

                if (scope.blockSize1 > 0)
                    queue[(size_t) scope.startIndex1] = slot, written = true;
                else if (scope.blockSize2 > 0)
                    queue[(size_t) scope.startIndex2] = slot, written = true;
            }

            // Cannot happen (see above); should it, the slot must not stay marked as queued, or
            // no later value would ever reach it.
            if (! written)
            {
                jassertfalse;
                s.queued.store (false, std::memory_order_release);
            }
        }
    }

    void ParameterInbox::applyPending() noexcept
    {
        // Only what is there now: a producer that keeps posting cannot hold the audio thread.
        for (auto remaining = fifo.getNumReady(); remaining > 0; --remaining)
        {
            int slot = -1;
            {
                // Out of the queue first (the read finishes at the end of this scope), and only
                // then `queued` off: the slot is never marked queued without being in the queue.
                const auto scope = fifo.read (1);

                if (scope.blockSize1 > 0)
                    slot = queue[(size_t) scope.startIndex1];
                else if (scope.blockSize2 > 0)
                    slot = queue[(size_t) scope.startIndex2];
            }

            if (! juce::isPositiveAndBelow (slot, numSlots))
                break;

            auto& s = slots[(size_t) slot];

            // An exchange, not a store: it reads the producer's exchange (acquire), so the value
            // that producer stored before it is the one read below, or the producer, coming
            // after this, finds `queued` off and queues the slot again.
            s.queued.exchange (false, std::memory_order_acq_rel);
            const auto value = s.value.load (std::memory_order_relaxed);

            // For a hosted LV2 plugin: an atomic store and a flag (no listener, no lock); the
            // plugin receives it as a patch:Set at the top of its next processBlock.
            if (s.parameter != nullptr)
                s.parameter->setValue (value);
        }
    }

    //==============================================================================
    Engine::Engine (OutputLimits limits, Role roleIn)
        : outputGain (validGain (limits.gain)),
          outputCeiling (juce::jmin (validGain (limits.gain), outputLimit)),
          tweeterMuted (limits.tweeterMuted),
          role (roleIn)
    {
    }

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
        inbox.reset();
    }

    //==============================================================================
    void Engine::park()
    {
        // The order is the handshake: the fade-in request goes off first, then the serial moves
        // on (release). A cycle that reads the new serial therefore reads the request off, and
        // only such a cycle, ending at gain 0, hands the serial back.
        fadeInRequested.store (false, std::memory_order_relaxed);
        const auto serial = parkSerial.fetch_add (1, std::memory_order_acq_rel) + 1;

        // Wait for the audio thread to fade out. If no cycle runs (no PipeWire node yet, or
        // nothing linked), nothing is playing and there is nothing to wait for.
        const auto start = juce::Time::getMillisecondCounter();
        auto lastProgress = start;
        auto lastCycles = cycles.load();

        while (fadedSerial.load (std::memory_order_acquire) != serial)
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
        // Whatever ran on the message thread (settling, priming) is not an audio cycle's time.
        for (auto* node : timedNodes)
            node->takeCycleNs();

        fadePosition = fadeIn ? 0 : fadeLength;
        fadeInRequested.store (true, std::memory_order_relaxed);
        owner.store (nobody, std::memory_order_release);
    }

    void Engine::setRate (double rate)
    {
        preparedRate = rate;
        preparedRateForReaders.store (rate);
        fadeLength = juce::jmax (1, juce::roundToInt (fadeSeconds * rate));
        upwardPrimeAfter = (juce::int64) std::ceil (upwardPrimeSeconds * rate);
    }

    void Engine::setPrimedBlock (int blockSize)
    {
        primedBlock = blockSize;
        primedBlockForReaders.store (blockSize);
        stableSamples = 0;
    }

    void Engine::settle (juce::AudioProcessorGraph& g, double sampleRate, int blockSize,
                         juce::AudioBuffer<float>& scratch, juce::MidiBuffer& m)
    {
        // Besides letting the plugins take their parameter values and finish their smoothing,
        // the last block primes the graph's output buffer at the block size the audio thread
        // will use.
        jassert (blockSize > 0 && scratch.getNumChannels() >= graphChannels && scratch.getNumSamples() >= blockSize);
        const auto total = (juce::int64) std::ceil (settleSeconds * sampleRate);

        for (juce::int64 done = 0; done < total; done += blockSize)
        {
            juce::AudioBuffer<float> block (scratch.getArrayOfWritePointers(), graphChannels, blockSize);
            block.clear();
            g.processBlock (block, m);
            m.clear();
        }
    }

    RunningGraph Engine::install (RunningGraph next, double sampleRate, int newMaxBlock, int settleBlock, bool fadeIn)
    {
        auto& newGraph = next.graph;
        jassert (newGraph != nullptr && newMaxBlock > 0);
        settleBlock = juce::jlimit (1, newMaxBlock, settleBlock);

        // Beside the running graph, on buffers of its own: the audio thread does not know this
        // graph yet, so its second of silence costs the graph that is playing nothing.
        {
            juce::AudioBuffer<float> scratch (graphChannels, settleBlock);
            juce::MidiBuffer scratchMidi;
            settle (*newGraph, sampleRate, settleBlock, scratch, scratchMidi);
        }

        // Room for maxBlock samples: a cycle whose blocks need more is the guard's (processCycle).
        juce::AudioBuffer<float> newIo (graphChannels, newMaxBlock);
        newIo.clear();

        park();    // a fade-out, if something is playing

        RunningGraph old { std::move (graph), std::move (inbox), std::move (timedNodes) };
        graph = std::move (next.graph);
        inbox = std::move (next.inbox);
        timedNodes = std::move (next.timedNodes);
        jassert ((int) timedNodes.size() <= TimingRecord::maxNodes);

        if ((int) timedNodes.size() > TimingRecord::maxNodes)
            timedNodes.resize ((size_t) TimingRecord::maxNodes);

        graphSerialForReaders.store (++graphSerial);
        io = std::move (newIo);
        maxBlock = newMaxBlock;
        maxBlockForReaders.store (newMaxBlock);
        setRate (sampleRate);
        setPrimedBlock (settleBlock);

        unpark (fadeIn);
        return old;
    }

    void Engine::reprepare (double rate, int newMaxBlock, int settleBlock)
    {
        jassert (owner.load() == messageThread && graph != nullptr && settleBlock <= newMaxBlock);

        if (newMaxBlock != maxBlock)
        {
            juce::AudioBuffer<float> newIo (graphChannels, newMaxBlock);
            newIo.clear();
            io = std::move (newIo);
            maxBlock = newMaxBlock;
            maxBlockForReaders.store (newMaxBlock);
        }

        // Hosted LV2 plugins are instantiated anew here (keeping their state), so their own
        // smoothing starts over: the graph settles before it is heard, as a new graph does.
        graph->prepareToPlay (rate, maxBlock);
        setRate (rate);
        settle (*graph, rate, settleBlock, io, midi);
        setPrimedBlock (settleBlock);

        // The cycles come at this size: not a change of quantum of its own (the line about the
        // re-prepare says what happened).
        lastCycleBlock = settleBlock;
        lastBlockForReaders.store (settleBlock);
    }

    Engine::Serviced Engine::service()
    {
        Serviced done;

        // preparedRate, maxBlock and primedBlock are only ever written on this thread, so they
        // can be read here unowned. A block the graph is not prepared for grows maxBlock, up to
        // the quantum limit.
        const auto preparedFor = [this] (int block) { return block <= maxBlock ? maxBlock
                                                           : block <= quantumLimit ? block : 0; };

        if (const auto rate = requestedRate.exchange (0, std::memory_order_acquire);
            rate > 0 && std::abs (rate - preparedRate) >= 0.5)
        {
            park();

            if (graph != nullptr)
            {
                const auto block = requestedBlock.load();
                auto settleBlock = juce::jmax (1, block > 0 ? block : primedBlock);
                auto newMaxBlock = preparedFor (settleBlock);

                if (newMaxBlock == 0)
                {
                    // Above the quantum limit: such cycles stay silent (the guard, processCycle).
                    done.refusedBlock = settleBlock;
                    newMaxBlock = maxBlock;
                    settleBlock = maxBlock;
                }

                if (newMaxBlock != maxBlock)
                {
                    done.fromMaxBlock = maxBlock;
                    done.maxBlock = newMaxBlock;
                }

                reprepare ((double) rate, newMaxBlock, settleBlock);
            }

            requestedPrime.store (0);
            unpark (true);
            done.rate = rate;
            return done;
        }

        // A cycle that did not fit the primed size or the buffer, or a lasting quantum of many
        // primed blocks; unless the quantum has moved on since, or back to a size that fits as
        // it is.
        const auto wanted = requestedPrime.exchange (0, std::memory_order_acquire);

        if (wanted <= 0 || wanted != lastBlockForReaders.load())
            return done;

        if (wanted > maxBlock)
        {
            // More than the graph is prepared for: the audio thread faded out and is silent.
            // A cycle above the quantum limit cannot come from PipeWire (it never runs a quantum
            // above clock.quantum-limit, forced or not); should one come, it stays silent.
            const auto newMaxBlock = preparedFor (wanted);

            if (newMaxBlock == 0)
            {
                done.refusedBlock = wanted;
                return done;
            }

            park();

            if (graph != nullptr)
            {
                done.fromMaxBlock = maxBlock;
                reprepare (preparedRate, newMaxBlock, wanted);
                done.maxBlock = newMaxBlock;
            }

            unpark (true);
            return done;
        }

        const auto fitsAsItIs = wanted % primedBlock == 0 && wanted / primedBlock < upwardPrimeRatio;

        if (fitsAsItIs)
            return done;

        // A size that does not fit: the audio thread has faded out already, and is silent. A
        // larger one that fits: cycles still play, and this is a normal 30 ms fade-out.
        park();

        if (graph != nullptr)
        {
            // One silent block at the new size: the graph reallocates its output buffer here,
            // on the message thread.
            juce::AudioBuffer<float> block (io.getArrayOfWritePointers(), graphChannels, wanted);
            block.clear();
            graph->processBlock (block, midi);
            midi.clear();
            setPrimedBlock (wanted);
        }

        unpark (true);
        done.primedBlock = wanted;
        return done;
    }

    void Engine::fadeOut()
    {
        park();     // and keep the graph: from now on every cycle is silent
        fadePosition = 0;

        // The cycle that ended the fade has run, but its output may not have reached the
        // amplifier yet: destroying the node now can drop it, and the end of the ramp with it
        // (a step to zero, measured on the virtual pair). So two silent cycles go by first,
        // unless no cycle runs.
        const auto start = juce::Time::getMillisecondCounter();
        auto lastProgress = start;
        const auto target = cycles.load() + 2;

        for (auto lastCycles = cycles.load(); lastCycles < target;)
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
    }

    //==============================================================================
    void Engine::process (const float* const* in, float* const* out, int numSamples, double cycleRate,
                          juce::int64 position) noexcept
    {
        const auto start = nowNs();

        cycleTiming.graphRan = false;
        cycleTiming.graphNs = 0;
        cycleTiming.numNodes = 0;

        processCycle (in, out, numSamples, cycleRate);

        if (levelWanted.load (std::memory_order_acquire))
            measureLevel (out, numSamples);

        // The timing of this cycle (README, Timing): its budget is its length in time.
        const auto rate = cycleRate > 0.0 ? cycleRate : getSampleRate();

        if (numSamples > 0 && rate > 0.0)
        {
            cycleTiming.positionJump = position >= 0 && lastPosition >= 0 && position != lastPosition + lastDuration;
            lastPosition = position;
            lastDuration = numSamples;

            cycleTiming.budgetNs = (juce::uint32) juce::jmin (4.0e9, 1.0e9 * numSamples / rate);
            cycleTiming.cycleNs = (juce::uint32) juce::jlimit ((juce::int64) 0, (juce::int64) 0xffffffff, nowNs() - start);

            const auto scope = timingFifo.write (1);
            auto* slot = scope.blockSize1 > 0 ? &timingRing[(size_t) scope.startIndex1]
                       : scope.blockSize2 > 0 ? &timingRing[(size_t) scope.startIndex2] : nullptr;

            if (slot != nullptr)
            {
                // Only what this cycle filled: the node times that are not used are not copied.
                slot->graphSerial = cycleTiming.graphSerial;
                slot->budgetNs = cycleTiming.budgetNs;
                slot->cycleNs = cycleTiming.cycleNs;
                slot->graphNs = cycleTiming.graphNs;
                slot->graphRan = cycleTiming.graphRan;
                slot->positionJump = cycleTiming.positionJump;
                slot->numNodes = cycleTiming.numNodes;
                std::copy (cycleTiming.nodeNs.begin(), cycleTiming.nodeNs.begin() + cycleTiming.numNodes, slot->nodeNs.begin());
            }
            else
            {
                timingDropped.fetch_add (1, std::memory_order_relaxed);
            }
        }
    }

    void Engine::measureLevel (float* const* out, int numSamples) noexcept
    {
        // What the amplifier gets: the samples just copied out (nothing, when a port has no
        // buffer). A full ring drops the cycle: the monitor thread is behind, or gone.
        if (numSamples <= 0)
            return;

        LevelRecord record;
        record.samples = numSamples;

        const auto measure = [numSamples] (const float* x, float& peak, float& squares)
        {
            if (x == nullptr)
                return;

            float p = 0.0f, s = 0.0f;

            for (int i = 0; i < numSamples; ++i)
            {
                const auto a = std::abs (x[i]);
                p = a > p ? a : p;
                s += x[i] * x[i];
            }

            peak = p;
            squares = s;
        };

        if (out != nullptr)
        {
            measure (out[0], record.wooferPeak, record.wooferSquares);
            measure (out[1], record.tweeterPeak, record.tweeterSquares);
        }

        const auto scope = levelFifo.write (1);

        if (scope.blockSize1 > 0)
            levelRing[(size_t) scope.startIndex1] = record;
        else if (scope.blockSize2 > 0)
            levelRing[(size_t) scope.startIndex2] = record;
    }

    void Engine::processCycle (const float* const* in, float* const* out, int numSamples, double cycleRate) noexcept
    {
        cycles.fetch_add (1, std::memory_order_relaxed);

        // The handshake with park(): the serial first, then the fade-in request.
        const auto serial = parkSerial.load (std::memory_order_acquire);
        const bool fadingIn = fadeInRequested.load (std::memory_order_acquire);

        // The output gate (see Engine.h): closed while the links are not all active.
        const bool gateOpen = outputGateOpen.load (std::memory_order_acquire);
        const bool gateOpened = gateOpen && ! gateWasOpen;
        gateWasOpen = gateOpen;

        if (gateOpened)
            gateOpenings.fetch_add (1, std::memory_order_relaxed);

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

        // Silent cycles that hold the graph: the output is at zero.
        const auto silentAtZero = [&]
        {
            fadePosition = 0;

            if (! fadingIn)
                fadedSerial.store (serial, std::memory_order_release);

            silence();
        };

        if (graph == nullptr)
            return silentAtZero();

        // Parameter values from the local link, before the graph runs: in force from this cycle.
        if (inbox != nullptr)
            inbox->applyPending();

        if (cycleRate > 0.0 && std::abs (cycleRate - preparedRate) > 0.5)
        {
            // Not prepared for this rate: silent until the message thread re-prepares (service()).
            // The block size comes along, since a new rate usually brings a new quantum.
            requestedBlock.store (numSamples, std::memory_order_relaxed);
            requestedRate.store (juce::roundToInt (cycleRate), std::memory_order_release);
            return silentAtZero();
        }

        if (numSamples != lastCycleBlock)
        {
            if (lastCycleBlock != 0)
                quantumChanges.fetch_add (1, std::memory_order_relaxed);

            lastCycleBlock = numSamples;
            lastBlockForReaders.store (numSamples, std::memory_order_relaxed);
            stableSamples = 0;
        }

        // The graph only ever runs blocks of the size it is primed for (see Engine.h), and only
        // as many as fit in `io` (maxBlock samples): the guard. A cycle padded past its end, or
        // longer than it, runs the whole blocks that fit, fades them out and is silent after
        // them, until service() re-prepares (or primes) the graph for its size.
        const auto block = primedBlock;
        const auto room = io.getNumSamples();
        const bool fits = numSamples % block == 0;
        const auto padded = ((juce::int64) numSamples + block - 1) / block * block;
        const bool hasRoom = padded <= room;

        if (! fits || ! hasRoom)
        {
            requestedPrime.store (numSamples, std::memory_order_release);

            if (fadePosition == 0)
                return silentAtZero();   // until service() primes the new size
        }
        else if (numSamples / block >= upwardPrimeRatio)
        {
            // Many small blocks per cycle, for upwardPrimeSeconds: ask for a prime at this size.
            stableSamples += numSamples;

            if (stableSamples >= upwardPrimeAfter)
                requestedPrime.store (numSamples, std::memory_order_release);
        }

        juce::ScopedNoDenormals noDenormals;

        // `run`: the samples the graph runs, whole blocks, at most `room`; `heard`: the cycle's
        // samples among them. Only a cycle without room has heard < numSamples.
        const auto run = hasRoom ? (int) padded : room / block * block;
        const auto blocks = run / block;
        const auto heard = juce::jmin (numSamples, run);

        for (int c = 0; c < 2; ++c)
        {
            auto* dst = io.getWritePointer (c);

            // No NaN or infinity reaches a plugin, where it could stick in a filter's state.
            if (in != nullptr && in[c] != nullptr)
                samples::copyFinite (dst, in[c], heard);
            else
                juce::FloatVectorOperations::clear (dst, heard);

            if (run > heard)
                juce::FloatVectorOperations::clear (dst + heard, run - heard);
        }

        // The Mine channel: this speaker's own channel of the stream, by its role.
        {
            auto* mine = io.getWritePointer (2);
            const auto* fl = io.getReadPointer (0);
            const auto* fr = io.getReadPointer (1);

            switch (role)
            {
                case Role::left:   juce::FloatVectorOperations::copy (mine, fl, run); break;
                case Role::right:  juce::FloatVectorOperations::copy (mine, fr, run); break;
                case Role::mono:
                    juce::FloatVectorOperations::add (mine, fl, fr, run);
                    juce::FloatVectorOperations::multiply (mine, 0.5f, run);
                    break;
            }
        }

        const auto graphStart = nowNs();

        for (int b = 0; b < blocks; ++b)
        {
            juce::AudioBuffer<float> chunk (io.getArrayOfWritePointers(), graphChannels, b * block, block);
            graph->processBlock (chunk, midi);
            midi.clear();
        }

        // The timing, while this thread holds the graph and its nodes.
        cycleTiming.graphNs = (juce::uint32) juce::jlimit ((juce::int64) 0, (juce::int64) 0xffffffff, nowNs() - graphStart);
        cycleTiming.graphRan = true;
        cycleTiming.graphSerial = graphSerial;
        cycleTiming.numNodes = (juce::uint16) timedNodes.size();

        for (size_t i = 0; i < timedNodes.size(); ++i)
            cycleTiming.nodeNs[i] = (juce::uint32) juce::jlimit ((juce::int64) 0, (juce::int64) 0xffffffff, timedNodes[i]->takeCycleNs());

        auto* woofer  = io.getWritePointer (0);
        auto* tweeter = io.getWritePointer (1);

        if (! gateOpen)
        {
            // Nobody hears this cycle (or not all of it): silent, and the fade-in, when it comes,
            // starts from 0.
            juce::FloatVectorOperations::clear (woofer, heard);
            juce::FloatVectorOperations::clear (tweeter, heard);
            fadePosition = 0;
        }
        else if (! fits || ! hasRoom)
        {
            // The real samples of a padded cycle (or those of a cycle without room that ran)
            // fade out within it; then silence until the graph is primed at the new size.
            const auto from = (float) fadePosition / (float) fadeLength;

            for (int i = 0; i < heard; ++i)
            {
                const auto gain = from * (float) (heard - 1 - i) / (float) heard;
                woofer[i]  *= gain;
                tweeter[i] *= gain;
            }

            fadePosition = 0;
        }
        else if (fadingIn && fadePosition >= fadeLength)
        {
            // full level
        }
        else if (! fadingIn && fadePosition <= 0)
        {
            juce::FloatVectorOperations::clear (woofer, heard);
            juce::FloatVectorOperations::clear (tweeter, heard);
        }
        else
        {
            const auto step = 1.0f / (float) fadeLength;

            for (int i = 0; i < heard; ++i)
            {
                fadePosition = juce::jlimit (0, fadeLength, fadePosition + (fadingIn ? 1 : -1));
                const auto gain = (float) fadePosition * step;
                woofer[i]  *= gain;
                tweeter[i] *= gain;
            }
        }

        // The output limits of device.json: constants, read here only. The gain, the tweeter
        // muted, then the cap at the ceiling (the gain), whatever the graph did.
        if (outputCeiling <= 0.0f)
        {
            juce::FloatVectorOperations::clear (woofer, heard);
            juce::FloatVectorOperations::clear (tweeter, heard);
        }
        else if (outputGain < 1.0f)
        {
            juce::FloatVectorOperations::multiply (woofer, outputGain, heard);

            if (! tweeterMuted)
                juce::FloatVectorOperations::multiply (tweeter, outputGain, heard);
        }

        if (tweeterMuted)
            juce::FloatVectorOperations::clear (tweeter, heard);   // exact zeros

        samples::cap (woofer, heard, outputCeiling);
        samples::cap (tweeter, heard, outputCeiling);

        if (! fadingIn && fadePosition == 0)
            fadedSerial.store (serial, std::memory_order_release);

        audible.store (fadePosition > 0, std::memory_order_relaxed);

        if (out != nullptr)
        {
            // A cycle without room: silent after what ran.
            for (int c = 0; c < 2; ++c)
            {
                if (out[c] == nullptr)
                    continue;

                juce::FloatVectorOperations::copy (out[c], c == 0 ? woofer : tweeter, heard);

                if (numSamples > heard)
                    juce::FloatVectorOperations::clear (out[c] + heard, numSamples - heard);
            }
        }
    }
}
