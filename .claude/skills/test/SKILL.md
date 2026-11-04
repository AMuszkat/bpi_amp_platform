---
name: test
description: Run the tests. Checks are focused and take seconds to two minutes: `/test plugin <name>`, `plugins`, `speakerd`, `ampd`, `pair`, `controller`, `installers`, `chain`, `tools`, `module <name>...`, several in one command, and `/test changed` runs the checks that cover the files changed. The virtual pair's scenarios run several at a time (`--jobs N`). The levels are end to end and run only when asked and before a push: `/test unit` (level 1: every plugin-check with LTO, the aarch64 build, the block FIR under qemu, the controller, the tools), `/test vpair` (level 2: every virtual pair scenario), `/test all` (both; what CI runs, there one at a time). Use a check after changing a plugin, speakerd, ampd, the controller, an installer or a scenario; the levels before a push, or when asked whether the build is green.
---

# /test

```
tools/test.sh <check> [args]... [--jobs N]                  while working; several in one run: tools/test.sh plugin loudness speakerd installers (module last)
tools/test.sh changed [REF] [--jobs N]                      the checks that cover the files changed since REF, as one run
tools/test.sh unit | vpair | all [--jobs N] [--no-record]   the levels, end to end: when asked, and before a push
```

Runs from the repository root, in the development container when Podman is installed
(`tools/container.sh`), natively with `BPI_CONTAINER=0`. Times measured on 24 threads (12 jobs),
build trees up to date but for the edit.

| Check | What runs | Takes |
|---|---|---|
| `plugin <name>` | `plugin-check/run.sh <name>` without LTO, in `build-check-nolto`: the plugin's three builds fit together; its bundles' build identity (with LTO, in `build-check`, where `CI` is set or with `BPI_TEST_LTO=1`) | 10 s after an edit; 8 s for a new worktree's first run once ccache holds the objects |
| `plugins` | the same with LTO, in `build-check`, for every plugin with three targets; the block FIR test; the plugin-versions test | seconds when nothing changed; about 45 s a plugin after a change to their shared code |
| `speakerd` | the `speakerd_*` scenarios, `audio_path`, `passthrough_safety`: speakerd alone on the virtual pair | 20 s; 35 s for a new worktree's first run |
| `ampd` | `ampd_protocol`, `two_controllers`: ampd against a stand-in speakerd, no audio | 30 s |
| `pair` | `knob_latency`, `graph_edit`, `hub_down`, `drag_during_revision`, `ampd_restart`, `ampd_persistence`, `meter_values`, `monitor_fanout`, `board_cpu`, `buffer_size` | 70 s (`monitor_fanout` alone takes 64 s) |
| `controller` | the controller built, its tests against the fake hub, `controller_end_to_end` | 2 min |
| `installers` | `firmware_speakerd_install`, `firmware_ampd_install`, `speakerd_installed`, `speakerd_crash_restart` | 25 s |
| `chain` | `plugin_identity`, then `chain_cost` alone over its quick 5 s window: the board's chain | 15 s, plus the LTO relink of an edited plugin (about 25 s) |
| `tools` | `tools/tests/test_*.py` (status.py and the hook's lab lock, impact.py and `tests/impact.json`, `tools/test.sh`'s arguments) and `tests/vpair/*_selftest.py` (the pool's) | 5 s |
| `module <name>...` | the named scenarios of `tests/vpair` | |

The scenarios run `--jobs N` at a time (anywhere on the line, or `BPI_TEST_JOBS=N`), each on a
virtual pair of its own, through `tests/vpair/pool.py`: one build first of what they all need
(each module's `POOL_BUILDS`), the longest first, `chain_cost` alone at the end, each module's
output whole as it ends (kept in `build/vpair-pool/`), then a table and the note. N is 1 to 64;
the default is half the processors, at most 16, and 1 where `CI` is set. `--jobs 1` runs them one
after another in one process, as CI does (level 2 then takes about 9.5 min). One pool runs at a
time per checkout; a second waits and says so. Where `CI` is set the defaults are CI's: one job,
LTO in `plugin <name>`, chain_cost's full window, no ccache. The levels always build with LTO and
measure the full window.

Several checks in one command run as one: what needs no virtual pair first (`plugin`, `plugins`,
`tools`, the controller's build and its own tests), then every scenario they name, once each, in
one pool run. `module` takes the rest of the line, so it goes last.

`changed` asks `tools/impact.py`, which maps the files changed (uncommitted, untracked, and
since the merge base with `develop` when on a branch; `REF` instead) to checks through
`tests/impact.json`: a changed plugin runs its own check, and `chain` unless only its editor
changed; a speakerd file runs `speakerd` and the checks that read what that file is for (each
rule's `why` says which); a scenario runs itself and what imports it. A file no rule names is
printed: only the levels cover it. The map is narrow on purpose, and a rule's `by_hand` advice is
printed before and after the checks: an edit of `speakerd/Engine.*` runs `speakerd` alone, so
after touching its parameter inbox, `Engine::install` or its level and timing rings, run
`tools/test.sh speakerd pair chain installers`. After an Engine edit `changed` takes about 17 s,
after a plugin DSP edit about 45 s.

The levels: `unit` is every `plugin` check with LTO (in `build-check`) plus the aarch64 build
identity, the block FIR under qemu, the plugin-versions test, `controller` and `tools`; `vpair`
is every scenario of `tests/vpair`, on the pool. `all` takes about 4 min on up-to-date trees
(unit 2.5, vpair 1.5), 6.5 min when the plugin-check trees are new. Each records its result for
the commit with `tools/status.py` (`tests/.status/<commit>.json`; `--no-record` not to);
`tools/status.py gate` says whether a commit is green: both on a clean tree. Nothing enforces it.

Read the output, not just the exit status: a scenario run ends with a note (tests run, failed,
skipped and why), and on the pool a table names the modules that failed. `plugin <name>` writes
`dsp/juce_apps/plugin-check/build-check-nolto/<name>-report.txt` (`plugins`, the levels and CI:
`build-check/<name>-report.txt`), with a screenshot of the control editor beside it. Exit status
3 with "BPI_TEST_DRY_RUN=1: nothing ran" means that variable is set by mistake: unset it. When a
check fails, quote the failing line and fix the cause; never mark a level as passed by hand.
