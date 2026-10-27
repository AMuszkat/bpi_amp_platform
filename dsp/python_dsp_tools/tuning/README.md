# Tuning GUI

`dsp_gui.py` designs and simulates a speaker's DSP against measured driver responses: a
two-way linear-phase crossover, time alignment and magnitude/phase correction, all adjusted
live. It then exports the filter coefficients as the C++ headers the plugins in
`../../juce_plugins/` compile in.

It doesn't touch audio hardware (numpy, scipy and pyqtgraph only) and is independent of the
measurement GUI. It only reads the WAVs that GUI saves.

```bash
./run.sh                    # foreground
./run.sh --bg               # detached; logs to $NEAR_FIELD_DSP_LOG (default /tmp/nf_dsp_gui.log)
pkill -f dsp_gui.py         # stop a detached one
```

## Inputs

Two impulse responses, **Woofer** and **Tweeter**. Any WAV can be loaded into either slot.
On startup it auto-loads these, from the data folder (`NF_DATA_DIR`) first, then from
`../examples/`:

| Slot | Default file | Fallback |
|---|---|---|
| Woofer | `ir_result_wf.wav` (the spliced near/far-field woofer) | |
| Tweeter | `ir_result_tw.wav` (the gated tweeter) | raw `ir_ff_tw.wav` |

So a fresh checkout opens with the bundled example measurement.

The IRs are used **as loaded**: there is no time gating here, because gating is the
measurement GUI's job. Each slot has a *peak mode* (the phase reference). The woofer is
normalised to 0 dB at 1 kHz, and the same gain is applied to the tweeter, so their level
relationship is kept. *HF gain* trims the tweeter on top of that.

## The crossover module

Every stage is live. The plots show **magnitude, phase and group delay**, and every
intermediate transfer function can be toggled (the coloured checkbox bar is the legend).

- **Crossover (linear-phase, Parks-McClellan):** crossover frequency, transition width,
  taps.
- **Alignment & level:** a delay per band (µs, simulation only, *not* exported), plus a
  detector that sweeps a trial delay, finds the one that maximises the coherence of the
  summed *post-crossover* bands, and reports which driver arrives first:
  *"tweeter arrives 93 µs before woofer → delay tweeter"*. *Apply detected delay to earlier
  band* adds it. (Use the post-crossover bands: the LP/HP filters add their own
  transition-region phase, so reading the raw driver phases picks the wrong band.)
- **Magnitude correction (firls inverse)**, optional: a least-squares inverse of the summed
  response. It is band-limited by a remez low-pass (*LP taps / passband / transition*) and a
  Butterworth high-pass (*HP order / cutoff*), so it only acts over the drivers' usable
  band. The inverse is designed on a coarse grid (*Design points*, default 4096) to keep
  dragging responsive. Raise it to approach a full-resolution design.
- **Group-delay correction (phase inverse)**, optional.

Invalid settings, such as a band edge below 0 Hz, are reported in the status bar.

## Export

**Export C++ headers** writes each header straight into the plugin that compiles it:

| Header | Arrays | Written to |
|---|---|---|
| `crossover_coefs.hpp` | `lp`, `hp` (FIR taps) | `juce_plugins/crossover/` |
| `filters.hpp` | `InvFilter` (FIR), `HPreLpTaps` (FIR), `HPreHpSos` (biquads, 6 floats each), `HPreHpNum`/`HPreHpDen` (reference only) | `juce_plugins/firConv/` |

Then rebuild the plugin (`juce_plugins/tools/build-podman.sh crossover`). The headers are
tracked in git, so `git diff` shows exactly which coefficients changed. `--export-dir DIR`
writes both files flat into DIR instead. `NF_EXPORT_DIR` in `config/local.env` moves the
root.

The high-pass is exported as second-order sections because a single high-order direct-form
IIR at these low cutoffs is numerically unstable in float32 (its poles cluster near z = 1
and the plugin's output overflows to NaN). A cascade of biquads is stable.

## Command line

| Flag | |
|---|---|
| `--data-dir DIR` | look for the input WAVs in DIR |
| `--export-dir DIR` | write exported headers flat into DIR |
| `--no-load` | start empty |
| `--opengl` | GPU line rendering (needs pyopengl) |

## Adding a module

Everything outside the crossover (plots, curve toggles, source loading, export) is
module-agnostic. A new analysis (single-driver correction, parametric EQ, a target curve, …)
is a `ProcessingModule` subclass that builds its control panel, declares `curve_specs()` and
computes them in `process(ctx)` from `ctx.freqs` / `ctx.Hlf` / `ctx.Hhf`, plus one entry
in the `MODULES` registry. The module selector unlocks once there are two.
