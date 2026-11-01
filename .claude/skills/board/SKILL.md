---
name: board
description: The one way to reach a real speaker board (rung 3). `/board status|journal` reads what is installed and what runs; `/board deploy` builds the aarch64 artifacts and installs them; `/board smoke` checks without a sound that speakerd plays device.json's limits; `/board rollback` makes the previous speakerd current. Gated by the lab lock and by a green test status for the exact commit. Use only for board work, normally through the board-ops agent.
---

# /board

```
tools/board.sh status                      what is installed and what runs (reads only)
tools/board.sh journal [-n N] [--follow]   journalctl --user-unit speakerd on the board (reads only)
tools/board.sh deploy [--role R] [--plugin P[:kind]]... [--factory F] [--import-filtergraph F] [--only|--skip C]
                                           builds the aarch64 artifacts, installs them;
                                           any other installer option is a person's
tools/board.sh smoke [--window S]          checks, without a sound, that speakerd plays device.json's limits
tools/board.sh rollback                    makes the previous speakerd current again
tools/board.sh --dry-run <command> ...     says what it would run; builds and reaches nothing
```

The board is `$BOARD_USER@$BOARD_HOST`, from the environment or the gitignored
`firmware/local.mk`; never write a board's name, address or user anywhere tracked. What the
installer puts where, its options and its safety rules are in `firmware/CLAUDE.md`
(`speakerd-setup.sh`, `setup.sh`, `tools/board.sh`).

**The gate.** Every command is refused while `.claude/lab.lock` exists: a person has the
boards. `deploy`, `smoke` and `rollback` also need HEAD green, with the tree clean now:
`tests/.status/<HEAD>.json` saying `unit: pass` and `vpair: pass`, run on a clean tree (`/test
all`), or a passing affected run that covers every file changed since a commit green in full
(`/test affected`, for a fix on top of one). `tools/status.py gate` is the one definition and
says why. The `pre-bash` hook enforces the same, on the checkout the command runs in, and
refuses `BPI_DEPLOY_OVERRIDE` from an agent: it is a person's, in their own shell.

**The limits.** There is no output cap. A deploy keeps `device.json`'s limits, and a first
install writes none (`"limits": { "gain_db": 0, "tweeter": "on" }`). With the tweeter on, the
installer refuses any layer that feeds the tweeter other than through a crossover's high band.
`firmware/speakerd-setup.sh limits --gain-db G --tweeter-limit muted|on` sets limits.

**What stays human** (`docs/autonomous-development.md`, section 8): the first deploy of a new
`speakerd` to a board, and any change to a board's network, boot
or Wi-Fi configuration. An agent deploys only builds a person has already run on that board.

Read the output, not just the exit status: `smoke` lists each check with its result (service
active, the `speakerd: playing:` line, the exact `device: limits: output at -20 dB, tweeter
muted` line, the link to the amplifier, no restarts or xrun lines in the window, the layer that
plays). On any failure: `rollback` first, report second.
