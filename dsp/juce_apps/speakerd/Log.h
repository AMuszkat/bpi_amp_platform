#pragma once

#include <juce_core/juce_core.h>

#include <cstdio>

namespace speakerd
{
    /** Writes one line to stderr as "speakerd: <line>". The lines a test may wait for are listed
        in README.md; keep their wording stable. Never call this from the audio thread. */
    inline void log (const juce::String& line)
    {
        std::fprintf (stderr, "speakerd: %s\n", line.toRawUTF8());
        std::fflush (stderr);
    }
}
