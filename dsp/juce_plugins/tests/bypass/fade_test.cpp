// shared/bypass/LevelMatchedBypass.h on its own (run.sh builds and runs it): the dry path's delay
// and the fade between the processed and the bypassed output, in blocks of several sizes. What
// the speakerd renders of test_bypass.py cannot do: change the bypass while audio plays.
#include "LevelMatchedBypass.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <vector>

namespace
{
int failures = 0;

void check (bool ok, const char* what, int at)
{
    if (! ok)
    {
        std::printf ("FAIL: %s (sample %d)\n", what, at);
        ++failures;
    }
}

// Runs `n` samples through a fade in blocks of `block`, wet = 1, dry = -1 (high band: dry null, so
// silence), bypassed at block k as bypassPerBlock[k] says (its last entry after its end); returns the output.
std::vector<float> run (speaker::BypassFade& fade, int n, int block, const std::vector<bool>& bypassPerBlock, bool silenceDry)
{
    std::vector<float> out;

    for (int start = 0, k = 0; start < n; start += block, ++k)
    {
        const int len = std::min (block, n - start);
        std::vector<float> wet ((size_t) len, 1.0f), dry ((size_t) len, -1.0f);
        float* w[] { wet.data() };
        const float* d[] { silenceDry ? nullptr : dry.data() };
        fade.setBypassed (bypassPerBlock[(size_t) std::min<size_t> ((size_t) k, bypassPerBlock.size() - 1)]);
        fade.mix (w, d, 1, len);
        out.insert (out.end(), wet.begin(), wet.end());
    }

    return out;
}
}

int main()
{
    // The delay: exactly `delay` samples, whatever the blocks.
    for (int block : { 1, 7, 64, 1024 })
    {
        speaker::DryDelay delay;
        delay.prepare (1030);
        std::vector<float> x (5000), y;
        for (size_t i = 0; i < x.size(); ++i)
            x[i] = (float) (i + 1);
        for (size_t start = 0; start < x.size(); start += (size_t) block)
        {
            std::vector<float> chunk (x.begin() + (long) start, x.begin() + (long) std::min (x.size(), start + (size_t) block));
            delay.process (chunk.data(), (int) chunk.size());
            y.insert (y.end(), chunk.begin(), chunk.end());
        }
        for (size_t i = 0; i < y.size(); ++i)
            check (std::fabs (y[i] - (i < 1030 ? 0.0f : x[i - 1030])) < 0.5f, "the delay is 1030 samples", (int) i);
        delay.reset();
        std::vector<float> one { 9.0f };
        delay.process (one.data(), 1);
        check (std::fabs (one[0]) < 0.5f, "reset clears the delay", 0);
    }

    // The fade: 480 samples at 48 kHz and 10 ms.
    for (int block : { 1, 100, 480, 1024 })
    {
        // Bypassed from the first block after prepare: no fade, the dry signal at once.
        speaker::BypassFade fade;
        fade.prepare (48000.0, 0.01);
        auto y = run (fade, 3000, block, { true }, false);
        for (size_t i = 0; i < y.size(); ++i)
            check (std::fabs (y[i] + 1.0f) < 1e-6f, "bypassed from the start: dry at once", (int) i);

        // Processing, then bypassed from sample 2048 (block-aligned to `block`): a linear fade of
        // 480 samples, monotonic, then dry exactly; then processing again: back to wet exactly.
        fade.prepare (48000.0, 0.01);
        const int switchBlock = (2048 + block - 1) / block, back = switchBlock + (3000 + block - 1) / block;
        std::vector<bool> plan ((size_t) back + 4000 / block + 2, false);
        for (int k = switchBlock; k < back; ++k)
            plan[(size_t) k] = true;
        y = run (fade, block * (int) plan.size(), block, plan, false);
        const int s0 = switchBlock * block, s1 = back * block;
        for (int i = 0; i < s0; ++i)
            check (std::fabs (y[(size_t) i] - 1.0f) < 1e-6f, "processing: wet exactly", i);
        for (int i = s0; i < s0 + 480; ++i)
        {
            const float expected = 1.0f - 2.0f * (float) (i - s0 + 1) / 480.0f;
            check (std::fabs (y[(size_t) i] - expected) < 1e-5f, "the fade to the dry signal is linear over 480 samples", i);
        }
        for (int i = s0 + 480; i < s1; ++i)
            check (std::fabs (y[(size_t) i] + 1.0f) < 1e-6f, "bypassed: dry exactly", i);
        for (int i = s1; i < s1 + 480; ++i)
            check (y[(size_t) i] > y[(size_t) i - 1], "the fade back rises", i);
        for (int i = s1 + 480; i < (int) y.size(); ++i)
            check (std::fabs (y[(size_t) i] - 1.0f) < 1e-6f, "processing again: wet exactly", i);

        // A null dry channel: bypassed, silence; and reset() makes the next block snap.
        fade.prepare (48000.0, 0.01);
        y = run (fade, 2000, block, { false, true }, true);
        check (std::fabs (y.back()) < 1e-9f, "a null dry channel is silence when bypassed", (int) y.size() - 1);
        fade.reset();
        y = run (fade, block, block, { false }, true);
        check (std::fabs (y.front() - 1.0f) < 1e-6f, "after reset() the next block takes its state at once", 0);
    }

    // A change while a fade runs reverses it from where it is.
    {
        speaker::BypassFade fade;
        fade.prepare (48000.0, 0.01);
        auto y = run (fade, 1000, 100, { false, true, false }, false);   // 100 samples down, then up
        check (std::fabs (y[199] - (1.0f - 2.0f * 100.0f / 480.0f)) < 1e-5f, "a fade goes down for a block", 199);
        check (std::fabs (y[299] - 1.0f) < 1e-5f, "and back up from there in as many samples", 299);
    }

    if (failures == 0)
        std::printf ("fade_test: all checks passed\n");
    else
        std::printf ("fade_test: %d check(s) FAILED\n", failures);

    return failures == 0 ? 0 : 1;
}
