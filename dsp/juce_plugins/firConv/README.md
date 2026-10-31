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
| `HPreHpNum`, `HPreHpDen` | direct-form `b`/`a` | the high-pass in direct form, **reference only**, not used. Not a valid reference as exported: in the 8 decimals of `filters.hpp` its denominator has a pole at z = 1 (+34 dB at 25 Hz against `HPreHpSos`, the same within 0.01 dB only above about 400 Hz). The tuning GUI graphs its own float64 `b`/`a` |

The high-pass runs as a cascade of second-order sections, not as the single direct-form IIR:
at cutoffs of a few tens of Hz its poles sit so close to z = 1 that a high-order direct form
overflows to NaN in float32. The cascade is stable. Since 0.0.3 it runs in double, in
Transposed Direct Form II (the equations of `juce::dsp::IIR::Filter`, which ran it in float32
until 0.0.2): the coefficients are `HPreHpSos`'s float values, the pre-LP's float output goes in,
and the cascade's output is rounded to float once, before the inverse FIR (below).

The two FIRs (61 and 2001 taps) are the shared block FIR, `../shared/blockfir/BlockFir.h`
(`SHARED blockfir` in `CMakeLists.txt`, [../README.md](../README.md#shared-source-folders)):
16 outputs per pass over the taps, sized in `prepareToPlay` for the host's block, and a longer
block is processed in chunks of that size. Since 0.0.2 it replaces `juce::dsp::FIR::Filter`,
a patch whose tolerance is, as measured: on x86_64 the same output bit for bit. On aarch64
every tap is a fused multiply-add, which differs from `FIR::Filter`'s mix of fused and unfused
taps by less than -131 dB re the FIRs' peak on full-scale white noise (`../tests/blockfir`;
on firConv's own band-limited signal the inverse FIR's rounding differs more, below). On x86_64
the inverse FIR's cost at blocks of 1024 dropped from about 14 % to 1 % of real time
(`../tests/blockfir/run.sh --time`).

**0.0.2 and the float32 high-pass.** 0.0.2's aarch64 output differed from 0.0.1's by up to about
-55 dB re peak (-59 dB at -6 dBFS input peak, -55.0 to -55.7 dB at -9 to -20 dBFS), all of it
below about 50 Hz, because the high-pass biquads (float32, poles close to z = 1) turned any change
of rounding before them, of even one ulp, into a difference at the level of their own
low-frequency rounding noise (about -69 dBFS RMS at -6 dBFS peak): the rounding of a TDF-II's
state passes through its poles, not its zeros, about +100 dB at 10-25 Hz in each section. That
predated P1: on x86_64 the same input moved by one ulp in 1000 samples moved 0.0.1's output as
much (-58 dB re peak), and 0.0.1's aarch64 build differed from its x86_64 build by more (-54 dB
re peak). Those numbers come from `speakerd` renders of 0.0.1 and 0.0.2, built for x86_64 and
for aarch64 (under qemu), of a sweep and white noise at -6, -9, -12 and -20 dBFS peak with the
gain at -12 dB. The peak of the biquads' noise moved with the level: -58.8 to -59.0 dB re peak
at -6 dBFS, -55.0 to -55.7 dB at the others, as large as 0.0.1's own difference between aarch64
and x86_64 (-54.9 dB at -9 dBFS). From 100 Hz up the difference stayed at least 76 dB below the
output in every 1/6-octave band.

**0.0.3, the high-pass in double.** Its state's rounding is now 29 bits further down, far below
the one rounding of its output. Measured with the response test's sweep (5 Hz to 24 kHz,
-9 dBFS peak, the gain at -12 dB) rendered by `speakerd`, against the same chain computed in
float64 with scipy from `filters.hpp`:

- The output differs from 0.0.2's by -59.7 dB re peak on x86_64 and -57.9 dB on aarch64 (RMS
  -74.7 and -74.1 dBFS), 99.9 % of it below 50 Hz: the float32 biquads' noise, gone.
- The error against the float64 chain is now the coefficients' own, their rounding to float32
  in `filters.hpp` (-77.4 dBFS RMS, the same in every build). The arithmetic's share fell from
  -74.7 to -107.2 dBFS RMS on x86_64 (-74.1 to -107.5 on aarch64), 98 to 99 % of it above
  20 kHz.
- The x86_64 and aarch64 builds differ by -92.1 dB re peak (0.0.2: -55.6), RMS -111.5 dBFS,
  93 % of it above 20 kHz. From 20 Hz to 20 kHz every 1/6-octave band of the difference is at
  least 84 dB below the output, at least 108 dB below 100 Hz (0.0.2: 53 dB, at 20 Hz).

What is left is the same mechanism at the other end of the band. The pre-LP FIR's float32
rounding is white, and the inverse FIR boosts it where the pre-LP cuts, by +40 dB at 20 kHz up to
+63 dB at 23 kHz (-107 dBFS RMS, the output's floor above 20 kHz). So any change of rounding
before or in the pre-LP makes a new realisation of that noise: aarch64's gain factor for
-12 dB, one float ulp above x86_64's (0.25118867 against 0.25118864), moves the output by
-91.3 dB re peak alone, its fused pre-LP taps by -92.8 dB alone; its fused inverse taps add
-101.7 dB of their own (every tap fused against every tap unfused: -134 dB re the output's peak
on full-scale white noise, whose output peaks at +62 dBFS, -101 dB on this signal). A numpy
model of the chain's arithmetic (float32 gain and FIRs, fused taps on aarch64, the cascade in
double) reproduces both renders to one step of the 32-bit WAV. On x86_64 the double cascade
costs what the float one did, 0.06 % of real time at blocks of 1024.

`../tests/firconv-response/run.sh` shows the chain's magnitude response: the design computed
from `filters.hpp` (scipy, float64, the high-pass as `HPreHpSos`) and this version built for
x86_64 and for aarch64, rendered by `speakerd` from a sweep, on one plot, with each build's
deviation from the design under it ([../README.md](../README.md#the-response-test)). Both builds
are within 0.0112 dB of the design from 20 Hz to 20 kHz (at 20 Hz; 0.0.2: 0.0153 dB), all of it
the rounding of the coefficients to float32: against the design with float32 coefficients they
are within 0.00002 dB.

```bash
../tools/build-podman.sh firConv              # then -install to deploy
```

| Parameter | Range | |
|---|---|---|
| Gain | −60 … 0 dB | input gain, headroom for the correction's boost |
| Freq | | unused (left over from the plugin template) |

The editor's plot does not read the filters: it draws `getMagnitudes()`, which nothing fills,
so it shows a flat line at the bottom of the plot.
