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
