# JUCE's SIMD on aarch64 with GCC: off

With GCC on aarch64 (the boards' builds: `aarch64-linux-gnu-g++` 12.2, Debian bookworm, the
development container), JUCE 8.0.12 does not detect NEON. `JUCE_USE_ARM_NEON` stays undefined
and `JUCE_USE_SIMD` is 0, so `juce::dsp::SIMDRegister` does not exist and
`FloatVectorOperations` runs its plain C++ loops. Verified against the pinned source and the
compiler, not measured on a board.

## Why

JUCE tests the old, pre-ACLE macro name; GCC on aarch64 defines only the ACLE one:

- `juce_audio_basics/juce_audio_basics.h:81`:
  `#if __ARM_NEON__ && ! (JUCE_USE_VDSP_FRAMEWORK || defined (JUCE_USE_ARM_NEON))` then
  `#define JUCE_USE_ARM_NEON 1`. Nothing else defines it (only the iOS simulator branch sets
  it to 0).
- `aarch64-linux-gnu-g++ -dM -E -x c++ /dev/null` defines `__ARM_NEON 1` (and `__aarch64__`,
  `__ARM_FEATURE_FMA`, `__FP_FAST_FMAF`) but not `__ARM_NEON__`.
- `juce_dsp/juce_dsp.h:85-93`, the `JUCE_ARM` branch: `JUCE_USE_SIMD` is 1 only
  `#if JUCE_USE_ARM_NEON`, otherwise 0. (`JUCE_ARM` itself is set from `__aarch64__`,
  `juce_core/system/juce_TargetPlatform.h:205`.)

Check it on any translation unit that includes `juce_dsp.h`:
`aarch64-linux-gnu-g++ ... -dM -E file.cpp | grep -E 'JUCE_USE_SIMD|JUCE_USE_ARM_NEON'` prints
`#define JUCE_USE_SIMD 0` and no `JUCE_USE_ARM_NEON`. On x86_64 `JUCE_USE_SIMD` is 1 (the
`JUCE_INTEL` branch, SSE2 always), so code that compiles there can fail on the boards.

## What it changes

| | x86_64 | aarch64, GCC |
|---|---|---|
| `juce::dsp::SIMDRegister`, `SIMDNativeOps` | available | **not declared** (`juce_dsp.h:246-263` include them only `#if JUCE_USE_SIMD`): code using them does not compile |
| `FloatVectorOperations` | SSE intrinsics | the `#else` branch of `juce_FloatVectorOperations.cpp:317`, plain loops (which GCC may still auto-vectorise) |
| `ScopedNoDenormals` | sets FTZ and DAZ (MXCSR `0x8040`) | **unaffected**: its condition is `JUCE_USE_SSE_INTRINSICS \|\| (JUCE_USE_ARM_NEON \|\| (JUCE_64BIT && JUCE_ARM))` (`juce_FloatVectorOperations.cpp:1544`), true through `JUCE_64BIT && JUCE_ARM`; it sets FPCR.FZ (bit 24) |

## Don't switch it on

Defining `JUCE_USE_ARM_NEON=1` makes `SIMDRegister` exist, but:

- It changes every `FloatVectorOperations` call of every plugin (gains, copies, mixes) from the
  plain loops to NEON intrinsics: a processing change of every plugin at once, for a flag.
- `SIMDRegister<float>::multiplyAdd` is `vmlaq_f32` (`juce_dsp/native/juce_SIMDNativeOps_neon.h:440`),
  which GCC 12's `arm_neon.h` implements as `__builtin_aarch64_float_mlav4sf`, a multiply and a
  separate add (the ACLE `vmla` is not fused). In the P1 measurement (firConv's and crossover's
  FIRs) a block FIR written with `SIMDRegister` under `JUCE_USE_ARM_NEON=1` was 2.5x slower on
  aarch64 than the same block FIR written with GCC vector types.

So the plugins' portable SIMD is GCC vector types (`float __attribute__ ((vector_size (16)))`),
which GCC compiles to NEON on aarch64 and SSE on x86_64 with no JUCE switch and no compiler
flag: `dsp/juce_plugins/shared/blockfir/BlockFir.h`, and
[`fir-filter-rounding.md`](fir-filter-rounding.md) for what that does to rounding. No
`-ffast-math`, no `JUCE_USE_ARM_NEON`, no flag changes.

Not checked: Clang on aarch64 (which may define `__ARM_NEON__`), and JUCE versions other than
8.0.12.
