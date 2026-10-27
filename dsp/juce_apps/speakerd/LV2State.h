#pragma once

#include <juce_audio_processors_headless/juce_audio_processors_headless.h>

namespace speakerd::lv2state
{
    /*  Plugin state across the LV2 URI boundary (docs/remote-plugin-host.md, section 7, finding 1).

        The graph document keeps a node's state in the plugin's own format: what its processor's
        getStateInformation() returns. A hosted LV2 instance speaks LV2 state instead, a preset in
        Turtle whose subject and keys are built from the plugin's URI, and the control, headless
        and complete builds have different URIs. JUCE's LV2 wrapper keeps the plugin's own state
        under one key, <plugin URI>:StateString, as JUCE base64 text. So wrapping means putting
        the plugin's own state under that key of the hosted build's state, and unwrapping means
        reading it back out: the same document state works whichever build is hosted. */

    /** Restores `pluginState` (the plugin's own format) into a hosted LV2 instance of a JUCE
        plugin. Must run on the message thread, while the instance is not processing.

        Checks that the plugin took it, from the plugin's own state before (S0) and after (S1)
        the restore: it fails if S1 is empty, or if the given state differs from S0 and S1
        still equals S0 ("its state did not change"). What this proves is that the state
        changed, not that every value in it arrived. A given state that restates the current
        values in other bytes is reported as not taken. */
    juce::Result restore (juce::AudioPluginInstance& instance, const juce::MemoryBlock& pluginState);

    /** Reads the plugin's own state out of a hosted LV2 instance of a JUCE plugin. */
    juce::Result extract (juce::AudioPluginInstance& instance, juce::MemoryBlock& pluginState);

    /** The text-level steps, for a state saved by any build of the plugin. `lv2State` is what
        the hosted instance's getStateInformation() returned, `pluginUri` its LV2 URI. */
    juce::Result wrap (const juce::String& lv2State, const juce::String& pluginUri,
                       const juce::MemoryBlock& pluginState, juce::String& wrapped);
    juce::Result unwrap (const juce::String& lv2State, const juce::String& pluginUri,
                         juce::MemoryBlock& pluginState);
}
