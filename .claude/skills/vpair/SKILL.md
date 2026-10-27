---
name: vpair
description: Operate the virtual pair, two virtual speakers on this machine (tests/vpair). `/vpair up|down|status` starts, stops or lists them; `/vpair scenario <name>` runs one test module against them; `/vpair env <left|right>` gives the environment that points pw-cli, pw-dump and pw-play at one speaker. Use when developing or debugging speakerd, ampd or the harness without boards.
---

# /vpair

```
tests/vpair/vpair.sh up | down | status
tests/vpair/vpair.sh run <left|right> <command...>   # e.g. pw-link -l, pw-dump, pw-top -b -n 1
python3 -m unittest -v tests.vpair.<module>          # one scenario, from the repository root
python3 -m unittest discover -s tests/vpair -v       # every scenario (what /test vpair runs)
```

With Podman installed the pair runs in the development container of this checkout
(`tools/container.sh`), and these commands move there themselves; the pair keeps running
between them. `vpair.sh env <speaker>` prints the environment for a shell in the same
place, so on the host use `run` instead, or `tools/container.sh` for a shell.

Read `tests/vpair/README.md` first. Each speaker has its own PipeWire, WirePlumber and D-Bus
session and a null-sink `amplifier` whose FL is the woofer and FR the tweeter. The harness
(`tests/vpair/harness.py`) plays into a speaker and records its amplifier; use it rather than
hand-rolled `pw-play`/`pw-record` lines in tests.

Rules:

- The tests bring the pair up and down themselves. Bring it up by hand only to inspect
  something, and take it down afterwards: a pair left running makes the next test run fail.
- The speakers live under `$VPAIR_DIR` (default `/tmp/vpair`, inside the container when it
  is used), one folder each, with `pipewire.log` and `wireplumber.log`. Read those when a
  speaker does not come up: `tools/container.sh cat /tmp/vpair/left/pipewire.log`.
- A new scenario is a new `tests/vpair/test_<name>.py`; the scenario names and pass criteria
  are in `docs/autonomous-development.md`, section 5. Never weaken a pass criterion to make
  a scenario pass.
