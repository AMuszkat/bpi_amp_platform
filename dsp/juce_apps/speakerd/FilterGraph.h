#pragma once

#include "DeviceConfig.h"

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
          FL, 1 = FR);
        - "Audio Output" becomes the output node, uid 2. Its channel i reached the amplifier's
          position i (0 = FL, port playback_FL; 1 = FR, playback_FR) through AudioPluginHost's
          "ALSA Playback"; speakerd sends document channel 0 to device.json's woofer port and 1
          to its tweeter port. With `device`, session channel i goes to the document channel of
          whichever of the two drives that port; without, playback_FL is taken as the woofer;
        - an LV2 node becomes a plugin node, uids from 16 in the session's order, its
          identifier the URI. Its STATE is the LV2 state the host saved (lilv's Turtle, with
          the plugin's own state under <URI>:StateString); it is unwrapped into the plugin's own
          state, the inverse of what speakerd does when it loads one (LV2State.h);
        - "MIDI Input" and "MIDI Output", which every new AudioPluginHost session has, are left
          out when nothing is connected to them.
        Anything else fails: another plugin format, an internal plugin other than those four, a
        MIDI connection, a channel the stream or the amplifier does not have, a disabled bus, an
        attribute that is not what AudioPluginHost writes. So does what is unsafe on a speaker,
        unless allowUnsafeRouting: the stream straight to the tweeter, and a control build (no
        DSP: it passes the stream through at full level). With preferHeadless, a complete or a
        control build's URI becomes its `_headless` sibling's when `formats` finds that plugin. */
    struct ImportOptions
    {
        bool preferHeadless = false;
        bool allowUnsafeRouting = false;
        juce::AudioPluginFormatManager* formats = nullptr;   // needed for preferHeadless

        /** The speaker's device.json, for which output channel is the woofer; null: playback_FL. */
        const DeviceConfig* device = nullptr;

        /** Called with each line of what was mapped, and each warning. */
        std::function<void (const juce::String&)> note;
    };

    /** The largest session file read. */
    constexpr juce::int64 maxSessionBytes = 64 * 1024 * 1024;

    /** Reads `session` and returns the document as JSON in `document`, or why not. */
    juce::Result import (const juce::File& session, const ImportOptions&, juce::String& document);
}
