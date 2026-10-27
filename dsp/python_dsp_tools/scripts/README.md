# Scripts

The original command-line drivers, from before the GUIs existed. They still run, but
parameters are edited in the source (sample rate, gate lengths, `a`, `r`, `fmatch`, …), and
they plot with plotly/bokeh/matplotlib. Prefer the GUIs. These are kept as worked examples
of the `nearfield.lib` API.

| Script | What it does |
|---|---|
| `near_field_ir.py` | Play/record one sweep, deconvolve and save an IR (`ir_ff_tw.wav` by default: edit it per measurement) |
| `analysis.py` | Load the three captures, gate, splice, and run `process_2_way_speaker`: the whole pipeline in one go |
| `comparison.py` | Self-contained firls inverse-filter experiment (matplotlib) |
| `analysis_sandbox.py` | Inspect an inverse filter saved as `hinv.wav` |
| `convert_sr.py` | Resample the three captures to another sample rate (librosa) |
| `fir_cross.py` | Stand-alone Parks-McClellan crossover demo (matplotlib) |

WAVs are read from and written to the data folder (`NF_DATA_DIR`, default
`python_dsp_tools/data`). Audio devices come from `NF_INPUT_DEVICE` / `NF_OUTPUT_DEVICE` in
`config/local.env`. Headers that `lib.process_2_way_speaker` exports are written to the
**current directory**; copy them into `juce_plugins/crossover/` or `juce_plugins/firConv/`,
or use the tuning GUI, which puts them there directly.

Run them inside the env:

```bash
conda run -n near_field python scripts/analysis.py
```

The sample rate is set per script (44.1, 48, 88.2 or 96 kHz), and the WAVs must match the
rate the script assumes (`convert_sr.py` resamples them).
