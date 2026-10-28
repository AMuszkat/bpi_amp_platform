# The remote plugin host's protocol, version 1

*The contract of step 3 of [remote-plugin-host.md](remote-plugin-host.md): what the
controller, `ampd` (agent and hub) and `speakerd` say to each other. Every implementation
follows this file; a change to it is made here first, in the same commit as the code, and is
the orchestrator's to route.*

Four links, one message vocabulary:

| Link | Between | Transport | Carries |
|---|---|---|---|
| **local** | `ampd` ↔ `speakerd`, on one speaker | Unix stream socket, JSON lines | parameters, graph revisions, status, monitor subscriptions and values |
| **control** | controller ↔ hub, hub ↔ agent | TCP, JSON lines | hello and state, graph revisions and their outcome, the reliable copy of parameters, gestures |
| **knobs** | controller → hub, hub → agents and controllers | UDP, OSC | parameters and gestures while a knob moves |
| **monitor** | controller ↔ each speaker's `ampd` | TCP, JSON lines | monitor subscriptions and values, straight from the speaker that measured them |

The same JSON objects travel on the local and control links, so `speakerd` cannot tell a change
from its own agent from one relayed by the hub. Monitoring uses plain TCP, not the WebSocket
section 5 of the design first named: the controller is a JUCE application, JUCE has a TCP
socket and no WebSocket client, and nothing in this system is a browser.

## Framing and addresses

- **JSON lines.** One JSON object per line, UTF-8, `\n`-terminated, no newline inside an object.
  Every object has an `"op"`. A line longer than 32 MiB, or one that does not parse, closes the
  connection (the peer reconnects). Unknown `op`s and unknown keys are ignored, so a newer
  peer can add to a message without breaking an older one; a change of meaning needs a new
  `op`.
- **OSC.** OSC 1.0 messages, one per UDP datagram, no bundles.
- **Ports.** Every `ampd` listens on TCP **7450** (control), UDP **7451** (knobs) and TCP
  **7452** (monitor), on the address in its own configuration (below). The hub role only
  changes what it does with what arrives. On the virtual pair the two speakers share one
  network namespace, so the left listens on `127.0.1.1` and the right on `127.0.1.2` (any
  `127.x` address is the loopback).
- **The local socket** is `$XDG_RUNTIME_DIR/speakerd/control.sock`, mode 0600. `speakerd`
  listens (it starts first and outlives `ampd` restarts); `ampd` connects and reconnects every
  second while it cannot. One client at a time; a second is refused with
  `{"op":"error","reason":"busy"}`.

## Values

A **parameter** is addressed by a node uid of the graph document and the plugin's parameter
ID, as in the document's `params`. Its **value** is in the plugin's own units, as in the
document: dB, Hz, a choice's index, 0 or 1 for a switch (on the local and control links a
choice may also be given by its text and a switch as `true`/`false`, exactly as in a document;
on OSC only numbers). A **monitor** is a read-only parameter whose ID starts with `mon_`
(below): it is never set, never in a document and never sent as a parameter.

**seq** is the hub's sequence number: a 32-bit counter it increments for every parameter
change it accepts, and stamps on everything it sends out. Controllers send `seq` 0. An agent or
`speakerd` ignores a parameter whose `seq` is not newer than the last it applied for that
same parameter (UDP may reorder), except `seq` 0 on the local link from a speaker's own
`ampd`, which always applies.

**origin** names the sender of a change: a controller's name, `hub`, or `knob`/`ir`/`web` for
the hub's own inputs. Controllers ignore echoes of their own origin while their own gesture on
that parameter is active.

## Parameters

| Link | Form |
|---|---|
| local, control | `{"op":"param","node":16,"id":"s0_e0_gain","value":4.5,"seq":1234,"origin":"ctl-mac"}` |
| knobs (OSC) | `/bpi/param` `,isfis`: node, id, value, seq, origin |

- The controller sends a moving knob over OSC, at most 50 messages a second per parameter, and
  **the last value of every gesture over the control link** (UDP may lose it). With no gesture
  (a typed value, a preset), it sends over the control link only.
- The hub accepts a change it can address (the node exists in the current revision and has
  that parameter; a `mon_` ID never), stamps `seq`, updates its state, applies it to its own
  `speakerd` (local), sends it to every agent (OSC, and over control when it came over
  control) and echoes it to every controller (OSC, and over control when it came over
  control). A change it cannot address is answered, on the control link only, with
  `{"op":"error","about":"param","node":..,"id":..,"reason":".."}`.
- An agent applies what the hub sends to its `speakerd` (local) and keeps it in its state.
- `speakerd` looks the parameter up when the line arrives (on its socket thread), and hands
  the audio thread only the hosted parameter and the normalised value through its lock-free
  queue; the value takes effect at the next cycle. An unknown node or ID is answered with
  `{"op":"error","about":"param","node":..,"id":..,"reason":".."}`; nothing else is answered.

## Gestures

| Link | Form |
|---|---|
| local, control | `{"op":"gesture","node":16,"id":"s0_e0_gain","state":"begin","origin":"ctl-mac"}` (`state`: `begin` or `end`) |
| knobs (OSC) | `/bpi/gesture` `,isss`: node, id, state, origin |

The hub relays gestures to the controllers only (agents and `speakerd` have no use for them),
and keeps, per parameter, who is dragging it, so a controller that connects mid-drag sees it.
A gesture with no `end` for 5 s ends by itself.

## Graph revisions

A **revision** is a whole graph document (version 1, as `speakerd` reads it, with every
node's current `params`), a number, a time and an origin:

```json
{"op":"graph","revision":8,"base":7,"time":"2026-10-28T09:15:02Z","origin":"ctl-mac","document":{...}}
```

- **A controller proposes** revision `base + 1` over the control link. The hub refuses it
  unless `base` is its current revision (`stale`), the document parses, and every plugin it
  names is one the hub has seen loaded somewhere (the full catalogue with version hashes is
  step 4).
- **The hub applies it everywhere it can.** It sends
  `{"op":"apply","revision":8,"document":{...}}` to its own `speakerd` (local) and to every
  connected agent (control); each agent passes it on to its `speakerd`. `speakerd` answers
  `{"op":"applied","revision":8}` once the new graph plays, or
  `{"op":"failed","revision":8,"reason":".."}` with the old one still playing; an agent relays
  the answer to the hub.
- **All or nothing among the speakers that are up.** When every connected speaker applied it,
  the hub makes it current, persists it, answers the proposer
  `{"op":"applied","revision":8}` and sends the `graph` message to every controller. When any
  failed (or did not answer within 10 s), the hub sends the previous revision back to those
  that applied it, and answers `{"op":"rejected","revision":8,"reason":"<speaker>: <reason>"}`.
- **A speaker that is down** gets the current revision and the current parameters when its
  agent connects again (below); until then the hub marks it offline in the state it gives
  controllers.

## Hello, state and reconnection

On the control link the first line each side sends is a `hello`:

```json
{"op":"hello","role":"controller","name":"ctl-mac","osc_port":9000,"protocol":1}
{"op":"hello","role":"agent","name":"right","revision":7,"time":"2026-10-28T09:10:00Z","protocol":1}
{"op":"hello","role":"hub","name":"left","revision":8,"protocol":1}
```

- **A controller** then gets `{"op":"state","revision":8,"seq":1234,"document":{...},"speakers":[{"name":"left","role":"left","online":true,"layer":"current"},{"name":"right",...}],"gestures":[...]}`
  and builds its graph from it; it never pushes its own older copy (rule 3 of section 8). The hub
  sends OSC echoes to the controller's address and `osc_port`.
- **An agent** whose revision is older than the hub's gets `apply` of the hub's revision and
  then every parameter (control). One whose revision is newer (it changed while the hub was
  off, and revisions compare by `time`, then by origin name) is asked
  `{"op":"get"}`, answers `{"op":"graph",...}` with its revision, and the hub adopts it and
  applies it everywhere.
- **`speakerd`** greets `ampd` on the local link with
  `{"op":"hello","role":"speakerd","version":"0.3.0 (<commit>)","revision":8,"layer":"current","protocol":1}`
  (`revision` 0 for passthrough or a document with none), and answers
  `{"op":"status"}` with the same fields as `status`.

## Persistence and layers (ampd)

- `ampd` writes `graph.json` in `speakerd`'s state directory, atomically, whenever a revision
  is applied, and again at most once a second while parameters change: the current revision's
  document with the current parameter values folded into its nodes' `params`.
- It promotes `graph.json` to `graph.lkg.json` once that revision has played for 60 s with no
  `speakerd` restart in between. It never writes `factory.json` or `device.json`.
- `speakerd` never writes a file (unchanged).

## Monitoring (step 3: values)

A **stream** is `output/level` or `node/<uid>/<mon_id>`:

- `output/level`: what the amplifier gets, after the limits: `[woofer_peak, woofer_rms,
  tweeter_peak, tweeter_rms]` in dBFS, over the last period, built into `speakerd` for any graph.
- `node/<uid>/<mon_id>`: a plugin's monitor, the value of its `mon_` parameter.

On the monitor link, straight to the speaker whose values are wanted:

```json
{"op":"subscribe","stream":"output/level","rate_hz":30}
{"op":"unsubscribe","stream":"output/level"}
{"op":"value","stream":"output/level","t":1234.567,"v":[-12.1,-18.3,-120.0,-120.0]}
```

- `ampd` passes subscriptions on to `speakerd` (local, the same objects), as the union of every
  controller's, at the highest rate any asks for (at most 60 Hz). `speakerd`'s monitor thread
  (normal priority, never the audio thread) samples the streams subscribed to and sends `value`
  lines; nothing is produced for a stream nobody subscribed to.
- `t` is `speakerd`'s monotonic clock in seconds. `ampd` keeps the newest value per stream and
  controller, and sends it at that controller's rate: newest wins, nothing queues.
- A subscription ends when the controller unsubscribes or its connection closes.
- Frames, records and credits are step 4.

**How a plugin publishes a monitor.** A monitor is a parameter whose ID starts with `mon_`,
in every build of the plugin (complete, control, headless), declared after the plugin's other
parameters, not automatable, of category output meter, and left out of the plugin's saved
state. The headless and complete builds write it from the audio thread's atomics through
`setValue` (no host notification from the audio thread); JUCE's LV2 wrapper then reports the
change to the host after the cycle, which is how `speakerd` sees it. The control build's
proxy fills it from the stream, and the plugin's own getters (`getLevelDb()` and friends) read
it there, so the editor code does not change. `loudness` publishes `mon_levelDb`
(dBFS, -120 to 0) and `mon_activeSetting` (0 to 3).

## The Mine pin

The input node gains channel **2, Mine**: the speaker's own channel from its role in
`device.json` (left: FL, right: FR, mono: (FL + FR) / 2). A document that connects from `1:2`
plays the same on both speakers of a pair, each its own side; one that connects from `1:0` or
`1:1` stays deliberately asymmetric. A document that uses channel 2 needs `speakerd` 0.3.0 or
later; an older one fails the layer.

## ampd's configuration

`ampd.json`, next to `device.json` in the state directory, written by the installer; never
from the hub, never by `ampd`:

```json
{"version":1,"name":"left","hub":true,"listen":"0.0.0.0","hub_address":null}
{"version":1,"name":"right","hub":false,"listen":"0.0.0.0","hub_address":"bpi-amp.local"}
```

Discovery over mDNS replaces `hub_address` later; until then it is written at install.
