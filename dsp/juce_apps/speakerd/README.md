# speakerd

The headless plugin host that runs on each speaker: step 2 of
[`docs/remote-plugin-host.md`](../../../docs/remote-plugin-host.md). It is AudioPluginHost with
the window removed. JUCE's `AudioProcessorGraph` hosts the headless LV2 builds of the plugins
and runs inside the PipeWire cycle. At boot it loads its graph from the first state layer that
loads, and it plays with no network, no controller and no `ampd`. It has no GUI and links no
X11. It opens no socket other than PipeWire's.

Not here yet (step 3): the local socket to `ampd`, parameter changes and graph revisions
while running, the role-aware **Mine** input pin, and monitoring (meters, level bars, scope
frames). The code that fades the output out, swaps a graph and fades back in is already the
path every graph takes at boot and on a rate change.

## Command line

```
speakerd --state-dir DIR [--node-name NAME] [--from LAYER] [--lv2-bundle PATH]...
speakerd --state-dir DIR --render IN.wav OUT.wav [--block N] [--from LAYER] [--lv2-bundle PATH]...
```

| Option | |
|---|---|
| `--state-dir DIR` | required: holds `graph.json`, `graph.lkg.json`, `factory.json`, `device.json` |
| `--node-name NAME` | the name players target (default `speakerd`); the filter node is `NAME-dsp` |
| `--from LAYER` | start trying at this layer: `current` (the default), `last-known-good`, `factory` or `passthrough`. speakerd keeps no count of its own crashes, since it writes no file. Whatever restarts it after a crash (systemd, `ampd`) says where to start |
| `--lv2-bundle PATH` | also load this LV2 bundle (repeatable). Plugins are otherwise found by URI in `~/.lv2`, `/usr/lib/lv2` and `/usr/local/lib/lv2`; JUCE ignores `$LV2_PATH` |
| `--render IN.wav OUT.wav` | the offline render: no PipeWire (see below) |
| `--block N` | the render's block size, default 1024 (the speaker's quantum) |

| Exit status | |
|---|---|
| 0 | stopped by SIGINT, SIGTERM or SIGHUP (after fading out), or the render was written |
| 1 | not used yet: kept for other failures |
| 2 | bad command line, or a `--lv2-bundle` from which no plugin loaded |
| 3 | the PipeWire node could not be created, or the connection to PipeWire was lost |
| 4 | the render could not read `IN.wav` or write `OUT.wav` |

No loadable layer is not an error: speakerd plays passthrough and keeps running. If PipeWire
is not there yet, speakerd waits for it, trying every second.

**Which build runs.** lilv keeps the first plugin of a URI it sees, and it sees the default
paths first. A `--lv2-bundle` whose URI is also in `~/.lv2` is therefore ignored, and speakerd
stops with status 2 rather than run a build it was not asked for. To run exactly the bundles
named, give speakerd an empty `HOME`, as plugin-check does. lilv's `attempt to map invalid URI`
line on loading a bundle is harmless.

## Building and running

```bash
tools/build.sh speakerd              # build/speakerd/speakerd_artefacts/Release/speakerd
tools/build.sh loudness headless     # puts loudness_headless.lv2 in ~/.lv2 (the container's)
```

On the virtual pair (`tests/vpair/README.md`), against the left speaker's PipeWire:

```bash
tests/vpair/vpair.sh up
tests/vpair/vpair.sh run left build/speakerd/speakerd_artefacts/Release/speakerd --state-dir DIR &
tests/vpair/vpair.sh run left pw-play --target speakerd tone.wav
```

## In PipeWire

| Node | `node.name` | Ports | Linked by |
|---|---|---|---|
| input sink | `speakerd` | `playback_FL`, `playback_FR` in; `monitor_FL`, `monitor_FR` out | the session manager links players into it |
| the graph | `speakerd-dsp` | `input_FL`, `input_FR` in; `output_woofer`, `output_tweeter` out | speakerd |

The graph runs in one `pw_filter` node, `speakerd-dsp`, on PipeWire's real-time data thread,
in the cycle. speakerd makes four links itself, by watching the registry:

```
speakerd:monitor_FL  -> speakerd-dsp:input_FL      speakerd-dsp:output_woofer  -> <amplifier>:<woofer port>
speakerd:monitor_FR  -> speakerd-dsp:input_FR      speakerd-dsp:output_tweeter -> <amplifier>:<tweeter port>
```

The amplifier node and its ports come from `device.json`. speakerd links as soon as all the
ports exist, and again whenever they come back: the amplifier may appear after speakerd, or go
away and return. Nobody runs `pw-link`. The links belong to speakerd and go when it goes.

**Why a sink in front of the filter.** WirePlumber 0.4.13 (the boards' version) links a
player only into an adapter node, one whose node format it can read. A `pw_filter` in
PipeWire 0.3.65 cannot publish a node format, so WirePlumber rejects it as a target ("no
usable format found") and sends the player to the default sink instead. So players play into
a sink. Its monitor ports carry exactly what was played (`monitor.channel-volumes = false`), in
the same cycle, so the sink adds no latency.

**Whose sink.** If a node called `speakerd` exists when speakerd starts, speakerd uses it.
Otherwise speakerd creates a null-audio-sink of that name, and the sink goes when speakerd
goes. When it goes, WirePlumber moves whatever was playing into it to the default sink, which
on a speaker is the amplifier: full range, at full level, tweeter included. A speaker should therefore
create the sink in its PipeWire configuration (`context.objects`, an `adapter` with
`factory.name = support.null-audio-sink`, `node.name = "speakerd"`,
`media.class = "Audio/Sink"`, `audio.position = [ FL FR ]`, `monitor.channel-volumes = false`),
so that it outlives speakerd and the stream goes nowhere while speakerd restarts. This is the
same rule `firmware/` already follows for snapclient's target.

**Rate and block size.** At start speakerd reads `default.clock.rate`, `default.clock.quantum`
and `default.clock.quantum-limit` from the PipeWire core. It prepares the graph for blocks up
to the quantum limit, and runs it at whatever block each cycle brings. The filter asks for
`node.latency = <quantum>/<rate>`, so a player asking for more latency (pw-play asks for
100 ms) does not raise the quantum. If a cycle comes at a rate the graph is not prepared for,
the output is silent until speakerd re-prepares the graph at the new rate, which takes a fraction
of a second. It logs that, and fades back in.

## The state directory

| File | Layer | Written by |
|---|---|---|
| `graph.json` | current | `ampd` (step 3) |
| `graph.lkg.json` | last known good | `ampd` promotes it |
| `factory.json` | factory | ships with the plugins |
| `device.json` | not a layer: this speaker's role and amplifier | set once, never from the hub |

speakerd only reads these files. At start it tries `graph.json`, `graph.lkg.json` and
`factory.json` in that order, from the one `--from` names, and uses the first that **loads
completely**: it parses, every plugin in it is found, instantiated and takes its state and
every parameter value, and every connection is possible. It says on stderr why each layer
before it failed. If none loads, it plays **passthrough**.

## device.json

```json
{
  "version": 1,
  "role": "left",
  "amplifier": { "node": "amplifier", "woofer": "playback_FL", "tweeter": "playback_FR" }
}
```

| Key | |
|---|---|
| `role` | `"left"`, `"right"` or `"mono"`: which channel this speaker plays |
| `amplifier` | the amplifier's PipeWire node: `{ "node": ..., "woofer": ..., "tweeter": ... }`, or just its name. The ports default to `playback_FL` (woofer) and `playback_FR` (tweeter) |
| `version` | optional; 1 if present |

The amplifier is `amplifier` on a virtual speaker and `bpi-alsa-playback-pcm` on a board.
Unknown keys are ignored, so the file can grow (trim, delay, `partner-lost`). A missing or broken
`device.json` is reported. speakerd then plays with the defaults: role `mono`, amplifier
`bpi-alsa-playback-pcm`.

## The graph document, version 1

This is AudioPluginHost's `.filtergraph` as JSON: nodes with uids, the plugin by format and
identifier, its state, and connections per channel between node uids. It adds a revision
number and each node's parameter values by parameter ID. It is what a state layer holds and
what the hub will send in step 3.

```json
{
  "format": "speakerd-graph",
  "version": 1,
  "revision": 7,
  "nodes": [
    {
      "uid": 16,
      "name": "loudness",
      "plugin": { "format": "LV2",
                  "identifier": "https://github.com/AMuszkat/bpi_amp_dsp_tuning/loudness_headless" },
      "params": { "mode": "Force 1", "s0_e0_gain": 12.0, "s0_e0_freq": 120.0 }
    }
  ],
  "connections": [
    { "src": { "node": 1,  "channel": 0 }, "dst": { "node": 16, "channel": 0 } },
    { "src": { "node": 16, "channel": 0 }, "dst": { "node": 2,  "channel": 0 } },
    { "src": { "node": 16, "channel": 0 }, "dst": { "node": 2,  "channel": 1 } }
  ]
}
```

| Key | |
|---|---|
| `format` | optional; `"speakerd-graph"` if present |
| `version` | `1` |
| `revision` | an integer, 0 or more; logged, and compared by the hub in step 3 |
| `nodes` | the plugin nodes (may be empty) |
| `connections` | per-channel connections (may be empty) |

**Node uids.** 1 is the input node, the stream: output channel 0 is FL, 1 is FR (step 3 adds
2, Mine). 2 is the output node, the amplifier: input channel 0 is amplifier 1 (the woofer), 1 is
amplifier 2 (the tweeter). 3 to 15 are reserved for nodes speakerd provides. Plugin nodes use
16 and up, each uid once.

**A node.**

| Key | |
|---|---|
| `uid` | the node's uid |
| `plugin.format` | `PluginDescription::pluginFormatName`: `"LV2"` |
| `plugin.identifier` | `PluginDescription::fileOrIdentifier`: for LV2, the plugin URI. The URI names the build: `.../loudness_headless` on a speaker, `.../loudness` for the complete build |
| `name` | optional, for messages; defaults to the URI's last segment |
| `params` | optional: `{ "<parameter ID>": value }`. The values are in the plugin's own units, the ones its parameter reports: dB, Hz, ms, or a choice's index. A choice also takes its text (`"Force 1"`), a switch takes `true`/`false`. Values outside the range are clamped, with a warning |
| `state` | optional: base64 (RFC 4648) of the plugin's own state, what its processor's `getStateInformation()` returns |
| `bypassed` | optional, `true` or `false` |

Anything else (for example the controller's canvas positions) is ignored.

The state goes in first, then the parameter values on top of it. A document written by hand
with parameter values and no state works. LV2 state carries the plugin's URI (finding 1 of
section 7), so `state` is never LV2 state. speakerd wraps the plugin's own state under
`<URI>:StateString` of the build it hosts, the key JUCE's LV2 wrapper keeps it under, so the
same document works whichever build its URI names (`LV2State.h`).

**Connections.** `src` is a node's output channel, `dst` a node's input channel. They are checked
against each plugin's real channel counts when it loads. A connection listed twice, or one the
graph refuses, fails the layer. So does an unknown plugin, an unknown parameter ID, or a choice
text the plugin does not have.

Not in version 1: bus layouts (each plugin keeps its default), and the Mine pin.

## Passthrough

The last layer, used when no other loads. The speaker's own channel goes to the woofer only, at
**-20 dB**, and the tweeter gets digital silence. Left plays FL, right plays FR, and mono plays
(FL + FR) / 2, so the woofer never sees more than -20 dBFS. No plugin is involved, so it works
even with no LV2 plugin installed at all. With no crossover running, a full-range signal must
never reach the tweeter. speakerd announces passthrough loudly:

```
speakerd: *** PASSTHROUGH: no state layer loaded ***
speakerd: using layer passthrough: woofer = FL at -20 dB, tweeter muted
```

## The render mode

`--render IN.wav OUT.wav` runs the same graph, from the same layers, with no PipeWire. It
uses the same `Engine`, the same settling (below) and the same block size as the live node,
1024 unless `--block` says otherwise. `IN.wav` may be mono or stereo. A mono input feeds both
FL and FR, which is what a mono player gets through PipeWire. `OUT.wav` has two channels, the
woofer and the tweeter, at the input's rate, as 32-bit integer PCM. The render starts at full
level (the live node fades in once, at start, before anything plays). Pointing the same
document's URI at `.../loudness` instead of `.../loudness_headless` gives what the complete
build outputs: the reference a live capture is compared with.

## Real time

- **The audio thread never blocks, allocates or makes a syscall of its own.** Per cycle it
  copies the stream in, calls `AudioProcessorGraph::processBlock`, ramps the fade and copies
  the two amplifier channels out. JUCE's graph takes each processor's callback lock around its
  `processBlock`. speakerd never takes those locks from another thread while the graph runs, so
  they are never contended.
- **The graph is handed over through one atomic.** The message thread takes the graph only
  after fading the output out. While it holds it, cycles output silence instead of waiting. It
  builds, prepares and settles the graph, then gives it back and the output fades in.
- **Fades.** The output fades in over 30 ms at every start, after a rate change and after
  every graph swap. It fades out over 30 ms before them and at a stop.
- **Values in force from the first audible sample.** JUCE's LV2 wrapper prepares a plugin's
  DSP when it is instantiated, with the default parameter values. A hosted LV2 plugin only
  takes parameter values while it processes (finding 2 of section 7). So every new graph runs
  on 1 s of silence, on the message thread, before it is heard. That is longer than loudness's
  longest glide.
- **Block size.** JUCE's graph reallocates one output buffer when a call's block size differs
  from the previous call's. Settling primes it at the quantum the cycles will bring. It only
  happens on the audio thread if PipeWire changes its quantum while speakerd runs, which is
  logged.

## Log lines

On stderr, one line each, `speakerd: ` first. A test can wait for these; their wording is
stable.

| Line | When |
|---|---|
| `device: role <role>, amplifier '<node>' (woofer <port>, tweeter <port>)` | always, at start |
| `starting at layer <name> (--from)` | `--from` names a layer after `current` |
| `device.json: FAILED: <reason>; using the defaults` | `device.json` missing or broken |
| `pipewire: <rate> Hz, quantum <n>, largest quantum <n>` | connected to PipeWire |
| `layer <name> (<file>): FAILED: <reason>` | a layer that did not load; `<name>` is `current`, `last-known-good` or `factory` |
| `layer <name> (<file>): warning: <text>` | a clamped value, or no connection to the amplifier |
| `layer <name> (<file>): loaded, revision <n>, <k> plugin node(s)` | the layer that loaded |
| `using layer <name> (<file>), revision <n>` | the layer speakerd plays from |
| `*** PASSTHROUGH: no state layer loaded ***` then `using layer passthrough: woofer = <channel> at -20 dB, tweeter muted` | no layer loaded |
| `pipewire: created the input sink '<name>'` or `pipewire: input sink '<name>' exists: using it` | at start |
| `pipewire: node '<name>-dsp' registered as id <id>` | the filter is in PipeWire |
| `waiting for amplifier '<node>' and its ports (...)`, `waiting for input sink '<name>' and its ports (...)` | a peer is not there (yet) |
| `linked to amplifier '<node>' (output_woofer -> <port>, output_tweeter -> <port>)` | the outputs are linked |
| `linked to input sink '<name>' (monitor_FL -> input_FL, monitor_FR -> input_FR)` | the inputs are linked |
| `unlinked from amplifier '<node>': linking again as soon as it is back` | the amplifier went away |
| `playing: layer <layer>, <rate> Hz, quantum <n>` | both sides linked and cycles running: from here a player is heard. Once per linking |
| `pipewire: the graph now runs at <rate> Hz: re-prepared the plugins` | after a rate change |
| `pipewire: the quantum changed to <n> (...)` | the quantum changed while running |
| `lost PipeWire: stopping` | exit status 3 follows |
| `stopping` | on SIGINT, SIGTERM, SIGHUP; exit status 0 follows |
| `render: <n> samples at <rate> Hz in blocks of <b>, layer <layer>, written to <path>` | the render is done |
| `render: <reason>` | the render failed; exit status 4 follows |

`<layer>` is `current (graph.json), revision 3`, `last-known-good (graph.lkg.json), revision 2`,
`factory (factory.json), revision 1` or `passthrough`.

## Comparing a live capture with the render

Through PipeWire, the output matches the render of the complete build to within the precision
PipeWire converts samples with. PipeWire converts a 32-bit integer WAV to floats with 24-bit
precision, and JUCE's reader converts it with 31-bit precision. `loudness`'s float low-shelf
biquads near DC have a large noise gain, which turns those last-bit differences into output
differences of up to about -91 dBFS. A render of the same tone with each sample moved by one
24-bit step differs from the plain render by the same amount. Compare levels, gains or the
residual against a threshold (say -80 dBFS), not samples bit for bit.

## Files

| File | |
|---|---|
| `Main.cpp` | command line, signals, the live mode and its controller timer, the render mode |
| `StateLayers.*` | the boot order: the first layer that loads, or passthrough |
| `GraphDocument.*` | the graph document, version 1: parsing and checks |
| `GraphBuilder.*` | the document as an `AudioProcessorGraph`, the way `PluginGraph::restoreFromXml` does it; the passthrough graph |
| `LV2State.*` | the plugin's own state into and out of the hosted build's LV2 state |
| `Engine.*` | the running graph, the audio-thread entry point, the fades, the hand-over between the threads, settling |
| `PipeWireNode.*` | the input sink, the `pw_filter` node, and the links |
| `Render.*` | the offline render |
| `DeviceConfig.*` | `device.json` |
