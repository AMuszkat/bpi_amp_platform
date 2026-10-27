# plugin-check

Checks that the three builds of a plugin fit together the way the remote plugin host needs
them to. The design is in `docs/remote-plugin-host.md` of
[bpi_amp_platform](https://github.com/AMuszkat/bpi_amp_platform); in short, the controller app
runs a plugin's **control** build (its editor, no DSP) and each speaker runs its **headless**
build (its DSP, no editor), and the two exchange parameter values and saved state.

```bash
JUCE_INSTALL_DIR=/path/to/juce-install juce_apps/plugin-check/run.sh loudness
```

`run.sh` builds `<plugin>`, `<plugin>_control` and `<plugin>_headless` natively, builds this
checker, loads the three LV2 bundles through JUCE's own plugin hosting, and checks:

| Check | Why it matters |
|---|---|
| all three load | |
| same parameters: IDs, names, defaults, value texts | the controller and the speaker address parameters by ID |
| only the headless build has no editor | |
| state saved by control restores into headless, and back | the session document moves between the two |
| headless, set up only from the control build's saved state, outputs exactly what the complete build outputs | the speaker sounds like the plugin you tuned with |
| the control build leaves audio untouched | it has no DSP compiled in |
| the control build's editor opens (and a screenshot) | the editor runs without its DSP |

Exit status 0 means every check passed; the report is in `build-check/<plugin>-report.txt`.
The audio check uses the parameter values in `juce_plugins/<plugin>/plugin-check.probes`, so it
exercises the DSP rather than a flat setting.

The editor check needs an X display. Without `DISPLAY` the script starts `Xvfb` and `openbox`
if they are installed (a window manager is needed: without one, opening an LV2 editor fails
with an X `BadAtom` error) and skips the check otherwise.

## Two things this checker found

- **LV2 state carries the plugin's URI.** JUCE saves an LV2 plugin's state as a preset whose
  subject and state key are built from the plugin URI, and the control and headless builds
  have different URIs. Restored unchanged into the other build, the state is silently ignored.
  The checker retargets the URI when it moves state; the controller and `speakerd` must do the
  same, or keep state in the plugin's own format.
- **A hosted LV2 plugin only sees parameter changes while it processes.** Changes set through
  the host travel on the plugin's atom port during `processBlock`, so the checker processes a
  few silent blocks after setting values and before saving state.
