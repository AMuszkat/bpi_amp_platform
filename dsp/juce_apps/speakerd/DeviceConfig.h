#pragma once

#include <juce_core/juce_core.h>

namespace speakerd
{
    /** Which channel of the stereo stream this speaker plays. */
    enum class Role { left, right, mono };

    juce::String toString (Role);

    /** device.json: what is local to this speaker and never comes from the hub. speakerd only
        reads it. See README.md for the format. */
    struct DeviceConfig
    {
        Role role = Role::mono;

        /** The PipeWire node of the amplifier, and its two input ports. */
        juce::String amplifierNode = "bpi-alsa-playback-pcm";
        juce::String wooferPort    = "playback_FL";
        juce::String tweeterPort   = "playback_FR";

        /** Reads device.json. On failure, returns the defaults above and describes the problem
            in `error`. */
        static DeviceConfig load (const juce::File&, juce::String& error);
    };
}
