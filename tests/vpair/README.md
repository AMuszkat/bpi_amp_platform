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
python3 -m unittest discover -s tests/vpair -v     # or tools/test.sh vpair, which records the result
python3 -m unittest -v tests.vpair.test_passthrough_safety    # one module, from the repository root
```

A scenario that runs speakerd builds what it needs first, once per test run:
`tools/build.sh speakerd`, and loudness's `headless` and `complete` builds. The builds are
incremental, so this takes about a second when nothing changed, and the first run of a fresh
checkout takes the minutes of a full build.

With Podman installed, the pair and the tests run in the development container
(`tools/container.sh`, Debian bookworm: PipeWire 0.3.65 and WirePlumber 0.4.13, as on the
boards), and the commands here move there themselves. Without it, or with `BPI_CONTAINER=0`,
they run natively and need `pipewire pipewire-bin wireplumber dbus python3-numpy`, and for
the speakerd scenarios the build packages of the root `CLAUDE.md` (and `JUCE_INSTALL_DIR`, or
a JUCE install that `tools/build.sh` makes).

The tests bring the pair up and take it down themselves. By hand:

```bash
tests/vpair/vpair.sh up                     # both speakers
tests/vpair/vpair.sh run left pw-link -l    # a pw-* tool against one of them
tests/vpair/vpair.sh down right             # one speaker (up takes names too)
tests/vpair/vpair.sh down
```

`eval "$(tests/vpair/vpair.sh env left)"` points a shell at one speaker; with the container,
do that in a shell inside it (`tools/container.sh`).

The speakers keep their sockets, state and logs in `$VPAIR_DIR` (default `/tmp/vpair`, in
the container when it is used), one folder each. Keep that path short: PipeWire's socket
path must fit in 108 bytes.

## Files

| File | What it is |
|---|---|
| `speaker.conf` | PipeWire configuration of one virtual speaker |
| `vpair.sh` | starts, stops and describes the pair or one speaker of it, runs a tool against one speaker |
| `harness.py` | plays into and records from a speaker, signal analysis, builds and runs `speakerd` and its offline render; moves the test run into the container when the pair lives there |
| `__init__.py` | puts the harness on the path for `python3 -m unittest tests.vpair.<module>` |
| `test_audio_path.py` | the pair itself works: tones arrive intact on both channels, the speakers are independent |
| `test_speakerd_same_sound.py` | speakerd running the headless build sounds like the complete build |
| `test_speakerd_state_layers.py` | a broken layer falls back to the next one, identified by the log and by its sound |
| `test_passthrough_safety.py` | the `passthrough_safety` scenario: with no plugin or no layer, the tweeter is silent and the woofer capped |
| `test_speakerd_start_order.py` | speakerd plays whatever starts after it: the amplifier, PipeWire; and with no network |

## What the scenarios prove

Each module's docstring states its pass criteria exactly; this is the summary. All of them
run `speakerd` (`dsp/juce_apps/speakerd`) on a virtual speaker with an empty `HOME` and the
bundles named on its command line, play into its `speakerd` sink and record the amplifier.
The reference is speakerd's own offline render (`--render`) of the same document pointed at
the complete build of the plugin, which is what the plugin outside speakerd outputs.

| Module | Pass when |
|---|---|
| `test_speakerd_same_sound` | loudness's headless build, from a hand-written `graph.json` with non-default values in Auto mode, gives every tone from 40 Hz to 6 kHz, at two levels, the gain of the complete build within 0.05 dB on both amplifier channels, with a residual under -80 dBFS; and the gain is not 0 dB, and depends on the level |
| `test_speakerd_state_layers` | a half-written or plugin-less `graph.json` plays `graph.lkg.json`, and with both broken speakerd plays `factory.json`, each said in the log and heard: the 100 Hz gain matches that layer's render within 0.05 dB and is at least 1 dB from every other layer's |
| `test_passthrough_safety` | with every plugin removed or no layer at all, for role left, right and mono: the tweeter is below -120 dBFS, the woofer is the own channel at -20 dB within 0.05 dB and never above -20 dBFS, also with the input above full scale |
| `test_speakerd_start_order` | started before its amplifier exists, with the amplifier going away and coming back, before PipeWire runs, and in a network namespace with no network, speakerd links, says it plays, and plays the complete build's gain within 0.05 dB, holding no IP socket |

## speakerd in a test

```python
from harness import Pair, Speakerd, built, graph_document, lv2_uri, render, state_dir, with_plugin

document = graph_document(lv2_uri("loudness_headless"), {"mode": "Force 1", "s0_e0_gain": 12.0})
state = state_dir(tmp / "live", role="left", current=document)       # also lkg=, factory=
with Pair() as pair, Speakerd(pair["left"], state, bundles=[built("loudness", "headless")]) as sd:
    capture = pair["left"].play_and_record(x, target="speakerd", record_target="amplifier")
reference, log = render(state_dir(tmp / "ref", current=with_plugin(document, lv2_uri("loudness"))),
                        x, bundles=[built("loudness", "complete")])
```

- `built(what, kind)` builds with `tools/build.sh` once per test process and returns the
  program or the LV2 bundle.
- `state_dir()` writes `device.json` and the layers. A layer is a document, a string (written
  as it is, for a broken file), or `None` for no file.
- `Speakerd` starts speakerd with the speaker's environment and an empty `HOME`, and waits for
  a log line, by default the `playing:` line from which a player is heard. `sd.wait_for(text,
  after=sd.mark())` waits for a later line. A timeout, or speakerd exiting, fails the test with
  speakerd's whole output. Leaving the block sends SIGTERM and checks for `stopping` and exit
  status 0. After a failure speakerd is stopped all the same. Log lines are matched as speakerd's
  README lists them, after the `speakerd: ` prefix.
- `play_and_record()` plays into one node and records another. The player has finished when
  it returns, so it is always stopped before speakerd.
- Samples beyond full scale are written as a 32-bit float WAV, which pw-play and PipeWire carry
  through unclipped. Recordings are 32-bit integer, so a capture cannot show anything above
  0 dBFS.
- `Pair.down(name)` and `Pair.up(name)` take one speaker down and up again, for a scenario
  that starts something before that speaker's PipeWire runs.

## Worth knowing

- **PipeWire needs its access module even with nothing to protect.** Without
  `libpipewire-module-access` a client connects but is never granted permissions, so
  `pw-dump` and `pw-cli` hang with no error.
- **WirePlumber 0.4 exits where there is no systemd** unless Bluetooth is off: its
  Bluetooth monitor watches logind. `vpair.sh` turns it off for each speaker, which has no
  Bluetooth anyway.
- **The amplifier only has ports once WirePlumber runs.** A bare PipeWire with a null sink
  shows no ports: the session manager configures them. `vpair.sh up` waits for
  `amplifier:playback_FL` before it reports a speaker as up.

## Next

`ampd` on each speaker, a Snapcast source feeding both, `tc netem` between them for packet
loss, and the rest of the acceptance scenarios of `docs/autonomous-development.md` (knob
latency, graph edits, hub down, monitoring under loss, and the others).
