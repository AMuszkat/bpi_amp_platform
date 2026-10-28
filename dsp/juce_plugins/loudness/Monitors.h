#pragma once

#include <juce_audio_processors/juce_audio_processors.h>

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

    - complete and headless builds: the audio thread, with publish(), at most about 60 times a
      second (the plugin counts samples).
    - control build: whoever has the values (the controller, from the speaker's stream). The
      plugin's getters read the parameters there, so its editor does not change.

    How a host learns of them. JUCE's LV2 wrapper (juce_audio_plugin_client_LV2.cpp) sends the
    host a patch:Set after processBlock() in each run() only for the parameters its
    ParameterStorage was told about through AudioProcessorListener::audioProcessorParameterChanged();
    a bare setValue() tells no listener and never reaches the host. So under that wrapper
    publish() also calls sendValueChangedMessageToListeners(), and the wrapper sends the value
    at the end of the same run(). Under any other wrapper (VST3 in a DAW, or no wrapper at all)
    it only stores the value: nothing there reads monitors through the host, the plugin's own
    editor reads the getters, and JUCE's VST3 wrapper would answer a notification on the audio
    thread with MessageManager::isThisTheMessageThread(), which takes a mutex the message
    thread takes all the time.

    Why from the audio thread. A first version notified from a thread of its own, and that is
    not safe: the wrapper destroys its ParameterStorage (which removes itself from the
    processor's listeners) before the processor, and AudioProcessor's ParameterChangeForwarder
    walks those listeners without a lock, so a notification during cleanup could call into a
    destroyed ParameterStorage. The wrapper never calls releaseResources(), its deactivate() is
    empty and it prepares only once, when it is instantiated, so nothing tells the plugin that
    cleanup is coming. run() and cleanup never overlap, so notifying from inside run() is the
    one point that is always safe, and it is what the wrapper itself does for every parameter
    the host sets (setValueFromHost() calls setValueNotifyingHost() on the audio thread).

    The cost on the audio thread, under the LV2 wrapper and only when a monitor's value moved by
    at least one step of its range (or once a second): the monitor's own listener lock, which
    nothing else ever takes, so it is uncontended (one compare-and-swap in glibc, no syscall);
    a walk of the processor's listeners, which the LV2 wrapper changes only at construction and
    destruction; and the wrapper's ParameterStorage, which sets an atomic flag. Nothing
    allocates. That lock is the named exception to "no lock on the audio thread" in
    dsp/CLAUDE.md.
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

    /** Whether a processor must tell its listeners about its monitors for a host to see them:
        only under JUCE's LV2 wrapper (see above). */
    inline bool hostNeedsNotifying (const juce::AudioProcessor& processor) noexcept
    {
        return processor.wrapperType == juce::AudioProcessor::wrapperType_LV2;
    }

    /** Audio thread: stores a monitor's value, in the parameter's own units. With `notify`
        (hostNeedsNotifying()), also tells the processor's listeners when the stored value moved,
        or always when `refresh` is set, so a host that missed one catches up. */
    inline void publish (juce::RangedAudioParameter& monitor, float value, bool notify, bool refresh)
    {
        const auto normalised = monitor.convertTo0to1 (value);

        // Compared as the parameter stores it, snapped to its range's step: a level that moves
        // by less than a step changes nothing, and notifies nobody.
        const bool changed = ! juce::exactlyEqual (monitor.convertFrom0to1 (normalised),
                                                   monitor.convertFrom0to1 (monitor.getValue()));

        if (changed)
            monitor.setValue (normalised);

        if (notify && (changed || refresh))
            monitor.sendValueChangedMessageToListeners (monitor.getValue());
    }
}
