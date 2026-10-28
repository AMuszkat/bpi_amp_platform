#pragma once

#include <juce_audio_processors_headless/juce_audio_processors_headless.h>

#include <chrono>
#include <memory>

namespace speakerd
{
    /** The monotonic clock in nanoseconds, for timing on the audio thread: the one syscall
        speakerd may make there, a named exception (dsp/CLAUDE.md). std::chrono's steady_clock
        is clock_gettime (CLOCK_MONOTONIC). glibc answers it from the vDSO when the kernel offers
        one (__vdso_clock_gettime on x86_64, __kernel_clock_gettime on aarch64): no syscall, a
        few tens of nanoseconds. On a Cortex-A73 whose kernel works around ARM erratum 858921
        (CONFIG_ARM64_ERRATUM_858921, on by default) the vDSO clock is off and every read is a
        real syscall: short, bounded and non-blocking, about a microsecond. speakerd logs what a
        read costs at start, `timing: a clock read costs <x> ns`, which tells which (README,
        Timing). */
    inline juce::int64 nowNs() noexcept
    {
        return (juce::int64) std::chrono::duration_cast<std::chrono::nanoseconds> (
                   std::chrono::steady_clock::now().time_since_epoch()).count();
    }

    /*  A hosted plugin, as a node of speakerd's graph, with the time its processing takes.

        AudioProcessorGraph has no hook around a node's processBlock, so every plugin node is
        this wrapper around the hosted instance. It forwards exactly what the graph asks of a
        node, so the graph runs the plugin as it did without it:

        - the buses: the instance's, as they are when it is wrapped (speakerd enables them all
          before, and changes none after);
        - prepare: the processing precision, the rate and block size, prepareToPlay, and then the
          instance's latency, which is when the graph reads it (it re-reads a node's latency
          only when it rebuilds);
        - process: the instance's own callback lock around its processBlock (the graph takes
          this wrapper's), the play head, and processBlock, or processBlockBypassed;
        - bypass: the instance's bypass parameter, so that bypassing the node sets it, as the
          graph does for a node whose processor has one.

        The time: two clock reads around the instance's processBlock, added up per audio cycle
        in a member only the thread that runs the graph touches. The Engine takes it at the end
        of each cycle (takeCycleNs) and starts every graph it installs from zero.

        Everything that is the plugin's own (its parameters, its state, its name) is the
        instance's: hostedProcessor() gives it. */
    class TimedNode final : public juce::AudioProcessor
    {
    public:
        TimedNode (std::unique_ptr<juce::AudioPluginInstance> instance, juce::uint32 uid);

        juce::AudioPluginInstance& getInstance() noexcept       { return *instance; }
        juce::uint32 getUid() const noexcept                     { return uid; }

        /** The nanoseconds spent in the instance since the last call. The thread that runs the
            graph, or one that holds it. */
        juce::int64 takeCycleNs() noexcept                       { return std::exchange (cycleNs, 0); }

        // AudioProcessor
        const juce::String getName() const override              { return instance->getName(); }
        void prepareToPlay (double sampleRate, int maximumExpectedSamplesPerBlock) override;
        void releaseResources() override                         { instance->releaseResources(); }
        void reset() override                                    { instance->reset(); }

        using AudioProcessor::processBlock;
        using AudioProcessor::processBlockBypassed;
        void processBlock (juce::AudioBuffer<float>&, juce::MidiBuffer&) override;
        void processBlockBypassed (juce::AudioBuffer<float>&, juce::MidiBuffer&) override;

        juce::AudioProcessorParameter* getBypassParameter() const override   { return instance->getBypassParameter(); }
        bool isBusesLayoutSupported (const BusesLayout& layout) const override { return layout == getBusesLayout(); }
        void setNonRealtime (bool isNonRealtime) noexcept override;

        double getTailLengthSeconds() const override             { return instance->getTailLengthSeconds(); }
        bool acceptsMidi() const override                        { return instance->acceptsMidi(); }
        bool producesMidi() const override                       { return instance->producesMidi(); }
        bool isMidiEffect() const override                       { return instance->isMidiEffect(); }
        juce::AudioProcessorEditor* createEditor() override      { return nullptr; }
        bool hasEditor() const override                          { return false; }
        int getNumPrograms() override                            { return instance->getNumPrograms(); }
        int getCurrentProgram() override                         { return instance->getCurrentProgram(); }
        void setCurrentProgram (int index) override              { instance->setCurrentProgram (index); }
        const juce::String getProgramName (int index) override   { return instance->getProgramName (index); }
        void changeProgramName (int index, const juce::String& name) override { instance->changeProgramName (index, name); }
        void getStateInformation (juce::MemoryBlock& block) override         { instance->getStateInformation (block); }
        void setStateInformation (const void* data, int size) override       { instance->setStateInformation (data, size); }

    private:
        static BusesProperties busesOf (juce::AudioProcessor&);

        std::unique_ptr<juce::AudioPluginInstance> instance;
        const juce::uint32 uid;
        juce::int64 cycleNs = 0;

        JUCE_DECLARE_NON_COPYABLE (TimedNode)
    };

    /** The processor that is the plugin itself: a TimedNode's instance, or the processor. */
    juce::AudioProcessor& hostedProcessor (juce::AudioProcessor&);
}
