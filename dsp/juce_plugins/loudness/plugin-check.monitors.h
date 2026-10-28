// The getters through which loudness's editor reads its monitors, for plugin-check's in-process
// check (dsp/juce_apps/plugin-check/in-process). Not part of any plugin build.
//
// For each monitor: its parameter ID, the getter that reads it, and how far two builds may
// disagree on it for the same input (the hosted headless build against the complete build).
#pragma once

#include "PluginProcessor.h"

namespace pluginCheck
{
    struct MonitorGetter
    {
        const char* id;
        float (*read) (juce::AudioProcessor&);
        float tolerance;
    };

    inline const MonitorGetter monitorGetters[]
    {
        { LoudnessIDs::monLevelDb,
          [] (juce::AudioProcessor& p) { return dynamic_cast<LoudnessProcessor&> (p).getLevelDb(); },
          1.0f },
        { LoudnessIDs::monActiveSetting,
          [] (juce::AudioProcessor& p) { return (float) dynamic_cast<LoudnessProcessor&> (p).getActiveSetting(); },
          0.0f },
    };
}
