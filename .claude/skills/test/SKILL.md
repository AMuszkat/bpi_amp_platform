---
name: test
description: Run the tests. Checks are focused and take minutes: `/test plugin <name>`, `plugins`, `speakerd`, `ampd`, `pair`, `controller`, `installers`, `chain`, `tools`, `module <name>...`, and `/test changed` runs the checks that cover the files changed. The levels are end to end and run only when asked and before a push: `/test unit` (level 1: every plugin-check, the aarch64 build, the block FIR under qemu, the controller, the tools), `/test vpair` (level 2: every virtual pair scenario), `/test all` (both; what CI runs). Use a check after changing a plugin, speakerd, ampd, the controller, an installer or a scenario; the levels before a push, or when asked whether the build is green.
---

# /test

```
tools/test.sh <check> [args]      minutes: while working (several: tools/test.sh speakerd installers)
tools/test.sh changed [REF]       the checks that cover the files changed since REF
tools/test.sh unit | vpair | all  the levels, end to end: when asked, and before a push
```

Runs from the repository root, in the development container when Podman is installed
(`tools/container.sh`), natively with `BPI_CONTAINER=0`.

| Check | What runs | Takes |
|---|---|---|
| `plugin <name>` | `plugin-check/run.sh <name>`: the plugin's three builds fit together; its bundles' build identity | 2 to 3 min |
| `plugins` | `plugin <name>` for every plugin with three targets, the block FIR test, the plugin-versions test | 10 min |
| `speakerd` | the `speakerd_*` scenarios, `audio_path`, `passthrough_safety`: speakerd alone on the virtual pair | 1.5 min |
| `ampd` | `ampd_protocol`, `two_controllers`: ampd against a stand-in speakerd, no audio | 1.5 min |
| `pair` | `knob_latency`, `graph_edit`, `hub_down`, `drag_during_revision`, `ampd_restart`, `ampd_persistence`, `meter_values`, `monitor_fanout`, `board_cpu` | 3.5 min |
| `controller` | the controller built, its tests against the fake hub, `controller_end_to_end` | 2 min |
| `installers` | `firmware_speakerd_install`, `firmware_ampd_install`, `speakerd_installed`, `speakerd_crash_restart` | 2.5 min |
| `chain` | `chain_cost`, `plugin_identity`: the board's chain | 0.5 min |
| `tools` | `tools/tests/`: status.py, impact.py, the hook's lab lock | seconds |
| `module <name>...` | the named scenarios of `tests/vpair` | |

`changed` asks `tools/impact.py`, which maps the files changed (uncommitted, untracked, and
since the merge base with `develop` when on a branch; `REF` instead) to checks through
`tests/impact.json`: a changed plugin runs its own check, a scenario runs itself and what
imports it. A file no rule names is printed: only the levels cover it.

The levels: `unit` is every `plugin` check plus the aarch64 build identity, the block FIR
under qemu, the plugin-versions test, `controller` and `tools`; `vpair` is every scenario
of `tests/vpair`. Each records its result for the commit with `tools/status.py`
(`tests/.status/<commit>.json`; `--no-record` not to); `tools/status.py gate` says whether
a commit is green: both on a clean tree. Nothing enforces it.

Read the output, not just the exit status: a scenario run ends with a note (tests run,
failed, skipped and why), and `plugin` writes
`dsp/juce_apps/plugin-check/build-check/<name>-report.txt` with a screenshot of the control
editor beside it. When a check fails, quote the failing line and fix the cause; never mark a
level as passed by hand.
