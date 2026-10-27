# python_dsp_tools

The Python half of the project: measure a speaker, then design its filters.

| Folder | What | Launch |
|---|---|---|
| [`measurement/`](measurement/README.md) | Near-field measurement GUI: sweep capture, time gating, NF+FF splice | `measurement/run.sh` |
| [`tuning/`](tuning/README.md) | Tuning GUI: crossover, delay alignment, correction filters, C++ export | `tuning/run.sh` |
| `nearfield/` | Shared package: `lib.py` (all the DSP) and `config.py` (local settings) | imported by everything |
| [`scripts/`](scripts/README.md) | The original command-line scripts the GUIs replaced | `python scripts/<name>.py` |
| `examples/` | One real woofer + tweeter result pair, so the tuning GUI works out of the box | |
| `data/` | *Gitignored.* Where your measurements go (default `NF_DATA_DIR`) | |

The two GUIs are **independent**: neither imports the other, and neither shares UI code with
the other. Small duplicated widgets, such as the log-frequency axis, are copied on purpose so
that editing one tool can't break the other. They share only the conda environment and the
`nearfield` package, and exchange data through WAV files in the data folder:

```
measurement GUI ──(Save)──► data/ir_result_wf.wav, ir_result_tw.wav ──(auto-load)──► tuning GUI
tuning GUI ──(Export C++ headers)──► ../juce_plugins/crossover/crossover_coefs.hpp
                                     ../juce_plugins/firConv/filters.hpp
```

## Setup

The environment is managed with [miniforge](https://github.com/conda-forge/miniforge). Set it
up once per machine:

```bash
./setup_env.sh        # creates the `near_field` env (incl. native PortAudio/libsndfile)
                      # and installs this folder's `nearfield` package in editable mode
```

Re-running updates the env. Nothing checks or installs dependencies at run time. The
launchers just use the env, and you can also work in it directly:

```bash
conda activate near_field
python measurement/near_field_gui.py --help
```

`requirements.txt` is a pip/venv fallback. With it you must supply PortAudio yourself
(e.g. `apt install libportaudio2`) and run `pip install -e .` in this folder.

Only `numpy` and `scipy` are hard requirements of `nearfield/lib.py`. The audio I/O and
plotting imports (`sounddevice`, `soundfile`, plotly, bokeh) are optional. A function that
needs a missing one raises a clear error when you call it, which is why the tuning GUI runs
without any audio stack installed.

## Settings

Private or machine-specific values live in `../config/local.env` (copy
`../config/local.env.example`), never in the code:

| Key | Used by | Default |
|---|---|---|
| `NF_INPUT_DEVICE`, `NF_OUTPUT_DEVICE` | measurement GUI, `scripts/near_field_ir.py` | the system default device |
| `NF_DATA_DIR` | both GUIs, scripts | `python_dsp_tools/data` |
| `NF_EXPORT_DIR` | tuning GUI export | `juce_plugins` |

Precedence: command-line flag > environment variable > `local.env` > default. Relative paths
are relative to the repository root. See `nearfield/config.py`.

## Linux audio note

conda's PortAudio is built with ALSA only and has no PipeWire plugin, so by default only raw
`hw:*` cards show up. `measurement/near_field_gui.py` points ALSA at the system PipeWire
plugin at startup, which makes the `pipewire` / `default` devices appear. Set
`NEAR_FIELD_NO_ALSA_FIX=1` to opt out.
