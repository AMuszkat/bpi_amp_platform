# Measurement GUI

`near_field_gui.py` captures the three measurements of the
[near-field method](../../docs/near-field-method.md) and reconstructs the woofer's free-field
response from them. What it adds over the original scripts is that the **time gate** of every
measurement can be adjusted live. The gate is the parameter that's hard to get right, and
the reason this GUI exists.

```bash
./run.sh                          # foreground
./run.sh --bg                     # detached; logs to $NEAR_FIELD_LOG (default /tmp/nf_gui.log)
pkill -f near_field_gui.py        # stop a detached one
```

Run `../setup_env.sh` once first.

## What you see

Three stacked plots, all with per-curve toggles:

- **Impulse responses and gate windows** vs time.
- **Magnitude**, log frequency: near field, gated far field, the spliced result and the
  tweeter. The NF and FF woofer curves are scaled to the 1 kHz-normalised result, so you can
  see the splice at `fmatch`.
- **Phase**, same curves.

## Workflow

1. **Devices.** Pick the input (mic) and output (speaker) devices. Use the *same* interface
   for both if you can: two devices have independent clocks, which drift and cause clicks on
   long sweeps (the GUI warns about this). Put your usual devices in `config/local.env` so
   they're preselected:
   ```
   NF_INPUT_DEVICE=Scarlett
   NF_OUTPUT_DEVICE=Scarlett
   ```
2. **Sweep.** Start/end frequency, duration, sample rate, silent padding and output level
   (default 0.5, i.e. −6 dBFS: drive SPL with the amplifier, not the digital level).
   *Show sweep* plots the exact signal that will be played.
3. **Measure each slot.** Each of *NF woofer*, *FF woofer* and *FF tweeter* has its own
   *Measure* button and mic channel (defaults 1 / 2 / 1). *Load wav* reads an existing
   capture instead. *Stop / abort* cancels a running capture.
4. **Gate.** Adjust each slot's *Window L/R (ms)* around the peak until the far-field gate
   ends before the first reflection. Defaults: NF 100/250 ms, FF 20/6 ms.
5. **Reconstruction.** Radius *a*, distance *r*, match frequency and smoothing. See the
   method page for what `fmatch` does and why *a* and *r* currently only document the rig.
6. **Save.** *Save IRs + results* writes into the data folder (`NF_DATA_DIR`, default
   `python_dsp_tools/data/`):

   | File | |
   |---|---|
   | `ir_nr_wf.wav`, `ir_ff_wf.wav`, `ir_ff_tw.wav` | raw captures, auto-loaded on the next start |
   | `ir_result_wf.wav` | spliced woofer, the input for the tuning GUI |
   | `ir_result_tw.wav` | gated tweeter, the input for the tuning GUI |

## Command line

| Flag | |
|---|---|
| `--measure {nf,ff,tweeter,all}` | start a measurement right after launch |
| `--input-device NAME`, `--output-device NAME` | device-name substrings (override `local.env`) |
| `--data-dir DIR` | read/write the wavs in DIR instead of `NF_DATA_DIR` |
| `--no-load` | don't auto-load existing wavs |
| `--opengl` | GPU line rendering (needs pyopengl; may flicker on some Linux GL drivers) |

## Troubleshooting

- **Clicks or pops mid-sweep.** On direct `hw:*` devices, raise *Block size* (default 1024)
  and keep *Latency* at `high`; latency doesn't matter for a measurement. Through PipeWire
  these settings are ignored (PipeWire uses its own quantum). Different input and output
  devices cause this too; see step 1.
- **The response rolls off early, at the same frequency on every driver.** The playback path
  delays the sound (e.g. a network audio server), and the end of the sweep falls outside the
  recording. Set *Extra record (ms)* to at least the playback latency. It lengthens the
  recording, not the sweep.
- **Only `hw:*` devices listed on Linux.** See the PipeWire note in the
  [parent README](../README.md#linux-audio-note).
- **Never call `sd.stop()` from the GUI thread.** Aborting a capture only sets a flag. The
  worker thread stops its own stream, because stopping it from another thread deadlocks on
  ALSA/PipeWire.
