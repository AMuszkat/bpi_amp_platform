# CLAUDE.md

Guidance for Claude Code (claude.ai/code) in `dsp/`, the DSP side of the BPI AMP Platform
repository (the board side is `firmware/`, the design documents are `docs/`, the virtual pair
is `tests/vpair/`; see the root `CLAUDE.md`). Paths below are relative to `dsp/`. User-facing documentation
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
| `juce_apps/speakerd/` | the headless plugin host that runs on each speaker: the graph inside PipeWire, the state layers | its README |
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
  plugin folder again. For the remote plugin host, the repository root's
  `tools/build.sh <plugin|app> aarch64 [target]` cross-compiles any target (headless included)
  and `speakerd` in the same image into `build/aarch64/`, which is what the board installer
  (`firmware/speakerd-setup.sh`) deploys.
- **Plugin CMake:** each `CMakeLists.txt` is one `speaker_add_plugin()` call
  (`juce_plugins/cmake/SpeakerPlugin.cmake`). The folder name must equal the target name.
  The build script discovers plugins by that call and reads `INPUTS`/`OUTPUTS` and the LV2
  URI from it. Plugin-specific extras go after the call.
- **Identity must stay unique and stable:** `CODE` differs per plugin (Cros, Fcnv, Loud,
  Ascp), and the LV2 URI is `SPEAKER_LV2_URI_BASE/<name>`. Changing the base orphans every
  saved host session.
- Deploy target: `DEPLOY_HOST`/`DEPLOY_LV2_DIR` from `config/local.env`. The bundle must land
  in a *default* LV2 path (`~/.lv2`), because JUCE's host ignores `$LV2_PATH`.
- **Nothing of ours runs on the audio thread when a parameter changes.** A host may set
  parameters on its audio thread (JUCE's LV2 wrapper does, in `run()`), so an APVTS listener
  or a parameter listener off the message thread only stores atomics: no
  `sendChangeMessage()`, `triggerAsyncUpdate()` or DSP object setters (`MorphPad` shows the
  pattern). The audio thread reads parameters at the start of a block; editors poll from a
  timer. Buffers are sized in `prepareToPlay`, and a larger block is processed in chunks,
  never by resizing.
- **Monitors** (`mon_` parameters, `docs/protocol.md`) are added with `addParameter()` after
  the APVTS, never through it, and published on the audio thread with `monitors::publish()`
  (`loudness/Monitors.h`). Their getters go in `<plugin>/plugin-check.monitors.h`.
- **The one lock allowed on the audio thread** (an exception to the root rule): under JUCE's
  LV2 wrapper only, `monitors::publish()` tells a monitor's listeners when its stored value
  moved by a step, or once a second, and `AudioProcessorParameter::sendValueChangedMessageToListeners()`
  takes that parameter's own listener lock. Nothing else ever takes it, so it is uncontended:
  one compare-and-swap, no syscall, no wait. Why it cannot be avoided: the LV2 wrapper reports
  to the host only parameters its listener heard change (a bare `setValue()` never leaves the
  plugin), and a thread of the plugin cannot notify safely, because the wrapper destroys its
  listener before the processor without telling the plugin, while `run()` never overlaps
  cleanup. Under any other wrapper `publish()` only stores the value: JUCE's VST3 wrapper would
  take a mutex the message thread holds constantly. No other lock is allowed, and no other
  plugin code may notify listeners from the audio thread.
- **The one syscall allowed on the audio thread** (an exception to the root rule): speakerd's
  timing reads the monotonic clock (`clock_gettime (CLOCK_MONOTONIC)`, `nowNs()` in
  `juce_apps/speakerd/TimedNode.h`) 4 + 2 × plugin nodes times per cycle. On most kernels that is
  the vDSO's, no syscall; on the board's Cortex-A73 the kernel's workaround for ARM erratum
  858921 (`CONFIG_ARM64_ERRATUM_858921`) disables the vDSO clock, and each read is a real
  syscall. Why acceptable: it is bounded and never blocks (no lock, no wait, about a
  microsecond), a few microseconds per cycle against 21 ms at the speaker's quantum, and it is
  what gives the board per-node CPU numbers. speakerd logs the measured cost of a read at start
  (`timing: a clock read costs <x> ns`) and the installer's smoke check reports it. No other
  syscall is allowed, and nothing else may read a clock on the audio thread.
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
(`git submodule update --init dsp/juce_plugins/JUCE` from the repository root). For what JUCE *actually* does, read and
grep it first: `modules/…` (header doc comments are the API reference; the `.cpp` is the
only answer for undocumented behaviour), `docs/CMake API.md`, `examples/`, `extras/`. It
outranks memory and the web, which is full of answers for other versions. Only if the tree
can't answer it, go online (docs.juce.com, github.com/juce-framework/JUCE, forum.juce.com),
note which version an answer applies to, and verify it against the pinned source. Write
findings that cost real effort into `docs/juce-knowledge/`.

The `/juce` skill (`.claude/skills/juce/SKILL.md` at the repository root) loads that knowledge
base and the JUCE docs before answering JUCE questions.
