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
| `HPreHpNum`, `HPreHpDen` | direct-form `b`/`a` | same high-pass, **reference only**, not used |

The high-pass runs as a cascade of `juce::dsp::IIR::Filter` biquads, not as the single
direct-form IIR: at cutoffs of a few tens of Hz its poles sit so close to z = 1 that a
high-order direct form overflows to NaN in float32. The biquad cascade is stable.

```bash
../tools/build-podman.sh firConv              # then -install to deploy
```

| Parameter | Range | |
|---|---|---|
| Gain | −60 … 0 dB | input gain, headroom for the correction's boost |
| Freq | | unused (left over from the plugin template) |

The editor plots the response of the compiled-in chain.
