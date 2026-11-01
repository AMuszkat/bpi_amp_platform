// The bypass of a plugin whose processing is a gain stage followed by linear-phase FIRs (firConv,
// crossover), a shared source folder (dsp/juce_plugins/README.md, "Shared source folders").
//
// Bypassed, such a plugin skips its filters only: it outputs its input times its gain, delayed by
// its FIRs' delay, so that a bypass changes neither the level by the gain stage nor the timing
// (a stereo pair with one speaker bypassed stays aligned, and an A/B compares the filters alone).
// The plugin computes both paths every block, the filters too while bypassed, so that both are
// current whenever the bypass changes; a change fades from one to the other over a few ms.
//
//   DryDelay     the dry path's delay line, a ring of exactly the FIRs' delay in samples;
//   BypassFade   the mix of the processed (wet) and the bypassed (dry) output, a linear fade
//                between them when the bypass changes.
//
// Real time: prepare() allocates and must be called off the audio thread (prepareToPlay);
// everything else never allocates, locks or makes a syscall.

#pragma once

#include <algorithm>
#include <cmath>
#include <cstring>
#include <vector>

namespace speaker
{

// Delays a signal by a fixed number of samples, in place, in blocks of any size.
class DryDelay
{
public:
    // A delay of `samples` (0 or more), cleared. Allocates.
    void prepare (int samples)
    {
        ring.assign ((size_t) std::max (samples, 0), 0.0f);
        pos = 0;
    }

    void reset() noexcept
    {
        std::fill (ring.begin(), ring.end(), 0.0f);
        pos = 0;
    }

    int getDelay() const noexcept   { return (int) ring.size(); }

    void process (float* samples, int numSamples) noexcept
    {
        const size_t n = ring.size();

        if (n == 0)
            return;

        for (int i = 0; i < numSamples; ++i)
        {
            const float out = ring[pos];
            ring[pos] = samples[i];
            samples[i] = out;

            if (++pos == n)
                pos = 0;
        }
    }

private:
    std::vector<float> ring;
    size_t pos = 0;
};

// The output of a plugin with a level-matched bypass: the wet signal while processing, the dry
// signal while bypassed, and a linear fade between them over `fadeSeconds` when the bypass changes.
// After prepare() and reset() the first block takes the state it is given at once, without a fade.
class BypassFade
{
public:
    void prepare (double sampleRate, double fadeSeconds) noexcept
    {
        fadeSamples = std::max (1, (int) std::lround (sampleRate * fadeSeconds));
        position = bypassed ? 0 : fadeSamples;
        reset();
    }

    // The next block starts in whatever state it is given, with no fade.
    void reset() noexcept   { snap = true; }

    // The bypass for the next block, before mix(). A change starts a fade, or reverses one.
    void setBypassed (bool shouldBeBypassed) noexcept
    {
        bypassed = shouldBeBypassed;

        if (snap)
        {
            position = bypassed ? 0 : fadeSamples;
            snap = false;
        }
    }

    bool isBypassed() const noexcept   { return bypassed; }

    // The output of `numChannels` channels over `numSamples`, in place in wet[ch]: the wet signal,
    // the dry signal dry[ch], or a fade between them. dry[ch] == nullptr makes that channel's
    // bypassed output silence.
    void mix (float* const* wetChannels, const float* const* dryChannels, int numChannels, int numSamples) noexcept
    {
        const int target = bypassed ? 0 : fadeSamples;
        int i = 0;

        // The fade, sample by sample, every channel with the same weight: position / fadeSamples
        // of the wet signal, the rest of the dry one.
        for (; i < numSamples && position != target; ++i)
        {
            position += position < target ? 1 : -1;
            const float w = (float) position / (float) fadeSamples;

            for (int ch = 0; ch < numChannels; ++ch)
            {
                const float d = dryChannels[ch] != nullptr ? dryChannels[ch][i] : 0.0f;
                wetChannels[ch][i] = d + w * (wetChannels[ch][i] - d);
            }
        }

        // The rest of the block in the steady state: processing leaves the wet signal as it is.
        if (i == numSamples || ! bypassed)
            return;

        for (int ch = 0; ch < numChannels; ++ch)
        {
            if (dryChannels[ch] != nullptr)
                std::memcpy (wetChannels[ch] + i, dryChannels[ch] + i, sizeof (float) * (size_t) (numSamples - i));
            else
                std::memset (wetChannels[ch] + i, 0, sizeof (float) * (size_t) (numSamples - i));
        }
    }

private:
    int fadeSamples = 1;
    int position = 1;        // of the fade: fadeSamples is processing, 0 is bypassed
    bool bypassed = false;
    bool snap = true;
};

}
