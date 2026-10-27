# ampScope

A bench oscilloscope for a **MAX98396/MAX98397 class-D amplifier's own telemetry**: speaker
output voltage and current, the PVDD and VBAT rails, and die temperature. The amplifier
measures these itself and sends them back over the TDM bus. Audio passes through
untouched. The plugin lives in the host only so the instrument can sit next to the rest of
the chain.

It has a ten-by-eight graticule, a timebase, per-trace units/division and offset, and a
trigger (free run / auto / normal / single, rising or falling edge, level in divisions),
plus min/max/average readouts per trace.

```bash
../tools/build-podman.sh ampScope
../tools/build-podman.sh ampScope -debug-linux     # Demo source works without hardware
```

## Sources

- **Amplifier.** Reads the codec driver's ALSA interfaces (below). The picker lists
  *amplifiers*, not sound cards. Several amps can share one card and one TDM data line, and
  each is identified by its device-tree `sound-name-prefix` (e.g. `amp1`). The selected amp
  is saved with the session, by name.
- **Demo.** A simulated amplifier (100 Hz, 12 V peak into 4 Ω, a 24 V rail sagging on the
  current peaks). It exercises the timebase, trigger, scaling and decimation without
  hardware.

## Hardware requirements

A MAX98396-family amplifier driven by the Linux `max98396` ASoC driver, with the driver
extensions this plugin relies on:

- a capture PCM carrying the transmit (sense) slots,
- `ampN VI Sense Switch`, `Meas ADC PVDD Switch`, `Meas ADC VBAT Switch` controls,
- read/write slot controls `ampN PCM VMON Slot` / `IMON Slot` / `Supply1 Slot` /
  `Supply2 Slot` / `Therm Slot` (64 = not transmitted).

Everything goes through the driver: the plugin calls only `snd_ctl_*` and `snd_pcm_*`, with
no `/dev/i2c` access and no register writes. Anything the plugin needs that isn't exposed
must be added to the driver, not worked around here.

- **The capture stream is the only data path.** Every trace is a waveform at the PCM
  sample rate. Nothing is plotted from polled controls.
- **The control interface configures the amp.** At start-up and on every device change the
  sense path and the measurement ADC rails are switched on. The `VI Sense Switch` is a DAPM
  switch in front of the whole transmit path: without it nothing is transmitted however the
  slots are set.
- **Slots are chosen per trace** (`off`, `ch0 hi`, `ch0 lo`, …). Slots another amp on the
  same data line already uses are shown and disabled: the amps share the line by
  tri-stating, so handing one slot to two amps would short two outputs together.

The amp numbers transmit slots in 8-bit units, so a 16-bit value at amp slot *s* sits in
capture channel *s*/4, shifted left by `16 − 8·(s mod 4)`. A value at `s mod 4 = 3` would
straddle two channels and is reported, not misread.

## Scaling

Straight from the datasheet (Rev 3), all in `amp::scale`:

| Trace | Scale |
|---|---|
| Voltage sense | 16-bit, ±30 V full scale |
| Current sense | 16-bit, ±8.3 A full scale |
| PVDD (Supply1) | 9-bit, 54.703125 mV/code |
| VBAT (Supply2) | 9-bit, 11.3965 mV/code |
| Temperature | 9-bit, 1 °C/code − 252 °C |

The datasheet doesn't say which end of the 16-bit field holds the 9-bit supply and
temperature codes, so that's a switch (*Supply alignment*, default MSB).

## Implementation notes

- **Record length** is fixed at 2¹⁹ frames (about 10.9 s at 48 kHz). The acquisition thread
  is the only writer of the ring buffer, so the buffer is never reallocated. A window longer
  than the record is clamped, and the footer says so.
- **Drawing.** The graticule is cached in an opaque image. When a window holds more samples
  than the screen has columns, each column is drawn as its min/max envelope rather than a
  sample, so short transients stay visible instead of aliasing away. Measurements are
  computed in the view's timer, not in `paint()`, so they're correct even when no editor is
  showing.
- **A blank screen always says why:** stopped, no acquisition, no trigger, or still filling
  the record.
- `Theme.h` is a copy of loudness's, not a shared file, so restyling one plugin can't change
  the other.
