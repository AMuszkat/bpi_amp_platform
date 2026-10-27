---
name: build
description: Build a plugin or a JUCE application of the remote plugin host. `/build <plugin> [complete|control|headless|all]` builds it for x86_64 (in the development container, or natively); `/build <plugin> aarch64` cross-compiles it for the board with the Podman image; `/build <app>` builds an application of dsp/juce_apps (plugin-check, speakerd). Use instead of writing cmake lines by hand.
---

# /build

```
tools/build.sh <plugin> [complete|control|headless|all]   # x86_64, into build/<plugin>/
tools/build.sh <plugin> aarch64                            # dsp/juce_plugins/tools/build-podman.sh <plugin>
tools/build.sh <app>                                       # dsp/juce_apps/<app>, into build/<app>/
```

The x86_64 builds run in the development container when Podman is installed
(`tools/container.sh`; `BPI_CONTAINER=0` builds natively), against the JUCE install in
`JUCE_INSTALL_DIR`, by default `build/juce-install` of the main checkout, which the first
build makes. The LV2 bundle lands in
`build/<plugin>/<target>_artefacts/Release/LV2/<target>.lv2`, and a copy in `~/.lv2` and
`~/.vst3` of wherever it was built: in the container that is the container's own home, which
is where `speakerd` and plugin hosts running in the container look.

The aarch64 build is the existing Podman cross-build; read
`dsp/juce_plugins/README.md` before touching it, and never deploy from here: deploying is
`build-podman.sh <plugin> -install`, which the deploy gate guards.

A build warning in our own sources (not under the JUCE install) is a defect: fix it. Warnings
from JUCE's own modules are not ours.
