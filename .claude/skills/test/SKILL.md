---
name: test
description: Run the remote plugin host's tests and record the result (tools/status.py). `/test unit` runs plugin-check on every plugin that builds control and headless targets, the build identity checks, the block FIR test (x86_64 and aarch64 under qemu), the plugin-versions test, the controller's test and the test of tools/status.py and the lab lock (rung 1); `/test vpair` runs the virtual pair tests (rung 2); `/test all` runs both; `/test affected` runs only the virtual pair tests that cover the files changed since the last commit green in full, and refuses when a changed file needs both rungs. Use before any deploy, after changing a plugin, speakerd, ampd or tests/vpair, and whenever asked whether the build is green.
---

# /test

```
tools/test.sh unit | vpair | all | affected
```

Runs from the repository root, in the development container when Podman is installed
(`tools/container.sh`), natively with `BPI_CONTAINER=0`. Every run records its result for the current commit with
`tools/status.py`, in `tests/.status/<commit>.json`. `tools/status.py gate` says whether a
commit is green: `unit` and `vpair` both `pass` on a clean tree, or a passing `affected` run
that covers what changed since a commit green in full. Nothing enforces it before a deploy
(2026-11-02, the person's decision).

| Rung | What runs | Natively, needs |
|---|---|---|
| `unit` | `dsp/juce_apps/plugin-check/run.sh <plugin>` for each plugin whose `CMakeLists.txt` lists `TARGETS`, and `tests/buildinfo/check.sh` on the bundles it built; `tools/build.sh loudness aarch64 headless` and the same check on it and its copy; `dsp/juce_plugins/tests/blockfir/run.sh`; `dsp/juce_plugins/tests/plugin-versions/run.sh`; the controller's `tests/run.sh` | the JUCE build packages; Xvfb and openbox for the editor check, else it is skipped; for the aarch64 half, which fails without them, the cross compiler, `qemu-user-static` and the `:arm64` libraries of `dsp/juce_plugins/tools/Containerfile`. The JUCE install is made on first use |
| `vpair` | `python3 -m unittest discover -s tests/vpair` | `pipewire`, `wireplumber`, `dbus`, `python3-numpy` |
| `affected` | the `tests/vpair` modules `tools/status.py plan` names; nothing of `unit` | as `vpair` |

The container has all of them. `unit` also runs `tools/tests/test_status_gate.py`, the test of
`tools/status.py` and of the hook's lab lock.

**`affected`** is for a fix on top of a commit green in full, typically one found at the
bench: an installer, the board script, a test module, the documentation. `tools/status.py plan`
finds the nearest commit green in full among HEAD and its ancestors (the base), lists the
files changed since (in the working tree, untracked ones included) and looks each one up in
the base's `tests/impact.json`: the first rule that matches says which test modules run again
(`self` is a changed test module itself; a module that imports one that runs runs too). A
file that no rule names (the plugins, `speakerd`, the controller, `ampd.py`, the tweeter rule,
the harness, the build and test tools, the gate, the map itself, CI) needs both rungs:
`affected` names it and stops, recording nothing. Otherwise it runs those modules, or nothing
when only documentation changed, and records `affected`, its base and what ran. No part of
`unit` runs: what `unit` covers always needs the full run. The gate does not take the record's
word for what changed or what had to run: it recomputes the files changed between the base and
HEAD, and the modules they need, from git and the base's map. A full run that failed on a clean
tree, on HEAD or since the base, bars an affected run until a full run passes. The base must be
green in full, not by an affected run, so fixes add up against the same base until the next
full run.

Read the report, not just the exit status. `vpair` writes a `vpair_note` into the status
file: how many tests ran, which failed and which were skipped and why, so a pass with skipped
scenarios is visible. `unit` writes
`dsp/juce_apps/plugin-check/build-check/<plugin>-report.txt` with one line per check, and a
screenshot of the control editor next to it. When a check fails, quote the failing line in
your answer and fix the cause; never mark a rung as passed by hand with `tools/status.py`.
