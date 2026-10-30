// The reference FIR of the block FIR's test (README.md): arithmetic written out, compiled so
// that the compiler can neither fuse nor reorder it (CMakeLists.txt).

#pragma once

#include <cstddef>

namespace reference
{
    // y[i] = h[0] x[i] + h[1] x[i - 1] + ... + h[m - 1] x[i - m + 1] for i in 0 .. n - 1, where
    // x[j] = padded[m - 1 + j] (so padded starts with the m - 1 inputs before the first, zeros
    // for a fresh filter). Each output starts from 0 and adds its taps in ascending order, tap 0
    // first: with `fused`, each tap is one std::fma (one rounding); without, its product is
    // rounded to float and then added (two roundings).
    void fir (const float* h, size_t m, const float* padded, float* y, size_t n, bool fused);
}
