# controller

The controller app of the remote plugin host: step 3 of
[`docs/remote-plugin-host.md`](../../../docs/remote-plugin-host.md). It is JUCE's
AudioPluginHost (`extras/AudioPluginHost` of the pinned JUCE) with the audio taken out: the same
canvas, pins, connection dragging and plugin windows, editing the speakers' graph and turning
their knobs through the hub. It speaks the protocol of [`docs/protocol.md`](../../../docs/protocol.md).

It processes no audio and holds no DSP:

- **No audio device.** No device type is compiled in (`JUCE_ALSA=0`, `JUCE_JACK=0`, and
  `juce_audio_devices` is not linked), no `AudioDeviceManager` exists, and the graph is never
  prepared or run: `AudioProcessorGraph` is only the canvas's model of nodes and connections.
- **Only control builds.** The plugin list is the control builds compiled in (below); nothing is
  scanned or loaded from disk (`JUCE_PLUGINHOST_LV2=0`, `JUCE_PLUGINHOST_VST3=0`). A control
  build's `processBlock()` is empty and its DSP sources are not compiled.
- **The hub's graph, never its own.** The canvas is built from the hub's `state`; every edit
  and every parameter change leaves through the bridge. Before the hub's state arrives, and
  while the hub is away, an edit is undone at once: nothing the controller had is ever pushed.

## Command line

```
controller [--hub HOST] [--secret-file FILE] [--name NAME] [--speaker NAME=HOST]... [options]
controller --hub HOST --secret-file FILE --script FILE [--gui] [--test-probe] [--trace]
controller --self-test
```

| Option | |
|---|---|
| `--hub HOST` | the hub: the left speaker's `ampd`. Also *Hub > Connect to a Hub...*, which saves it |
| `--secret-file FILE` | the file holding the pair's secret (below). Also *Hub > Connect to a Hub...*, which saves the path |
| `--name NAME` | this controller's `origin` (default `ctl-<computer name>`) |
| `--speaker NAME=HOST` | where speaker NAME's monitor link listens. Repeatable. Without it, the hub's own speaker (the hub's `hello` name) is at the hub's address, and a speaker the hub lists with an `address` key is there |
| `--osc-port PORT` | the UDP port the hub sends OSC echoes to (the hello's `osc_port`); default 0: any free port |
| `--control-port`, `--knobs-port`, `--monitor-port` | default 7450, 7451, 7452 (docs/protocol.md) |
| `--monitor-rate HZ` | the `rate_hz` of subscriptions (default 30, at most 60) |
| `--script FILE` | the test mode (below) |
| `--gui` | with `--script`: open the windows too |
| `--test-probe` | add the test probe to the plugins (below) |
| `--send-queue-limit BYTES` | what a link may hold for a peer that stopped reading before it drops the connection and reconnects (default 4 MiB) |
| `--trace` | every line in and out, on stderr |
| `--self-test` | checks the HMAC-SHA256 against RFC 4231 (cases 1, 2, 3 and 6) and exits 0 or 1 |
| `--version`, `--help` | |

**Settings.** `~/.config/BPI Controller.settings` (JUCE's `PropertiesFile`): `hub`, `name`,
`speakers` (`left=HOST,right=HOST`), `secretFile` (a path, never the secret) and the window
position. The command line wins.

**The pair's secret** (docs/protocol.md, "Authentication") is the one in the speakers'
`ampd.json`: 16 to 256 printable characters without spaces. The controller reads it from a file,
the whole file without its final line break; keep that file mode 600 (`chmod 600`: the
controller warns when others may read it). The secret is never an argument (`--secret` is
refused), never logged, printed or shown: only the proofs made with it leave the controller, and
`--trace` hides those too. A secret file that is missing or malformed stops the controller at
start (exit 2) or, from the Connect dialog, says why. Without a secret file it connects without a
proof, which only a hub that has no secret yet accepts.

## The bridge

One object, `Source/Bridge/Bridge.cpp`, between the canvas and the speakers. It does what
docs/protocol.md asks of a controller:

- **Connection.** It connects to the hub's control link, reconnecting every second while it
  cannot. The hub speaks first: its `hello` brings a `nonce`, and the controller answers with
  its own `hello` (`role` controller, its `name`, `osc_port`, `protocol` 1, and `auth`: the
  lowercase hex HMAC-SHA256, keyed with the secret's UTF-8 bytes, of `<nonce>:controller:<name>`;
  HMAC per RFC 2104 over JUCE's SHA-256). It builds the canvas from the `state` that answers,
  every time it connects: the hub's document, its parameter values, its speakers and who is
  dragging what. A refusal (`{"op":"error","about":"hello"}`, a wrong or missing proof) is shown
  in the status bar and `state`'s `refused`, and the link asks again only after 5 s.
- **OSC from the authenticated address.** The hub takes OSC only from the address of an
  authenticated control connection, so the OSC socket is bound to that connection's own local
  address, rebound at every connection.
- **Parameters.** It listens to every parameter of every control build. While a gesture is
  active (an editor's slider is held), a change goes over OSC, `/bpi/param ,isfis`, at most 50
  a second per parameter (later values wait for the next 20 ms slot, the newest wins). At the
  end of the gesture the last value goes over the control link, then the gesture's `end`. A
  change with no gesture (a typed value, a preset) goes over the control link only, at the same
  rate at most. Gestures (`begin`, `end`) go over the control link. `seq` is 0, `origin` its
  name.
- **Echoes.** A `param` from the hub (control or OSC) is applied unless its `seq` is not newer
  than the last one applied for that parameter (UDP may reorder; a new `state` starts the
  count again), or it is the controller's own while its gesture on that parameter is active,
  or after that gesture until the control link's echo of its last value arrives (2 s at most),
  so a late echo of an earlier value never pulls the knob back. A `gesture` of another
  controller is recorded (and shown by the test mode's `state`).
- **Graph edits.** Adding or removing a node, connecting, disconnecting and bypassing are
  structural edits: the canvas changes at once, and the bridge proposes the next revision,
  `{"op":"graph","revision":N+1,"base":N,...}`. The document it sends is the accepted
  document with the edit applied, so whatever the canvas cannot show (a placeholder's
  parameters and state, keys of a newer protocol) travels on untouched, and every node carries
  its current `params` and its canvas position under `"ui": {"x", "y"}`. On `applied` the
  revision is kept; on `rejected` (or no answer in 15 s) the canvas goes back to the accepted
  document and the reason is shown (a message box and the status bar). One proposal at a time:
  an edit made while one is waiting is undone with a note. A `graph` message from the hub with
  a newer revision (another controller's edit) is applied to the canvas; nodes that stay keep
  their open windows.
- **Never waiting for a peer.** The control and monitor links do all their socket work on their
  own threads: `send()` only queues the line and wakes the link, so the message thread (the UI,
  the knobs) never blocks on a hub that is slow, stalled or stopped. The kernel's send buffer
  is bounded (64 KiB), and the queue too: a peer that has not read for more than
  `--send-queue-limit` bytes is dropped, and the link connects again, starting from the hub's
  `state`. Lines from an earlier connection are never sent on a new one.
- **Placeholders.** A node whose plugin has no control build in this controller is shown as
  a placeholder (orange frame) with the pins its connections use: it is never dropped from
  a proposal.
- **Monitoring.** It connects to every speaker's monitor link, answers the speaker's `hello`
  with its proof (the same `<nonce>:controller:<name>`) before any `subscribe`, and shows a
  refusal in the speakers panel. A plugin window that is open
  subscribes to its node's `mon_` streams (`node/<uid>/<mon_id>`, any parameter whose ID starts
  with `mon_`) on one speaker, chosen by the L / R buttons at the top of the window (the left
  speaker first); closing the last window of a node, or switching speaker, unsubscribes. The
  values are written into the control build's `mon_` parameters, which its getters read; the
  window's header also shows them as text. The speakers panel subscribes to every speaker's
  `output/level`.
- **Speakers panel.** Beside the canvas: each speaker of the hub's `state` with its role,
  online or offline, the state layer it plays, its monitor link, and its output level bar
  (woofer and tweeter: RMS as the bar, peak as the tick).

The canvas's pins are the speaker's: the stream in (node 1) has FL, FR and Mine (channels 0, 1,
2), the amplifier out (node 2) has woofer and tweeter (0, 1). Plugin nodes have the document's
uids; a new node takes the next uid above the highest, 16 at least. Moving a node is not an
edit: its position goes with the next revision.

## Test mode

`controller --hub HOST --script FILE` runs FILE's commands one after the other, prints one JSON
line per command on stdout, and exits 0 when all passed, 1 at the first that failed, 2 for a
bad command line. It needs no display unless `--gui` is given (then the windows open, and
`open` and `screenshot` use them). A command that waits polls until its condition holds or its
timeout (in seconds, the last argument where shown; default 5) passes.

| Command | |
|---|---|
| `wait-synced [s]` (or `connect`) | until the hub's state has arrived (default 10 s) |
| `state` | prints the controller's view (below) |
| `set UID ID VALUE` | sets a parameter as an editor would without a gesture: over the control link. VALUE is a number, `true`/`false`, or a choice's text in quotes |
| `drag UID ID FROM TO [N] [MS]` | a gesture: begin, N values from FROM to TO over MS milliseconds (default 20 over 500), end |
| `wait-echo UID ID VALUE [s]` | until the last echo from the hub of that parameter has VALUE |
| `wait-param UID ID VALUE [s]` | until the control build's parameter has VALUE (an echo of another controller) |
| `wait-monitor UID MON_ID VALUE [s]` | the same, for a monitor |
| `add PLUGIN [X Y]` | adds a node of a control build (by its folder name: `loudness`) at a canvas position (0 to 1); prints its `uid`. `$new` stands for it in later commands |
| `remove UID` | removes a node |
| `connect-pins A:CH B:CH`, `disconnect A:CH B:CH` | a connection, by node uid and channel |
| `bypass UID on/off` | |
| `expect-applied [s]` | the last edit's proposal was applied (default 20 s) |
| `expect-rejected [TEXT]` | it was rejected, with TEXT in the reason; the canvas is back |
| `expect-revision N [s]`, `expect-node UID [s]`, `expect-no-node UID [s]`, `expect-connection A:CH B:CH [s]`, `expect-no-connection ...` | |
| `open UID [X Y]`, `close UID` | a node's plugin window (with `--gui`, at screen X, Y); without a GUI, the same subscriptions as a window |
| `monitor-speaker UID SPEAKER` | the window's L / R choice |
| `subscribe SPEAKER STREAM`, `unsubscribe SPEAKER STREAM` | a subscription by hand |
| `output-levels on/off` | every speaker's `output/level`, as the speakers panel does |
| `wait-value SPEAKER STREAM [s]` | until a new `value` of that stream arrives; prints `v` and `t` |
| `expect-speaker NAME online/offline [s]` | |
| `main-window X Y W H` | places the main window (`--gui`) |
| `screenshot FILE` | every window, as JUCE paints it, into one PNG (`--gui`) |
| `sweep UID SECONDS` | sets every parameter of a node on every tick, without a gesture (a preset morph): as much traffic as the control link takes, 50 a second per parameter |
| `sleep MS`, `print TEXT` | |

Lines starting with `#` are comments. Every line carries `max_tick_ms`, the longest gap between two
ticks of the test mode's timer during that step: the message thread's pulse (10 ms when all is
well). Each command prints, for example:

```json
{"step":4,"cmd":"wait-echo","line":"wait-echo 16 s0_e0_gain 4.5","ok":true,"ms":3,"value":4.5,"seq":1001,"origin":"ctl-test","link":"control","echoes":2}
{"step":6,"cmd":"expect-rejected","line":"expect-rejected no plugin","ok":true,"ms":2,"outcome":"rejected 8: right: failed: no plugin ...","revision":7,"status":"..."}
{"step":9,"cmd":"wait-param","line":"wait-param 16 mode 2","ok":false,"ms":5001,"error":"timed out","value":1}
{"result":"fail","steps":9,"failed":1}
```

`state` prints `{"state": {...}}` with `connected`, `connections_made` (control link connections
so far), `queued_bytes` (waiting to be written to the hub), `refused` (the hub's reason, if it
refused this controller since the last state), `synced`, `hub`, `revision`, `seq`, `pending`
(the revision waiting for an answer, or null), `outcome` (`applied N` or `rejected N: reason`),
`status`, `nodes` (uid, kind: `input`, `output`, `control` or `placeholder`; for plugins name,
identifier, bypassed, `params` and `monitors` in the plugin's units, `monitor_speaker`),
`connections` (`[src, channel, dst, channel]`), `speakers` (name, role, online, layer, address,
monitor_connected, `level` as `[woofer peak, woofer rms, tweeter peak, tweeter rms]`,
level_count, subscriptions) and `gestures` (other controllers' active gestures).

A scenario on the virtual pair runs it against the real hub, for example:

```
wait-synced
set 16 s0_e0_gain 4.5
wait-echo 16 s0_e0_gain 4.5
add loudness
expect-applied
open $new
wait-value left output/level
state
```

**The test probe** (`--test-probe`) is a control build compiled into the controller only for
tests: LV2 identifier `urn:bpi-amp:controller:test-probe`, mono in and out, parameters `gain`
(dB), `mode` (Auto, Force 1, Force 2), `bypass`, and a monitor `mon_level` (dBFS, -120 to 0),
declared as docs/protocol.md says (last, not automatable, an output meter). It tests the monitor
path before a real plugin publishes a monitor. A hub must know its identifier to accept it.

## Tests

`tests/fake_hub.py` is a small hub for these tests (Python, standard library only): the control
link, the knobs link and both speakers' monitor links, on 127.0.3.1 (the hub, left) and
127.0.3.2 (right). It authenticates as docs/protocol.md says, with the tests' secret
(`fake_hub.SECRET`, or `--secret-file`): it speaks first with a nonce, checks the proof of each
hello with Python's `hmac`, refuses a wrong one, and takes OSC only from an authenticated
controller's address. It stamps `seq`, echoes, relays gestures, applies or rejects proposals
(stale base, an unknown plugin, or a reason a test sets), and produces monitor values at the
rate asked for. Run alone (`python3 fake_hub.py --probe --trace`) it serves a document until
interrupted.

```sh
dsp/juce_apps/controller/tests/run.sh                # builds the controller if needed, runs the tests
dsp/juce_apps/controller/tests/run.sh --screenshot   # and the GUI under Xvfb + openbox, with screenshots
```

`tests/test_controller.py` runs the controller's test mode against the fake hub and checks what
it printed and what the hub received: hello and state; a change without a gesture over the
control link only; a 1.2 s drag over OSC at no more than 50 a second with the last value and the
gesture's end over the control link (and late echoes of its own ignored); another controller's
echoes applied; proposals applied (add, connect, bypass, disconnect: revisions 8 to 11, the new
node with all 35 loudness parameters and the headless URI); a rejection and a stale base undone;
another controller's revision with a placeholder kept untouched in the next proposal; monitor
subscriptions moving between speakers and the output levels; no edit without a hub; a
reconnect that takes the new hub's state and pushes nothing; a hub that stops reading during a
3 s sweep (its receive buffer 4 KiB), with the message thread ticking on and the backed-up lines
going out in order once it reads again; and one that never reads again, dropped past a 64 KiB
queue and reconnected; a wrong secret refused, shown, and retried every 5 s (2 attempts in 7 s),
with neither secret anywhere in the output; and the HMAC against RFC 4231, a malformed secret file
and a secret on the command line refused. Each test prints its numbers.

The screenshot run opens the loudness control editor and the test probe beside the canvas and
writes `build/controller-check/controller-screen.png` (the X screen) and `controller-juce.png`
(JUCE's own rendering of the windows).

## Adding a plugin

A plugin whose `speaker_add_plugin()` lists `TARGETS complete control headless` is added by
its name in `CMakeLists.txt`:

```cmake
controller_add_control_plugins(controller
    loudness
    crossover)
```

That is all: its control target is the description of the build. `ControlPlugins.cmake` adds
the plugin's own `CMakeLists.txt` (nothing of it is built), reads its `<plugin>_control` target
(the shared-code target `juce_add_plugin()` makes) for its sources (`SOURCES` and `EDITOR`,
no `DSP`), its plugin definitions (`JucePlugin_Name`, `SPEAKER_CONTROL=1`, ...) and the JUCE
modules it uses, and compiles those sources into the controller as an object library with the
controller's own JUCE configuration. The plugin's `createPluginFilter()` is renamed
`bpiControl_create_<plugin>()` for its sources, and the generated `ControlPluginList.h` lists
each plugin with its factory and the LV2 URIs of its headless, complete and control builds
(`Source/Plugins/ControlPlugins.cpp` reads it). A document may name any of the three; a new node
names the headless build, which is what a speaker runs.

Why not link the control target's static library itself: it carries a second copy of every
JUCE module compiled as a plugin, its public definitions (`JUCE_STANDALONE_APPLICATION=
JucePlugin_Build_Standalone`, `JucePlugin_Name`, ...) would redefine the application's (tried:
17 "JUCE_STANDALONE_APPLICATION redefined" warnings, and the application's own JUCE then
compiled as a plugin), and every plugin's archive defines `createPluginFilter()`, so a second
plugin would not link. The same compiled-in build is what a tablet needs (iOS and Android load no
plugins), as section 7 of the design says.

An editor that needs `juce_audio_utils` or `juce_audio_devices` does not build here: they are
left out on purpose (no audio device in the controller).

Two JUCE details met on the way: JUCE 8.0.12's `JUCEConfig.cmake` fails when `find_package(JUCE)`
runs twice in one build (it compares its targets against a misspelt list), so
`ControlPlugins.cmake` makes the plugins' own `find_package(JUCE)` a no-op once JUCE is found;
and a control build's monitors only reach its editor through the parameters, so a plugin's
getters (`getLevelDb()`) must read its `mon_` parameter in the control build (docs/protocol.md,
"How a plugin publishes a monitor").

## Files

| | |
|---|---|
| `CMakeLists.txt`, `ControlPlugins.cmake` | the app, and the control builds compiled in |
| `Source/Main.cpp` | the application, command line, settings, test mode start (from `HostStartup.cpp`) |
| `Source/Bridge/Bridge.*` | the bridge |
| `Source/Bridge/JsonLineLink.*` | a TCP JSON-lines client that reconnects (control and monitor links) |
| `Source/Bridge/GraphDocument.*` | the graph document as JSON: structure, and an edit applied to it |
| `Source/Bridge/ScriptRunner.*` | the test mode |
| `Source/Plugins/ControlPlugins.*` | the plugins compiled in, the speaker's I/O nodes, placeholders, the test probe, parameter values in the document's units |
| `Source/Plugins/PluginGraph.*` | the canvas's graph, mirroring a document (from AudioPluginHost's `PluginGraph`) |
| `Source/UI/*` | the canvas, main window and plugin windows (from AudioPluginHost), the speakers panel |
| `tests/` | the fake hub, the tests, `run.sh` |

The files derived from AudioPluginHost say so at their top, with what changed. JUCE is under the
AGPLv3 or the JUCE licence, as for the plugins.
