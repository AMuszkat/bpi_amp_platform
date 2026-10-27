#pragma once

#include <juce_core/juce_core.h>

#include <cstdio>
#include <mutex>

namespace speakerd
{
    namespace detail
    {
        inline std::mutex logMutex;
        inline bool logClosed = false;    // guarded by logMutex: set by logLast()

        inline void writeLine (const juce::String& line)
        {
            std::fprintf (stderr, "speakerd: %s\n", line.toRawUTF8());
            std::fflush (stderr);
        }
    }

    /** Writes one line to stderr as "speakerd: <line>". The lines a test may wait for are listed
        in README.md; keep their wording stable. The message thread and PipeWire's loop thread
        log; never call this from the audio thread. */
    inline void log (const juce::String& line)
    {
        const std::lock_guard<std::mutex> lock (detail::logMutex);

        if (! detail::logClosed)
            detail::writeLine (line);
    }

    /** Writes the last line of the live mode ("stopping"): nothing is written after it, from
        any thread. */
    inline void logLast (const juce::String& line)
    {
        const std::lock_guard<std::mutex> lock (detail::logMutex);

        if (! detail::logClosed)
            detail::writeLine (line);

        detail::logClosed = true;
    }
}
