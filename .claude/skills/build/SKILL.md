---
name: build
description: Build a plugin or a JUCE application of the remote plugin host. `/build <plugin> [complete|control|headless|all]` builds it for x86_64 (in the development container, or natively); `/build <plugin|app> aarch64 [complete|control|headless|all]` cross-compiles it for the board into build/aarch64/; `/build <app>` builds an application of dsp/juce_apps (plugin-check, speakerd). Use instead of writing cmake lines by hand.
---

# /build

```
tools/build.sh <plugin> [complete|control|headless|all]           # x86_64, into build/<plugin>/
tools/build.sh <app>                                               # dsp/juce_apps/<app>, into build/<app>/
tools/build.sh <plugin|app> aarch64 [complete|control|headless|all] # the board, into build/aarch64/<what>/
```

Anything after the target kind is refused (`tools/build.sh loudness headless aarch64` is an
error, not an x86_64 build). The builds run in the development container when Podman is installed
(`tools/container.sh`; `BPI_CONTAINER=0` builds natively), against the JUCE install in
`JUCE_INSTALL_DIR`, by default `build/juce-install` of the main checkout, which the first
build makes. Plugins without `TARGETS` build only their complete target (`all` is then just
`complete`; `control` or `headless` is an error).

**x86_64.** The LV2 bundle lands in
`build/<plugin>/<target>_artefacts/Release/LV2/<target>.lv2`, and a copy in `~/.lv2` and
`~/.vst3` of wherever it was built: in the container that is the container's own home, which
is where `speakerd` and plugin hosts running in the container look.

**aarch64**, for the board (Debian bookworm). The same layout under `build/aarch64/`:

- `build/aarch64/speakerd/speakerd_artefacts/Release/speakerd`
- `build/aarch64/<plugin>/<target>_artefacts/Release/LV2/<target>.lv2`

It cross-compiles with the native JUCE install (its `juceaide` runs here), the toolchain file
`dsp/juce_plugins/tools/toolchain-aarch64-linux.cmake`, the image's `:arm64` libraries through
pkg-config (`PKG_CONFIG_LIBDIR`) and `qemu-aarch64-static` for the LV2 manifest helper. It never
copies an aarch64 bundle into a `~/.lv2`, where an x86_64 host would load it under the URI of
its own build: the copy directories are pinned into the build tree (`copy-dirs.cmake`, kept in
its CMake cache), the build runs with `HOME=build/aarch64/home`, and the script fails if it
finds an aarch64 binary in `~/.lv2`. It checks that what it built is aarch64 code. The
libraries the binaries need on the board are in `dsp/juce_apps/speakerd/README.md`. To run an
aarch64 binary here: `qemu-aarch64-static <binary>` (the image's multiarch libraries are
found without `-L`), with an empty `HOME` when it loads plugins.

This replaces the old `aarch64` path, which ran `dsp/juce_plugins/tools/build-podman.sh
<plugin>`. That script stays as it is for its own flows (its own JUCE install, the debug host);
read `dsp/juce_plugins/README.md` before touching it. Never deploy from here: deploying goes
through the deploy gate.

A build warning in our own sources (not under the JUCE install) is a defect: fix it. Warnings
from JUCE's own modules are not ours.
