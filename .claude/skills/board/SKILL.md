---
name: board
description: The one way to reach a real speaker board (rung 3). `/board status|journal` reads what is installed and what runs; `/board deploy` builds the aarch64 artifacts and installs them capped (-20 dB, tweeter muted); `/board smoke` checks without a sound that speakerd plays capped; `/board rollback` makes the previous speakerd current. Gated by the lab lock and by a green test status for the exact commit. Use only for board work, normally through the board-ops agent.
---

# /board

```
tools/board.sh status                      what is installed and what runs (reads only)
tools/board.sh journal [-n N] [--follow]   journalctl --user -u speakerd on the board (reads only)
tools/board.sh deploy [--role R] [--plugin P[:kind]]... [--factory F] [--import-filtergraph F] [--only|--skip C]
                                           builds the aarch64 artifacts, installs them capped;
                                           any other installer option is a person's
tools/board.sh smoke [--window S]          checks, without a sound, that speakerd plays capped
tools/board.sh rollback                    makes the previous speakerd current again
tools/board.sh --dry-run <command> ...     says what it would run; builds and reaches nothing
```

The board is `$BOARD_USER@$BOARD_HOST`, from the environment or the gitignored
`firmware/local.mk`; never write a board's name, address or user anywhere tracked. What the
installer puts where, its options and its safety rules are in `firmware/CLAUDE.md`
(`speakerd-setup.sh`, `setup.sh`, `tools/board.sh`).

**The gate.** Every command is refused while `.claude/lab.lock` exists: a person has the
boards. `deploy`, `smoke` and `rollback` also need `tests/.status/<HEAD>.json` to say
`unit: pass` and `vpair: pass`, run on a clean tree, and the tree clean now (run `/test all`
first). The `pre-bash` hook enforces the same, on the checkout the command runs in, and
refuses `BPI_DEPLOY_OVERRIDE` and `--person-present` from an agent: both are a person's, in
their own shell.

**The cap.** Every deploy writes `device.json` with `"limits": { "gain_db": -20.0, "tweeter":
"muted" }`, also over limits a person lifted. Nothing in this skill lifts it: that is
`firmware/speakerd-setup.sh limits ... --person-present`, run by a person at the speaker after a
smoke test and a listen.

**What stays human** (`docs/autonomous-development.md`, section 8): the first deploy of a new
`speakerd` to a board, anything that raises the cap, and any change to a board's network, boot
or Wi-Fi configuration. An agent deploys only builds a person has already run on that board.

Read the output, not just the exit status: `smoke` lists each check with its result (service
active, the `speakerd: playing:` line, the exact `device: limits: output at -20 dB, tweeter
muted` line, the link to the amplifier, no restarts or xrun lines in the window, the layer that
plays). On any failure: `rollback` first, report second.
