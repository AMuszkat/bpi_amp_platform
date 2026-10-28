#pragma once

#include <juce_audio_processors/juce_audio_processors.h>

#include <limits>
#include <vector>

//==============================================================================
/** A plugin's monitors: the live values its editor shows, published as read-only parameters
    so that a host on the speaker can read them (docs/protocol.md, "How a plugin publishes a
    monitor").

    A monitor is a parameter whose ID starts with "mon_", in every build (complete, control,
    headless), declared after the plugin's other parameters, not automatable, of category
    output meter, and never part of the saved state. A plugin adds its monitors to the
    processor itself with addParameter(), not through its AudioProcessorValueTreeState: the
    APVTS then never sees them, so they are not in copyState(), and replaceState() cannot set
    them. A state that names one anyway is cleaned with removeFromState().

    Who writes them:

    - complete and headless builds: the audio thread, with write(). That is a plain setValue(),
      which stores an atomic and tells nobody: it does not allocate, lock or make a syscall.
    - control build: whoever has the values (the controller, from the speaker's stream). The
      plugin's getters read the parameters there, so its editor does not change.

    How a host learns of them: JUCE's LV2 wrapper (juce_audio_plugin_client_LV2.cpp) sends the
    host a patch:Set after each run() only for the parameters its ParameterStorage was told
    about through AudioProcessorListener::audioProcessorParameterChanged(), and setValue() tells
    no listener. So the complete and headless builds also run a Notifier: a low-priority
    thread that, about 60 times a second, tells the listeners about every monitor whose value
    moved (and about all of them once a second, so a host that missed one catches up). The
    wrapper then reports it at the end of the next cycle. Nothing is notified from the audio
    thread.
*/
namespace monitors
{
    inline constexpr const char* prefix = "mon_";

    inline bool isMonitorId (const juce::String& parameterId)
    {
        return parameterId.startsWith (prefix);
    }

    /** Removes the monitors from a saved APVTS state: every PARAM child whose id starts with
        "mon_". A state is never allowed to carry a monitor, in or out. */
    inline void removeFromState (juce::ValueTree& state)
    {
        for (int i = state.getNumChildren(); --i >= 0;)
            if (isMonitorId (state.getChild (i).getProperty ("id").toString()))
                state.removeChild (i, nullptr);
    }

    /** The attributes every monitor has. */
    inline juce::AudioParameterFloatAttributes floatAttributes (const juce::String& label)
    {
        return juce::AudioParameterFloatAttributes()
                   .withLabel (label)
                   .withAutomatable (false)
                   .withCategory (juce::AudioProcessorParameter::outputMeter);
    }

    inline juce::AudioParameterIntAttributes intAttributes()
    {
        return juce::AudioParameterIntAttributes()
                   .withAutomatable (false)
                   .withCategory (juce::AudioProcessorParameter::outputMeter);
    }

    /** Audio thread: stores a value, in the parameter's own units, without notifying anyone. */
    inline void write (juce::RangedAudioParameter& monitor, float value) noexcept
    {
        monitor.setValue (monitor.convertTo0to1 (value));
    }

    //==========================================================================
    /** Tells a processor's listeners (the plugin wrapper among them) about monitors that
        moved, from its own low-priority thread. Construct it after the monitors have
        been added; destroy it before the processor goes (the destructor joins the thread). */
    class Notifier final : private juce::Thread
    {
    public:
        explicit Notifier (const juce::Array<juce::AudioProcessorParameter*>& monitorsToWatch)
            : juce::Thread ("monitor notifier"),
              watched (monitorsToWatch.begin(), monitorsToWatch.end()),
              lastSent (watched.size(), std::numeric_limits<float>::quiet_NaN())
        {
            startThread (juce::Thread::Priority::low);
        }

        ~Notifier() override
        {
            stopThread (1000);
        }

    private:
        static constexpr int intervalMs = 16;       // about 60 Hz
        static constexpr int refreshEvery = 60;     // every value again, about once a second

        void run() override
        {
            int tick = 0;

            while (! threadShouldExit())
            {
                const bool refresh = (tick++ % refreshEvery) == 0;

                for (size_t i = 0; i < watched.size(); ++i)
                {
                    const auto value = watched[i]->getValue();

                    // Notifies without writing: the audio thread owns the value, and a
                    // setValueNotifyingHost() here could overwrite a newer one with this one.
                    if (refresh || ! juce::exactlyEqual (value, lastSent[i]))
                    {
                        watched[i]->sendValueChangedMessageToListeners (value);
                        lastSent[i] = value;
                    }
                }

                wait (intervalMs);
            }
        }

        const std::vector<juce::AudioProcessorParameter*> watched;
        std::vector<float> lastSent;

        JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (Notifier)
    };
}
