# JUCE CMake: how this repo uses it

The full API reference is `juce_plugins/JUCE/docs/CMake API.md` (every `juce_add_plugin`
option, module handling, binary data). This file covers only how *this* repo consumes JUCE,
and the traps that came up. Verified against JUCE 8.0.12.

## One wrapper for every plugin

Each plugin's `CMakeLists.txt` is a single `speaker_add_plugin()` call
(`juce_plugins/cmake/SpeakerPlugin.cmake`, whose header comment documents the arguments).
It wraps `juce_add_plugin` and fixes what must be the same everywhere:

- `FORMATS LV2 VST3`, `PLUGIN_MANUFACTURER_CODE Juce` (may be shared), `PLUGIN_CODE` from
  `CODE` (must be unique; VST3/AU hosts key on it).
- `LV2URI "${SPEAKER_LV2_URI_BASE}/<target>"`. The URI is the plugin's identity in every LV2
  host, so it is unique by construction. Changing the base orphans saved sessions; see
  [`../hosting/lv2-discovery.md`](../hosting/lv2-discovery.md).
- Target name = `PRODUCT_NAME` = `LV2_SHARED_LIBRARY_NAME` = URI tail = folder name.
  `tools/build-podman.sh` builds `<name>_LV2` and expects `<name>.lv2`, and `tools/common.sh`
  finds plugins by grepping for `speaker_add_plugin(`. It also reads `INPUTS`/`OUTPUTS` from
  that call with `sed` to wire the debug session, so keep each on its own line.
- `JUCE_WEB_BROWSER=0`, `JUCE_USE_CURL=0` (re-enabling either also needs `NEEDS_WEB_BROWSER`
  / `NEEDS_CURL TRUE` in `juce_add_plugin`), `JUCE_VST3_CAN_REPLACE_VST2=0`.

## Adding a file to a plugin bundle: before JUCE's copy step

`buildinfo.ttl` (the version and source identity, `juce_plugins/README.md`) is written into
every LV2 bundle after JUCE has built it. JUCE's LV2 wrapper has no hook for extra metadata, so
it is a `POST_BUILD` step on `<target>_LV2`, and the order of `POST_BUILD` steps is the order
in which they were added (`extras/Build/CMake/JUCEUtils.cmake`):

1. `juce_add_plugin()` adds, per format, the manifest helper: for LV2, `juce_lv2_helper`, which
   rewrites (truncates) `manifest.ttl`, `dsp.ttl` and `ui.ttl` at every link
   (`_juce_set_plugin_target_properties`; the writer is `writeManifestTtl` in
   `juce_audio_plugin_client_LV2.cpp`).
2. At its very end, if `COPY_PLUGIN_AFTER_BUILD` is set, it calls
   `juce_enable_copy_plugin_step()`, which adds the copy of the whole bundle into
   `JUCE_LV2_COPY_DIR` (`~/.lv2`, or a cross build's `copied/lv2`, `tools/build.sh`) with
   `copyDir.cmake` (remove the old copy, then install the bundle).

A step added after `juce_add_plugin()` returns would run after the copy, and the copy would lack
the file. So `speaker_add_plugin()` passes `COPY_PLUGIN_AFTER_BUILD FALSE`, adds its step, and
then calls `juce_enable_copy_plugin_step(<target>)` itself, which is what `docs/CMake API.md`
recommends for "extra build steps (such as signing or modifying the plugin bundle)". The
bundle's path is the `JUCE_PLUGIN_ARTEFACT_FILE` property of `<target>_LV2` (not of the
shared-code target). The VST3 manifest step is unaffected: it is added during
`juce_add_plugin()`, and `juce_enable_vst3_manifest_step` refuses only to run after a copy
step that is already there.

**Do not name an extra file in `manifest.ttl`.** The helper rewrites the manifest whenever the
plugin is linked, so a line added to it would have to be added again after every helper run;
but the reason not to is lilv. A file the manifest names with `rdfs:seeAlso <file>` for the
plugin's URI is plugin data to lilv: when a host first asks about the plugin, lilv parses every
such file (`lilv_plugin_load`, in
`juce_audio_processors_headless/format_types/LV2_SDK/lilv/src/plugin.c`), and a parse error in
any of them sets `parse_errors`, after which `lilv_plugin_instantiate` (`instance.c`, what
JUCE's `LV2PluginFormat` calls) returns null and `lilv_plugin_verify` false. lilv also parses
them when two bundles have the same URI, to compare their versions (`load_plugin_model`,
`world.c`; [`../hosting/lv2-discovery.md`](../hosting/lv2-discovery.md)). So a half-written
`buildinfo.ttl` named in the manifest made the plugin fail to instantiate in `speakerd`
(`lilv_world_load_file(): error ... end of file in short string`, then the layer failed and the
speaker played passthrough). `buildinfo.ttl` is diagnostic and no LV2 host needs it, so the
manifest stays exactly as the helper writes it, and `speakerd` reads the file directly; a
damaged copy then costs a log line, never the plugin. Checked with `speakerd --render` on a
firConv bundle whose `buildinfo.ttl` is cut after 60 bytes: with the `rdfs:seeAlso` line,
`invalid IRI character`, `did not instantiate: Plugin was located, but could not be opened`,
passthrough; without it, the layer loads.

**Data that must reach the binary.** A plugin is a static shared-code library
(`<target>`) linked into one module per format (`<target>_LV2`, `<target>_VST3`, listed in
the `JUCE_ACTIVE_PLUGIN_TARGETS` property). An object in that archive that nothing references
is never linked. The build-info string is therefore a generated source added to each format
target (an object on the link line is always linked), with `__attribute__ ((used))` so that
LTO keeps it although nothing reads it. Check with `strings <binary> | grep bpi-build-info`.

**`include_guard(GLOBAL)` and variables.** A helper file included once per build with a global
guard defines its functions for everyone, but its variables (`SPEAKER_LV2_URI_BASE`) only in
the directory that included it first: a second plugin added to the same build (the controller
adds plugins with `add_subdirectory`) would see none of them. `SpeakerPlugin.cmake` uses
`include_guard(DIRECTORY)`.

## `-flto=auto` instead of `juce_recommended_lto_flags`

For GCC/Clang `juce::juce_recommended_lto_flags` passes a bare `-flto`, which runs the LTO
link phase (LTRANS) single-threaded, the slowest step of the build. The wrapper adds
`-flto=auto` for Release instead: same binary, one LTRANS job per core.

## JUCE is consumed as an installed package, built once

The plugins use `find_package(JUCE CONFIG REQUIRED)`, not `add_subdirectory(JUCE)`.
`build-podman.sh -setup-juce` configures the submodule **natively** (no toolchain file) and
installs it into `juce_plugins/build-podman/juce-install`. That install is mostly a source
distribution plus `juceaide`, the helper JUCE runs at build time to generate `JuceHeader.h`,
plists and similar. Because `juceaide` was built for the build machine, one install serves
both the aarch64 cross-build and the native Debug build.

The cross configure then needs, besides the toolchain file
(`juce_plugins/tools/toolchain-aarch64-linux.cmake`):

- `-DCMAKE_PREFIX_PATH=<juce-install>` **and** `-DCMAKE_FIND_ROOT_PATH="/;<juce-install>"`.
  A cross toolchain sets strict find-root modes, under which `find_package` can't see a
  prefix that is outside the find root.
- `PKG_CONFIG_LIBDIR=/usr/lib/aarch64-linux-gnu/pkgconfig:/usr/share/pkgconfig`, so JUCE's
  `pkg_check_modules` resolves the `:arm64` libraries (ALSA, freetype, …), not the host ones.
- `-DCMAKE_CROSSCOMPILING_EMULATOR=/usr/bin/qemu-aarch64-static`. After linking, JUCE builds
  and *runs* a helper from the plugin binary to write the LV2 `.ttl` manifests and the
  VST3 `moduleinfo.json`. Cross-compiled, that helper is an aarch64 executable, so CMake
  must run it under qemu.

A plugin build directory configured against a JUCE install that has since moved keeps the
stale path in its cache; the script reconfigures with `--fresh` when it detects that.

## Compile definitions a target sets can't be overridden with `-D`

`target_compile_definitions(... PRIVATE FOO=1)` on a JUCE target beats any `-DFOO=…` cache
entry, and defining it twice with different values is worse. A config macro can only be
injected from the command line if the target leaves it unset and the module header guards
it with `#ifndef` (e.g. `JUCE_JACK`, but not AudioPluginHost's `JUCE_PLUGINHOST_LADSPA`).
Details in [`../hosting/audiopluginhost-linux.md`](../hosting/audiopluginhost-linux.md) §1 and §4.
