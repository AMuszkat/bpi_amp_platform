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
| knobs (OSC) | `/bpi/param` `,isfiss`: node, id, value, seq, origin, token (see Authentication) |

- The controller sends a moving knob over OSC, at most 50 messages a second per parameter, and
  **the last value of every gesture over the control link** (UDP may lose it). With no gesture
  (a typed value, a preset), it sends over the control link only.
- The hub accepts a change it can address (the node exists in the current revision and has
  that parameter; a `mon_` ID never), stamps `seq`, updates its state, applies it to its own
  `speakerd` (local), sends it to every agent over the control link (agents take no OSC)
  and echoes it to every controller (OSC with that controller's token, and over control when
  it came over control). A change it cannot address is answered, on the control link only, with
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
| knobs (OSC) | `/bpi/gesture` `,issss`: node, id, state, origin, token |

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
  names is one the hub has seen loaded somewhere (the full catalogue, with versions and source identities, is
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

On the control link the hub speaks first and both sides prove the pair's secret before
anything else (the handshake is in "Authentication" below; these show the other fields):

```json
{"op":"hello","role":"hub","name":"left","revision":8,"seq":1234,"nonce":"<Ns>","protocol":1}
{"op":"hello","role":"controller","name":"ctl-mac","osc_port":9000,"protocol":1,"nonce":"<Nc>","auth":"<client proof>"}
{"op":"hello","role":"agent","name":"right","revision":7,"time":"2026-10-28T09:10:00Z","protocol":1,"nonce":"<Nc>","auth":"<client proof>"}
{"op":"welcome","auth":"<server proof>"}
```

- **A controller** then gets `{"op":"state","revision":8,"seq":1234,"document":{...},"speakers":[{"name":"left","role":"left","online":true,"layer":"current"},{"name":"right",...}],"gestures":[...]}`
  and builds its graph from it; it never pushes its own older copy (rule 3 of section 8). The hub
  sends OSC echoes, stamped with the controller's token, to its address and `osc_port`.
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

A **stream** is `output/level`, `speakerd/timing` or `node/<uid>/<mon_id>`:

- `output/level`: what the amplifier gets, after the limits: `[woofer_peak, woofer_rms,
  tweeter_peak, tweeter_rms]` in dBFS, over the last period, built into `speakerd` for any graph.
- `node/<uid>/<mon_id>`: a plugin's monitor, the value of its `mon_` parameter.
- `speakerd/timing`: what the audio cycles since the previous value cost, built into
  `speakerd`. `v` is an object:
  `{"cycles":47,"late":0,"jumps":0,"cycle":{"mean":..,"max":..},"graph":{"mean":..,"max":..},"nodes":[{"uid":16,"name":"loudness_headless","mean":..,"max":..}],"cycles_total":..,"late_total":..,"jumps_total":..,"dropped_total":..}`.
  `mean` and `max` are the average and the largest single cycle's processing time as a
  fraction of the cycle's length (quantum / rate); `late` counts cycles whose processing took
  longer than their length; `jumps` counts cycles whose driver position does not follow on from
  the previous one (an xrun, or another driver); the `*_total` counters run since `speakerd`
  started; `dropped_total` counts cycles not recorded. `status` carries the same object over
  the last minute, with `"seconds"`.

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
- `t` is `speakerd`'s monotonic clock in seconds. `ampd` keeps the newest value per stream, and
  sends each value as it arrives, at most at that controller's rate: newest wins, nothing
  queues. A new subscription gets the stream's newest value at once.
- A subscription ends when the controller unsubscribes or its connection closes.
- Frames, records and credits are step 4.

**How a plugin publishes a monitor.** A monitor is a parameter whose ID starts with `mon_`,
in every build of the plugin (complete, control, headless), declared after the plugin's other
parameters, not automatable, of category output meter, and left out of the plugin's saved
state. The headless and complete builds write it from the audio thread's atomics with
`setValue` (at most about 60 times a second) and, under JUCE's LV2 wrapper only, tell the
parameter's listeners from inside that same audio cycle when the stored value moved by a step
of its range, and once a second anyway; under any other wrapper (VST3 in a DAW) they only write
it, since no host there reads monitors and JUCE's VST3 wrapper would take a mutex on the audio
thread. JUCE's LV2 wrapper
reports only parameters its listener was told about, and sends them to the host at the end of
the cycle, which is how `speakerd` sees them, with `getValue()` on its monitor thread. The only
lock this takes on the plugin's audio thread is the monitor parameter's own listener lock,
which nothing else takes (the exception named in `dsp/CLAUDE.md`); a thread of the plugin's own cannot do it safely, because the LV2
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
{"version":1,"name":"left","hub":true,"listen":"0.0.0.0","hub_address":null,"secret":"<the pair's secret>"}
{"version":1,"name":"right","hub":false,"listen":"0.0.0.0","hub_address":"bpi-amp.local","secret":"<the pair's secret>"}
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
    `node/<uid>/<mon_id>`. Each stream has its own rate (`rate_hz`, default 30; outside 0.1 to
    60 it is refused with an error); a period with no audio cycle sends nothing, so at a quantum of 1024 at
    48 kHz a stream gives about 47 values a second at most. RMS is plain RMS (a full-scale sine
    reads -3.01 dBFS); -120 is the floor. A `subscribe` to a name that is no stream is answered
    `{"op":"error","about":"subscribe","stream":..,"reason":..}`; a well-formed `node/...`
    stream the graph lacks is kept and starts when a revision has it.
20. **`status`** is answered with the `hello`'s fields and `"op":"status"`.
21. **Files.** `speakerd` writes no state file; it creates and removes its socket.
22. **The agent's `hello`** also carries `layer`, `speakerd` (its version), `device_role` and
    `osc_port` (unused since agents take no OSC; kept for compatibility); later changes of its speaker go to the hub as
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

## Authentication

Every pair has a **secret**: 16 to 256 printable characters without spaces, in each speaker's
`ampd.json` (`"secret"`, mode 0600, written by the installer from a file, never printed) and in
each controller's settings (as a file). It never crosses the network. Both ends of every
TCP link prove they know it, and every OSC datagram carries a token derived from it, so
neither a fake hub nor a forged datagram is believed.

- **Proofs** are lowercase hex HMAC-SHA256, keyed with the secret's UTF-8 bytes, of a UTF-8
  string. The two directions use different prefixes, so a proof can never be reflected back:
  - client: `HMAC("client:<Ns>:<Nc>:<role>:<name>")`, with the role and name of the client's
    `hello`;
  - server: `HMAC("server:<Ns>:<Nc>:<role>:<name>")`, with the role and name of the server's
    `hello`.

  `Ns` and `Nc` are the server's and the client's nonces: 32 lowercase hex characters (128
  bits) from a cryptographic random source, new for every connection. Proofs are compared in
  constant time, as bytes; anything else (a missing, non-hex or wrong proof, a missing or
  malformed nonce) gets `{"op":"error","about":"hello","reason":..}` and the connection
  closes.
- **The handshake,** on the control link (the hub is the server; a controller or an agent the
  client) and on the monitor link (the speaker is the server, a controller the client):
  1. The server speaks first:
     `{"op":"hello","role":"hub","name":..,"revision":..,"seq":..,"nonce":"<Ns>","protocol":1}`
     (`"role":"speaker"`, with only `name`, `nonce` and `protocol`, on the monitor link).
  2. The client answers with its own nonce and proof:
     `{"op":"hello","role":"controller","name":..,"osc_port":..,"protocol":1,"nonce":"<Nc>","auth":"<client proof>"}`.
  3. The server checks it and answers `{"op":"welcome","auth":"<server proof>"}` before
     anything else (before `state` on the control link, before any `value` on the monitor
     link).
  4. The client checks the server's proof before it believes or applies anything, and that the
     server's `name` is the one it expected (on the monitor link, the speaker's name in the
     hub's `state`; on the control link, the hub's name when one is configured, else the proven
     name is shown); until then it
     sends nothing but its `hello`. An agent never applies a revision, a parameter or an `apply`
     from a hub that has not proved itself.
- **A session key** per control connection, `K = HMAC("session:<Ns>:<Nc>")` (hex), gives the
  **OSC token** `T = first 32 hex characters of HMAC-SHA256(K, "osc")`, keyed with the UTF-8
  bytes of K's hex string. With no secret configured the token is the empty string, accepted
  only by a controller run with `--insecure`.
- **OSC carries the token.** `/bpi/param` is `,isfiss` (node, id, value, seq, origin, token) and
  `/bpi/gesture` is `,issss` (node, id, state, origin, token). The hub accepts a datagram only
  with the token of an authenticated controller connection from that datagram's source
  address; it stamps its echoes to each controller with that controller's token, and a
  controller ignores OSC without its own token. A datagram without a valid token is dropped
  and counted, never answered.
- **Agents ignore OSC.** The hub sends every parameter to an agent over the control link, which
  is authenticated both ways; an agent drops every datagram.
- **What this does not stop.** An attacker who can read a connection's traffic can replay its
  OSC datagrams (within that session's range of values) and, by placing itself in the path
  (ARP or mDNS spoofing), relay a handshake and then alter messages afterwards: the links are
  authenticated, not encrypted or signed per message. On a home network that is out of scope
  for now; TLS or a per-message MAC is the step after. Nothing over the network can lift the
  output cap, unmute the tweeter, or (while the tweeter is on) feed it other than through a
  crossover's high band: those are enforced by `device.json` and the tweeter rule on the
  speaker.
- **Limits.** A hub takes at most 32 control peers and 32 monitor clients with at most 32
  subscriptions each, and at most 16 connections per link still in their handshake (10 s
  each); a controller with more than 1 MiB unread is dropped. A document has at most 64
  plugin nodes and 512 connections.
- **No secret configured** (only before an installer has run): `ampd` logs it loudly and
  accepts every peer, sending no `welcome` proof (`"auth":null`); a controller connects to
  such a hub only when told to (`--insecure`), and says so.
