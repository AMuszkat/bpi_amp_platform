// Compiled with -ffp-contract=off and without LTO (CMakeLists.txt): the unfused branch stays a
// rounded multiply and a rounded add, the fused one is std::fma, and neither is reordered (no
// -ffast-math: GCC keeps the order of floating-point adds as written).

#include "Reference.h"

#include <cmath>

namespace reference
{
    void fir (const float* h, size_t m, const float* padded, float* y, size_t n, bool fused)
    {
        for (size_t i = 0; i < n; ++i)
        {
            // x[i - k] is padded[m - 1 + i - k].
            const float* x = padded + (m - 1) + i;
            float acc = 0.0f;

            if (fused)
            {
                for (size_t k = 0; k < m; ++k)
                    acc = std::fma (*(x - k), h[k], acc);
            }
            else
            {
                for (size_t k = 0; k < m; ++k)
                {
                    const float product = *(x - k) * h[k];
                    acc = acc + product;
                }
            }

            y[i] = acc;
        }
    }
}
