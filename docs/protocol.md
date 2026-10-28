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
state. The headless and complete builds write it from the audio thread's atomics with
`setValue` and, when the value moved (at most about 60 times a second, and once a second
anyway), tell the parameter's listeners from inside that same audio cycle. JUCE's LV2 wrapper
reports only parameters its listener was told about, and sends them to the host at the end of
the cycle, which is how `speakerd` sees them, with `getValue()` on its monitor thread. The only
lock this takes on the plugin's audio thread is the monitor parameter's own listener lock,
which nothing else takes; a thread of the plugin's own cannot do it safely, because the LV2
wrapper destroys its listener before the plugin without telling it. The control build's
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

## Clarifications (from the first implementations)

These settle what version 1 left open; every implementation follows them.

1. **Speaker addresses.** Each entry of `state.speakers` carries `"address"`: the host the
   speaker's monitor link is reached at (the hub fills it from its configuration and from
   the agent's connection). A controller may override it with its own settings.
2. **Speaker status.** The hub sends `state` again, whole, to every controller whenever a
   speaker comes online, goes offline or changes layer, and after it adopts a revision. A
   controller treats any `state` as a full refresh.
3. **Gestures in `state`.** `"gestures":[{"node":16,"id":"s0_e0_gain","origin":"ctl-mac"}]`:
   the parameters being dragged now and by whom.
4. **Outcome order.** The proposer gets `applied` (or `rejected`) first; then the hub sends
   the `graph` message to every controller, the proposer included.
5. **Unknown keys in documents** (the controller's canvas positions, `"ui":{"x":..,"y":..}`
   on a node) are kept by the hub and agents as they are and passed on; `speakerd` ignores
   them.
6. **seq after a hub restart.** The hub persists its seq with its state and continues from it.
   Should it ever restart from a lower seq, its `hello` and `state` carry it, and every
   receiver resets its per-parameter tracking when it gets a hub `hello` or a `state`. seq is
   compared wrap-safe (as a signed 32-bit difference), since OSC carries it as `i`.
7. **Own echoes.** A controller ignores echoes of its own origin while its gesture on that
   parameter is active and until the control-link echo of its last value arrives (at most
   2 s), so a late OSC echo cannot pull the knob back.
8. **Gestures** go over the control link (a lost UDP `end` would hold a parameter "dragged"
   for 5 s); OSC `/bpi/gesture` stays accepted.
9. **Duplicate echoes.** A change that came over the control link is echoed on both OSC and the
   control link with the same seq; receivers drop the second by seq.
10. **Choice text.** The hub relays a value as it got it. A value given as text goes out over
    the control link only (OSC cannot carry it); receivers convert it as in a document.
11. **Waiting for an outcome.** A controller gives up on a proposal after 15 s (the hub's 10 s
    plus a margin) and undoes it.
12. **The document's own revision.** A proposal's `document.revision` equals its `revision`.
13. **Rate.** A controller sends at most 50 changes a second per parameter on either link.
14. **Monitor values** are in the plugin's units (dBFS for `mon_levelDb`), never normalised.
15. **`speakerd` answers more than unknown parameters.** It also answers a `mon_` ID and an
    invalid value (a choice text the plugin lacks, `true` for a non-switch) with
    `{"op":"error","about":"param",...}`.
16. **The layer after an apply** is `current` (ampd writes every applied revision to
    `graph.json`).
17. **An apply's `revision`** is authoritative; its document may omit its own `revision`, and
    one that names a different revision fails.
18. **Applies are queued in order** (at most 8), never refused for being busy, so a hub's
    rollback right behind a failed revision is applied. A parameter that arrives while a
    revision is building goes to the playing graph and is carried over to the new one.
    `applied` is sent once the new graph has run one audio cycle (or 200 ms after the swap
    when no cycle runs).
19. **Monitor streams.** `v` is an array for `output/level` and a plain number for
    `node/<uid>/<mon_id>`. Each stream has its own rate (`rate_hz`, default 30, capped at 60,
    0 or less refused); a period with no audio cycle sends nothing, so at a quantum of 1024 at
    48 kHz a stream gives about 47 values a second at most. RMS is plain RMS (a full-scale sine
    reads -3.01 dBFS); -120 is the floor. A `subscribe` to a name that is no stream is answered
    `{"op":"error","about":"subscribe","stream":..,"reason":..}`; a well-formed `node/...`
    stream the graph lacks is kept and starts when a revision has it.
20. **`status`** is answered with the `hello`'s fields and `"op":"status"`.
21. **Files.** `speakerd` writes no state file; it creates and removes its socket.
22. **The agent's `hello`** also carries `layer`, `speakerd` (its version), `device_role` and
    `osc_port`; later changes of its speaker go to the hub as
    `{"op":"status","speakerd":..,"layer":..,"revision":..}` on the control link.
23. **`apply` carries `time` and `origin`** as well (and ampd writes them at the top of
    `graph.json`, which `speakerd` ignores), so an agent can compare revisions by time.
24. **The local link's seq.** `ampd` sends `speakerd` seq 0 and orders values itself, so a hub
    restart cannot stall a speaker.
25. **Which parameters exist.** The hub knows a node's parameters from the document's
    `params`, and refuses any other: a document lists every parameter a controller may change
    (controller-built documents do).
26. **Exact values win.** OSC carries a 32-bit float; when the same seq arrives over both links,
    the control link's copy is the one kept, so every `graph.json` holds the same value.
27. **Gesture timeout.** 5 s from the `begin` or the dragger's last value; the hub then sends
    every controller an `end` in the dragger's name.
28. **Late answers.** A speaker that applies a revision after the 10 s gets the current
    revision again. The hub's own `speakerd` being down during a proposal counts as a speaker
    that is down.
29. **The hub's own address** in `state.speakers`, when it listens on every address, is the
    one the controller connected to.

## Authentication (version 1, from the review of step 3)

Every pair has a **secret**: 16 to 256 printable characters without spaces, in each speaker's
`ampd.json` (`"secret"`, mode 0600, written by the installer from a file, never printed) and in
each controller's settings. It never crosses the network.

- **The proof** is `"auth"`: the lowercase hex HMAC-SHA256, keyed with the secret's UTF-8
  bytes, of the UTF-8 string `<nonce>:<role>:<name>`, with the role and name of the same
  `hello`.
- **Control link.** The hub always speaks first:
  `{"op":"hello","role":"hub","name":..,"revision":..,"seq":..,"nonce":"<32 hex>","protocol":1}`.
  A controller or agent reads it before sending its own `hello` with `"auth"`. Agents
  connecting to the hub do the same.
- **Monitor link.** On connect the speaker sends
  `{"op":"hello","role":"speaker","name":..,"nonce":..,"protocol":1}`; the client answers
  `{"op":"hello","role":"controller","name":..,"auth":..}` before any `subscribe`.
- **Refusal.** A missing or wrong proof, on either link, gets
  `{"op":"error","about":"hello","reason":..}` and the connection closes.
- **OSC** is accepted by the hub only from the IP address of an authenticated controller, and
  by an agent only from its hub. A second agent with a connected agent's name replaces it only
  with a valid proof.
- **Limits.** A hub takes at most 32 control peers and 32 monitor clients with at most 32
  subscriptions each; a controller with more than 1 MiB unread is dropped. A document has at
  most 64 plugin nodes and 512 connections.
- **No secret configured** (only before an installer has run): `ampd` logs it loudly and
  accepts every peer.
