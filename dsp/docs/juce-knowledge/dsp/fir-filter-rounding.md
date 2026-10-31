# `juce::dsp::FIR::Filter`'s rounding with GCC 12, and the block FIR

What `FIR::Filter<float>` (JUCE 8.0.12) compiles to with GCC 12.2 at `-O3`, why its output on
aarch64 depends on the build flags and changes from sample to sample, and what
`dsp/juce_plugins/shared/blockfir/BlockFir.h`, which replaced it in firConv and crossover
(version 0.0.2), does instead. Verified in the generated code of both compilers of the
development container and with `dsp/juce_plugins/tests/blockfir`.

## The source

`processSingleSample` (`juce_dsp/processors/juce_FIRFilter.h:190`) keeps the history in a ring
of `m` samples, the newest at position `p`, and adds the taps in ascending order, tap 0 first,
from `out = 0`, in two loops because the ring wraps:

```cpp
for (k = 0; k < m - p; ++k)  out += buf[p + k] * fir[k];
for (size_t j = 0; j < p; ++j)  out += buf[j] * fir[j + k];
```

One output per pass over the taps, every tap read once per output: about 1.5 ns per
multiply-add on x86_64 (14.4 % of real time at 48 kHz for firConv's 2001 taps).

## What GCC 12 makes of it

Without `-ffast-math` GCC may not reorder the sum, but it may still vectorise it as an in-order
reduction, and with its C++ default `-ffp-contract=fast` it fuses a multiply and an add where
the target has an instruction for it:

- **aarch64** (`aarch64-linux-gnu-g++ -O3`): each loop becomes a 4-wide `fmul v.4s` of four
  products, which are then added to the scalar sum one by one with `fadd s0, s0, s<i>`: in order,
  **unfused** (each product rounded, then each add rounded). The up to three iterations left
  over at the end of each loop stay scalar and are contracted to `fmadd`: **fused**. Since the
  two loops' lengths `m - p` and `p` change with the ring position every sample, which taps
  are fused changes from one output to the next.
- **x86_64** (no FMA in the default target): `mulps` and in-order `addss`, the leftovers
  `mulss` and `addss`: every tap unfused, the same result as the plain scalar loop.

So `FIR::Filter`'s output on aarch64 depends on the compiler's vectorisation and contraction
(`-O` level, `-ffp-contract`, `-march`), and is not the x86_64 output: with `-ffp-contract=off`
every tap would be unfused, with another vectoriser other taps would be fused. (The same
contraction is why `loudness`'s biquads on aarch64 differ from its x86_64 render by up to
-75 dBFS: `dsp/juce_apps/speakerd/README.md`, "Same output on both".)

Check it: `aarch64-linux-gnu-g++ -O3 -S` on a translation unit that calls `FIR::Filter::process`
and look for `fmul v*.4s` followed by four `fadd s0` (the vector part) and `fmadd` (the rest).

## The block FIR

`BlockFir.h` computes 16 outputs per pass over the taps: four accumulators of four floats (GCC
vector types, `float __attribute__ ((vector_size (16)))`), one lane per output, from a linear
history (`m - 1` old samples, then the block, allocated in `prepare()`), each lane adding its taps
in ascending order from 0, as `FIR::Filter` does:

```cpp
const Vec hk = { h[k], h[k], h[k], h[k] };
a0 += load (x - k) * hk;   // and a1, a2, a3 at x - k + 4, + 8, + 12
```

- Every tap is the same operation in every lane. On aarch64 GCC contracts it into `fmla v.4s`
  (or its by-element form): **every tap fused**, in every output. On x86_64 it is `mulps` and
  `addps`: **every tap unfused**, in order, which is exactly `FIR::Filter`'s x86_64 result.
- **The last outputs of a block** (fewer than 16) go through the same kernel, over the end of
  the history padded with zeros, and only the outputs needed are stored. A plain scalar tail
  loop would not do: GCC compiles `for (k) acc += x[i - k] * h[k]` exactly as it compiles
  `FIR::Filter`'s loop (vector `fmul`, in-order unfused `fadd`, fused leftovers), so it would
  round differently from the 16-lane kernel; and an explicit `std::fma` there would differ from
  the unfused x86_64 kernel and is a slow library call on x86_64.
- Each output is computed alone in its lane, so the result does not depend on how the signal is
  cut into blocks (or on the chunking of a block longer than the prepared size).

Measured (`tests/blockfir`, 32768 samples of full-scale noise per run): on x86_64 the block FIR
is **bit-identical** to `FIR::Filter` for the four sets of taps (61, 2001, 259, 259); on aarch64
it differs in 49 to 71 % of the samples, by at most **-131.5 dB re the output's peak**, the
difference between always fused and sometimes fused. The test checks the kernel bit for bit
against a reference written out with `std::fma` where the kernel fuses and as a rounded
multiply and add where it does not (in its own translation unit, `-ffp-contract=off`, no LTO),
on both architectures, the aarch64 build under `qemu-aarch64-static`; built with
`-ffp-contract=off`, the aarch64 kernel fails that check in every run, so it does see the
fusion.

Cost on x86_64 (Ryzen AI 9 HX 375, blocks of 1024, % of real time at 48 kHz,
`tests/blockfir/run.sh --time`):

| Taps | `FIR::Filter` | block FIR |
|---|---|---|
| 61 (firConv pre-LP) | 0.443 % | 0.045 % |
| 2001 (firConv inverse) | 14.37 % | 0.97 % |
| 259 (crossover low) | 1.87 % | 0.155 % |
| 259 (crossover high) | 1.87 % | 0.125 % |

Inlined into a function with many live values the kernel can lose its loop bound to a stack
reload and run about 25 % slower (the test times it through an out-of-line function, as the
plugins' `processBlock` has it). Not measured here: the board's Cortex-A73.

## Downstream: a one-ulp change through firConv's biquads

-131 dB is the FIRs' own difference, and crossover's output (two FIRs after a gain) keeps it:
-129.5 and -132.6 dB re peak, P1 against 0.0.1, on aarch64, rendered by `speakerd` from a
sweep and noise. firConv's output does not: after its pre-LP FIR come the high-pass biquads
(`juce::dsp::IIR::Filter`, Transposed Direct Form II, float32, `HPreHpSos`: poles at radius
0.997 and 0.9987, a 25 Hz Butterworth), and P1 against 0.0.1 on aarch64 differs by up to
**-55 to -59 dB re peak**, 99.9 % of the difference's energy below 50 Hz: -59 dB (-69 dBFS RMS)
at -6 dBFS input peak, -55.0 to -55.7 dB at -9, -12 and -20 dBFS, because the peak of the
biquads' noise does not scale with the signal (`dsp/juce_plugins/tests/firconv-response`
plots it against frequency). Not because of
P1: rendered on x86_64, where P1 is bit-identical, the same chain's output moves by -58 dB re
peak when the input moves by about one ulp in 1000 of its 480000 samples, and 0.0.1's aarch64
build already differs from its x86_64 build by -54 to -55 dB re peak. The biquads turn any change of
rounding before them into a different realisation of their own low-frequency rounding noise
(a TDF-II's state errors pass through its poles, not its zeros). So an output comparison of a
chain with low-cutoff float32 biquads, across builds, compilers or architectures, can only
hold to that noise level, and a bit-exact comparison has to be made before them.

Why not `juce::dsp::SIMDRegister`: it does not exist in the aarch64 builds, and with NEON
forced on it was slower ([`simd-on-aarch64-gcc.md`](simd-on-aarch64-gcc.md)).
