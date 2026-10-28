# The virtual pair

Two virtual speakers, `left` and `right`, on one machine, for testing the remote plugin host
(`docs/remote-plugin-host.md`) without hardware. This is rung 2 of
`docs/autonomous-development.md`.

Each virtual speaker has what a real one has around its audio path:

- its own **PipeWire**, at 48 kHz and quantum 1024 (`speaker.conf`), with the profiler module
  as on the boards; a test may start a speaker from another configuration, and with a folder of
  drop-ins, as a board loads `~/.config/pipewire/pipewire.conf.d`;
- its own **WirePlumber**, which links streams to their targets as on the board;
- its own **D-Bus session**, which WirePlumber needs;
- an **amplifier**: a null sink with the two amplifier channels, woofer as FL and tweeter as FR,
  at `priority.driver` 1000 like the board's ALSA sink in `firmware/pipewire/pipewire.conf`.

What a test plays into a speaker reaches its amplifier; the harness records the amplifier's
monitor, which is exactly what the MAX98397s would have received.

## Running

```bash
python3 -m unittest discover -s tests/vpair -v     # or tools/test.sh vpair, which records the result
python3 -m unittest -v tests.vpair.test_passthrough_safety    # one module, from the repository root
```

`tools/test.sh vpair` records `vpair: pass` or `fail` for the deploy gate, with a note
(`vpair_note`) of how many tests ran, which failed, and which were skipped and why. A skipped
test does not fail the run, but a pass with skips says so: `test_no_network` skips where
`unshare -rn` is refused, as on GitHub's runners.

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
| `harness.py` | plays into and records from a speaker, also in the background, once the links exist; signal analysis, builds and runs `speakerd` and its offline render; moves the test run into the container when the pair lives there |
| `__init__.py` | puts the harness on the path for `python3 -m unittest tests.vpair.<module>` |
| `test_audio_path.py` | the pair itself works: tones arrive intact on both channels, the speakers are independent |
| `test_speakerd_same_sound.py` | speakerd running the headless build sounds like the complete build |
| `test_speakerd_state_layers.py` | a broken layer falls back to the next one, identified by the log and by its sound |
| `test_passthrough_safety.py` | the `passthrough_safety` scenario: with no plugin or no layer, the tweeter is silent and the woofer capped |
| `test_speakerd_start_order.py` | speakerd plays whatever starts after it: the amplifier, PipeWire; and with no network |
| `test_speakerd_restart.py` | speakerd stopped under a playing stream leaves the amplifier silent and the stream on its sink; the next one adopts it |
| `test_speakerd_second_instance.py` | a second speakerd on the same PipeWire refuses, and the first plays on |
| `test_speakerd_quantum_change.py` | the quantum changes while a stream plays, and the gain comes back each time |
| `test_speakerd_device_limits.py` | device.json's limits, live and rendered, as a ceiling, and failing closed |
| `test_speakerd_import.py` | an AudioPluginHost session imported renders like the same settings as parameters; unsafe sessions are refused |
| `test_firmware_speakerd_install.py` | `firmware/speakerd-setup.sh` and `setup.sh`: the layout, always capped, versions and rollback, the refusals before any change, the systemd path, the remote mode and the board script |
| `test_speakerd_crash_restart.py` | the installed unit's wrapper starts speakerd on the right layer after crashes |
| `test_speakerd_installed.py` | a speaker set up like a board: the persistent sink, the amplifier drives, capped, the deployed bundle runs, smoke; and a PipeWire that does not load the drop-in, where nothing is routed into speakerd |
| `stub_speakerd.c` | a stand-in speakerd that dies as it is told, for the installer and wrapper tests |

## Before and after the speakerd that enforces the limits

`test_speakerd_device_limits`, `test_speakerd_import`, `test_speakerd_installed` and the real
speakerd's part of `test_speakerd_crash_restart` need the speakerd of the board readiness work
(the limits, failing closed, `--version`, the import and its example session). Against an
older speakerd they fail, each saying what it lacks: the installer refuses a speakerd older
than `SPEAKERD_MIN_VERSION` (firmware/speakerd-setup.sh), which would ignore the cap.

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
| `test_passthrough_safety` | with every plugin removed or no layer at all, for role left, right and mono: every sample of the tweeter capture is exactly 0, the woofer is the own channel at -20 dB within 0.05 dB and never above -20 dBFS; with the input above full scale, the `speakerd` sink's monitor, recorded as floats, peaks above 0 dBFS (so the cap holds the woofer, not PipeWire), and the woofer still does not exceed -20 dBFS; and speakerd's render of that input with no layer is the own channel at -20 dB clamped to -20 dBFS sample for sample, peaking at exactly -20 dBFS, with a tweeter of zeros |
| `test_speakerd_start_order` | started before its amplifier exists, with the amplifier going away and coming back, before PipeWire runs, and in a network namespace with no network, speakerd links, says it plays, and plays the complete build's gain within 0.05 dB, holding no IP socket |
| `test_speakerd_restart` | speakerd stopped by SIGTERM while a stream plays exits with status 0 and "stopping" last; the amplifier is then silent for 1 s (woofer below -100 dBFS, tweeter all zeros) and the stream stays linked to the `speakerd` sink alone; a new speakerd says it uses the sink that exists and gives the render's gain within 0.05 dB, under the same stream |
| `test_speakerd_second_instance` | a second speakerd on the same PipeWire says that another one runs (its `speakerd-dsp` exists), then `stopping`, and exits with status 3, and the first still gives the render's gain within 0.05 dB (not 6 dB more). It needs the refusal in speakerd, which came after step 2's first speakerd: against one without it, it fails, and says so |
| `test_speakerd_quantum_change` | with a stream playing, the quantum forced to 256, 2048 and back to 1024: speakerd says each change (re-primed, or run as several blocks), the gain comes back to the render's within 0.05 dB on both channels after each, and the amplifier, recorded as floats throughout, holds no NaN, infinity or sample above full scale. The gap at a change is printed, not asserted: PipeWire 0.3.65 drops out or jumps at a quantum change by itself |
| `test_speakerd_device_limits` | with limits -20 dB and the tweeter muted: the tweeter capture is exact zeros and the woofer's gain is the unlimited capture's minus 20 dB within 0.05 dB, live and in the render; a +6 dBFS input and a +20 dB boost document never put a woofer sample above -20 dBFS, live or rendered; passthrough peaks at no more than -40 dBFS and plays; a device.json without limits says it plays the fail-safe limits, one with a misspelt `limts`, one not JSON and a missing one say `device.json: FAILED`, and each plays the boost document with the tweeter at exact zeros and the woofer at no more than -20 dBFS |
| `test_speakerd_import` | speakerd's example session imported (complete build, and with `--prefer-headless`) renders sample for sample like the same settings given as parameters; an empty file, text, XML that is not a FILTERGRAPH, a connection to output channel 2, and the input wired straight to the tweeter each exit 2 with nothing on stdout |
| `test_firmware_speakerd_install` | the bundle step and `speakerd-setup.sh install --home --no-systemd` lay out exactly the documented tree (versions, `current`, an empty HOME, `device.json` with `limits` -20 dB and the tweeter muted, the unit and the drop-in byte for byte, ampd's layers untouched); a reinstall adds nothing and caps lifted limits again, keeping role, ports and other keys; rollback swaps `current` and `previous` both ways and `--keep` prunes the oldest; one of `--amplifier`, `--woofer-port`, `--tweeter-port` changes only itself; install refuses without `--role` (a running plugin host stays running), a speakerd that cannot say its version, is older than `SPEAKERD_MIN_VERSION` (rollback too) or cannot be executed (noexec), a device.json with a key speakerd does not know or a key twice, while the stand-in plugin host runs (and stops it with `--stop-plugin-host`, only once every other check passed, disabling its user unit, which uninstall re-enables), and a factory graph in which anything but a crossover's high band reaches the tweeter; a same-URI plugin in a system LV2 folder is named; smoke reports a `device.json: FAILED` line, either fail-safe suffix, `NO INPUT SINK CONFIGURED` and no limits line as failures, and always prints its summary; `--import-filtergraph` installs speakerd's conversion, given the device.json to be (`--device`); the amplifier is found in a pw-dump (one ALSA sink, the two known names, refusals naming the sinks; the virtual amplifier's ports); `--route speakerd` is refused without the configured sink, keeps the routing there was, and uninstall puts it back exactly; `setup.sh` checks every component before it changes any (also refusing `--route plugin-host` with speakerd) and installs in boot order; under stand-in systemd, install fails unless speakerd runs and says exactly its limits, and setup.sh stops on a failed PipeWire restart and stops the plugin host before restarting PipeWire; the remote mode, through a stand-in ssh that runs the board's side here as `nobody`, copies one bundle to `~/.cache`, runs check (with the artifacts) and the board-side dry run, delivers every argument as given (a path with spaces, a semicolon that runs nothing), cleans up, and refuses root; the board script (`--dry-run`) refuses a `journal -n` that is not a number and a deploy option that is not an agent's, quotes journal's argument, and reads status from the board's installed script |
| `test_speakerd_crash_restart` | the installed unit (no network unit, `Restart=on-failure`, 1 s, never after exit 2, no start limit, an empty HOME), run by `harness.UserUnit`: the real speakerd killed by SIGSEGV is restarted on `last-known-good`, after SIGTERM on `current`; with the stand-in, six crashes give current, last-known-good twice, factory three times (three within 60 s), then passthrough (three on factory), exit 3 keeps the layer, SIGTERM and exit 2 are not restarted, SIGPIPE and a core dump are crashes; crashes older than 60 s do not count towards factory; a start with no exit counts as a crash; a start after five failures waits and says so; no speakerd starts while an AudioPluginHost runs |
| `test_speakerd_installed` | two versions installed, each speaker's PipeWire started with the installed drop-ins (the right one's amplifier without a priority): the `speakerd` sink exists before speakerd at `priority.driver` 0 and is the default sink; speakerd, started by the unit, adopts it and plays factory; `pw-profiler` shows the amplifier as the only driver with the sink, speakerd-dsp and the stream as followers; speakerd says `device: limits: output at -20 dB, tweeter muted`, the tweeter is all zeros and the woofer is the complete build's render minus 20 dB within 0.05 dB; the current version's bundle loads although a broken copy with its URI lies in the home's `~/.lv2` (with HOME there speakerd stops with status 2), and after a rollback the previous version's does; speakerd's HOME stays empty; `speakerd-setup.sh smoke` passes every check, the live sink's drop-in properties and the default sink included; `--route speakerd` routes; and with a PipeWire that does not load the drop-in, the route is refused both with no `speakerd` node and with the sink speakerd made itself, smoke fails on that sink, and `sink` and `install` refuse. Against a speakerd older than `SPEAKERD_MIN_VERSION` the install refuses it, and the module fails saying so |

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
  a log line, by default the `playing:` line from which a player is heard (`wait_for=None`
  waits for none). `sd.wait_for(text, after=sd.mark())` waits for a later line, or for the
  first of several given as a tuple. A timeout, or speakerd exiting, fails the test with
  speakerd's whole output. `sd.stop()` sends SIGTERM and `sd.wait_exit()` waits for speakerd to
  exit on its own; both return its exit status. Leaving the block sends SIGTERM if it still
  runs, and checks that `stopping` was its last line, said once, and that it exited with
  `exit_status` (0 unless given). After a failure speakerd is stopped all the same. Log lines
  are matched as speakerd's README lists them, after the `speakerd: ` prefix.
- `play_and_record()` plays into one node and records another; `play_and_record_many()`
  records several at once. Each recording is linked before the player starts, and the player
  has finished when they return, so it is always stopped before speakerd.
- `Player(speaker, samples, target)` plays in the background for a `with` block, from the
  moment WirePlumber has linked it to `target`: a stream that outlives a speakerd, or one a
  test changes things under. `Recorder(speaker, node)` records a node's monitor for a `with`
  block or from `start()` to `stop()`, from the moment it is linked. `speaker.links()` is
  `pw-link -l` as a set of `("node:port", "node:port")` pairs, and `speaker.wait_for_links()`
  waits for some. `speaker.run()` runs a tool through `vpair.sh run`.
- Samples beyond full scale are written as a 32-bit float WAV, which pw-play and PipeWire carry
  through unclipped. Recordings are 32-bit integer (`fmt="s32"`, the default), which cannot show
  anything above 0 dBFS, or 32-bit float (`fmt="f32"`), which shows every value as it is, NaN
  and above full scale included.
- `Pair.down(name)` and `Pair.up(name)` take one speaker down and up again, for a scenario
  that starts something before that speaker's PipeWire runs. `speaker.create_sink(name)` adds a
  null sink like the amplifier and `speaker.remove_sink(name)` destroys it (see Worth knowing).
  `speaker.set_quantum(n)` forces a speaker's quantum, and `set_quantum(0)` lets it go.
- A wait for a link, a port or a log line that times out says, in its failure, whether the
  speaker's PipeWire or WirePlumber has gone, with the end of its log (`speaker.health()`).

## The board side in a test

```python
from harness import Pair, UserUnit, drivers, firmware, stub_speakerd

firmware("speakerd-setup.sh", "install", "--bundle", b, "--home", home, "--no-systemd", "--role", "left")
with Pair({"left": {"dropins": home / ".config/pipewire/pipewire.conf.d"}}) as pair:
    unit = UserUnit(home / ".config/systemd/user/speakerd.service", home, pair["left"])
    run = unit.start(log_path=log)           # ExecStart, with the unit's Environment=
    run.wait_for("speakerd: playing: ")
    found, followers, _ = drivers(pair["left"])   # who drives, from pw-profiler
    run.signal(signal.SIGSEGV)
    result = run.finish()                    # ExecStopPost with SERVICE_RESULT, EXIT_CODE, EXIT_STATUS
    unit.would_restart(result)               # what systemd would do, from Restart=...
```

- `firmware(script, *args)` runs a `firmware/` script with `TEST_ENV` on top of the environment:
  `SPEAKERD_PLUGIN_HOST_NAME` (the installers and speakerd-run look for a process called
  `TEST_PLUGIN_HOST`, never this machine's own AudioPluginHost, which a test must neither wait
  for nor stop) and `SPEAKERD_SYSTEM_LV2_DIRS` (no system LV2 folder of this machine).
  `UserUnit` runs with them too. `stub_plugin_host(folder)` starts the stand-in plugin host, a
  copy of sleep with that name; only the processes a test started are ever stopped.
- `Pair(setup)` starts a speaker from
  another PipeWire configuration (`speaker_conf`) or with a folder of drop-ins (`dropins`), as
  `vpair.sh up` does with `VPAIR_SPEAKER_CONF` and `VPAIR_PIPEWIRE_DROPINS`.
- `UserUnit` reads a real unit file and runs it as systemd would, as far as a test can: there is
  no systemd in the container, so nothing restarts by itself.
- `stub_speakerd()` compiles `stub_speakerd.c` (an ELF, which the installer's machine check
  needs): it dies by the signal or exits with the code its `STUB_DIR/fate` says, prints
  `STUB_IMPORT` for `--import-filtergraph`, and says `STUB_VERSION` for `--version` (without
  it, it answers as a speakerd from before `--version`, which the installer refuses). The
  installer tests use it, so that they test the installer whatever speakerd is built.
- `state_dir()` writes `device.json` with `"limits": {"gain_db": 0, "tweeter": "on"}`: no limit,
  said explicitly, because a device.json without limits plays capped.

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
- **WirePlumber 0.4.13 dies silently about 5 s after a null sink is destroyed that went idle
  less than 5 s earlier** (its suspend timer is still pending), and from then on new nodes get
  no ports. `remove_sink()` therefore destroys a sink while a player of silence keeps it
  running. Destroy a sink some other way only while it runs, or more than 5 s after it went
  idle.
- **The `speakerd` sink stays.** speakerd leaves its input sink in PipeWire when it stops (on
  purpose: speakerd's README, "Whose sink"), so within one module, which keeps the pair up, every
  speakerd after the first on a speaker adopts the sink the first one made.
- **A forced quantum is rounded down to a power of two.** PipeWire 0.3.65 does that by default
  (`clock.power-of-two-quantum`, as on the boards): `clock.force-quantum 1536` runs at 1024.
- **PipeWire glitches on a quantum change by itself.** A tone played straight into the
  amplifier, quantum 1024 to 256 to 2048 to 1024, jumps by 0.6 to 0.9 of its amplitude at each
  change and drops out for 42 ms at the change to 2048, the same every time.
- **A null sink is always a driver, and `priority.driver` is compared as if unsigned.**
  `node.driver = false` on a `support.null-audio-sink` is ignored, and a sink at
  `priority.driver = -1` drives an amplifier at 1. With equal priorities the node created first
  drives. PipeWire 0.3.65's `pw-dump` does not say who drives and its `pw-top` has no batch mode:
  `pw-profiler` prints `logging driver <id>` (`harness.drivers()`).
- **A killed child is a zombie until its parent waits for it**, and `/proc/<pid>/comm` still
  names it: the installer and speakerd-run skip processes in state Z.
- **A recording often starts with a quantum or two of zeros**, before the first buffer reaches
  the recorder. The recorder is linked before anything is played, so a capture misses nothing.

## Next

`ampd` on each speaker, a Snapcast source feeding both, `tc netem` between them for packet
loss, and the rest of the acceptance scenarios of `docs/autonomous-development.md` (knob
latency, graph edits, hub down, monitoring under loss, and the others).
