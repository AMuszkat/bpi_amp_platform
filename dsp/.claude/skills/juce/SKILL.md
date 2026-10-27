---
name: juce
description: JUCE framework expertise for this repo's LV2/VST3 plugins (crossover, firConv, loudness, ampScope). Use for any question or change involving JUCE APIs, juce_add_plugin / speaker_add_plugin CMake, the aarch64 Podman cross-build, LV2 URIs and host discovery, the AudioPluginHost -debug-linux flow (PipeWire crash, .filtergraph sessions), juce::dsp filters and realtime-safe coefficient updates, or plugin editor windows on X11 (resize limits, floating panels, HiDPI scale, repaint cost).
---

# JUCE in this repo

Context that applies to every answer:

- **JUCE 8.0.12**, pinned as the submodule `juce_plugins/JUCE/`
  (`git submodule update --init juce_plugins/JUCE` if it is empty). JUCE 8 renamed and
  removed APIs, so answers written for JUCE 6/7 are often wrong here.
- Formats are **LV2 + VST3** only, on Linux. The target is an ARM64 board (Debian bookworm,
  cross-built in Podman). The local debug loop is the JUCE AudioPluginHost on an x86_64
  desktop (X11, PipeWire).
- Plugins live in `juce_plugins/<name>/`. Shared CMake is `juce_plugins/cmake/SpeakerPlugin.cmake`
  and the build is `juce_plugins/tools/build-podman.sh`. Read `juce_plugins/README.md` and the
  plugin's own README before changing a plugin.

## 1. Read the matching knowledge file first

All under `docs/juce-knowledge/`. Read every file whose topic matches; skip the rest.

| File | Read it for |
|---|---|
| `build-system/cmake-reference.md` | `speaker_add_plugin` / `juce_add_plugin`, the installed-JUCE + `juceaide` setup, cross-compile flags (find root, pkg-config, qemu for the LV2/VST3 manifest helpers), LTO, compile definitions that can't be overridden with `-D` |
| `hosting/lv2-discovery.md` | "Unable to locate plugin with the requested URI", plugin missing after reopen or only one of two showing up, `$LV2_PATH` ignored, `~/.lv2` deployment, unique `LV2URI` / `PLUGIN_CODE` |
| `hosting/audiopluginhost-linux.md` | `-setup-host` / `-debug-linux`, building AudioPluginHost (`ladspa.h`), hand-written `.filtergraph`, silent load failures, verifying a session headlessly, the PipeWire SIGSEGV and the `XDG_CONFIG_HOME` asoundrc fix, `prepareToPlay(0, 0)`, UTF-8 literals asserting in `juce::String` |
| `gui/x11-plugin-windows.md` | fixed-size editor maximised or stretched (`setResizeLimits`, the X11 size-hint patch in `juce_plugins/patches/`), floating panels/extra windows from an embedded editor (focus, override-redirect, scale factor, stale screen position, `JUCE_GUI_BASICS_INCLUDE_XHEADERS`), slow Debug repaints at HiDPI and image caching |
| `dsp/modulated-filter-topology.md` | parameters changing while audio runs: biquad vs SVF, TDF-II vs DF-I, zipper/clicks, ramping, float32 pole conditioning, allocation-free `ArrayCoefficients` updates |

## 2. Verify against the pinned source

The submodule outranks memory and the web. For what JUCE actually does:

- Grep `juce_plugins/JUCE/modules/`. Header doc comments are the API reference; the `.cpp`
  is the only answer for undocumented behaviour.
- `juce_plugins/JUCE/docs/CMake API.md` for every CMake option; `examples/` and `extras/`
  (AudioPluginHost source is in `extras/AudioPluginHost/`) for working usage.
- `BREAKING_CHANGES.md` in the submodule when an API seems to be missing.

Go online (docs.juce.com, github.com/juce-framework/JUCE, forum.juce.com) only when the tree
can't answer. Say which JUCE version an online answer is for, and check it against the
pinned source before relying on it.

## 3. Answer

- Cite `file:line` in the submodule for claims about JUCE behaviour, and say when something
  is inferred rather than read.
- Fit the repo: new plugins go through `speaker_add_plugin`, builds through
  `build-podman.sh`, machine-specific values through `config/local.env` (never hardcoded).
  Audio-thread code must not allocate or lock.
- Use repo code as the worked example when it exists (e.g. `loudness/PluginProcessor.cpp`
  for smoothed coefficient updates, `loudness/HostWindowTracker.cpp` for X11 access).

## 4. Write back what was hard to find

If finding the answer took real digging (reading JUCE internals, a misleading symptom, a
measurement), add it to the matching file in `docs/juce-knowledge/`, or create a new one in
the right subfolder. Then add a row to the table in step 1 of this skill. Record the JUCE
version, the source location and how it was verified. Keep machine-specific details
(hostnames, user names, card IDs beyond an example) out of it, because the repo is public.
