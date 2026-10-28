#include "TimedNode.h"

namespace speakerd
{
    juce::AudioProcessor::BusesProperties TimedNode::busesOf (juce::AudioProcessor& p)
    {
        BusesProperties props;

        for (const auto isInput : { true, false })
            for (int i = 0; i < p.getBusCount (isInput); ++i)
                if (const auto* bus = p.getBus (isInput, i))
                    props.addBus (isInput, bus->getName(), bus->getCurrentLayout(), bus->isEnabled());

        return props;
    }

    TimedNode::TimedNode (std::unique_ptr<juce::AudioPluginInstance> instanceIn, juce::uint32 uidIn)
        : AudioProcessor (busesOf (*instanceIn)),
          instance (std::move (instanceIn)),
          uid (uidIn)
    {
        setLatencySamples (instance->getLatencySamples());
    }

    void TimedNode::prepareToPlay (double sampleRate, int maximumExpectedSamplesPerBlock)
    {
        // What AudioProcessorGraph does to a node's processor when it prepares it.
        instance->setProcessingPrecision (getProcessingPrecision());
        instance->setRateAndBufferSizeDetails (sampleRate, maximumExpectedSamplesPerBlock);
        instance->prepareToPlay (sampleRate, maximumExpectedSamplesPerBlock);

        // The graph reads a node's latency right after it prepared it.
        setLatencySamples (instance->getLatencySamples());
        cycleNs = 0;
    }

    void TimedNode::processBlock (juce::AudioBuffer<float>& audio, juce::MidiBuffer& midi)
    {
        const auto start = nowNs();
        {
            const juce::ScopedLock lock { instance->getCallbackLock() };
            instance->setPlayHead (getPlayHead());
            instance->processBlock (audio, midi);
        }
        cycleNs += nowNs() - start;
    }

    void TimedNode::processBlockBypassed (juce::AudioBuffer<float>& audio, juce::MidiBuffer& midi)
    {
        const auto start = nowNs();
        {
            const juce::ScopedLock lock { instance->getCallbackLock() };
            instance->setPlayHead (getPlayHead());
            instance->processBlockBypassed (audio, midi);
        }
        cycleNs += nowNs() - start;
    }

    void TimedNode::setNonRealtime (bool isNonRealtime) noexcept
    {
        AudioProcessor::setNonRealtime (isNonRealtime);
        instance->setNonRealtime (isNonRealtime);
    }

    juce::AudioProcessor& hostedProcessor (juce::AudioProcessor& p)
    {
        if (auto* timed = dynamic_cast<TimedNode*> (&p))
            return timed->getInstance();

        return p;
    }
}
