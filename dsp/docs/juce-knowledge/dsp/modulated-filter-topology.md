# Filter topology for real-time-modulated EQ (biquad vs SVF)

Which filter structure to use when gain / frequency / Q are **changed while audio runs**
(level-dependent EQ, automated filters, morphing presets). Measured for the `loudness`
plugin with throwaway numpy simulations (not kept in the repo); the setup below is enough to
reproduce them.

## What JUCE actually gives you

- `juce::dsp::IIR::Filter` is **Transposed Direct Form II** (`juce_IIRFilter.h:293`).
- `juce::dsp::StateVariableFilter` and `juce::dsp::StateVariableTPTFilter` support
  **only lowpass / bandpass / highpass — no shelving or peaking with gain**.

So for a **shelf or peak EQ there is no ready-made JUCE SVF**. You either use the biquad or
hand-roll a Simper/Cytomic shelving SVF.

## The measurement

Low shelf, per-sample coefficient update, 60 Hz sine input. Metric is *splatter*: energy
above 1 kHz during the parameter move, i.e. the click. Each topology is compared against
itself, so there is no cross-design confound (the Simper SVF shelf and the RBJ biquad were
verified to have **identical static responses to 1e-11 dB**, so it is a fair fight).

100 ms ramp:

| modulating          | TDF-II biquad | DF-I    | TPT SVF |
|---------------------|---------------|---------|---------|
| gain only +12→0 dB  | **−126 dB**   | −127 dB | −97 dB  |
| fc only 300→60 Hz   | −110 dB       | −130 dB | −123 dB |
| Q only 0.5→2.0      | −125 dB       | −133 dB | −116 dB |
| all three           | **−122 dB**   | −122 dB | −92 dB  |

Hard switch (no ramp), all three params: TDF-II −63 dB, **DF-I −94 dB**, TPT SVF −47 dB.

## Conclusions

1. **For gain modulation the biquad wins by ~30 dB.** The SVF's modulation-immunity is a
   *cutoff* property. A shelving SVF's integrator coefficient is `g = tan(pi*f0/fs)/sqrt(A)`
   — gain modulation drags `g` around, which is exactly what TPT exists to avoid. Confirmed
   by the per-parameter breakdown: the SVF only beats TDF-II in the `fc only` row.
2. **At sane ramp times (≥50 ms) the topology is irrelevant** — everything is below −80 dB,
   far under audibility. Pick the biquad for the free
   `Coefficients::getMagnitudeForFrequencyArray()` (needed to draw the response).
3. **If you must switch instantly, use Direct Form I**, not TDF-II: −94 vs −63 dB on a hard
   switch. DF-I stores raw x/y history, so its state stays meaningful when coefficients jump;
   TDF-II's state is an encoding that assumes the old coefficients.
4. Always **ramp** rather than jump. `juce::SmoothedValue` on the *parameters* plus a
   coefficient recompute every ~32 samples is enough (see `juce_plugins/loudness/PluginProcessor.cpp`).

## Numerical conditioning (float32)

Single shelf biquads are safe far lower than you might fear — max pole radius:

| fc     | 20 Hz  | 40 Hz  | 60 Hz  | 100 Hz | 200 Hz | 500 Hz |
|--------|--------|--------|--------|--------|--------|--------|
| \|pole\| | 0.9987 | 0.9974 | 0.9961 | 0.9935 | 0.9870 | 0.9678 |

Contrast with firConv's **4th-order** Butterworth high-pass at 25 Hz, whose clustered poles
overflow to NaN in float32 and had to be exported as second-order sections (see the SOS note
in the root `CLAUDE.md`). **Order**, not just cutoff, is what kills direct-form IIRs — a
single biquad at 20 Hz is fine, a 4th-order direct form at 25 Hz is not.

## Realtime-safe coefficient updates

Assigning a `std::array` into existing coefficients does **not** allocate after the first
call — `Coefficients::assignImpl` uses `clearQuick()` + `ensureStorageAllocated()`
(`juce_IIRFilter_Impl.h:41`). So allocate once in `prepareToPlay`, then per block:

```cpp
// prepareToPlay -- the only allocation
filter.coefficients = new juce::dsp::IIR::Coefficients<float> (
    juce::dsp::IIR::ArrayCoefficients<float>::makeLowShelf (sr, 100.0, 0.7, 1.0));

// processBlock -- reuses the storage above, no allocation
*filter.coefficients = juce::dsp::IIR::ArrayCoefficients<float>::makeLowShelf (
    sr, freq, q, juce::Decibels::decibelsToGain (gainDb));
```

Note the `ArrayCoefficients<float>` (returns `std::array`) rather than
`Coefficients<float>::makeLowShelf` (returns a newly **allocated** ref-counted `Ptr` — fine
on the message thread, never on the audio thread).
