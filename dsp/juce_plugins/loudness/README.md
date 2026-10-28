# loudness

A level-dependent **loudness contour**. As playback gets quieter the ear loses bass
sensitivity (the Fletcher–Munson effect), so quiet playback wants more low-frequency lift.
Instead of one fixed shelf, each of **four settings** has its own detector threshold and two
low-shelf EQs (gain / frequency / Q each). The setting whose threshold the input level falls
into is the one applied. Mono in, mono out.

No generated header: everything is a live, automatable parameter (35 of them), plus two
read-only monitors (below).

```bash
../tools/build-podman.sh loudness
../tools/build-podman.sh loudness -debug-linux     # try it locally
```

**Every setting starts flat** (all gains 0 dB), so the plugin passes audio unchanged until
you draw the contours. Only the thresholds and the shelf starting points (60 Hz / 160 Hz,
Q 0.7) are preset.

## Using it

- **Morph Pad.** A log-frequency pad showing all four settings. The focused one is bold, and
  its two shelves are draggable dots (x = frequency, y = gain). The others are faint ghosts;
  click one to focus it. Which setting is bold follows the open panel, otherwise the
  detector's live zone.
- **S1–S4 chips** open a setting's panel in its own window below the plugin: seven drag
  rows plus FLAT / COPY → / DONE. Every value can also be typed; units and a `k` multiplier
  are understood, so `1.5k` means 1500 Hz.
- **Detector meter** on the side, with the four thresholds as ticks you can drag.
- **SETUP** holds the detector parameters: Mode (Auto / Force 1–4, to audition one setting
  while editing it), Detector (RMS / Peak), Attack, Release, Hysteresis, Glide.
- **VIEW** sets the pad's frequency/gain range. It's view state saved with the session,
  not a host parameter.

## How it behaves, and why

- **RMS detection with VU-like ballistics** (100 ms attack / 300 ms release by default),
  not peak detection, so the contour follows loudness and doesn't pump on transients.
- **The detector reads the input, before the EQ.** Reading the output would let a bass boost
  raise the measured level, select a flatter setting, drop the level again, and oscillate.
- **Thresholds are hysteretic** (default 3 dB), so a level sitting on a boundary doesn't
  chatter between two settings. Thresholds don't need to be in order; below all of them the
  lowest-threshold setting applies.
- **Switching glides** all six EQ values to the new setting (default 120 ms) and recomputes
  coefficients every 32 samples, so there is never a click.
- **Plain TDF-II biquads, not a state-variable filter.** Measured under gain modulation, the
  biquad splatters about 30 dB less than a TPT shelving SVF. See
  [modulated-filter-topology.md](../../docs/juce-knowledge/dsp/modulated-filter-topology.md).

## Monitors

The detector's level and the active setting are also published as two read-only parameters,
so that the speaker host can read them and the controller can show them
([docs/protocol.md](../../../docs/protocol.md), "How a plugin publishes a monitor"):

| ID | Range | Getter |
|---|---|---|
| `mon_levelDb` | -120 to 0 dBFS | `getLevelDb()` |
| `mon_activeSetting` | 0 to 3 (the setting's index) | `getActiveSetting()` |

They are in every build, declared after the other parameters, not automatable, of category
output meter, and never in the saved state: they are added with `addParameter()`, not through
the APVTS, and `setStateInformation()` drops any `mon_` entry a state brings. The complete and
headless builds write them from the audio thread about 60 times a second with a plain
`setValue()`, and a notifier thread tells the host (see `Monitors.h` for why JUCE's LV2 wrapper
needs that). In the control build nothing measures: the getters read the two parameters, so
the editor shows whatever value they are given. With the 35 parameters, the host-side bypass
JUCE's LV2 hosting adds and the two monitors, an LV2 host sees 38 parameters.

## Implementation notes

- `PluginProcessor.*`: parameters, state and the getters (every build). `LoudnessDsp.cpp`:
  detector, setting selection, glide, biquads, and writing the monitors (not in the control
  build).
- `Monitors.h`: the monitor helpers (declaring, writing, removing from a state, the notifier).
- `plugin-check.monitors.h`: which getter reads which monitor, for plugin-check's in-process
  check; not compiled into the plugin.
- `MorphPad.*`: the pad. Its static half (grid, ghost curves) is cached in an opaque
  `Image::RGB` drawn with a translation-only transform, because an ARGB or scaled image is
  resampled per pixel. That's the difference between about 9 ms and about 20 ms a frame at
  4K.
- `HostWindowTracker.*`: keeps the floating settings panel attached to the host window.
  JUCE's cached screen position for an embedded editor goes stale when the host window
  moves, so it asks the X server directly (JUCE's `JUCE_GUI_BASICS_INCLUDE_XHEADERS`
  opt-in, no link-time libX11). The panel is a managed utility window, not
  override-redirect, because an override-redirect window can't keep keyboard focus for
  typing.
- `Theme.h`: palette and fonts (`Theme::sans` / `Theme::mono`).
- The editor is fixed at 620×474 and calls `setResizeLimits` with that size, so no host can
  stretch it.
