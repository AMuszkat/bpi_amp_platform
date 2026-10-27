---
name: build
description: Build a plugin or a JUCE application of the remote plugin host. `/build <plugin> [complete|control|headless|all]` builds it natively for this machine; `/build <plugin> aarch64` cross-compiles it for the board with the Podman image; `/build plugin-check` builds the checker. Use instead of writing cmake lines by hand.
---

# /build

```
tools/build.sh <plugin> [complete|control|headless|all]   # native, into build/<plugin>/
tools/build.sh <plugin> aarch64                            # dsp/juce_plugins/tools/build-podman.sh <plugin>
tools/build.sh plugin-check                                # native, into build/plugin-check/
```

Native builds need a JUCE install in `JUCE_INSTALL_DIR` (root `CLAUDE.md` says how to make
one). The LV2 bundle of a native build lands in
`build/<plugin>/<target>_artefacts/Release/LV2/<target>.lv2`, and a copy in `~/.lv2` and
`~/.vst3`, which is where a local plugin host looks.

The aarch64 build is the existing Podman cross-build; read
`dsp/juce_plugins/README.md` before touching it, and never deploy from here: deploying is
`build-podman.sh <plugin> -install`, which the deploy gate guards.

A build warning in our own sources (not under the JUCE install) is a defect: fix it. Warnings
from JUCE's own modules are not ours.
