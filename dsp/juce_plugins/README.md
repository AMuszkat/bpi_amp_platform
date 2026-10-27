# juce_plugins

LV2 (and VST3) audio plugins built with [JUCE](https://juce.com), meant to run in a plugin
host on a small ARM64 Linux board next to the speaker. Two of them run the filters designed
in [`../python_dsp_tools/tuning`](../python_dsp_tools/tuning/README.md). The other two are
tools for the rest of the audio chain.

| Plugin | I/O | What it does | Coefficients from |
|---|---|---|---|
| [`crossover`](crossover/README.md) | 1 → 2 | Linear-phase FIR crossover: low band on out 1, high band on out 2 | `crossover_coefs.hpp` (tuning GUI) |
| [`firConv`](firConv/README.md) | 1 → 1 | Magnitude-correction chain: band-limit FIR + high-pass biquads + inverse FIR | `filters.hpp` (tuning GUI) |
| [`loudness`](loudness/README.md) | 1 → 1 | Level-dependent loudness contour from two low-shelf EQs per setting, four settings | live parameters |
| [`ampScope`](ampScope/README.md) | 1 → 1 | Oscilloscope for a MAX98397 amplifier's own voltage/current/supply telemetry (audio passes through) | live parameters |

## Layout

```
juce_plugins/
├── cmake/SpeakerPlugin.cmake   speaker_add_plugin(): identity, formats, flags, LTO for every plugin
├── tools/                      the build system, shared by every plugin
│   ├── build-podman.sh           recommended: containerised cross-build, deploy, local debug
│   ├── common.sh                 config loading + plugin discovery used by the scripts
│   ├── Containerfile             the builder image (Debian bookworm + aarch64 cross toolchain)
│   ├── toolchain-aarch64-linux.cmake
│   ├── build-aarch64.sh          alternative: cross-build on a bare Linux host
│   ├── build-native-linux.sh     alternative: build on the ARM board itself
│   └── build-linux-vm.sh, setup-linux-vm.sh   alternative: ARM64 VM on a Mac (UTM)
├── patches/                    fixes applied to JUCE for the local plugin host only
├── JUCE/                       git submodule, pinned
├── <plugin>/                   sources + CMakeLists.txt (a single speaker_add_plugin() call)
│   ├── build-podman/             gitignored build output
│   └── plugin_host_debug/        gitignored per-developer debug session
├── build-podman/               gitignored: the JUCE install shared by all plugins
└── pluginHost/                 gitignored: the AudioPluginHost built by -setup-host
```

A plugin is any folder here whose `CMakeLists.txt` calls `speaker_add_plugin()`. The build
scripts find plugins by that call, and read the channel counts and the LV2 URI from it, so
**adding a plugin needs no change to the tooling**. See [Adding a plugin](#adding-a-plugin).

## Building

Prerequisites: [Podman](https://podman.io) and the JUCE submodule
(`git submodule update --init juce_plugins/JUCE`).

```bash
tools/build-podman.sh -install-podman      # one-time, if Podman is missing
tools/build-podman.sh crossover            # image (first run) + JUCE (first run) + plugin
tools/build-podman.sh all                  # every plugin
tools/build-podman.sh -h                   # all steps
```

The target is **Debian 12 (bookworm) on ARM64**. It was developed on a Banana Pi CM4 IO
(Amlogic A311D), but any aarch64 board running bookworm should work. On a Linux x86_64 host
the container runs natively, so the cross-build runs at full speed. On macOS/Windows Podman
runs it in its own VM. One builder image and one JUCE install are shared by every plugin.
Each plugin's result lands in
`<plugin>/build-podman/plugin/<plugin>_artefacts/Release/LV2/<plugin>.lv2`.

| Step | |
|---|---|
| `-image`, `-setup-juce` | build the shared image / JUCE install (automatic when missing) |
| `-setup`, `-build` | configure / compile the plugin (aarch64 Release) |
| `-install` | deploy to the board (below) |
| `-clean`, `-clean-juce` | remove a plugin's build / the shared JUCE build |
| `-shell` | interactive shell in the builder container |
| `-setup-host`, `-debug-linux` | local debugging (below) |

## Deploying

Put the board's ssh address in `../config/local.env` (copied from `local.env.example`):

```
DEPLOY_HOST=user@my-board.local
DEPLOY_LV2_DIR=.lv2          # optional, the default
```

```bash
tools/build-podman.sh all -install
```

The bundle is streamed with `tar` over ssh into `~/.lv2` on the board. That has to be one
of the **default** LV2 search paths: JUCE's plugin host builds its LV2 world at startup from
`~/.lv2`, `/usr/lib/lv2` and `/usr/local/lib/lv2` only, and ignores `$LV2_PATH`. A plugin
deployed anywhere else is missing when a saved session is reopened. The remote folder has
to exist and be writable by you; the script tells you how to fix it if not. See
[lv2-discovery.md](../docs/juce-knowledge/hosting/lv2-discovery.md).

## Debugging locally (Linux)

```bash
tools/build-podman.sh -setup-host            # one-time: build JUCE's AudioPluginHost
tools/build-podman.sh loudness -debug-linux
```

`-debug-linux` makes a native x86_64 **Debug** build in the same container, installs it into
`~/.lv2` on this machine and opens it in the AudioPluginHost. The session
(`<plugin>/plugin_host_debug/<plugin>.filtergraph`) is created once, with the plugin wired
between audio in and out and its editor open. After that it's never overwritten, so your
changes to it survive rebuilds.

Gotchas, all handled or explained by the script. Details in
[audiopluginhost-linux.md](../docs/juce-knowledge/hosting/audiopluginhost-linux.md):

- **The host crashes on a PipeWire desktop** when it opens the audio device. The crash is
  inside PipeWire's ALSA path, not the plugin. The script starts the host with a private
  `XDG_CONFIG_HOME` whose `alsa/asoundrc` points ALSA's `default` at a real sound card.
  `PLUGIN_HOST_ALSA_CARD=<id|index>` picks another card. Don't choose "pipewire" in the
  host's audio settings.
- **A failed plugin load is silent.** "The host opened" doesn't mean the plugin loaded.
- **Fixed-size editors can be maximised** because JUCE 8.0.12's X11 code drops the size
  hints. [`patches/`](patches/) fixes this for the host that `-setup-host` builds, and a
  fixed-size editor should also call `setResizeLimits(w, h, w, h)`. Details in
  [x11-plugin-windows.md](../docs/juce-knowledge/gui/x11-plugin-windows.md).

## Adding a plugin

1. Create `juce_plugins/myPlugin/` with the sources and this `CMakeLists.txt`:
   ```cmake
   cmake_minimum_required(VERSION 3.22)
   project(MY_PLUGIN VERSION 0.0.1)
   find_package(JUCE CONFIG REQUIRED)
   include(${CMAKE_CURRENT_LIST_DIR}/../cmake/SpeakerPlugin.cmake)

   speaker_add_plugin(myPlugin
       CODE    Mypl          # unique across plugins: VST3/AU hosts key on it
       INPUTS  1             # must match the processor's BusesProperties
       OUTPUTS 1
       SOURCES PluginProcessor.cpp PluginEditor.cpp
       MODULES juce_dsp)     # beyond juce_audio_utils/processors, gui_basics/extra
   ```
2. `tools/build-podman.sh myPlugin`. That's all.

The folder name is the target, product, binary and URI name. The LV2 URI becomes
`<SPEAKER_LV2_URI_BASE>/myPlugin`, which is unique by construction. Hosts key their plugin
database on that URI, so two plugins sharing one collide and only one survives a scan.

## Control and headless builds

For the remote plugin host (controller app plus headless speakers, see
`docs/remote-plugin-host.md` in bpi_amp_platform) a plugin can also be built without its DSP
and without its editor. List the sources by role and name the targets:

```cmake
speaker_add_plugin(loudness
    CODE    Loud
    INPUTS  1
    OUTPUTS 1
    SOURCES PluginProcessor.cpp                        # parameters, state: every target
    DSP     LoudnessDsp.cpp                            # left out of the control build
    EDITOR  PluginEditor.cpp MorphPad.cpp HostWindowTracker.cpp   # left out of the headless build
    MODULES juce_dsp
    TARGETS complete control headless)
```

| Target | CMake target | Contains | Runs in |
|---|---|---|---|
| complete | `loudness` | everything, as before | the desktop plugin host, tuning |
| control | `loudness_control` | parameters and editor, no DSP | the controller app |
| headless | `loudness_headless` | parameters and DSP, no editor | `speakerd` on the speaker |

The sources see `SPEAKER_COMPLETE`, `SPEAKER_CONTROL` and `SPEAKER_HEADLESS` (0 or 1). The
control build must still define `prepareToPlay`, `reset` and `processBlock`, as empty stubs;
the headless build's `hasEditor()` returns false. `tools/build-podman.sh` keeps building the
complete target only. [`../juce_apps/plugin-check`](../juce_apps/plugin-check/README.md)
checks that the three builds fit together. So far `loudness` is split this way.

## JUCE

`JUCE/` is pinned to the commit these plugins compile against. When you need to know what
JUCE *actually* does, read the source there (`modules/`, `docs/CMake API.md`, `examples/`,
`extras/`) before trusting anything found online for another version. Hard-won findings go
in [`docs/juce-knowledge/`](../docs/juce-knowledge/).

**License:** AGPLv3, like the rest of the repository (see [`../LICENSE`](../LICENSE)). This
matches JUCE's own open-source license.
