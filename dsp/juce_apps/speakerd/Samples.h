#pragma once

#include <cstdint>
#include <cstring>

namespace speakerd::samples
{
    /*  The absolute caps on what speakerd sends to an amplifier, whatever a plugin or a graph
        does. Scalar loops on purpose: they are cheap next to the plugins, allocate nothing, and
        the finiteness test reads the exponent bits, so it holds whatever the optimisation flags
        (with -ffinite-math-only, std::isfinite may be folded to true). */

    /** False for NaN and for both infinities: their exponent bits are all ones. */
    inline bool isFinite (float x) noexcept
    {
        std::uint32_t bits;
        std::memcpy (&bits, &x, sizeof bits);
        return (bits & 0x7f800000u) != 0x7f800000u;
    }

    /** x, or 0 if it is not finite, clamped to [-limit, limit]. */
    inline float capped (float x, float limit) noexcept
    {
        if (! isFinite (x))
            return 0.0f;

        return x > limit ? limit : (x < -limit ? -limit : x);
    }

    /** Copies `n` samples, turning NaN and infinity into 0. */
    inline void copyFinite (float* dst, const float* src, int n) noexcept
    {
        for (int i = 0; i < n; ++i)
            dst[i] = isFinite (src[i]) ? src[i] : 0.0f;
    }

    /** In place: NaN and infinity become 0, everything else is clamped to [-limit, limit]. */
    inline void cap (float* x, int n, float limit) noexcept
    {
        for (int i = 0; i < n; ++i)
            x[i] = capped (x[i], limit);
    }
}
