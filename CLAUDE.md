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
| `hardware/` | KiCad projects: the carrier board and the amplifier board | `README.md` |
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
- **Nothing is deployed to a board unless the virtual pair tests pass**
  (`python3 -m unittest discover -s tests/vpair`), and every deploy keeps the volume capped.
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
| more than one of the above, or a protocol they share | `orchestrator`, which runs the area agents in parallel worktrees and merges |
| running the tests, "is it green?" | `verifier` |
| before a pull request is merged | `reviewer` |
| a real board | `board-ops`, only after the verifier reports both rungs green |

A single-area task goes straight to its agent; the orchestrator is for work that has to be
split and merged, not a required entry point. Agents do not cross areas: an agent that
needs a change outside its files says so and stops.

## Hooks, skills and agents

`docs/autonomous-development.md` is the plan; this is what of it exists.

- **Hooks** (`.claude/settings.json`, scripts in `.claude/hooks/`) enforce the rules above:
  no private data in an edit or a commit, no push to `main`, no force push, and the
  **deploy gate**: nothing reaches a board (`ssh`, `scp`, `rsync`, `-install`,
  `--target-host`) while `.claude/lab.lock` exists, or without `tests/.status/<commit>.json`
  saying `unit: pass` and `vpair: pass` for the exact commit with a clean tree. A person
  may bypass the status (never the lock) once with `BPI_DEPLOY_OVERRIDE=1 <command>`; it is
  logged in `tests/.status/overrides.log`. Create `.claude/lab.lock` whenever the boards are
  on a bench.
- **Skills**: `/build` (a plugin or app, x86_64 or aarch64), `/test` (`unit` = plugin-check,
  `vpair` = the virtual pair, `all`; writes the status file), `/vpair` (operate the virtual
  pair), `/pw-inspect` (a PipeWire graph, summarised), `/board` (a real board: status,
  journal, deploy capped, smoke, rollback; gated), `/juce` (JUCE knowledge). Their
  scripts are in `tools/`, work without Claude, and run in the development container
  (below).
- **Agents** (`.claude/agents/`): `orchestrator`, `plugin-dev`, `host-dev`,
  `controller-dev`, `board-dev`, `verifier`, `reviewer`, `board-ops`, each limited to its
  area and tools as the document's table says.
- **CI** (`.github/workflows/ci.yml`) runs rungs 1 and 2 on pull requests and on `develop`.

## Developing the remote plugin host

Everything below runs on a Linux x86_64 machine with no boards attached, and needs only
**Podman** and git: the builds and both test rungs run in the **development container**,
the builder image of `dsp/juce_plugins/tools/Containerfile` (Debian bookworm, so PipeWire
0.3.65 and WirePlumber 0.4.13, the versions the boards run).

```sh
git clone --recurse-submodules <this repository>
tools/test.sh all                # rung 1 (plugin-check) and rung 2 (the virtual pair)
tools/build.sh loudness          # one plugin or application, into build/
tools/container.sh               # a shell in the container
```

The scripts move into the container themselves (`tools/container.sh`, which says how), so
nothing changes in how they are called. The first run builds the image and a JUCE install
in `build/juce-install`, which takes a few minutes; later runs start at once. Each checkout
and each git worktree has its own long-running container, with the checkout mounted at its
own path, so worktrees can run the virtual pair at the same time.
`BPI_CONTAINER=0` runs the same scripts natively instead, where Podman is missing (cloud
sessions) or the packages are installed (CI): the package list is the one in
`.github/workflows/ci.yml`.

For the boards, `tools/build.sh <plugin|app> aarch64 [target]` cross-compiles in the same
container into `build/aarch64/`; that is what `firmware/speakerd-setup.sh` and `/board deploy`
install. `dsp/juce_plugins/tools/build-podman.sh` keeps its own flows (complete builds deployed
to `~/.lv2`, the local AudioPluginHost debug loop), and `firmware/Makefile` builds the kernel
side. See `docs/autonomous-development.md` for the three test rungs and what each one proves.
