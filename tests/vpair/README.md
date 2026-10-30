# The virtual pair

Two virtual speakers, `left` and `right`, on one machine, for testing the remote plugin host
(`docs/remote-plugin-host.md`) without hardware. This is rung 2 of
`docs/autonomous-development.md`.

Each virtual speaker has what a real one has around its audio path:

- its own **PipeWire**, at 48 kHz and quantum 1024 (`speaker.conf`), with the profiler module and the real-time module (its data thread runs the amplifier's timer, so a
  loaded machine does not starve the cycles)
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
`tools/build.sh speakerd`, and the plugin builds it plays: loudness's `headless` and
`complete`, the complete builds of firConv and the crossover (and `tools/build.sh controller`
for `test_controller_end_to_end`). The builds are
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
| `stand_in_speakerd.py` | a stand-in speakerd that speaks only the local link of `docs/protocol.md` and answers as a test tells it, failing included: for ampd's tests that need no audio |
| `test_ampd_protocol.py` | ampd, hub and agent, does what `docs/protocol.md` says, against stand-in speakerds |
| `test_two_controllers.py` | two controllers' interleaved changes end in the hub's state; a controller connecting mid-drag sees the gesture |
| `test_firmware_ampd_install.py` | `firmware/ampd-setup.sh` and setup.sh's ampd component: the layout, the refusals, the unit, status and smoke |
| `test_knob_latency.py` | a parameter sent to the hub is heard on both speakers within 60 ms, and less than 5 ms apart at a quantum of 128 (at 1024 the gap is up to one quantum) |
| `test_graph_edit.py` | a new revision changes both captures with no gap or click; one a speaker fails is rolled back on the other |
| `test_hub_down.py` | the right speaker plays on, unchanged, when the left stops |
| `test_meter_values.py` | `output/level` and loudness's `mon_levelDb` track a stepped tone on each speaker; the Mine pin |
| `test_ampd_restart.py` | killing ampd leaves the audio alone, and a subscriber gets values again within 2 s |
| `test_speakerd_tweeter_rule.py` | speakerd's own tweeter rule, as on a board: a breaking layer and apply refused, and the same verdicts as `tweeter_rule.py` |
| `test_tweeter_rule.py` | with a speaker's tweeter on, ampd refuses every revision that breaks the tweeter rule; with it muted, it refuses none |
| `test_drag_during_revision.py` | a value set while a revision applies is kept, everywhere, and after a speakerd restart |
| `test_ampd_persistence.py` | `graph.json` follows the values, speakerd boots on it, `graph.lkg.json` follows a revision that played long enough |
| `test_controller_end_to_end.py` | the real controller in its script test mode against the real pair: connect, knob, edits, a refusal, monitors, another secret, and its GUI under Xvfb |
| `test_chain_cost.py` | the board's chain (loudness, firConv, the crossover) at quantum 1024: what each FIR plugin costs speakerd's audio thread, by speakerd's own timing |
| `test_plugin_identity.py` | each plugin bundle's `buildinfo.ttl` (version and source id, as `plugin-versions.sh list` says), and speakerd's line for each plugin node, live and rendered; an older build and a broken `buildinfo.ttl` |

## Before and after the speakerd that enforces the limits

`test_speakerd_device_limits`, `test_speakerd_import`, `test_speakerd_installed` and the real
speakerd's part of `test_speakerd_crash_restart` need the speakerd of the board readiness work
(the limits, failing closed, `--version`, the import and its example session). Against an
older speakerd they fail, each saying what it lacks: the installer refuses a speakerd older
than `SPEAKERD_MIN_VERSION` (firmware/speakerd-setup.sh), which would ignore the cap.

## Before and after the build identity

`test_chain_cost` and `test_plugin_identity` prove phase A of the plugins (the block FIR that
firConv and the crossover share, the plugins' version and source id in `buildinfo.ttl`,
`plugin-versions.sh`) and of speakerd (its `plugin node` lines). Against the plugins and speakerd
from before it they fail, each saying what it lacks: firConv and the crossover over their limits
(15.1 % and 3.8 % of the cycle), no `plugin-versions.sh`, no `buildinfo.ttl`, no `plugin node`
line.

## What the scenarios prove

Each module's docstring states its pass criteria exactly; this is the summary. The speakerd ones
run `speakerd` (`dsp/juce_apps/speakerd`) on a virtual speaker with an empty `HOME` and the
bundles named on its command line, play into its `speakerd` sink and record the amplifier.
The reference is speakerd's own offline render (`--render`) of the same document pointed at
the complete build of the plugin, which is what the plugin outside speakerd outputs.

| Module | Pass when |
|---|---|
| `test_speakerd_same_sound` | loudness's headless build, from a hand-written `graph.json` with non-default values in Auto mode, gives every tone from 40 Hz to 6 kHz, at two levels, the gain of the complete build within 0.05 dB on both amplifier channels, with a residual under -80 dBFS; and the gain is not 0 dB, and depends on the level |
| `test_speakerd_state_layers` | a half-written or plugin-less `graph.json` plays `graph.lkg.json`, and with both broken speakerd plays `factory.json`, each said in the log and heard: the 100 Hz gain matches that layer's render within 0.05 dB and is at least 1 dB from every other layer's |
| `test_passthrough_safety` | with every plugin removed or no layer at all, for role left, right and mono: every sample of the tweeter capture is exactly 0, the woofer is the own channel at -20 dB within 0.05 dB and never above -20 dBFS; with the input above full scale, the `speakerd` sink's monitor, recorded as floats, peaks above 0 dBFS (so the cap holds the woofer, not PipeWire), and the woofer still does not exceed -20 dBFS; and speakerd's render of that input with no layer is the own channel at -20 dB clamped to -20 dBFS sample for sample, peaking at exactly -20 dBFS, with a tweeter of zeros. A capture with a 50 Hz period of the woofer more than 1 dB quieter than the others (an xrun) is played again, up to three times; every attempt is first checked for what an xrun cannot do (a tweeter sample not 0, the woofer above the cap, a period more than 1 dB louder), which fails at once |
| `test_speakerd_start_order` | started before its amplifier exists, with the amplifier going away and coming back, before PipeWire runs, and in a network namespace with no network, speakerd links, says it plays, and plays the complete build's gain within 0.05 dB, holding no IP socket |
| `test_speakerd_restart` | speakerd stopped by SIGTERM while a stream plays exits with status 0 and "stopping" last; the amplifier is then silent for 1 s (woofer below -100 dBFS, tweeter all zeros) and the stream stays linked to the `speakerd` sink alone; a new speakerd says it uses the sink that exists and gives the render's gain within 0.05 dB, under the same stream |
| `test_speakerd_second_instance` | a second speakerd on the same PipeWire says that another one runs (its `speakerd-dsp` exists), then `stopping`, and exits with status 3, and the first still gives the render's gain within 0.05 dB (not 6 dB more). It needs the refusal in speakerd, which came after step 2's first speakerd: against one without it, it fails, and says so |
| `test_speakerd_quantum_change` | with a stream playing, the quantum forced to 256, 2048 and back to 1024: speakerd says each change (re-primed, or run as several blocks), the gain comes back to the render's within 0.05 dB on both channels after each, and the amplifier, recorded as floats throughout, holds no NaN, infinity or sample above full scale. The gap at a change is printed, not asserted: PipeWire 0.3.65 drops out or jumps at a quantum change by itself |
| `test_speakerd_device_limits` | with limits -20 dB and the tweeter muted: the tweeter capture is exact zeros and the woofer's gain is the unlimited capture's minus 20 dB within 0.05 dB, live and in the render; a +6 dBFS input and a +20 dB boost document never put a woofer sample above -20 dBFS, live or rendered; passthrough peaks at no more than -40 dBFS and plays; a device.json without limits says it plays the fail-safe limits, one with a misspelt `limts`, one not JSON and a missing one say `device.json: FAILED`, and each plays the boost document with the tweeter at exact zeros and the woofer at no more than -20 dBFS |
| `test_speakerd_import` | speakerd's example session imported (complete build, and with `--prefer-headless`) renders sample for sample like the same settings given as parameters; an empty file, text, XML that is not a FILTERGRAPH, a connection to output channel 2, and the input wired straight to the tweeter each exit 2 with nothing on stdout |
| `test_firmware_speakerd_install` | the bundle step and `speakerd-setup.sh install --home --no-systemd` lay out exactly the documented tree (versions, `current`, an empty HOME, `device.json` with `limits` -20 dB and the tweeter muted, the unit and the drop-in byte for byte, ampd's layers untouched); a reinstall adds nothing and caps lifted limits again, keeping role, ports and other keys; rollback swaps `current` and `previous` both ways, capping lifted limits again, and `--keep` prunes the oldest; VERSION records speakerd's whole `--version` line and the installed scripts are executable and run when called directly; one of `--amplifier`, `--woofer-port`, `--tweeter-port` changes only itself; install refuses without `--role` (a running plugin host stays running), a speakerd that cannot say its version, is older than `SPEAKERD_MIN_VERSION` (rollback too) or cannot be executed (noexec), a device.json with a key speakerd does not know or a key twice, while the stand-in plugin host runs (and stops it with `--stop-plugin-host`, only once every other check passed, disabling its user unit, which uninstall re-enables), while a desktop autostart or a profile line would start it again (naming each), and a factory graph, new or kept, in which anything but a crossover's high band reaches the tweeter; `limits` turns the tweeter on only when no layer (factory.json, ampd's graph.json) breaks that rule, and status marks one that does; `--allow-tweeter-without-crossover` is refused without `--home` and in the remote mode; a same-URI plugin in a system LV2 folder is named; smoke reports a `device.json: FAILED` line, either fail-safe suffix, `NO INPUT SINK CONFIGURED` and no limits line as failures, and always prints its summary; smoke reports speakerd's timing (per node, the graph, the cycle) from its status before and after the window or, its local link busy, its log, fails when the graph's longest cycle took more than half its cycle or a cycle was late in the window, and warns on position jumps in it, late cycles before it, timing not measured and a clock read above 150 ns (system calls), reading a passthrough minute line (no node) too; `--import-filtergraph` installs speakerd's conversion, given the device.json to be (`--device`); the amplifier is found in a pw-dump (one ALSA sink, the two known names, refusals naming the sinks; the virtual amplifier's ports); `--route speakerd` is refused without the configured sink, with a snapclient unit that does not read client-rt.conf and with `--no-client-rt`, keeps the routing there was, and uninstall puts it back exactly, and with a running PipeWire stops and disables snapclient when that routing's target is not there; `setup.sh` checks every component before it changes any (also refusing `--route plugin-host` with speakerd) and installs in boot order; under stand-in systemd, install fails unless speakerd runs and says exactly its limits after its start, `limits` and `rollback` pass on a speakerd that was running (whose `speakerd ended` line precedes the new start) and fail on a crash after it, and setup.sh stops on a failed PipeWire restart and, for the whole architecture and `--only pipewire` alike, refuses to restart PipeWire under the plugin host or stops it first with `--stop-plugin-host`; the remote mode, through a stand-in ssh that runs the board's side here as `nobody`, copies one bundle to `~/.cache`, runs check (with the artifacts) and the board-side dry run, delivers every argument as given (a path with spaces, a semicolon that runs nothing), cleans up, and refuses root, and applies the deploy gate itself (a scratch checkout: no git, no status, a red status, a dirty tree and a lab lock each refuse before ssh is called); the board script (`--dry-run`) refuses a `journal -n` that is not a number and a deploy option that is not an agent's, quotes journal's argument, and reads status from the board's installed script |
| `test_speakerd_crash_restart` | the installed unit (no network unit, `Restart=on-failure`, 1 s, never after exit 2, no start limit, an empty HOME), run by `harness.UserUnit`: the real speakerd killed by SIGSEGV is restarted on `last-known-good`, after SIGTERM on `current`; with the stand-in, six crashes give current, last-known-good twice, factory three times (three within 60 s), then passthrough (three on factory), exit 3 keeps the layer, SIGTERM and exit 2 are not restarted, SIGPIPE and a core dump are crashes; crashes older than 60 s do not count towards factory; a start with no exit counts as a crash; a start after five failures waits and says so; no speakerd starts while an AudioPluginHost runs |
| `test_speakerd_installed` | two versions installed, each speaker's PipeWire started with the installed drop-ins (the right one's amplifier without a priority): the `speakerd` sink exists before speakerd at `priority.driver` 0 and is the default sink; speakerd, started by the unit, adopts it and plays factory; `pw-profiler` shows the amplifier as the only driver with the sink, speakerd-dsp and the stream as followers; speakerd says `device: limits: output at -20 dB, tweeter muted`, the tweeter is all zeros and the woofer is the complete build's render minus 20 dB within 0.05 dB; the current version's bundle loads although a broken copy with its URI lies in the home's `~/.lv2` (with HOME there speakerd stops with status 2), and after a rollback the previous version's does; speakerd's HOME stays empty; `speakerd-setup.sh smoke` passes every check, the live sink's drop-in properties, the default sink and speakerd's timing (the graph's longest cycle within half its cycle, no late cycle while watched) included, and fails when WirePlumber is told to make the amplifier the default sink; `--route speakerd` routes; and with a PipeWire that does not load the drop-in, the route is refused both with no `speakerd` node and with the sink speakerd made itself, smoke fails on that sink, and `sink` and `install` refuse. Against a speakerd older than `SPEAKERD_MIN_VERSION` the install refuses it, and the module fails saying so |
| `test_ampd_protocol` | against stand-in speakerds (which answer applies in order and send speakerd's errors): parameters with the hub's seq to both speakerds and every controller, errors for what cannot be addressed, the agent's seq order; proposals rejected for each reason, applied on both, rolled back when the right fails or is silent, the late answer; an offline agent caught up, a newer one adopted, a time that is not one ignored; gestures and their 5 s end; speakerd busy, restarted, fallen back, on an outdated current layer; the monitor union and per-client rates; graph.json at most once a second, at once for a revision, graph.lkg.json after --lkg-after; the pair's secret on every hello, OSC only from authenticated addresses, an agent displaced only with the secret; the limits (64 nodes, 512 connections, 32 subscriptions, exact stream names, a 400-digit number), speakerd's subscribe refusal relayed; a value set while a revision applies kept by it, in both graph.json, every controller and a restarted speakerd; the tweeter rule refused on an agent (rolled back on the left) and on the hub; unknown keys kept; the hub's seq after a restart; a line before the peer proved the secret, and on the monitor link, is at most 64 KiB (a 100 KiB hello closes the connection on the control and monitor links and at an agent, a 60 KiB one is welcomed); a legal 24 MiB document reaches a controller that does not read for a while, with the values after it (the 1 MiB limit counts what queues besides documents); the known answer of `tests/vpair/auth_vector.json` (client and server proof, K, OSC token) computed from protocol.md's definitions and by ampd's functions |
| `test_two_controllers` | against stand-in speakerds: two controllers drag (OSC at 50 a second, the last value over the control link) different and the same parameters, interleaved, and end with the same values as the hub's state, the right's graph.json and what each stand-in speakerd was last given; one connecting mid-drag finds the gesture in its state and gets its end |
| `test_firmware_ampd_install` | ampd comes with speakerd's version (bin/ampd and bin/tweeter_rule.py byte for byte); `ampd-setup.sh install` refuses before speakerd, without the pair's secret or with a malformed one (not shown; the file read as the controller reads it: exactly one trailing `\n` or `\r\n` dropped, nothing else), an agent without a hub address, `--hub-address` on the hub and a bad name, writing nothing; writes exactly the documented ampd.json (with the secret, mode 600; status shows it as `(set)`) and the unit byte for byte; a reinstall keeps the secret; the unit is after speakerd, with no network unit, restarts on failure in 1 s, not after exit 2, no start limit; under stand-in systemd install fails unless ampd says it listens; setup.sh refuses an agent without a hub address before any change and installs ampd after speakerd, never printing the secret; from the installed unit, smoke passes on hub and agent and fails, saying why, with the hub or ampd stopped; speakerd's status shows ampd's, rollback restarts ampd, uninstall removes its unit and keeps ampd.json |
| `test_knob_latency` | changes sent over OSC to the hub change both captures within 60 ms of the send (never before it); at quantum 128 also less than 5 ms apart; at the boards' 1024 the gap is printed (the two graph clocks can be a quantum apart). A change is set aside only on evidence that does not come from the change itself: the recorder's lost-sample check, "not steady before the send", or an xrun or late cycle recorded for speakerd-dsp or the recorder in that window (pw-profiler, which this test uses; speakerd's own late-cycle and position-jump counts are also available, in its `status` and its `speakerd/timing` stream and log, and are not used here). "Not at its new level 3 ms after it began", "a dropout after the change" and "not a clean change" with no recorded xrun are failures, not set-asides. Up to 30 changes are sent, at least 5 must be measured, each measured change within 60 ms, and less than 5 ms apart at quantum 128 (approved by the person) |
| `test_graph_edit` | a revision adding a node and a connection changes both captures by more than 6 dB, with no run of 1 ms blocks 40 dB down longer than 30 ms and nothing above 4 kHz louder than -40 dB under the tone; a revision the right does not answer (its ampd stopped) is rejected with "right: no answer within 10 s", heard on the left and rolled back there within 0.5 dB, and the right comes back to the current revision when it answers late; one a stand-in right fails is rejected with its reason and rolled back on the real left within 0.5 dB |
| `test_hub_down` | with the whole left speaker stopped, the right's level stays within 0.05 dB, no 10 ms block drops 1 dB or is zeros, its ampd says it lost the hub and runs on, its speakerd plays on, and it stays on revision 2; the L+R mix is skipped (not in device.json yet) |
| `test_meter_values` | with the Mine pin, a stepped stereo 1 kHz tone (right 12 dB under left): each speaker's `output/level` woofer peak is its own side's level and its RMS 3.01 dB under, within 1 dB; with the right speaker mono, (FL + FR) / 2, 4.08 dB under the left; the muted tweeter reads under -100 dBFS; `node/16/mon_levelDb` is that side's level as loudness's detector measures it (JUCE's BallisticsFilter, computed in the test: about 1.3 dB over the RMS of a steady sine), not the raw input level, within 1 dB |
| `test_ampd_restart` | each ampd (hub, then agent) killed and started again 1 s later: the subscriber has values again within 2 s of the kill, the agent is back on the hub, and no 10 ms block of either capture moves 0.5 dB or is zeros |
| `test_tweeter_rule` | on the pair (loudness and the crossover): with both tweeters on, a revision with the stream straight to the tweeter, one with the crossover's low band on it and one with the crossover bypassed are each rejected with "left: the tweeter is on here (device.json) and ..." and both graph.json stay on revision 1; a crossover's high band is applied on both; with the right's tweeter on only, the straight one is rejected with "right: ..." and the left rolled back; with both muted, all four are applied |
| `test_speakerd_tweeter_rule` | speakerd run as on a board (allow_tweeter=False) with the tweeter on: a graph.json sending loudness to both channels fails with "the stream reaches the tweeter ... (output 16:0;" and graph.lkg.json plays, its render's level within 0.05 dB on both channels; an apply of it on the local link is answered `failed` with that reason and the capture goes on (level within 0.05 dB, no 10 ms block 0.5 dB off); and 15 documents (straight from FL and from Mine, a low band, a bypassed crossover, a control build, plugins before and after the bands, both bands or the high band to both channels, crossovers in series, several paths, an unreached plugin) get the same verdict and the same named outputs from `firmware/speakerd/tweeter_rule.py` and speakerd's render |
| `test_drag_during_revision` | while controller B's revision 2 applies (the right's ampd held 1.5 s), controller A drags node 16's gain and ends at +6 dB: the graph sent, both graph.json, A, B and a later controller have +6 dB, and after the left's speakerd restarts its 200 Hz gain is its graph.json's render within 0.05 dB, 3 dB or more from B's document's |
| `test_controller_end_to_end` | the real controller (`tools/build.sh controller`, its script test mode, `--trace`) against the real pair (the Mine pin through loudness, a stereo 200 Hz tone, left -20 and right -32 dBFS, the tweeter muted): (a) synced and proven on revision 1 with the hub's document on its canvas, both speakers online on layer current, both monitor links connected; no `error` reaches it, ampd ignores, drops or refuses nothing, one control connection, no OSC echo dropped; (b) nine `set`s (+12 / 0 dB, 1.2 s apart) echoed, each heard on both captures within 60 ms of its line (knob_latency's onset() and set-asides, at least 5 measured), both graph.json at the last value within 2 s; a 1 s `drag` to 3.5 dB heard on both through at least 10 intermediate 20 ms blocks, then at the render's 200 Hz level within 0.1 dB, its value echoed, in both graph.json, the hub's state and the controller; (c) `add loudness` applied (both graph.json have the node, its headless URI, 35 parameters), a `set` of its mode echoed, `disconnect 16:0 2:0` applied and heard (no woofer sample above -100 dBFS), two `connect-pins` putting the new node in the woofer's path applied and heard (the render's level within 0.1 dB), revision 5 in both graph.json, its state the hub's; with the right's device.json saying the tweeter is on, `connect-pins 1:0 2:1` rejected with "right: the tweeter is on here (device.json)", undone in it, rolled back on the left, both graph.json on revision 5, its state the hub's; (d) `open 16`: 8 `mon_levelDb` values from the left, then (`monitor-speaker 16 right`) from the right, each side's level as loudness's detector reads it (BallisticsFilter, computed here for 200 Hz) within 1 dB, the subscription moved; `output/level` woofer peak each side's level and RMS 3.01 dB under within 1 dB, 12 dB apart within 1 dB, tweeters under -100 dBFS; (e) another secret: not proven, not synced, the refusal in `refused`, revision 0, no plugin node, `refused <address>: its hello does not prove the pair's secret` in the hub's log, neither secret printed; (f) `--gui` under Xvfb and openbox (`harness.VirtualDisplay`), the tone stepping -30 / -12 dBFS: synced, both monitor links feeding levels above -60 dBFS, the window's `mon_levelDb` spanning more than 6 dB, `build/e2e-controller/controller-juce.png` and `controller-screen.png` written, neither one flat colour |
| `test_chain_cost` | speakerd on the left speaker plays the board's chain from graph.json (the Mine pin -> `loudness_headless` 16 -> `firConv` 17 -> `crossover` 18, its low band to the woofer and its high band to the tweeter; the tweeter on, without --allow-tweeter-without-crossover) at 48 kHz and quantum 1024, stereo noise at -26 dBFS RMS playing; a client of its local link (`harness.SpeakerdLink`) subscribes to `speakerd/timing` and `output/level` at 2 Hz and, after 5 s, adds up a 20 s window, each value weighted by its cycles: firConv's mean share of the cycle is under 5 % and the crossover's under 1.5 % (juce::dsp::FIR::Filter: 15.1 % and 3.8 % here, x86_64; the block FIR about 1.0 % and 0.27 %); the window holds at least 80 % of 937.5 cycles, each node measured in 80 % of them, no quantum change, the woofer's median RMS above -60 dBFS. Each node's mean and max, the graph's, the cycle's, the cycles, late cycles, position jumps and dropped records are printed, and in the failure; late cycles are not asserted (they measure a runner's scheduler, not the chain) |
| `test_plugin_identity` | `dsp/juce_plugins/tools/plugin-versions.sh list` exits 0 with one `<plugin> <version> <source-id>` line per plugin folder that calls `speaker_add_plugin()` and nothing else, each version its CMakeLists.txt's `project(... VERSION)`; `loudness_headless`, `loudness`, `firConv` and `crossover` each have `buildinfo.ttl` exactly as the module's docstring shows it (its `bpi:version` and `bpi:sourceId`, a final newline or not) with the listed version and source id, and a manifest that names it only in its last line, `<URI> rdfs:seeAlso <buildinfo.ttl> .`, or not at all; live on both speakers, speakerd says `plugin node <uid>: <URI>, version <version>, source <source-id>, bundle <dir>` exactly once for each of 16, 17 and 18 at start (the bundle the one given, a trailing `/` allowed) and once each again at an apply of revision 2 on the left's local link, and its render once each; a copy of firConv's bundle without buildinfo.ttl or the manifest line naming it, and one with buildinfo.ttl cut off inside its version, each given alone, render Mine -> firConv -> woofer from layer current, sample for sample as the original bundle (whose woofer peaks above -60 dBFS), and say `plugin node 17: <URI>, no build identity (an older build), bundle <the copy>` and `... (buildinfo.ttl unreadable), bundle <the copy>` |
| `test_ampd_persistence` | during a 2 s drag graph.json is replaced at most 3 times, and holds the last value on both speakers 1.2 s later; speakerd restarted with ampd stopped boots on graph.json and gives its render's gain within 0.05 dB, 3 dB or more from revision 1's original; graph.lkg.json follows after --lkg-after (4 s), and a speakerd restart starts that time again |

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
- `SpeakerdLink(speaker)` is a client of a real speakerd's local link where no ampd runs (speakerd
  takes one client at a time): `subscribe(stream, rate_hz)`, `values(stream, since=, until=)` (the
  values that arrived in a window, by `time.monotonic()`), `apply(document, revision)` (its answer,
  `applied` or `failed`) and `status()`.

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
  installer tests use it, so that they test the installer whatever speakerd is built. It is
  compiled into a `vpair-stub-*` folder, removed when the test process exits, as every test
  removes its own `vpair-*` folders.
- `state_dir()` writes `device.json` with `"limits": {"gain_db": 0, "tweeter": "on"}`: no limit,
  said explicitly, because a device.json without limits plays capped.

## ampd in a test

```python
from harness import (ADDRESS, Controller, MonitorClient, SpeakerPair, StandInPair, TimedRecorder,
                     built, graph_document, lv2_uri, revision_document)

document = revision_document(graph_document(lv2_uri("loudness_headless"), {"mode": "Force 1"}), 1)
with SpeakerPair(tmp, {"left": document, "right": document}, [built("loudness", "headless")]) as pair, \
        Controller("ctl-test") as ctl, MonitorClient(ADDRESS["right"]) as meter:
    ctl.osc_param(16, "s0_e0_gain", 6.0)          # a moving knob; ctl.param() over the control link
    answer = ctl.propose(new_document)             # {"op": "applied"} or {"op": "rejected", "reason": ...}
    meter.subscribe("output/level", 30)
    t, value = meter.wait_value("output/level")
```

- `SpeakerPair(tmp, documents, bundles)` brings the pair up as step 3 runs it: on each speaker its
  PipeWire, speakerd (from `documents[name]` as graph.json) and ampd, the left the hub on 127.0.1.1,
  the right an agent on 127.0.1.2 (the speakers share one network namespace; any 127.x address is
  the loopback), and waits until each speakerd plays and the hub has caught the agent up.
  `pair.speakerd[name]`, `pair.ampd[name]`, `pair.state[name]`, `pair.graph(name)` (its graph.json),
  `pair.start_speakerd(name)` and `pair.start_ampd(name)` after a stop or a kill.
- `StandInPair(tmp, documents)` is the same with stand-in speakerds and no PipeWire: the protocol
  alone. `pair.sd[name].set_control({"fail": {"5": "why"}, "delay": 11.0})` makes it fail or delay an
  apply; `pair.sd[name].events(op, "in")` is what ampd sent it.
- The step 3 scenarios run with the tweeter muted in device.json (`harness.TWEETER_MUTED`, no gain
  limit): their documents send a plugin to both amplifier channels, which ampd refuses once the
  tweeter is on (the tweeter rule); `test_tweeter_rule` turns it on. `limits={"right": NO_LIMITS}`
  gives one speaker its own.
- Every ampd of a test has the pair's secret `harness.TEST_SECRET` in its ampd.json, and
  `Controller` and `MonitorClient` do docs/protocol.md's handshake (their hello with a nonce and
  the client's proof, the server's `welcome` checked), and the controller carries its OSC token
  (`ctl.token`) and ignores OSC without it (`secret=` another, or None, to see a refusal;
  `client_hello()` and `check_welcome()` for a hand-made client).
- `Ampd(env, state, name, args=...)` runs `firmware/ampd/ampd.py` with the speaker's environment
  (its own `XDG_RUNTIME_DIR`, so the default speakerd socket is that speaker's); it writes ampd.json
  into the state directory, waits for `listening on`, and at the end checks `stopping` was its last
  line. `wait_for()`, `mark()`, `stop()`, `kill()` (SIGKILL), `signal()`, `status()` (its status.json)
  as for `Speakerd`.
- `Controller(name)` is a controller: the control link (hello, state) and an OSC socket for the knobs
  and the hub's echoes; every message is kept with its arrival time and `via`; `values()` is what it
  knows of each parameter (newer seq wins; for one seq, the control link's exact copy). `MonitorClient`
  subscribes on one speaker's monitor link, and with `reconnect=True` connects and subscribes again
  as an open plugin window does.
- `TimedRecorder(speaker, latency=quantum)` records a node's monitor as floats on a pipe and knows
  when each sample was in the graph (`stop()` gives the samples and `t0`): what `knob_latency` measures
  latency with.
- `speaker_conf_with(path, default_clock_quantum=128)` is speaker.conf with a property changed, for
  `Pair({"left": {"speaker_conf": path}, ...})`.
- `VPAIR_PREBUILT=<build tree>` makes `built()` use a build tree laid out like `build/` (another
  branch's speakerd and plugins, say) and build nothing.

## The controller in a test

```python
from harness import ControllerScript, ScriptedController, VirtualDisplay

script = ControllerScript()
synced = script.add("wait-synced 15")
knob = script.add("set 16 s0_e0_gain 6")
script.add("wait-echo 16 s0_e0_gain 6")
with ScriptedController(script, name="ctl-test", args=["--trace"]) as ctl:
    arrived, line = ctl.step(knob)          # the step's JSON line and when it arrived
    status, result = ctl.finish()           # its exit status and {"result": "pass", ...}
```

- `ControllerScript.add(line)` returns the step number the controller gives that line, so a test
  finds each step's JSON line by it (dsp/juce_apps/controller/README.md, "Test mode").
- `ScriptedController` runs `built("controller")` (tools/build.sh controller) with its own empty
  HOME (its settings file, never the user's), the pair's secret in a file of mode 600 (`secret=`
  another, or None for no file), `--hub` the left speaker's address, `--name` and `--hub-name left`.
  `step(n)` waits for step n's line and fails, with everything the controller printed, when a step
  failed or it exited first; `stderr_path` is its stderr (`--trace` writes every message there).
  Leaving the block kills it if it still runs.
- `VirtualDisplay()` is an Xvfb display (the first free number, `-displayfd`) with openbox, for
  `--gui`: pass `env=display.env` to the controller; `display.screenshot(path)` captures the X
  screen with ImageMagick's `import`.

## Before and after step 3's speakerd

`test_knob_latency`, `test_graph_edit`, `test_hub_down`, `test_meter_values`, `test_ampd_restart`,
`test_ampd_persistence`, `test_tweeter_rule`, `test_drag_during_revision` and `test_controller_end_to_end` drive step 3's speakerd (0.3.0: the local socket, the Mine pin, the monitor
thread), and `test_meter_values` also loudness's `mon_` monitors. Against an older speakerd they fail
at once, saying so (`harness.require_speakerd_local_link`). `test_ampd_protocol`,
`test_two_controllers` and `test_firmware_ampd_install` need neither: they run ampd against stand-in
speakerds.

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

A Snapcast source feeding both speakers, `tc netem` between them for packet loss, and the rest of the acceptance scenarios of
`docs/autonomous-development.md` (graph_rejected and catalogue with step 4's catalogue, power_cut,
monitoring under loss, frames and records).
