#pragma once

#include <juce_core/juce_core.h>

namespace speakerd
{
    /** Which channel of the stereo stream this speaker plays. */
    enum class Role { left, right, mono };

    juce::String toString (Role);

    /** device.json: what is local to this speaker and never comes from the hub. speakerd only
        reads it. See README.md for the format.

        It fails closed. Every key it does not know, a key given twice, and a file without
        "limits" leave the output limited the way a deploy limits it (failSafeLimits): a typo
        must never be heard as an unlimited tweeter. */
    struct DeviceConfig
    {
        /** "limits": what a deploy caps (docs/autonomous-development.md, section 2). A fixed
            gain on both amplifier outputs, which is also their ceiling, applied after the graph
            (passthrough included) and before the absolute cap; and the tweeter muted (exact
            zeros) or not. No limit at all is { 0 dB, tweeter on }, written out. */
        struct Limits
        {
            double gainDb = -20.0;      // at most 0
            bool tweeterMuted = true;

            juce::String describe() const;   // "output at -20 dB, tweeter muted"
        };

        /** The limits of a device.json without "limits", or one that is missing or invalid:
            those of a deploy. */
        static constexpr Limits failSafeLimits { -20.0, true };

        /** Where the limits in force came from. */
        enum class LimitsFrom
        {
            file,           // device.json's "limits"
            notInFile,      // device.json is valid and has no "limits": failSafeLimits
            fileNotRead     // device.json is missing or invalid: failSafeLimits
        };

        Role role = Role::mono;

        /** The PipeWire node of the amplifier, and its two input ports. */
        juce::String amplifierNode = "bpi-alsa-playback-pcm";
        juce::String wooferPort    = "playback_FL";
        juce::String tweeterPort   = "playback_FR";

        Limits limits = failSafeLimits;
        LimitsFrom limitsFrom = LimitsFrom::fileNotRead;

        /** "output at -20 dB, tweeter muted", and why, when they are the fail-safe:
            " (no limits in device.json: the fail-safe)", " (device.json not read: the fail-safe)". */
        juce::String describeLimits() const;

        /** Reads device.json. On failure (a missing file, or anything in it invalid: an unknown
            or repeated key, an invalid "limits", the woofer and the tweeter on one port)
            returns the defaults above, limited by failSafeLimits, and describes the problem in
            `error`. */
        static DeviceConfig load (const juce::File&, juce::String& error);

        /** Parses the text of a device.json, with the same checks. */
        static juce::Result parse (const juce::String& text, DeviceConfig& out);
    };
}
