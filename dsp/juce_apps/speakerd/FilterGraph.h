#pragma once

#include <juce_audio_processors_headless/juce_audio_processors_headless.h>

#include <functional>

namespace speakerd::filtergraph
{
    /*  AudioPluginHost's session, a .filtergraph file, as a graph document version 1: what
        `speakerd --import-filtergraph` does. The format is what PluginGraph::createXml writes
        (extras/AudioPluginHost/Source/Plugins/PluginGraph.cpp): a <FILTERGRAPH> of <FILTER>s,
        each with a uid, a <PLUGIN> (PluginDescription::createXml), the processor's state as
        <STATE> (MemoryBlock::toBase64Encoding) and its bus <LAYOUT>, and <CONNECTION>s per
        channel between uids.

        The mapping:
        - AudioPluginHost's internal "Audio Input" becomes the input node, uid 1 (channel 0 =
          FL, 1 = FR), and "Audio Output" the output node, uid 2 (channel i = amplifier channel
          i: 0 = woofer, 1 = tweeter);
        - an LV2 node becomes a plugin node, uids from 16 in the session's order, its
          identifier the URI. Its STATE is the LV2 state the host saved (lilv's Turtle, with
          the plugin's own state under <URI>:StateString); it is unwrapped into the plugin's own
          state, the inverse of what speakerd does when it loads one (LV2State.h);
        - "MIDI Input" and "MIDI Output", which every new AudioPluginHost session has, are left
          out when nothing is connected to them.
        Anything else fails: another plugin format, an internal plugin other than those four, a
        MIDI connection, a channel the stream or the amplifier does not have, a disabled bus.
        With preferHeadless, a complete build's URI becomes its `_headless` sibling's when
        `formats` finds that plugin. */
    struct ImportOptions
    {
        bool preferHeadless = false;
        juce::AudioPluginFormatManager* formats = nullptr;   // needed for preferHeadless

        /** Called with each line of what was mapped, and each warning. */
        std::function<void (const juce::String&)> note;
    };

    /** Reads `session` and returns the document as JSON in `document`, or why not. */
    juce::Result import (const juce::File& session, const ImportOptions&, juce::String& document);
}
