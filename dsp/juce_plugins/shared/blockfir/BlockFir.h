// A direct-form FIR filter that computes 16 outputs per pass over its taps: the FIRs of
// firConv and crossover, a shared source folder (dsp/juce_plugins/README.md, "Shared source
// folders").
//
// It computes what juce::dsp::FIR::Filter computes, y[i] = sum over k of h[k] x[i - k], with each
// output's taps added in the same order, tap 0 first, but reads each tap once per 16 outputs
// instead of once per output, from a linear history instead of a ring, with GCC vector types:
// four accumulators of four floats, one lane per output. Each output is computed alone, in
// its own lane, so the result does not depend on how the signal is cut into blocks.
//
// Rounding. The kernel is `a += x * h` on vectors, compiled with the compiler's default
// contraction (-ffp-contract=fast, GCC's default for C++): where the target has a fused
// multiply-add (aarch64), every tap is one fused multiply-add; where it has none (x86_64
// without -mfma, what the plugins build for), every tap is a rounded multiply and a rounded add.
// The last outputs of a block, fewer than 16, go through the same kernel, over a zero-padded
// end of the history, so that they round exactly as the others do: a plain scalar loop would
// be vectorised differently (GCC 12 at -O3 turns it into a vector multiply followed by in-order
// scalar adds, unfused). On x86_64 the output is bit-identical to FIR::Filter's; on aarch64
// FIR::Filter mixes fused and unfused taps, and this differs from it by less than -120 dB re
// the output's peak (dsp/docs/juce-knowledge/dsp/fir-filter-rounding.md). The test is
// dsp/juce_plugins/tests/blockfir.
//
// Real time: prepare() allocates and must be called off the audio thread (prepareToPlay);
// process() and reset() never allocate, lock or make a syscall. A block longer than the
// prepared size is processed in chunks of the prepared size.

#pragma once

#include <algorithm>
#include <cstddef>
#include <cstring>
#include <vector>

namespace speaker
{

class BlockFir
{
public:
    // Copies the taps and sizes the history for blocks of up to maxBlockSize samples (at
    // least 1), cleared. Allocates.
    void prepare (const float* newTaps, int numTaps, int maxBlockSize)
    {
        m = (size_t) std::max (numTaps, 0);
        chunk = (size_t) std::max (maxBlockSize, 1);
        taps.assign (newTaps, newTaps + m);
        // The m - 1 previous inputs, the block, and the pass after the block's last full one
        // reads up to 15 samples past its end.
        history.assign (m == 0 ? 0 : (m - 1) + chunk + (lanes - 1), 0.0f);
    }

    // Clears the history: the next output is as if every earlier input had been 0.
    void reset() noexcept
    {
        std::fill (history.begin(), history.end(), 0.0f);
    }

    // Filters numSamples samples of in into out; in and out may be the same buffer (they must
    // not overlap otherwise).
    void process (const float* in, float* out, int numSamples) noexcept
    {
        const auto n = (size_t) std::max (numSamples, 0);

        if (m == 0)
        {
            std::fill (out, out + n, 0.0f);
            return;
        }

        for (size_t start = 0; start < n; start += chunk)
            processChunk (in + start, out + start, std::min (chunk, n - start));
    }

    int getNumTaps() const noexcept        { return (int) m; }
    int getMaxBlockSize() const noexcept   { return (int) chunk; }

private:
    typedef float Vec __attribute__ ((vector_size (16)));
    static constexpr size_t vecSize = 4;              // floats in a Vec
    static constexpr size_t lanes = 4 * vecSize;      // outputs per pass

    std::vector<float> taps;
    std::vector<float> history;   // the m - 1 previous inputs, then the current chunk
    size_t m = 0, chunk = 1;

    static Vec load (const float* p) noexcept
    {
        Vec v;
        std::memcpy (&v, p, sizeof (v));
        return v;
    }

    // Outputs first .. first + 15 of the chunk: lane j of acc[q] is output first + 4q + j,
    // its taps added in ascending order.
    void pass (size_t first, Vec (&acc)[4]) const noexcept
    {
        const float* h = taps.data();
        // The input x[first - k] of output `first` for tap k is history[m - 1 + first - k].
        const float* x = history.data() + (m - 1) + first;
        Vec a0 {}, a1 {}, a2 {}, a3 {};

        for (size_t k = 0; k < m; ++k)
        {
            const Vec hk = { h[k], h[k], h[k], h[k] };
            const float* p = x - k;
            a0 += load (p) * hk;
            a1 += load (p + 4) * hk;
            a2 += load (p + 8) * hk;
            a3 += load (p + 12) * hk;
        }

        acc[0] = a0;
        acc[1] = a1;
        acc[2] = a2;
        acc[3] = a3;
    }

    void processChunk (const float* in, float* out, size_t n) noexcept
    {
        float* x = history.data() + (m - 1);
        std::memcpy (x, in, n * sizeof (float));   // in is read in full before out is written

        Vec acc[4];
        size_t first = 0;

        for (; first + lanes <= n; first += lanes)
        {
            pass (first, acc);
            std::memcpy (out + first, acc, sizeof (acc));
        }

        if (first < n)
        {
            // The same kernel over a zero-padded end: the lanes past n compute outputs that are
            // thrown away (an output depends only on inputs before it, so the padding changes
            // none of the n kept).
            std::fill (x + n, x + first + lanes, 0.0f);
            pass (first, acc);
            std::memcpy (out + first, acc, (n - first) * sizeof (float));
        }

        // The last m - 1 inputs become the history of the next chunk.
        std::memmove (history.data(), history.data() + n, (m - 1) * sizeof (float));
    }
};

} // namespace speaker
