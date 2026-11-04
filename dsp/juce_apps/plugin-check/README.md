# plugin-check

Checks that the three builds of a plugin fit together the way the remote plugin host needs
them to. The design is in [`docs/remote-plugin-host.md`](../../../docs/remote-plugin-host.md); in short, the controller app
runs a plugin's **control** build (its editor, no DSP) and each speaker runs its **headless**
build (its DSP, no editor), and the two exchange parameter values and saved state.

```bash
dsp/juce_apps/plugin-check/run.sh loudness                      # or tools/test.sh unit, for every plugin
PLUGIN_CHECK_LTO=0 dsp/juce_apps/plugin-check/run.sh loudness   # without LTO: tools/test.sh plugin loudness
```

`run.sh` runs in the development container when Podman is installed (`tools/container.sh`),
against the JUCE install in `JUCE_INSTALL_DIR` (default `build/juce-install` at the
repository root, made on first use). It builds `<plugin>`, `<plugin>_control` and
`<plugin>_headless` for x86_64 together with the in-process checker (below), builds this
checker, loads the three LV2 bundles through JUCE's own plugin hosting, and checks:

| Check | Why it matters |
|---|---|
| all three load | |
| same parameters: IDs, names, defaults, value texts | the controller and the speaker address parameters by ID |
| only the headless build has no editor | |
| state saved by control restores into headless, and back | the session document moves between the two |
| headless, set up only from the control build's saved state, outputs exactly what the complete build outputs | the speaker sounds like the plugin you tuned with |
| the control build leaves audio untouched | it has no DSP compiled in |
| the three builds have the same monitors (`mon_` parameters) | the controller subscribes to what the speaker publishes |
| no build's saved state carries a monitor (the plugin's own state, decoded from the LV2 state) | a monitor is measured, never restored |
| the hosted headless build, after a -20 dBFS tone, publishes each monitor within its tolerance of what the complete build's getter reads for the same tone (the hosted complete build too) | this is how `speakerd` reads a monitor |
| ... and so does a fresh headless instance, 40 times over, each played at 44.1, 48 and 96 kHz and then destroyed | `speakerd` creates and destroys instances on every graph it applies |
| the control build's editor opens (and a screenshot) | the editor runs without its DSP |

Before that, `run.sh` runs the **in-process check** (`in-process/`) on each build: the build's
shared code linked straight into a small program, the way the controller app links its control
builds, for what LV2 hosting cannot see. It checks that the monitors are declared after the
other parameters, not automatable and of category output meter; that the build's own saved
state carries none and restoring a state that names them sets none; in the complete and
headless builds, that each monitor holds what its getter reads after the tone, and that it
tells the processor's listeners only when created as for JUCE's LV2 wrapper (with
`setTypeOfNextNewPlugin`), when its stored value moved by a step or once a second, and never
as the VST3 wrapper; and in the control build, that setting a monitor moves its getter
(`getLevelDb()` and friends). The
getters are named in `juce_plugins/<plugin>/plugin-check.monitors.h`; the complete build prints
what they read (`EXPECT` lines), which `run.sh` passes to the checker above as
`--expect-monitor`.

Exit status 0 means every check passed; the report (in-process checks first) is in
`<BUILD_DIR>/<plugin>-report.txt`, the builds in `<BUILD_DIR>/<plugin>-builds/` (the bundles in
`<plugin>-builds/plugin/<target>_artefacts/Release/LV2/<target>.lv2`).
The audio check uses the parameter values in `juce_plugins/<plugin>/plugin-check.probes`, so it
exercises the DSP rather than a flat setting.

## Build trees: with and without LTO, ccache

`run.sh` reads these from the environment (`tools/container.sh` gives each of them to the
development container):

| Variable | | Default |
|---|---|---|
| `JUCE_INSTALL_DIR` | the JUCE install | `build/juce-install` at the repository root |
| `PLUGIN_CHECK_LTO` | `1`: the plugin's builds with link-time optimisation, as they ship; `0`: without | `1` |
| `BUILD_DIR` | where the builds and the report go | `build-check` here; `build-check-nolto` with `PLUGIN_CHECK_LTO=0` |
| `BPI_CCACHE` | `1`: ccache as the compiler launcher, when it is on PATH; anything else: none | unset: none |

`PLUGIN_CHECK_LTO` becomes `-DSPEAKER_LTO=ON` or `OFF` on every configure of the plugin's
builds (the option of `juce_plugins/cmake/SpeakerPlugin.cmake`, which every build that does not
pass it keeps on). Most of a check after an edit is the plugin's six LTO links, so without LTO
it is several times faster. Measured for loudness after a one-line edit of
`PluginProcessor.cpp`, through ccache, on 24 threads that other builds were loading (2026-11-04):
58, 63 and 73 s with LTO, 8.4, 14 and 16 s without (on a quieter machine before ccache, 54 s
against 9 to 12.5 s). `tools/test.sh plugin <name>`, the focused
check, runs without LTO, in `build-check-nolto`; `tools/test.sh plugins` and level 1
(`tools/test.sh unit`) run with it, in `build-check`, so the levels check what ships. The no-LTO
builds' report reads as the LTO builds' does (loudness: line for line), but their binaries are
not the ones that ship.

The two trees are kept apart because `SPEAKER_LTO` changes every compile command: a tree switched
from one to the other rebuilds everything. An explicit `BUILD_DIR` is configured the way
`PLUGIN_CHECK_LTO` says on every run, so one used both ways rebuilds each time. Each tree also
builds the checker itself, once (`<BUILD_DIR>/plugin-check`, the same in both trees), under a
lock, so the checks of two plugins can run at once in one `BUILD_DIR`.

ccache is the caller's choice: `run.sh` has no policy of its own. `tools/test.sh` sets
`BPI_CCACHE`, `CCACHE_DIR` and `CCACHE_BASEDIR` (`ccache_env` in `tools/container.sh`: in the
development container, not under CI), and both configures (the plugin's builds and this checker)
then set ccache as the compiler launcher. Run on its own, with `BPI_CCACHE` unset, `run.sh` sets
an empty launcher, which also takes ccache off a tree configured with it before; switching
rebuilds the tree.

## The display and the editor check

The editor check needs an X display. Without `DISPLAY` the script starts `Xvfb` and `openbox` if
they are installed (a window manager is needed: without one, opening an LV2 editor fails with an
X `BadAtom` error) and skips the check otherwise. Xvfb runs on the first free display number
from `:200` up (to `:299`), never on `:0` and the low numbers: a JUCE program with no `DISPLAY`
opens `:0.0`, the plugins `speakerd` hosts included, and exits when that X server stops
([`tests/vpair/README.md`](../../../tests/vpair/README.md)), and an Xvfb given `-displayfd`
alone takes the first free number from `:0`. The numbers between are others' in the same
container: `:96` the controller's screenshot run's, `:100` to `:199` the vpair pool's workers'.
Given a number with `-displayfd`, Xvfb writes it once it accepts connections, and exits at once
(in a few milliseconds) when an X server already listens there; it keeps no lock file then, so
its socket alone decides
([`x11-plugin-windows.md`](../../docs/juce-knowledge/gui/x11-plugin-windows.md), 4). `run.sh`
then tries the next number, so checks running at once each get their own display. openbox runs
its `--startup` command once it manages the screen. The script waits for both, at most 10 s each
(Xvfb's 10 s for all its tries), rather than sleep a fixed 1.5 s, and fails if either does not
start; their logs are `<plugin>-xvfb.log` (a `--- Xvfb :<n>` line before each try's output) and
`<plugin>-openbox.log` in `BUILD_DIR`.

The editor stays up until `run.sh` has a screenshot of it painted (at most 40 s of trying),
because under load an editor can take seconds to paint for the first time. Under load the
embedded editor also sometimes stays an unpainted rectangle for good: JUCE's XEmbed host never
maps the plugin's window into its own. An editor still unpainted after 3 s is therefore nudged
(plugin-check hides and shows its window, which makes the host look at the client again), and
the report says so. Under full CPU load about one run in three needed one nudge; all painted.
`run.sh` watches the report for the editor every 0.05 s and captures the screen every 0.1 s (plus
the capture's own time). Against captures every 0.5 s the screenshots were the same pixel for
pixel (a nudged editor's and an un-nudged one's alike), and nudges no more frequent (4 runs of 11
against 6 of 9; loudness, 2026-11-04, under load).

## Four things this checker found

- **LV2 state carries the plugin's URI.** JUCE saves an LV2 plugin's state as a preset whose
  subject and state key are built from the plugin URI, and the control and headless builds
  have different URIs. Restored unchanged into the other build, the state is silently ignored.
  The checker retargets the URI when it moves state; the controller and `speakerd` must do the
  same, or keep state in the plugin's own format.
- **A hosted LV2 plugin only sees parameter changes while it processes.** Changes set through
  the host travel on the plugin's atom port during `processBlock`, so the checker processes a
  few silent blocks after setting values and before saving state.
- **JUCE's LV2 wrapper does not report a parameter the plugin set with `setValue()`.** After
  each `run()` it sends the host a `patch:Set` only for the parameters its `ParameterStorage`
  heard about through `AudioProcessorListener::audioProcessorParameterChanged()`, and
  `setValue()` tells no listener. With the audio thread writing a monitor that way and nothing
  else, the hosted headless build's `mon_levelDb` stayed at -120 while the plugin's own value was
  -21.7. A plugin therefore also notifies the listeners when a monitor moved, from the audio
  thread (`juce_plugins/loudness/Monitors.h` says why no other thread is safe), and the wrapper
  reports it at the end of the same cycle.
- **Several copies of JUCE in one process can leave X errors fatal.** Every LV2 bundle carries
  its own JUCE, and each copy saves, replaces and restores the process's X error handler, the
  plugins' copies on their own message threads. The saves and restores can interleave and leave
  Xlib's default handler in place, which exits the process on any X error. Under
  load the embedded editor is sometimes configured to 0x0 before it has its size (BadValue in
  `X_ConfigureWindow`), and plugin-check died with the editor window never painted. The checker
  installs a handler that ignores X errors before it opens the editor, and says so in the
  report when it replaced Xlib's default or ignored an error. The controller compiles its
  control builds in, with one copy of JUCE, so it is not exposed to this.
