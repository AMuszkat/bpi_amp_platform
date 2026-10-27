# The virtual pair

Two virtual speakers, `left` and `right`, on one machine, for testing the remote plugin host
(`docs/remote-plugin-host.md`) without hardware. This is rung 2 of
`docs/autonomous-development.md`.

Each virtual speaker has what a real one has around its audio path:

- its own **PipeWire**, at the board's 48 kHz and quantum 1024 (`speaker.conf`);
- its own **WirePlumber**, which links streams to their targets as on the board;
- its own **D-Bus session**, which WirePlumber needs;
- an **amplifier**: a null sink with the two amplifier channels, woofer as FL and tweeter as FR.

What a test plays into a speaker reaches its amplifier; the harness records the amplifier's
monitor, which is exactly what the MAX98397s would have received.

## Running

```bash
sudo apt install pipewire pipewire-bin wireplumber dbus python3-numpy
python3 -m unittest discover -s tests/vpair -v
```

The tests bring the pair up and take it down themselves. By hand:

```bash
tests/vpair/vpair.sh up                     # both speakers
eval "$(tests/vpair/vpair.sh env left)"     # point pw-cli, pw-dump, pw-play ... at one of them
pw-link -l
tests/vpair/vpair.sh down
```

The speakers keep their sockets, state and logs in `$VPAIR_DIR` (default `/tmp/vpair`), one
folder each. Keep that path short: PipeWire's socket path must fit in 108 bytes.

## Files

| File | What it is |
|---|---|
| `speaker.conf` | PipeWire configuration of one virtual speaker |
| `vpair.sh` | starts, stops and describes the pair |
| `harness.py` | plays into and records from a speaker, plus signal analysis |
| `test_audio_path.py` | the pair itself works: tones arrive intact on both channels, the speakers are independent |

## Two things worth knowing

- **PipeWire needs its access module even with nothing to protect.** Without
  `libpipewire-module-access` a client connects but is never granted permissions, so
  `pw-dump` and `pw-cli` hang with no error.
- **The amplifier only has ports once WirePlumber runs.** A bare PipeWire with a null sink
  shows no ports: the session manager configures them. `vpair.sh up` waits for
  `amplifier:playback_FL` before it reports a speaker as up.

## Next

`speakerd` and `ampd` on each speaker, a Snapcast source feeding both, `tc netem` between
them for packet loss, and the acceptance scenarios of `docs/autonomous-development.md`
(knob latency, graph edits, hub down, monitoring under loss, and the rest).
