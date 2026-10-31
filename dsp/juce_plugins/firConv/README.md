# firConv

The speaker's **magnitude-correction** filter chain. Mono in, mono out.

```
in ─ gain ─ pre-LP FIR (HPreLpTaps) ─ pre-HP biquads (HPreHpSos) ─ inverse FIR (InvFilter) ─ out
```

All coefficients come from `filters.hpp`, compiled in. The tuning GUI's *Export C++
headers* writes it straight into this folder:

| Array | Form | Role |
|---|---|---|
| `InvFilter` | FIR taps | least-squares inverse of the summed speaker response |
| `HPreLpTaps` | FIR taps | remez low-pass: stops the inverse boosting above the usable band |
| `HPreHpSos` | biquads, 6 floats each `[b0 b1 b2 a0 a1 a2]` | Butterworth high-pass: stops it boosting below the usable band |
| `HPreHpNum`, `HPreHpDen` | direct-form `b`/`a` | the high-pass in direct form, **reference only**, not used. Not a valid reference as exported: in the 8 decimals of `filters.hpp` its denominator has a pole at z = 1 (+34 dB at 25 Hz against `HPreHpSos`, the same within 0.01 dB only above about 400 Hz; `../tests/firconv-response`). The tuning GUI graphs its own float64 `b`/`a` |

The high-pass runs as a cascade of `juce::dsp::IIR::Filter` biquads, not as the single
direct-form IIR: at cutoffs of a few tens of Hz its poles sit so close to z = 1 that a
high-order direct form overflows to NaN in float32. The biquad cascade is stable.

The two FIRs (61 and 2001 taps) are the shared block FIR, `../shared/blockfir/BlockFir.h`
(`SHARED blockfir` in `CMakeLists.txt`, [../README.md](../README.md#shared-source-folders)):
16 outputs per pass over the taps, sized in `prepareToPlay` for the host's block, and a longer
block is processed in chunks of that size. Since 0.0.2 it replaces `juce::dsp::FIR::Filter`,
a patch whose tolerance is, as measured: on x86_64 the same output bit for bit. On aarch64
every tap is a fused multiply-add, which differs from `FIR::Filter`'s mix of fused and unfused
taps by less than -131 dB re the FIRs' peak (`../tests/blockfir`); the plugin's output differs
from 0.0.1's by up to about -55 dB re peak (-59 dB at -6 dBFS input peak, -55.0 to -55.7 dB at
-9 to -20 dBFS: below; rendered by `speakerd` from a sweep and noise), all of it
below about 50 Hz, because the high-pass biquads (float32, poles close to z = 1) turn any
change of rounding before them, of even one ulp, into a difference at the level of their own
low-frequency rounding noise (about -69 dBFS RMS at -6 dBFS peak). That predates P1: on x86_64
the same input moved by one ulp in 1000 samples moves 0.0.1's output as much (-58 dB re peak),
and 0.0.1's aarch64 build differs from its x86_64 build by more (-54 dB re peak). On x86_64
the inverse FIR's cost at blocks of 1024 dropped from about 14 % to 1 % of real time
(`../tests/blockfir/run.sh --time`).

To look at that difference, `../tests/firconv-response/run.sh [--base REF]` renders a sweep and
noise through this version and the previous one (or REF) with `speakerd`, on x86_64 and on
aarch64, and plots each pair's responses, the design computed from `filters.hpp` (scipy), and
their difference against frequency ([../README.md](../README.md#the-response-test)). At its
input level (-9 dBFS peak, gain -12 dB), 0.0.2 differs from 0.0.1 on aarch64 by up to -55.0 dB
re peak, not -59: the peak of the biquads' noise moves with the level (-58.8 to -59.0 dB at
-6 dBFS peak, the level of the measurement above; -55.0 to -55.7 dB at -9, -12 and -20 dBFS),
and it is as large as 0.0.1's own difference between aarch64 and x86_64 (-54.9 dB). From 100 Hz
up the difference stays at least 76.8 dB below the output in every 1/6-octave band (89.7 dB from
1 kHz), and every build is within 0.016 dB of the design from 20 Hz to 20 kHz, most of that the
rounding of the coefficients to float32.

```bash
../tools/build-podman.sh firConv              # then -install to deploy
```

| Parameter | Range | |
|---|---|---|
| Gain | −60 … 0 dB | input gain, headroom for the correction's boost |
| Freq | | unused (left over from the plugin template) |

The editor plots the response of the compiled-in chain.
