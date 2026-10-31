---
name: test
description: Run the remote plugin host's tests and record the result for the deploy gate. `/test unit` runs plugin-check on every plugin that builds control and headless targets, the build identity checks, the block FIR test (x86_64 and aarch64 under qemu), the plugin-versions test and the controller's test (rung 1); `/test vpair` runs the virtual pair tests (rung 2); `/test all` runs both. Use before any deploy, after changing a plugin, speakerd, ampd or tests/vpair, and whenever asked whether the build is green.
---

# /test

```
tools/test.sh unit | vpair | all
```

Runs from the repository root, in the development container when Podman is installed
(`tools/container.sh`), natively with `BPI_CONTAINER=0`. Every run records its result for the current commit with
`tools/status.py`, in `tests/.status/<commit>.json`; the deploy gate in
`.claude/hooks/pre-bash.py` refuses to let anything reach a board unless both `unit` and
`vpair` read `pass` for the exact commit, with a clean tree.

| Rung | What runs | Natively, needs |
|---|---|---|
| `unit` | `dsp/juce_apps/plugin-check/run.sh <plugin>` for each plugin whose `CMakeLists.txt` lists `TARGETS`, and `tests/buildinfo/check.sh` on the bundles it built; `tools/build.sh loudness aarch64 headless` and the same check on it and its copy; `dsp/juce_plugins/tests/blockfir/run.sh`; `dsp/juce_plugins/tests/plugin-versions/run.sh`; the controller's `tests/run.sh` | the JUCE build packages; Xvfb and openbox for the editor check, else it is skipped; for the aarch64 half, which fails without them, the cross compiler, `qemu-user-static` and the `:arm64` libraries of `dsp/juce_plugins/tools/Containerfile`. The JUCE install is made on first use |
| `vpair` | `python3 -m unittest discover -s tests/vpair` | `pipewire`, `wireplumber`, `dbus`, `python3-numpy` |

The container has all of them.

Read the report, not just the exit status. `vpair` writes a `vpair_note` into the status
file: how many tests ran, which failed and which were skipped and why, so a pass with skipped
scenarios is visible. `unit` writes
`dsp/juce_apps/plugin-check/build-check/<plugin>-report.txt` with one line per check, and a
screenshot of the control editor next to it. When a check fails, quote the failing line in
your answer and fix the cause; never mark a rung as passed by hand with `tools/status.py`.
