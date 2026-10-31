# juce_plugins

LV2 (and VST3) audio plugins built with [JUCE](https://juce.com), meant to run in a plugin
host on a small ARM64 Linux board next to the speaker. Two of them run the filters designed
in [`../python_dsp_tools/tuning`](../python_dsp_tools/tuning/README.md). The other two are
tools for the rest of the audio chain.

| Plugin | I/O | What it does | Coefficients from |
|---|---|---|---|
| [`crossover`](crossover/README.md) | 1 → 2 | Linear-phase FIR crossover: low band on out 1, high band on out 2 | `crossover_coefs.hpp` (tuning GUI) |
| [`firConv`](firConv/README.md) | 1 → 1 | Magnitude-correction chain: band-limit FIR + high-pass biquads + inverse FIR | `filters.hpp` (tuning GUI) |
| [`loudness`](loudness/README.md) | 1 → 1 | Level-dependent loudness contour from two low-shelf EQs per setting, four settings | live parameters |
| [`ampScope`](ampScope/README.md) | 1 → 1 | Oscilloscope for a MAX98397 amplifier's own voltage/current/supply telemetry (audio passes through) | live parameters |

## Layout

```
juce_plugins/
├── cmake/SpeakerPlugin.cmake   speaker_add_plugin(): identity, formats, flags, LTO for every plugin
├── cmake/SourceId.cmake        the version and source identity every target embeds (below)
├── shared/<name>/              source folders several plugins compile (SHARED), e.g. blockfir/
├── tests/                      tests of shared code, outside every plugin's identity
│   ├── blockfir/                 the block FIR, bit for bit on x86_64 and aarch64 (rung 1)
│   ├── buildinfo/                a built bundle and its copy carry the identity of its sources (rung 1)
│   ├── firconv-response/         firConv through speakerd, two builds compared, plotted (standalone)
│   └── plugin-versions/          plugin-versions.sh check and list on a scratch repository (rung 1)
├── tools/                      the build system, shared by every plugin
│   ├── build-podman.sh           recommended: containerised cross-build, deploy, local debug
│   ├── common.sh                 config loading + plugin discovery used by the scripts
│   ├── plugin-versions.sh        every plugin's version and source identity; the version rule
│   ├── Containerfile             the builder image (Debian bookworm + aarch64 cross toolchain),
│   │                             also the repository's development container (tools/container.sh)
│   ├── toolchain-aarch64-linux.cmake
│   ├── build-aarch64.sh          alternative: cross-build on a bare Linux host
│   ├── build-native-linux.sh     alternative: build on the ARM board itself
│   └── build-linux-vm.sh, setup-linux-vm.sh   alternative: ARM64 VM on a Mac (UTM)
├── patches/                    fixes applied to JUCE for the local plugin host only
├── JUCE/                       git submodule, pinned
├── <plugin>/                   sources + CMakeLists.txt (a single speaker_add_plugin() call)
│   ├── build-podman/             gitignored build output
│   └── plugin_host_debug/        gitignored per-developer debug session
├── build-podman/               gitignored: the JUCE install shared by all plugins
└── pluginHost/                 gitignored: the AudioPluginHost built by -setup-host
```

A plugin is any folder here whose `CMakeLists.txt` calls `speaker_add_plugin()` at the start
of a line. The build scripts find plugins by that call, and read the channel counts and the LV2
URI from it, so **adding a plugin needs no change to the tooling**. See
[Adding a plugin](#adding-a-plugin). `shared/`, `tests/`, `cmake/` and `tools/` have no such
`CMakeLists.txt`, so nothing takes them for plugins.

## Building

Prerequisites: [Podman](https://podman.io) and the JUCE submodule
(`git submodule update --init dsp/juce_plugins/JUCE` from the repository root).

```bash
tools/build-podman.sh -install-podman      # one-time, if Podman is missing
tools/build-podman.sh crossover            # image (first run) + JUCE (first run) + plugin
tools/build-podman.sh all                  # every plugin
tools/build-podman.sh -h                   # all steps
```

The target is **Debian 12 (bookworm) on ARM64**. It was developed on a Banana Pi CM4 IO
(Amlogic A311D), but any aarch64 board running bookworm should work. On a Linux x86_64 host
the container runs natively, so the cross-build runs at full speed. On macOS/Windows Podman
runs it in its own VM. One builder image and one JUCE install are shared by every plugin.
Each plugin's result lands in
`<plugin>/build-podman/plugin/<plugin>_artefacts/Release/LV2/<plugin>.lv2`.

| Step | |
|---|---|
| `-image`, `-setup-juce` | build the shared image / JUCE install (automatic when missing) |
| `-setup`, `-build` | configure / compile the plugin (aarch64 Release) |
| `-install` | deploy to the board (below) |
| `-clean`, `-clean-juce` | remove a plugin's build / the shared JUCE build |
| `-shell` | interactive shell in the builder container |
| `-setup-host`, `-debug-linux` | local debugging (below) |

## Deploying

Put the board's ssh address in `../config/local.env` (copied from `local.env.example`):

```
DEPLOY_HOST=user@my-board.local
DEPLOY_LV2_DIR=.lv2          # optional, the default
```

```bash
tools/build-podman.sh all -install
```

The bundle is streamed with `tar` over ssh into `~/.lv2` on the board. That has to be one
of the **default** LV2 search paths: JUCE's plugin host builds its LV2 world at startup from
`~/.lv2`, `/usr/lib/lv2` and `/usr/local/lib/lv2` only, and ignores `$LV2_PATH`. A plugin
deployed anywhere else is missing when a saved session is reopened. The remote folder has
to exist and be writable by you; the script tells you how to fix it if not. See
[lv2-discovery.md](../docs/juce-knowledge/hosting/lv2-discovery.md).

## Debugging locally (Linux)

```bash
tools/build-podman.sh -setup-host            # one-time: build JUCE's AudioPluginHost
tools/build-podman.sh loudness -debug-linux
```

`-debug-linux` makes a native x86_64 **Debug** build in the same container, installs it into
`~/.lv2` on this machine and opens it in the AudioPluginHost. The session
(`<plugin>/plugin_host_debug/<plugin>.filtergraph`) is created once, with the plugin wired
between audio in and out and its editor open. After that it's never overwritten, so your
changes to it survive rebuilds.

Gotchas, all handled or explained by the script. Details in
[audiopluginhost-linux.md](../docs/juce-knowledge/hosting/audiopluginhost-linux.md):

- **The host crashes on a PipeWire desktop** when it opens the audio device. The crash is
  inside PipeWire's ALSA path, not the plugin. The script starts the host with a private
  `XDG_CONFIG_HOME` whose `alsa/asoundrc` points ALSA's `default` at a real sound card.
  `PLUGIN_HOST_ALSA_CARD=<id|index>` picks another card. Don't choose "pipewire" in the
  host's audio settings.
- **A failed plugin load is silent.** "The host opened" doesn't mean the plugin loaded.
- **Fixed-size editors can be maximised** because JUCE 8.0.12's X11 code drops the size
  hints. [`patches/`](patches/) fixes this for the host that `-setup-host` builds, and a
  fixed-size editor should also call `setResizeLimits(w, h, w, h)`. Details in
  [x11-plugin-windows.md](../docs/juce-knowledge/gui/x11-plugin-windows.md).

## Adding a plugin

1. Create `juce_plugins/myPlugin/` with the sources and this `CMakeLists.txt`:
   ```cmake
   cmake_minimum_required(VERSION 3.22)
   project(MY_PLUGIN VERSION 0.0.1)
   find_package(JUCE CONFIG REQUIRED)
   include(${CMAKE_CURRENT_LIST_DIR}/../cmake/SpeakerPlugin.cmake)

   speaker_add_plugin(myPlugin
       CODE    Mypl          # unique across plugins: VST3/AU hosts key on it
       INPUTS  1             # must match the processor's BusesProperties
       OUTPUTS 1
       SOURCES PluginProcessor.cpp PluginEditor.cpp
       MODULES juce_dsp)     # beyond juce_audio_utils/processors, gui_basics/extra
   ```
   `VERSION` must be `MAJOR.MINOR.PATCH`, written out (see
   [Versions and source identity](#versions-and-source-identity)); a plugin that compiles a
   shared folder adds `SHARED <name>`.
2. `tools/build-podman.sh myPlugin`. That's all.

The folder name is the target, product, binary and URI name. The LV2 URI becomes
`<SPEAKER_LV2_URI_BASE>/myPlugin`, which is unique by construction. Hosts key their plugin
database on that URI, so two plugins sharing one collide and only one survives a scan.

## Control and headless builds

For the remote plugin host (controller app plus headless speakers, see
[`../../docs/remote-plugin-host.md`](../../docs/remote-plugin-host.md)) a plugin can also be built without its DSP
and without its editor. List the sources by role and name the targets:

```cmake
speaker_add_plugin(loudness
    CODE    Loud
    INPUTS  1
    OUTPUTS 1
    SOURCES PluginProcessor.cpp                        # parameters, state: every target
    DSP     LoudnessDsp.cpp                            # left out of the control build
    EDITOR  PluginEditor.cpp MorphPad.cpp HostWindowTracker.cpp   # left out of the headless build
    MODULES juce_dsp
    TARGETS complete control headless)
```

| Target | CMake target | Contains | Runs in |
|---|---|---|---|
| complete | `loudness` | everything, as before | the desktop plugin host, tuning |
| control | `loudness_control` | parameters and editor, no DSP | the controller app |
| headless | `loudness_headless` | parameters and DSP, no editor | `speakerd` on the speaker |

The sources see `SPEAKER_COMPLETE`, `SPEAKER_CONTROL` and `SPEAKER_HEADLESS` (0 or 1). The
control build must still define `prepareToPlay`, `reset` and `processBlock`, as empty stubs;
the headless build's `hasEditor()` returns false. `tools/build-podman.sh` keeps building the
complete target only. [`../juce_apps/plugin-check`](../juce_apps/plugin-check/README.md)
checks that the three builds fit together. So far `loudness` is split this way.

**Monitors.** What a plugin's editor shows live (a level, the active setting) reaches the
controller as read-only parameters whose IDs start with `mon_`
([`../../docs/protocol.md`](../../docs/protocol.md)): in every build, declared last with
`addParameter()` (not through the APVTS, so no saved state carries them), not automatable, of
category output meter. The complete and headless builds publish them from the audio thread
(`setValue()` and, under JUCE's LV2 wrapper, a notification when the value moved, without which
that wrapper does not report them to the host); in the control build the plugin's getters read
them. `loudness/Monitors.h` has the helpers, and
`<plugin>/plugin-check.monitors.h` names the getters for plugin-check.

## Shared source folders

Code that several plugins compile lives in `shared/<name>/`, header-only, and a plugin names
the folders it uses:

```cmake
speaker_add_plugin(firConv
    ...
    SHARED  blockfir)
```

`SHARED` puts each folder on the include path of every target of the plugin (only the folders
it names) and makes it an input of the plugin's source identity, so a change in it changes the
identity of every plugin that compiles it, and `plugin-versions.sh check` asks each of them for
a new version. Tests of shared code go in `tests/`, outside every plugin's identity.

| Folder | What | Compiled into |
|---|---|---|
| `shared/blockfir` | `BlockFir.h`: a direct-form FIR that computes 16 outputs per pass over its taps with GCC vector types, each output's taps added in ascending order as `juce::dsp::FIR::Filter` does; a linear history allocated in `prepare()`, longer blocks in chunks. Bit-identical to `FIR::Filter` on x86_64; on aarch64 every tap is a fused multiply-add, within -131 dB of `FIR::Filter`'s output (which mixes fused and unfused taps) | `firConv` (pre-LP, inverse), `crossover` (both bands) |

`tests/blockfir/run.sh` (part of `tools/test.sh unit`) builds its test with the plugins' flags
for x86_64 and for aarch64 and runs both, the aarch64 build under `qemu-aarch64-static`: (a) bit
for bit against a reference FIR whose arithmetic is written out (`std::fma` per tap where the
kernel fuses, a rounded multiply and add where it does not, never reordered), in blocks of 1024,
of random sizes, longer than the prepared size, in place and after `reset()`; (b) against
`juce::dsp::FIR::Filter`, the worst difference below -120 dB re the output's peak. Without the
aarch64 toolchain or qemu it fails and names the packages (`g++-aarch64-linux-gnu`,
`qemu-user-static`). `run.sh --time` prints the cost of each filter. Why the rounding differs on
aarch64: [`simd-on-aarch64-gcc.md`](../docs/juce-knowledge/dsp/simd-on-aarch64-gcc.md),
[`fir-filter-rounding.md`](../docs/juce-knowledge/dsp/fir-filter-rounding.md).

## The response test

`tests/firconv-response/run.sh [--base REF]` shows what a change does to firConv's output: it
renders one input through two builds of the plugin with `speakerd --render`, plots the
responses and their difference against frequency, and asserts limits on them. It is standalone,
not part of rung 1: a run took 4 min 41 s cold (nothing built yet in the checkout, 24 threads)
and 22 s warm (nothing changed), of which the renders and the analysis take 19 s. It moves into
the development container like the other test scripts.

- **Builds** (`builds.py`): speakerd and firConv at the current tree, for x86_64 and aarch64, with
  `tools/build.sh` (where it puts them); and firConv at the base, by default its previous version
  (the parent of the commit that raised its `VERSION`), from a `git archive` export of
  `dsp/juce_plugins` into `build/tests/firconv-response/base-<commit>/`, built by today's
  `tools/build.sh` with the export as its root. Nothing is checked out, and the base's x86_64
  build copies its bundle into a throwaway `HOME`. The export's git sees no repository, so the
  base's bundles carry no source identity of their own; the figures give the one git computes
  for that commit. A base that pins another JUCE is refused.
- **Renders**: an exponential sweep from 10 Hz to 22 kHz (6 s) and white noise (4 s), both at
  -9 dBFS peak, gain -12 dB, through each of the four builds at 48 kHz in blocks of 1024, alone
  in a one-node graph to the woofer, the aarch64 builds under `qemu-aarch64-static` with an
  empty `HOME`.
- **Analysis** (`response.py`, numpy, and scipy for the design): each excitation is transformed
  whole, with the silence after it, so the averaged cross-spectrum over the input spectrum is the
  exact response of each build, with no window; the difference of two outputs goes through the
  same transforms, and every curve is a 1/6-octave power sum. The design is `filters.hpp` as the
  tuning GUI graphs it (`freqz` of the two FIRs, `sosfreqz` of `HPreHpSos`, in float64), times
  the gain.
- **Figures** (`svgplot.py`, no plotting package): one SVG per pair, aarch64 current against base
  (the change the board hears), x86_64 current against base, and the current build on aarch64
  against x86_64, each with the magnitude responses and the design, the output's and the
  difference's band power, the difference relative to the output with the limits, and each
  build's deviation from the design; the same curves as CSV beside each, and `landmarks.csv`
  (the design and each build at 20 Hz to 10 kHz). All in `build/tests/firconv-response/firConv/`.
- **Assertions** (`test_firconv_response.py`, each limit with how it was measured): on x86_64,
  bit for bit against 0.0.1; on aarch64, the peak difference within -53 dB re peak, every band
  from 100 Hz to 20 kHz at least 73 dB below the output (86 dB from 1 kHz), the two responses
  within 0.01 dB from 30 Hz to 17 kHz; and for every build, within 0.05 dB of the design from
  20 Hz to 20 kHz, its high-pass's -3 dB point within 0.5 % of the design's, its peak below
  -1 dBFS. A failure names the band and the numbers. Against a base other than 0.0.1, an x86_64
  difference is reported as a skipped test, with its size.

Measured, 0.0.2 against 0.0.1: identical on x86_64; on aarch64 up to -55.0 dB re peak (-59 dB
at -6 dBFS input peak only, [`firConv/README.md`](firConv/README.md)), 99.9 % of the
difference's energy below 50 Hz, at least 76.8 dB below the output in every band from 100 Hz,
as large as 0.0.1's own difference between aarch64 and x86_64. A second plugin is an entry of
`PLUGINS` in the test module. Without python3-scipy (in the development container's
`Containerfile`) the design's tests fail and name the package.

## Versions and source identity

Every plugin has a **version**, its `project(... VERSION MAJOR.MINOR.PATCH)`, and a **source
identity**, 12 hex characters over the inputs it is compiled from. All its targets (complete,
control, headless; x86_64 and aarch64) embed both, without any plugin source having to change.

**The source identity** is computed over, in this order: the plugin's own folder, each shared
folder it declares (`SHARED`, in declared order), `cmake/`, and the pinned JUCE commit (the
submodule's gitlink). From the repository root, for firConv:

```bash
git rev-parse HEAD:dsp/juce_plugins/firConv HEAD:dsp/juce_plugins/shared/blockfir \
              HEAD:dsp/juce_plugins/cmake HEAD:dsp/juce_plugins/JUCE \
    | git hash-object --stdin | cut -c1-12
```

with `-dirty` appended when `git status --porcelain -- <the same paths>` prints anything
(tracked changes, staged or not, and untracked files that are not ignored), and `unknown`
without git, outside a repository, or in one without a commit. A folder that is not in HEAD
yet (a new plugin or shared folder) counts as the empty tree
(`git hash-object -t tree /dev/null`); `-dirty` follows, as for every input, exactly when
`git status --porcelain` over the inputs prints anything: a new plugin's untracked files do, a
declared shared folder that is missing or holds only ignored files does not. On a clean tree
the identity is reproducible from git alone. It follows the tree, not a commit: a commit that
touches none of a plugin's inputs leaves its identity as it was. It works in a git worktree
(the gitlink is read from HEAD's tree, so an uninitialised submodule changes nothing).
`tools/build-podman.sh` mounts only `juce_plugins/` into its container, where git finds no
repository: its bundles say `unknown`.

`cmake/SourceId.cmake` is the one implementation, used by the build and by
`plugin-versions.sh`. The build recomputes it at every build, without a reconfigure (a custom
target, `<plugin>_build_info`), and rewrites the generated header only when it changes, so an
unchanged identity recompiles nothing. Where it ends up:

| Where | What |
|---|---|
| `SpeakerBuildInfo.h`, generated, on every target's include path | `SPEAKER_PLUGIN_NAME`, `SPEAKER_PLUGIN_VERSION`, `SPEAKER_PLUGIN_SOURCE_ID` |
| every LV2 and VST3 binary | the string `bpi-build-info: <LV2 URI> version <version> source <source-id>` (`strings libfirConv.so \| grep bpi-build-info`) |
| every LV2 bundle, next to `manifest.ttl` | `buildinfo.ttl`, below; `manifest.ttl` stays as JUCE writes it and does not name it |
| `speakerd`'s log, from `speakerd` 0.3.1 | `plugin node <uid>: <URI>, version <version>, source <source-id>, bundle <dir>` for each plugin node, read from the bundle's `buildinfo.ttl` |

```turtle
@prefix bpi: <https://github.com/AMuszkat/bpi_amp_dsp_tuning#> .

<https://github.com/AMuszkat/bpi_amp_dsp_tuning/firConv>
    bpi:version "0.0.2" ;
    bpi:sourceId "0123456789ab" .
```

The subject is the target's own LV2 URI (`.../loudness_headless` for the headless build), the
`bpi:` namespace is `SPEAKER_LV2_URI_BASE` with `#`. The file is written by a `POST_BUILD` step
after JUCE's `juce_lv2_helper` has written the bundle's `.ttl` files and before JUCE copies the
bundle (into `~/.lv2`, or a cross build's `copied/lv2`), so the copy has it too
([cmake-reference.md](../docs/juce-knowledge/build-system/cmake-reference.md)).

The manifest does not name `buildinfo.ttl` (no `rdfs:seeAlso`), on purpose: the file is
diagnostic and no LV2 host reads it, so a damaged one can never stop the plugin loading. Named
in the manifest, lilv would parse it with the plugin's data, and one parse error there (a
half-written copy) makes lilv refuse to instantiate the plugin: the layer fails, and the speaker
falls back to an older one or to passthrough. `speakerd` reads the file directly, from the
bundle each plugin runs from (`../juce_apps/speakerd/README.md`, "Which build it is").

`tests/buildinfo/check.sh` checks that a bundle's `buildinfo.ttl` and binary agree with
`plugin-versions.sh list`, that its manifest does not mention `buildinfo.ttl`, and with `--arch`
that the binary is code for that machine; it checks any bundle it is given, a copy included.
Rung 1 (`tools/test.sh unit`) runs it on the x86_64 bundles plugin-check builds, and on
`loudness_headless` cross-built for aarch64 with its copy in the build's `copied/lv2`
(`tools/build.sh loudness aarch64 headless`: what the board installer deploys, and the cheapest
target to cross-build). That copy proves the copy step's order (the copy is why
`speaker_add_plugin()` writes `buildinfo.ttl` before it), because only that build tree writes
`copied/lv2`. Rung 1 does not check the copies in `~/.lv2`: any other build in the same HOME
(`tools/build.sh` of another tree, `build-podman.sh`'s local debug loop, whose bundles say
`unknown`) may have written them since, and JUCE copies a bundle again only when it links the
binary. The controller, which compiles `loudness_control`'s sources in
(`../juce_apps/controller/ControlPlugins.cmake`), does not compile the generated source and
carries no plugin identity.

**The version rule.** A plugin's version goes up whenever its sources change (its folder or a
shared folder it declares):

| Change | Raise |
|---|---|
| keeps the interface (parameter IDs, ranges, defaults, monitors, state format) and changes the processing (output differences within a stated tolerance included), or fixes a bug | **patch** (0.0.1 -> 0.0.2) |
| changes the interface and keeps old saved states loadable | **minor** (0.0.2 -> 0.1.0) |
| changes the interface and breaks old saved states | **major** (0.1.0 -> 1.0.0) |
| any other change to its sources (its README, its editor alone, a comment) | **patch** |

A change only to `cmake/` or to the JUCE pin changes every identity but no version. The
P1 block FIR is a patch: firConv and crossover 0.0.1 -> 0.0.2, the same parameters and state.
Its tolerance, as measured: the output bit-identical on x86_64; on aarch64 the FIRs within
-131 dB re peak, crossover's output within -129 dB re peak, and firConv's within about -55 dB re
peak (-59 dB at -6 dBFS input; measured by [the response test](#the-response-test)),
all below 50 Hz, the rounding noise of its float32 high-pass biquads, which any one-ulp change
before them moves that much, and did before P1 ([`firConv/README.md`](firConv/README.md)).

lilv, which picks the bundle a host runs when two have one URI, sees only a version's last two
numbers: JUCE writes `x.y.z` as `lv2:minorVersion y`, `lv2:microVersion z`, and lilv runs the
greater, so 1.0.0 (0.0 to lilv) would lose to a 0.9.9, in either order
([lv2-discovery.md](../docs/juce-knowledge/hosting/lv2-discovery.md)).

**`tools/plugin-versions.sh`** (bash; git 2.15 or later, coreutils and cmake; any directory,
natively or in the development container):

```bash
tools/plugin-versions.sh list           # "<plugin> <version> <source-id>", one line per plugin
tools/plugin-versions.sh check [REF]    # the version rule against git merge-base HEAD REF
```

`list` prints what a build made at that moment would embed, sorted by name, and nothing else.
`check` compares every plugin's sources (its folder and its `SHARED` folders, not `cmake/` or
the JUCE pin) between `git merge-base HEAD REF` (REF defaults to `origin/develop`) and the
working tree, tracked changes and untracked files included, and prints one line per plugin:
`<plugin> unchanged <version>`, `<plugin> changed, raised <old> -> <new>`,
`<plugin> changed, NOT raised <old> -> <new>`, or `<plugin> new <version>` for one absent at the
base. Exit status 0 when every changed plugin raised its version, 1 when one did not, 2 on a
usage, git or parse error (a `CMakeLists.txt` whose version or `SHARED` cannot be read, at HEAD
or at the base, or a tree of the base that git cannot read, fails the check rather than pass a
plugin unread). The version and `SHARED` are read from the text of the plugin's
`CMakeLists.txt`, so both must be written out literally; the configure step fails when that
reading disagrees with CMake's. `tests/plugin-versions/run.sh` (rung 1, a few seconds) runs
both commands on a scratch repository with this layout: every verdict of `check` and its exit
status, and every identity `list` prints against the `git` command above, `-dirty` exactly when
the plugin's inputs changed. It touches no other repository, wherever `TMPDIR` is (inside a
repository too), and reads no system or user git configuration: git never searches above its
temporary directory (`GIT_CEILING_DIRECTORIES`), the test stops unless git finds the scratch
repository where it made it, and `HOME` points into the temporary directory. It needs nothing
newer than the tool's git 2.15, and is run on 2.39 (the development container's) and 2.53.

## JUCE

`JUCE/` is pinned to the commit these plugins compile against. When you need to know what
JUCE *actually* does, read the source there (`modules/`, `docs/CMake API.md`, `examples/`,
`extras/`) before trusting anything found online for another version. Hard-won findings go
in [`docs/juce-knowledge/`](../docs/juce-knowledge/).

**License:** AGPLv3, like the rest of the repository (see [`../LICENSE`](../LICENSE)). This
matches JUCE's own open-source license.
