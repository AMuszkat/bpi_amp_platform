// The test of the shared block FIR, dsp/juce_plugins/shared/blockfir/BlockFir.h (README.md):
//
//   (a) bit for bit against reference::fir (Reference.cpp), whose arithmetic is written out:
//       fused where the kernel fuses (where the target has a fused multiply-add), unfused
//       where it does not; in blocks of the prepared size, of random sizes, longer than the
//       prepared size, in place, and after reset();
//   (b) against juce::dsp::FIR::Filter, which firConv and crossover ran before: the worst
//       difference below -120 dB re the output's peak;
//
// for the four sets of taps the plugins compile in, compiled with the plugins' flags.
//
//     blockfir_test            the checks; exit status 0 when every one passes
//     blockfir_test --time     the cost of each filter in blocks of 1024 (no check)

#include <juce_dsp/juce_dsp.h>

#include "BlockFir.h"
#include "Reference.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <iterator>
#include <string>
#include <vector>

namespace coefs
{
#include "filters.hpp"            // firConv: HPreLpTaps (61 taps), InvFilter (2001)
#include "crossover_coefs.hpp"    // crossover: lp, hp (259 each)
}

#if defined (__aarch64__) && ! defined (__FP_FAST_FMAF)
 #error "aarch64 without a fast fmaf: the block FIR would not fuse, which this test does not expect"
#endif

namespace
{
// Where the target has a fused multiply-add, GCC contracts the kernel's `a += x * h` into one
// (-ffp-contract=fast, its default for C++); __FP_FAST_FMAF says the target has one.
#if defined (__FP_FAST_FMAF)
constexpr bool kernelFuses = true;
#else
constexpr bool kernelFuses = false;
#endif

#if defined (__aarch64__)
constexpr const char* architecture = "aarch64";
#elif defined (__x86_64__)
constexpr const char* architecture = "x86_64";
#else
constexpr const char* architecture = "another architecture";
#endif

struct TapSet
{
    const char* name;
    const float* taps;
    int numTaps;
};

const TapSet tapSets[] = {
    { "firConv pre-LP (61 taps)",    coefs::HPreLpTaps, (int) std::size (coefs::HPreLpTaps) },
    { "firConv inverse (2001 taps)", coefs::InvFilter,  (int) std::size (coefs::InvFilter) },
    { "crossover low (259 taps)",    coefs::lp,         (int) std::size (coefs::lp) },
    { "crossover high (259 taps)",   coefs::hp,         (int) std::size (coefs::hp) },
};

constexpr int quantum = 1024;          // the speaker's
constexpr size_t signalLength = 32 * quantum;
constexpr double limitDb = -120.0;     // (b)

uint32_t nextRandom (uint32_t& state)
{
    state = state * 1664525u + 1013904223u;
    return state >> 8;   // 24 bits
}

// Uniform noise in [-1, 1).
std::vector<float> noise (size_t n, uint32_t seed)
{
    std::vector<float> v (n);
    for (auto& x : v)
        x = (float) ((double) nextRandom (seed) / 8388608.0 - 1.0);
    return v;
}

// The edge cases first (one sample, around a pass of 16, a whole quantum), then random sizes.
std::vector<int> randomBlocks (int maxSize, uint32_t seed)
{
    std::vector<int> sizes { 1, 2, 15, 16, 17, 31, 32, 33, 1023, 1024 };
    for (int i = 0; i < 400; ++i)
        sizes.push_back (1 + (int) (nextRandom (seed) % (uint32_t) maxSize));
    return sizes;
}

// The block FIR over the whole input, prepared for `prepared`, in blocks of `sizes` in turn.
std::vector<float> runBlockFir (speaker::BlockFir& f, const std::vector<float>& in,
                                const std::vector<int>& sizes, bool inPlace)
{
    std::vector<float> out (in.size());
    if (inPlace)
        out = in;

    size_t pos = 0, block = 0;
    while (pos < in.size())
    {
        const auto n = std::min ((size_t) sizes[block++ % sizes.size()], in.size() - pos);
        f.process (inPlace ? out.data() + pos : in.data() + pos, out.data() + pos, (int) n);
        pos += n;
    }
    return out;
}

std::vector<float> runBlockFir (const TapSet& t, const std::vector<float>& in, int prepared,
                                const std::vector<int>& sizes, bool inPlace)
{
    speaker::BlockFir f;
    f.prepare (t.taps, t.numTaps, prepared);
    return runBlockFir (f, in, sizes, inPlace);
}

std::vector<float> runReference (const TapSet& t, const std::vector<float>& in)
{
    std::vector<float> padded ((size_t) t.numTaps - 1, 0.0f);
    padded.insert (padded.end(), in.begin(), in.end());
    std::vector<float> out (in.size());
    reference::fir (t.taps, (size_t) t.numTaps, padded.data(), out.data(), in.size(), kernelFuses);
    return out;
}

void processJuce (juce::dsp::FIR::Filter<float>& f, std::vector<float>& data)
{
    for (size_t pos = 0; pos < data.size(); pos += quantum)
    {
        float* channels[] = { data.data() + pos };
        juce::dsp::AudioBlock<float> block (channels, 1, std::min ((size_t) quantum, data.size() - pos));
        f.process (juce::dsp::ProcessContextReplacing<float> (block));
    }
}

void prepareJuce (juce::dsp::FIR::Filter<float>& f, const TapSet& t)
{
    f.coefficients = new juce::dsp::FIR::Coefficients<float> (t.taps, (size_t) t.numTaps);
    f.prepare ({ 48000.0, (juce::uint32) quantum, 1 });
}

std::vector<float> runJuce (const TapSet& t, const std::vector<float>& in)
{
    juce::dsp::FIR::Filter<float> f;
    prepareJuce (f, t);
    std::vector<float> out = in;
    processJuce (f, out);
    return out;
}

struct Difference
{
    size_t samples = 0;    // that differ in any bit
    double worst = 0.0;    // the largest absolute difference
};

Difference compare (const std::vector<float>& a, const std::vector<float>& b)
{
    Difference d;
    for (size_t i = 0; i < a.size(); ++i)
    {
        uint32_t ua, ub;
        std::memcpy (&ua, &a[i], sizeof (ua));
        std::memcpy (&ub, &b[i], sizeof (ub));
        if (ua != ub)
        {
            ++d.samples;
            d.worst = std::max (d.worst, std::abs ((double) a[i] - (double) b[i]));
        }
    }
    return d;
}

double peakOf (const std::vector<float>& v)
{
    double peak = 0.0;
    for (float x : v)
        peak = std::max (peak, std::abs ((double) x));
    return peak;
}

int runChecks()
{
    // As in the plugins' processBlock: denormals flushed (FTZ, and DAZ on x86_64).
    juce::ScopedNoDenormals noDenormals;

    const auto in = noise (signalLength, 12345);
    const auto otherSignal = noise (5000, 999);
    const auto random1024 = randomBlocks (quantum, 777);
    const auto random100 = randomBlocks (100, 4242);   // up to the prepared size of 100
    const std::vector<int> fixed { quantum };

    std::printf ("blockfir test: %s, kernel arithmetic: %s; %zu samples of noise per run\n", architecture,
                 kernelFuses ? "one fused multiply-add per tap (reference: std::fma)"
                             : "a rounded multiply and a rounded add per tap (reference: the same, unfused)",
                 signalLength);

    bool passed = true;
    double worstOfAll = -1000.0;

    for (const auto& t : tapSets)
    {
        const auto expected = runReference (t, in);
        bool aPassed = true;
        std::string aLine;

        auto check = [&] (const char* what, const std::vector<float>& got)
        {
            const auto d = compare (got, expected);
            aLine += std::string (aLine.empty() ? "" : ", ") + what + " " + std::to_string (d.samples);
            aPassed = aPassed && d.samples == 0;
        };

        check ("in place, blocks of 1024:", runBlockFir (t, in, quantum, fixed, true));
        check ("random blocks 1..1024:", runBlockFir (t, in, quantum, random1024, false));
        check ("prepared 100, blocks of 1024 (chunked):", runBlockFir (t, in, 100, fixed, true));
        check ("prepared 100, random blocks 1..100:", runBlockFir (t, in, 100, random100, false));
        check ("prepared 7, random blocks 1..1024 (chunked):", runBlockFir (t, in, 7, random1024, false));

        {
            speaker::BlockFir f;
            f.prepare (t.taps, t.numTaps, quantum);
            runBlockFir (f, otherSignal, random1024, false);
            f.reset();
            check ("after reset():", runBlockFir (f, in, fixed, false));
        }

        std::printf ("(a) %-28s samples differing from the reference: %s -> %s\n", t.name, aLine.c_str(),
                     aPassed ? "bit-identical" : "FAILED");

        const auto juceOut = runJuce (t, in);
        const auto d = compare (runBlockFir (t, in, quantum, fixed, true), juceOut);
        const double peak = peakOf (juceOut);
        const double db = d.samples == 0 ? -1000.0 : 20.0 * std::log10 (d.worst / peak);
        const bool bPassed = db < limitDb;
        worstOfAll = std::max (worstOfAll, db);

        char worst[32];
        if (d.samples == 0)
            std::snprintf (worst, sizeof (worst), "none");
        else
            std::snprintf (worst, sizeof (worst), "%.1f dB re peak", db);

        std::printf ("(b) %-28s against juce::dsp::FIR::Filter: %zu/%zu samples differ (%.1f %%), worst %s (limit %.0f dB) -> %s\n",
                     t.name, d.samples, juceOut.size(), 100.0 * (double) d.samples / (double) juceOut.size(), worst,
                     limitDb, bPassed ? "passed" : "FAILED");

        passed = passed && aPassed && bPassed;
    }

    char summary[64];
    if (worstOfAll <= -1000.0)
        std::snprintf (summary, sizeof (summary), "identical to FIR::Filter in every set");
    else
        std::snprintf (summary, sizeof (summary), "worst difference %.1f dB re peak", worstOfAll);

    if (passed)
        std::printf ("blockfir test: PASSED on %s: (a) bit-identical to the reference in every run, (b) %s\n",
                     architecture, summary);
    else
        std::printf ("blockfir test: FAILED on %s\n", architecture);

    return passed ? 0 : 1;
}

// Out of line, as in a plugin's processBlock: inlined into the timing loop below, the kernel
// would compete with it for registers and time slower than it runs in the plugins.
__attribute__ ((noinline)) void processInBlocks (speaker::BlockFir& f, std::vector<float>& data)
{
    for (size_t pos = 0; pos < data.size(); pos += quantum)
        f.process (data.data() + pos, data.data() + pos, quantum);
}

// % of real time at 48 kHz in blocks of 1024, the best of three runs over 4 s of noise.
int runTiming()
{
    juce::ScopedNoDenormals noDenormals;
    const size_t n = 48000 * 4 / quantum * quantum;
    const auto in = noise (n, 7);

    auto best = [&] (auto&& process)
    {
        double seconds = 1.0e9;
        for (int r = 0; r < 3; ++r)
        {
            auto data = in;
            const auto start = std::chrono::steady_clock::now();
            process (data);
            seconds = std::min (seconds, std::chrono::duration<double> (std::chrono::steady_clock::now() - start).count());
        }
        return 100.0 * seconds * 48000.0 / (double) n;
    };

    std::printf ("blockfir timing: %s, %% of real time at 48 kHz in blocks of %d, best of 3\n", architecture, quantum);

    for (const auto& t : tapSets)
    {
        const double block = best ([&] (std::vector<float>& data)
        {
            speaker::BlockFir f;
            f.prepare (t.taps, t.numTaps, quantum);
            processInBlocks (f, data);
        });
        const double juceTime = best ([&] (std::vector<float>& data)
        {
            juce::dsp::FIR::Filter<float> f;
            prepareJuce (f, t);
            processJuce (f, data);
        });
        std::printf ("time %-28s block FIR %.3f %%, juce::dsp::FIR::Filter %.3f %% (%.1fx)\n",
                     t.name, block, juceTime, juceTime / block);
    }
    return 0;
}
} // namespace

int main (int argc, char** argv)
{
    if (argc > 1 && std::string (argv[1]) == "--time")
        return runTiming();

    if (argc > 1)
    {
        std::fprintf (stderr, "usage: %s [--time]\n", argv[0]);
        return 2;
    }

    return runChecks();
}
