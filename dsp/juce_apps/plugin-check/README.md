# plugin-check

Checks that the three builds of a plugin fit together the way the remote plugin host needs
them to. The design is in [`docs/remote-plugin-host.md`](../../../docs/remote-plugin-host.md); in short, the controller app
runs a plugin's **control** build (its editor, no DSP) and each speaker runs its **headless**
build (its DSP, no editor), and the two exchange parameter values and saved state.

```bash
dsp/juce_apps/plugin-check/run.sh loudness     # or tools/test.sh unit, for every plugin
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
| the control build's editor opens (and a screenshot) | the editor runs without its DSP |

Before that, `run.sh` runs the **in-process check** (`in-process/`) on each build: the build's
shared code linked straight into a small program, the way the controller app links its control
builds, for what LV2 hosting cannot see. It checks that the monitors are declared after the
other parameters, not automatable and of category output meter; that the build's own saved
state carries none and restoring a state that names them sets none; in the complete and
headless builds, that each monitor holds what its getter reads after the tone; and in the
control build, that setting a monitor moves its getter (`getLevelDb()` and friends). The
getters are named in `juce_plugins/<plugin>/plugin-check.monitors.h`; the complete build prints
what they read (`EXPECT` lines), which `run.sh` passes to the checker above as
`--expect-monitor`.

Exit status 0 means every check passed; the report (in-process checks first) is in
`build-check/<plugin>-report.txt`, the builds in `build-check/<plugin>-builds/`.
The audio check uses the parameter values in `juce_plugins/<plugin>/plugin-check.probes`, so it
exercises the DSP rather than a flat setting.

The editor check needs an X display. Without `DISPLAY` the script starts `Xvfb` and `openbox`
if they are installed (a window manager is needed: without one, opening an LV2 editor fails
with an X `BadAtom` error) and skips the check otherwise.

## Three things this checker found

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
  -21.7. A plugin therefore runs a notifier thread that tells the listeners when a monitor moved
  (`juce_plugins/loudness/Monitors.h`); the wrapper reports it after the next cycle. The value
  reaches the host only while the plugin processes, which is why the monitor check plays its
  tone in real time for half a second before reading.
