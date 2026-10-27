# CLAUDE.md

Guidance for Claude Code (claude.ai/code) in this repository. User-facing documentation
lives in the READMEs; this file is the map plus the rules and traps that aren't obvious from
the code. **Read the README of the area you're changing first**, since most design
rationale is there.

## What this is

Measure a loudspeaker's free-field response with the near-field method, design crossover and
correction filters against it, and run them as JUCE LV2 plugins on an ARM64 board.

| Path | | Docs |
|---|---|---|
| `python_dsp_tools/measurement/` | near-field measurement GUI (`near_field_gui.py`) | its README |
| `python_dsp_tools/tuning/` | filter-design GUI (`dsp_gui.py`), exports the plugin headers | its README |
| `python_dsp_tools/nearfield/` | shared package: `lib.py` (all DSP), `config.py` (local settings) | `python_dsp_tools/README.md` |
| `python_dsp_tools/scripts/` | original CLI drivers (`analysis.py`, `near_field_ir.py`, …) | its README |
| `juce_plugins/` | `crossover`, `firConv`, `loudness`, `ampScope` + shared build in `tools/`, `cmake/` | `juce_plugins/README.md`, one per plugin |
| `juce_apps/plugin-check/` | checks that a plugin's complete, control and headless builds fit together | its README |
| `docs/near-field-method.md` | the measurement theory | |
| `docs/juce-knowledge/` | hard-won JUCE findings, read by the `/juce` skill | |
| `config/local.env.example` | template for machine-specific settings | |

## Private data never goes in tracked files

This repository is meant to be public. Device names, hostnames, usernames, IPs and absolute
home paths belong in `config/local.env` (gitignored). The Python side reads it through
`nearfield.config.get()`, the shell side through `load_local_config` in
`juce_plugins/tools/common.sh`. Both parse the file without executing it, and an environment
variable wins over it. When a script needs a new machine-specific value, add the key to
`config/local.env.example` with a comment and read it through those helpers. Never hardcode
the value. Per-user CMake settings go in `CMakeUserPresets.json`, which is also ignored.
Measurement data goes in `python_dsp_tools/data/` (ignored). Only the small sample pair in
`python_dsp_tools/examples/` is tracked.

## Python

- Environment: miniforge/conda, env `near_field`, created once by
  `python_dsp_tools/setup_env.sh`. It also runs `pip install -e python_dsp_tools`, which is
  what makes `from nearfield import lib, config` work from every folder. Launch with
  `python_dsp_tools/{measurement,tuning}/run.sh [--bg]` or
  `conda run -n near_field python <script>`. No test suite, linter or build step.
- **The two GUIs are independent.** Neither imports the other, and shared UI bits (e.g. the
  log-frequency axis) are duplicated on purpose. They share only `nearfield` and exchange
  data as WAVs in the data folder (`config.data_dir()`).
- **lib import policy:** `lib.py` hard-requires only numpy + scipy. `sounddevice`,
  `soundfile`, plotly and bokeh are imported in try/except and set to `None` when absent;
  functions that need one call `_require(obj, name)`. Keep that pattern for new lib
  functions that touch them, since it's what lets the tuning GUI run without an audio stack.
- `lib.py` is a flat ~1800-line module with overlapping experimental variants
  (`compute_magnitude_phase` vs `_freqz`, several `trim_*` / inverse-filter functions).
  Check which one a caller actually uses before editing.
- Magnitude/phase are on the positive-frequency half (`rfft`); `half2full_freqz` rebuilds the
  full spectrum when taps are needed. Sample rate is per script/WAV, never global.
- **Measurement GUI capture threading:** the worker owns the stream
  (`sd.playrec(blocking=False)` + polling). *Abort* only sets a flag and the worker calls
  `sd.stop()` itself. Calling `sd.stop()` from the GUI thread deadlocks on ALSA/PipeWire.
  `MeasureWorker` takes a *list* of captures on purpose, so simultaneous multi-channel
  capture can come back without a refactor.
- **Tuning GUI:** the relative-delay detector works on the *post-crossover* bands (reading
  the raw driver phase picks the wrong band). Exported taps come from the coarse "Design
  points" grid. The export writes `crossover_coefs.hpp` into `juce_plugins/crossover/` and
  `filters.hpp` into `juce_plugins/firConv/` (`EXPORT_TARGETS`). The high-pass is exported as
  SOS because a direct-form high-order IIR is float32-unstable in the plugin.
  New analyses = a `ProcessingModule` subclass + a `MODULES` entry.
- The older scripts still write headers to the cwd (lib's
  `write_cpp_float_arrays_to_header`).

## JUCE plugins

- **One build system for all plugins:** `juce_plugins/tools/build-podman.sh <plugin|all>
  [steps]` (Podman, Debian bookworm, aarch64 cross-build; `-debug-linux` for a native Debug
  build in the local AudioPluginHost). One image and one JUCE install
  (`juce_plugins/build-podman/`) are shared; per-plugin output is in
  `<plugin>/build-podman/`. `tools/build-aarch64.sh`, `build-native-linux.sh` and
  `build-linux-vm.sh` are the non-container alternatives. Never copy build scripts into a
  plugin folder again.
- **Plugin CMake:** each `CMakeLists.txt` is one `speaker_add_plugin()` call
  (`juce_plugins/cmake/SpeakerPlugin.cmake`). The folder name must equal the target name.
  The build script discovers plugins by that call and reads `INPUTS`/`OUTPUTS` and the LV2
  URI from it. Plugin-specific extras go after the call.
- **Identity must stay unique and stable:** `CODE` differs per plugin (Cros, Fcnv, Loud,
  Ascp), and the LV2 URI is `SPEAKER_LV2_URI_BASE/<name>`. Changing the base orphans every
  saved host session.
- Deploy target: `DEPLOY_HOST`/`DEPLOY_LV2_DIR` from `config/local.env`. The bundle must land
  in a *default* LV2 path (`~/.lv2`), because JUCE's host ignores `$LV2_PATH`.
- `crossover` and `firConv` compile in the generated headers; `loudness` and `ampScope` are
  pure-parameter plugins. `ampScope` talks to the MAX98396 driver only via `snd_ctl_*` /
  `snd_pcm_*`, never I²C. Extend the driver rather than work around it.
- Local debug gotchas (PipeWire crash handled via a private `XDG_CONFIG_HOME` asoundrc;
  silent plugin-load failures): `docs/juce-knowledge/hosting/audiopluginhost-linux.md`.
  Editor-window gotchas (fixed-size windows maximised by JUCE's X11 size-hint bug, patched
  for the host via `juce_plugins/patches/`; floating panels from an embedded editor; Debug
  repaint cost): `docs/juce-knowledge/gui/x11-plugin-windows.md`.
- `juce_plugins/ampScope/build-podman/plot-check/` is a gitignored off-tree render harness
  (builds the editor without a host, drives it from the Demo source, asserts on PNGs).
  Configure it in the container with `-DCMAKE_PREFIX_PATH=/work/build-podman/juce-install`.
  The per-plugin JUCE install it was first configured against no longer exists.

### Researching JUCE itself

`juce_plugins/JUCE/` is a submodule pinned to the commit the plugins compile against
(`git submodule update --init juce_plugins/JUCE`). For what JUCE *actually* does, read and
grep it first: `modules/…` (header doc comments are the API reference; the `.cpp` is the
only answer for undocumented behaviour), `docs/CMake API.md`, `examples/`, `extras/`. It
outranks memory and the web, which is full of answers for other versions. Only if the tree
can't answer it, go online (docs.juce.com, github.com/juce-framework/JUCE, forum.juce.com),
note which version an answer applies to, and verify it against the pinned source. Write
findings that cost real effort into `docs/juce-knowledge/`.

The `/juce` skill (`.claude/skills/juce/SKILL.md`) loads that knowledge base and the JUCE docs
before answering JUCE questions.
