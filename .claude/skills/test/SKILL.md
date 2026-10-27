---
name: test
description: Run the remote plugin host's tests and record the result for the deploy gate. `/test unit` runs plugin-check on every plugin that builds control and headless targets (rung 1); `/test vpair` runs the virtual pair tests (rung 2); `/test all` runs both. Use before any deploy, after changing a plugin, speakerd, ampd or tests/vpair, and whenever asked whether the build is green.
---

# /test

```
tools/test.sh unit | vpair | all
```

Runs from the repository root. Every run records its result for the current commit with
`tools/status.py`, in `tests/.status/<commit>.json`; the deploy gate in
`.claude/hooks/pre-bash.py` refuses to let anything reach a board unless both `unit` and
`vpair` read `pass` for the exact commit, with a clean tree.

| Rung | What runs | Needs |
|---|---|---|
| `unit` | `dsp/juce_apps/plugin-check/run.sh <plugin>` for each plugin whose `CMakeLists.txt` lists `TARGETS` | a JUCE install in `JUCE_INSTALL_DIR` (see the root `CLAUDE.md`); Xvfb and openbox for the editor check, else it is skipped |
| `vpair` | `python3 -m unittest discover -s tests/vpair` | `pipewire`, `wireplumber`, `dbus`, `python3-numpy` |

Read the report, not just the exit status: `unit` writes
`dsp/juce_apps/plugin-check/build-check/<plugin>-report.txt` with one line per check, and a
screenshot of the control editor next to it. When a check fails, quote the failing line in
your answer and fix the cause; never mark a rung as passed by hand with `tools/status.py`.
