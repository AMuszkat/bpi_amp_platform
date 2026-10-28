#pragma once

#include <juce_core/juce_core.h>

#include <optional>

namespace speakerd
{
    /** Which channel of the stereo stream this speaker plays. */
    enum class Role { left, right, mono };

    juce::String toString (Role);

    /** device.json: what is local to this speaker and never comes from the hub. speakerd only
        reads it. See README.md for the format. */
    struct DeviceConfig
    {
        /** "limits": what a deploy caps (docs/autonomous-development.md, section 2). A fixed
            gain on both amplifier outputs, applied after the graph (passthrough included) and
            before the absolute cap, and the tweeter muted (exact zeros) or not. */
        struct Limits
        {
            double gainDb = 0.0;        // at most 0
            bool tweeterMuted = false;

            juce::String describe() const;   // "output at -20 dB, tweeter muted"
        };

        /** The limits of a speaker whose device.json is missing or invalid: those of a deploy. */
        static constexpr Limits failSafeLimits { -20.0, true };

        Role role = Role::mono;

        /** The PipeWire node of the amplifier, and its two input ports. */
        juce::String amplifierNode = "bpi-alsa-playback-pcm";
        juce::String wooferPort    = "playback_FL";
        juce::String tweeterPort   = "playback_FR";

        /** None when device.json has no "limits". */
        std::optional<Limits> limits;

        /** Reads device.json. On failure (a missing file, or anything in it invalid, its
            "limits" included) returns the defaults above with failSafeLimits, and describes the
            problem in `error`. */
        static DeviceConfig load (const juce::File&, juce::String& error);

        /** The defaults, limited by failSafeLimits. */
        static DeviceConfig failSafe();
    };
}
