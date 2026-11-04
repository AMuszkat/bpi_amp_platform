# CLAUDE.md

Guidance for Claude Code (claude.ai/code) in this repository. User-facing documentation is in
the READMEs; this file is the map plus the rules that are not obvious from the code. Each
area has its own `CLAUDE.md` with its details: read the one for the area you are changing.

## What this is

The BPI AMP Platform: a Linux audio streaming platform for custom loudspeaker tuning. A
BananaPi BPI-CM4 module receives audio over the network, processes it in PipeWire with JUCE
plugins and drives two MAX98397 amplifiers. Two such boards make a stereo pair.

| Path | What | Guidance |
|---|---|---|
| `hardware/` | KiCad projects: the carrier board and the amplifier board; `render/`, the boards' Blender render | `hardware/README.md` |
| `firmware/` | Device tree, kernel config, amplifier drivers, PipeWire config, board setup scripts, the installers of `speakerd` and of the whole audio side (`setup.sh`); later `ampd` | `firmware/CLAUDE.md` |
| `dsp/` | Measurement and tuning tools, the JUCE plugins, `plugin-check`, `speakerd` (the headless plugin host); later the controller app (formerly the `bpi_amp_dsp_tuning` repository) | `dsp/CLAUDE.md` |
| `docs/` | The remote plugin host design (`remote-plugin-host.md`) and how to develop it autonomously (`autonomous-development.md`) | |
| `tests/vpair/` | Two virtual speakers on one machine, for developing the remote plugin host without boards | `tests/vpair/README.md` |
| `.claude/skills/juce/` | The `/juce` skill: JUCE knowledge for the plugins under `dsp/` | |

## Rules for the whole repository

- **Private data never goes in tracked files.** The repository is public. Board addresses,
  user names, IPs and home paths go in the gitignored `firmware/local.mk` (template
  `local.mk.example`) and `dsp/config/local.env` (template `local.env.example`). Use
  placeholders like `bpi-amp.local` / `user` in docs and examples.
- **The JUCE submodule** is `dsp/juce_plugins/JUCE`, pinned to the commit the plugins compile
  against: `git submodule update --init dsp/juce_plugins/JUCE` once after cloning.
- **The design documents in `docs/` are the plan.** When the implementation diverges from
  them, change the document in the same commit, and keep their Mermaid diagrams rendering
  (every ```mermaid block must parse; a label that starts with `1. ` is read as a list).
- **No test gate on a deploy** (2026-11-02, the person's decision): nothing checks that a
  commit passed the tests before it reaches a board; only the lab lock stops a deploy. Run the
  tests before deploying all the same (`tools/test.sh all`). There is no output cap
  (2026-11-01, the person's decision): an install keeps `device.json`'s limits and writes none
  on a first install.
- **Real-time code never blocks:** no allocation, lock or syscall on the audio thread, in
  plugins and in `speakerd` alike.

## Who does what

Start every session on the remote plugin host with `/roadmap`: it says which step of
`docs/remote-plugin-host.md` is next and which agent owns it. Then route by the files a
task would change:

| Files | Agent |
|---|---|
| `dsp/juce_plugins/` | `plugin-dev` |
| `dsp/juce_apps/speakerd/` | `host-dev` |
| `dsp/juce_apps/controller/` | `controller-dev` |
| `firmware/`, `tests/vpair/` | `board-dev` |
| `hardware/render/`, `docs/diagrams/`, the pictures they make in `docs/images/` | `render-dev` |
| documentation alone | the main session, or the area agent whose code it describes |
| more than one of the above, work that can run in parallel, or a protocol they share | `orchestrator`, which runs the area agents in parallel worktrees, merges, and runs the full suite once |
| before a pull request is merged | `reviewer` |
| a real board | `board-ops` |

A single-area task goes straight to its agent; the orchestrator is for work that has to be
split and merged, not a required entry point. Agents do not cross areas: an agent that
needs a change outside its files says so and stops. Each area agent updates the documents
its change affects in the same commit, and ends its report with its documentation impact.
The rules in the `CLAUDE.md` files and everything in `.claude/` stay the person's.

**Checks while working, the levels before a push.** While working, an agent runs the check
that covers its change (`tools/test.sh speakerd`, `plugin <name>`, `installers`, ..., or
`tools/test.sh changed` for whatever changed; seconds to two minutes each); no hook runs tests
at the end of a turn. Several checks in one command, like the levels, run as one schedule
(`tools/schedule.py`): the builds first, at most two heavy steps at once at nice 19, then the
tests, and the timing scenarios (`POOL_QUIET`) last, behind a gate, on at most 12 workers, with
`chain_cost` and knob_latency's quantum-128 test alone after them; the scenarios run `--jobs N`
at a time (default two thirds of the processors, at most 16; 1 where `CI` is set, which runs
everything one after another as before). `plugin <name>` builds
without LTO; `plugins`, the levels and CI keep it. The levels, end to end (`tools/test.sh all`: level 1 `unit`, level 2 `vpair`), run
once on the finished branch before it is pushed, and again only if code changed since; CI
repeats them on the pull request. Level 3 is a board (`/board smoke`).

## Hooks, skills and agents

`docs/autonomous-development.md` is the plan; this is what of it exists.

- **Hooks** (`.claude/settings.json`, scripts in `.claude/hooks/`) enforce the rules above:
  no private data in an edit or a commit, no push to `main`, no force push, and the
  **lab lock**: nothing reaches a board (`ssh`, `scp`, `rsync`, `-install`,
  `--target-host`, the installers' `--mode target-remote`, `tools/board.sh`) while a
  `.claude/lab.lock` exists (`tools/board.sh` and the installers check it too). Create
  `.claude/lab.lock` whenever the boards are on a bench. There is no test-status gate
  (2026-11-02): `tools/status.py gate` only reports whether a commit is green.
- **Skills**: `/build` (a plugin or app, x86_64 or aarch64), `/test` (the checks: `plugin <name>`, `speakerd`,
  `ampd`, `pair`, `controller`, `installers`, `chain`, `tools`, `changed`; the levels: `unit`,
  `vpair`, `all`, which write the status file), `/vpair` (operate the virtual
  pair), `/pw-inspect` (a PipeWire graph, summarised), `/board` (a real board: status,
  journal, deploy, smoke, rollback; refused while the lab is locked), `/juce` (JUCE knowledge), `/render` (the boards
  in Blender, and the README's slider). Their scripts are in `tools/`, work without Claude,
  and run in the development container (below), except `tools/render.sh` and
  `tools/blender.sh`, which run Blender natively from `build/tools/`.
- **Agents** (`.claude/agents/`): `orchestrator`, `plugin-dev`, `host-dev`,
  `controller-dev`, `board-dev`, `reviewer`, `board-ops`, `render-dev`, each limited to its
  area and tools as the document's table says.
- **CI** (`.github/workflows/ci.yml`) runs levels 1 and 2 on pull requests and on `develop`.

## Developing the remote plugin host

Everything below runs on a Linux x86_64 machine with no boards attached, and needs only
**Podman** and git: the builds and both test levels run in the **development container**,
the builder image of `dsp/juce_plugins/tools/Containerfile` (Debian bookworm, so PipeWire
0.3.65 and WirePlumber 0.4.13, the versions the boards run). The image is tagged
`localhost/bpi-amp-builder:bookworm-<hash of the Containerfile>`; `tools/container.sh --status`
lists images and containers, `--prune` removes unused ones.

```sh
git clone --recurse-submodules <this repository>
tools/test.sh all                # level 1 (plugin-check) and level 2 (the virtual pair)
tools/build.sh loudness          # one plugin or application, into build/
tools/container.sh               # a shell in the container
```

The scripts move into the container themselves (`tools/container.sh`, which says how), so
nothing changes in how they are called. The first run builds the image and a JUCE install
in `build/juce-install`, which takes a few minutes; later runs start at once. Each checkout
and each git worktree has its own long-running container, with the checkout mounted at its
own path, so worktrees can run the virtual pair at the same time. Builds go through ccache,
one cache in the main checkout's `build/ccache` for it and its worktrees (`BPI_CCACHE=0` turns
it off; it is off where `CI` is set), and one test pool or serial run, and one schedule, run at
a time per checkout. `tools/build.sh` and plugin-check never write a `~/.lv2`: JUCE's copies go
to the build tree's `copied/`.
`BPI_CONTAINER=0` runs the same scripts natively instead, where Podman is missing (cloud
sessions) or the packages are installed (CI): the package list is the one in
`.github/workflows/ci.yml`.

For the boards, `tools/build.sh <plugin|app> aarch64 [kind]...` cross-compiles in the same
container into `build/aarch64/`; that is what `firmware/speakerd-setup.sh` and `/board deploy`
install. `dsp/juce_plugins/tools/build-podman.sh` keeps its own flows (complete builds deployed
to `~/.lv2`, the local AudioPluginHost debug loop), and `firmware/Makefile` builds the kernel
side. See `docs/autonomous-development.md` for the three test levels and what each one proves.
