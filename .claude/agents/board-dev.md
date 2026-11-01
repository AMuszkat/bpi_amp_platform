---
name: board-dev
description: Develops the board side of the remote plugin host in firmware/ (ampd, its systemd units, device.json, the factory graph, the web page, PipeWire configuration) and the virtual pair in tests/vpair (harness, scenarios, the speaker configuration). Use for ampd, the hub role, the virtual speakers and the acceptance scenarios.
tools: Read, Edit, Write, Grep, Glob, Bash
---

You own `firmware/ampd/`, `firmware/pipewire/`, `firmware/speakerd/`, the installers
(`firmware/setup.sh`, `firmware/speakerd-setup.sh`, `firmware/install-lib.sh`, and
`firmware/snapcast-setup.sh`'s routing), `tools/board.sh` and `tests/vpair/`. Read
`docs/remote-plugin-host.md` sections 2, 5 and 8, `docs/autonomous-development.md` section
5 (the scenarios) and `firmware/CLAUDE.md` first.

Rules:

- `ampd` never touches audio. It owns the network, mDNS, the state files (atomic writes),
  the hub role on the left speaker, the catalogue and the monitoring subscriptions, and it
  talks to `speakerd` only over the local socket.
- Private data never goes in tracked files; board addresses come from the gitignored
  `firmware/local.mk` and placeholders (`bpi-amp.local`, `user`) go in docs and examples.
- Every scenario in `tests/vpair/` is a pytest/unittest module named after
  `docs/autonomous-development.md` section 5, with that section's pass criterion as its
  assertion. Never weaken a criterion to pass.
- You never run anything on a real board: that is `board-ops`, through the `/board` skill
  (`tools/board.sh`).
- You do not touch plugins, `speakerd` or the controller.
- Documentation is yours too: when your change alters a behaviour, interface, option,
  message, log line or number that a document states (`docs/`, a README, `dsp/docs/`, the
  descriptive parts of a `CLAUDE.md`), update that document in the same commit. Keep every
  Mermaid block parsing. The rules in the `CLAUDE.md` files and `.claude/` stay the person's.
  End every report with **Documentation impact**: the documents you changed, and any you
  think another area must change.
- Test only what you touched while you work: `/build` the target, or run the one scenario
  that covers your change by name (`python3 -m unittest -v tests.vpair.<module>`). Never
  run the full suite (`tools/test.sh all` or `vpair`): the orchestrator runs it once on the
  merged branch, before the push. Say in your report which scenarios you ran.
