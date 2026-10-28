# speakerd

The headless plugin host that runs on each speaker: step 2 of
[`docs/remote-plugin-host.md`](../../../docs/remote-plugin-host.md). It is AudioPluginHost with
the window removed. JUCE's `AudioProcessorGraph` hosts the headless LV2 builds of the plugins
and runs inside the PipeWire cycle. At boot it loads its graph from the first state layer that
loads, and it plays with no network, no controller and no `ampd`. It has no GUI and links no
X11. It opens no network socket: only PipeWire's local one, and the local socket pair JUCE's
message loop uses inside the process.

Not here yet (step 3): the local socket to `ampd`, parameter changes and graph revisions
while running, the role-aware **Mine** input pin, and monitoring (meters, level bars, scope
frames). The path a revision will take exists (`Engine::install`): the new graph is built and
settled beside the running one, then swapped in between a 30 ms fade-out and a 30 ms fade-in.
In step 2 it runs once, at boot, before anything plays. A rate change does not swap the graph:
it re-prepares the running one in place, behind silence.

## Command line

```
speakerd --state-dir DIR [--node-name NAME] [--from LAYER] [--lv2-bundle PATH]...
speakerd --state-dir DIR --render IN.wav OUT.wav [--block N] [--from LAYER] [--lv2-bundle PATH]...
speakerd --import-filtergraph FILE [--device DEVICE_JSON] [--prefer-headless] [--allow-unsafe-routing] [--lv2-bundle PATH]...
speakerd --version
```

| Option | |
|---|---|
| `--state-dir DIR` | required: holds `graph.json`, `graph.lkg.json`, `factory.json`, `device.json` |
| `--node-name NAME` | the name players target (default `speakerd`); the filter node is `NAME-dsp` |
| `--from LAYER` | start trying at this layer: `current` (the default), `last-known-good`, `factory` or `passthrough`. speakerd keeps no count of its own crashes, since it writes no file. Whatever restarts it after a crash (systemd, `ampd`) says where to start |
| `--lv2-bundle PATH` | also load this LV2 bundle (repeatable). Plugins are otherwise found by URI in `~/.lv2`, `/usr/lib/lv2` and `/usr/local/lib/lv2`; JUCE ignores `$LV2_PATH` |
| `--render IN.wav OUT.wav` | the offline render: no PipeWire (see below) |
| `--block N` | the render's block size, default 1024 (the speaker's quantum). Render only: the live node runs at PipeWire's quantum, and `--block` without `--render` is a bad command line |
| `--import-filtergraph FILE` | no PipeWire, no state directory: writes an AudioPluginHost session as a graph document to stdout (see [Importing an AudioPluginHost session](#importing-an-audiopluginhost-session)). It takes `--device`, `--prefer-headless`, `--allow-unsafe-routing` and `--lv2-bundle` only; those three are for it only |
| `--device DEVICE_JSON` | with `--import-filtergraph`: the speaker's `device.json`, which says whether the session's output channel 0 (`playback_FL`) or 1 (`playback_FR`) is the woofer. Without it, `playback_FL` is |
| `--prefer-headless` | with `--import-filtergraph`: a complete or control build's URI becomes its `_headless` sibling's, when that plugin is found (in the default paths or a `--lv2-bundle`) |
| `--allow-unsafe-routing` | with `--import-filtergraph`: import the stream straight to the tweeter, or a control build, with a warning, instead of failing |
| `--version` | prints `speakerd <version> (<commit>)` on stdout, for example `speakerd 0.2.1 (999f8ed1a668)`, and exits 0. `<commit>` is the short hash (12 characters) of the commit it was built from, with `-dirty` when the working tree had changes (tracked or untracked) at build time, or `unknown` without git. It is taken at every build (`BuildInfo.cmake`), so it follows HEAD without a reconfigure. A script reads the version as the first number after `speakerd ` |

| Exit status | |
|---|---|
| 0 | the live mode stopped by SIGINT, SIGTERM or SIGHUP, at any point, also while it waits for PipeWire (after fading out, if it played), or the render was written. The render does not catch signals: they end it the usual way, with no output file promised |
| 1 | not used yet: kept for other failures |
| 2 | bad command line, a `--lv2-bundle` from which no plugin loaded, or `--import-filtergraph` failed (the reason is on stderr, and nothing on stdout) |
| 3 | another speakerd already runs on this PipeWire, the input sink or the filter node could not be started, the connection to PipeWire was lost (the daemon went away), the filter or the input sink failed, or the filter node went away (destroyed by PipeWire or with `pw-cli destroy`). An amplifier or a link that goes away is not a failure: speakerd links again when it is back |
| 4 | the render could not read `IN.wav` or write `OUT.wav` |

No loadable layer is not an error: speakerd plays passthrough and keeps running. If PipeWire
is not there yet, speakerd waits for it, trying every second. From the moment it starts
waiting for PipeWire, every way out logs `stopping` once, as its last line before it exits
(a bad command line or `--lv2-bundle`, exit 2, comes earlier and does not).

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

### For the board (aarch64)

`tools/build.sh <what> aarch64 [complete|control|headless|all]`
cross-compiles in the development container into `build/aarch64/<what>/`, with the layout of
the x86_64 build:

```bash
tools/build.sh speakerd aarch64      # build/aarch64/speakerd/speakerd_artefacts/Release/speakerd
tools/build.sh loudness aarch64      # build/aarch64/loudness/<target>_artefacts/Release/LV2/<target>.lv2
```

It uses the native JUCE install (its `juceaide` runs on the build machine) with
`dsp/juce_plugins/tools/toolchain-aarch64-linux.cmake`, the `:arm64` libraries of the image
through pkg-config, and `qemu-aarch64-static` for the LV2 manifest helper JUCE runs after each
plugin. A cross build never copies a bundle into a `~/.lv2`: its copy directories are pinned
inside the build tree and it runs with a throwaway `HOME`, and the script fails if an aarch64
binary is in the `~/.lv2` of the machine that builds. When cross-compiling, speakerd's
`CMakeLists.txt` points pkg-config at the target's multiarch directory unless `PKG_CONFIG_LIBDIR`
says otherwise, and stops if the libpipewire it finds is not the target's.

What the aarch64 builds need on the board (Debian bookworm: glibc 2.36, libstdc++ of GCC 12),
from `readelf -d` and their symbol versions:

| Binary | `NEEDED` | Newest symbol versions | Packages |
|---|---|---|---|
| `speakerd` | `libpipewire-0.3.so.0`, `libstdc++.so.6`, `libm.so.6`, `libgcc_s.so.1`, `libc.so.6` | `GLIBC_2.34`, `GLIBCXX_3.4.30`, `CXXABI_1.3.13` | `libpipewire-0.3-0`, `libstdc++6`, `libgcc-s1`, `libc6` |
| the plugins: `loudness` (all three builds), `crossover`, `firConv` | `libfontconfig.so.1`, `libfreetype.so.6`, `libstdc++.so.6`, `libm.so.6`, `libgcc_s.so.1`, `libc.so.6` | `GLIBC_2.35`, `GLIBCXX_3.4.30` (loudness) | `libfontconfig1`, `libfreetype6`, and the above |
| `ampScope` | the same and `libasound.so.2` | | `libasound2` too |

No X11, and no ALSA library but ampScope's. At run time libpipewire loads PipeWire's client
modules and SPA plugins (`libpipewire-0.3-modules`, `libspa-0.2-modules`), which any board
running PipeWire has.
The plugins link fontconfig and freetype because every target links `juce_gui_basics`
(`SpeakerPlugin.cmake`); the headless build never opens a display.

**Same output on both.** Rendered with the aarch64 `speakerd` and `loudness_headless` under
`qemu-aarch64-static`, the document of `test_speakerd_same_sound` differs from the x86_64 render
by at most -75.2 dBFS (residual -89.3 dBFS RMS) and 0.0044 dB in gain, at 40 Hz. That is GCC's
floating-point contraction: on aarch64 it fuses multiplies and adds (FMA) by default, and
loudness's float shelving biquads near DC turn the different rounding into that much. Built
with `-ffp-contract=off`, the aarch64 plugin matches the x86_64 render to -128.8 dBFS (98 % of
the samples bit for bit, the gains equal to 1e-6 dB). speakerd's own path (copies, the fade,
the limits, the caps) has no multiply-add to fuse.

To run the aarch64 build in the container, under qemu (the image's multiarch libraries are
found without `-L`), give it an empty `HOME`, so that lilv does not find the x86_64 bundles of
`~/.lv2`:

```bash
HOME=$(mktemp -d) qemu-aarch64-static build/aarch64/speakerd/speakerd_artefacts/Release/speakerd \
    --state-dir DIR --render IN.wav OUT.wav \
    --lv2-bundle build/aarch64/loudness/loudness_headless_artefacts/Release/LV2/loudness_headless.lv2
```

The live mode would also need the `:arm64` PipeWire client modules (`libpipewire-0.3-modules`),
which the image does not have.

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
away and return, and speakerd keeps running. Nobody runs `pw-link`. The links belong to
speakerd and go when it goes.

It links once the registry has settled: after a change it waits one round trip to PipeWire
(`pw_core_sync`), because PipeWire sends every change of one event (a node going away: its
links, then its ports, then the node) before it answers. Linking on the first change of such
a burst would link to a port that is already going away; with PipeWire 0.3.65 that ends in an
error on the core about the link speakerd then destroys. A link of speakerd's that fails, or
that the server removes, is dropped and made again at the next settle. Only a lost connection
(`-EPIPE` on the core, as `pw_stream` and `pw_filter` read it) is fatal; any other error is
logged and speakerd carries on.

**The output gate.** speakerd's sink is a driver, so it keeps the filter running while the
amplifier is away, and PipeWire 0.3.65 keeps handing the filter output buffers that nobody
reads. So the output is held at gain 0 (the plugins keep running) until every link of both
sides is active (the state PipeWire reports on speakerd's own links; a link somebody else made
counts once it is in the registry), and the fade-in starts from 0 then. An amplifier that comes
back under a playing stream hears a 30 ms ramp, not the stream at full level, and so does the
amplifier at start when the stream was already playing into the sink.

**One speakerd per PipeWire.** If a node called `<name>-dsp` exists at start, another speakerd
runs: a second one would adopt the same sink and link to the same amplifier, and the two
outputs would add up (+6 dB, tweeter included). So the second one says so and stops, exit 3,
and the first one is not touched. A speakerd that crashed took its filter node with it.

**The filter node going away** (destroyed by PipeWire, or with `pw-cli destroy speakerd-dsp`)
is a failure: nothing would play again, so speakerd stops with exit status 3, for whatever
restarts it.

**Why a sink in front of the filter.** WirePlumber 0.4.13 (the boards' version) links a
player only into an adapter node, one whose node format it can read. A `pw_filter` in
PipeWire 0.3.65 cannot publish a node format, so WirePlumber rejects it as a target ("no
usable format found") and sends the player to the default sink instead. So players play into
a sink. Its monitor ports carry exactly what was played (`monitor.channel-volumes = false`), in
the same cycle, so the sink adds no latency.

**Whose sink.** If a node called `speakerd` exists when speakerd starts, speakerd uses it.
Otherwise speakerd creates a null-audio-sink of that name with `object.linger = true`, and the
sink stays in PipeWire when speakerd stops: a stream playing into it stays there, nothing reads
the sink's monitor, so the amplifier gets silence, and the next speakerd adopts the sink and
fades in under the running stream. Were the sink to go, WirePlumber would move the stream to the default sink, which on a
speaker is the amplifier: full range, at full level, tweeter included. speakerd then says loudly
that no sink was configured (Log lines): on a board, it means the firmware's drop-in is missing.

**The amplifier drives, never the sink.** A null sink is always a driver (`node.driver =
false` is ignored), and PipeWire gives a group of linked nodes to the driver with the highest
`priority.driver`. The sink speakerd makes has `priority.driver = 0`, the lowest there is (not
a negative number: PipeWire 0.3.65 compares it as if unsigned), so the amplifier's DAC clocks
the graph, not the sink's timer: checked with `pw-profiler` on the virtual pair, the amplifier
drives both with `priority.driver = 1000` and at a tie (0, where the amplifier, made first, wins).
A board's amplifier needs a `priority.driver` above 0 (`firmware/pipewire/pipewire.conf` gives it
1000). With the amplifier gone, the sink drives its players on its own.

A lingering sink only exists once speakerd has run since PipeWire started. So **before any
deploy, a speaker must create the sink in its PipeWire configuration** (`context.objects`, an
`adapter` with `factory.name = support.null-audio-sink`, `node.name = "speakerd"`,
`media.class = "Audio/Sink"`, `audio.position = [ FL FR ]`, `monitor.channel-volumes = false`,
`priority.driver = 0`; the firmware's drop-in is
`firmware/pipewire/pipewire.conf.d/60-speakerd-sink.conf`), so that a stream has somewhere safe
to go from the moment PipeWire starts, before speakerd and between its restarts. This is the
same rule `firmware/` already follows for snapclient's target.

**Real-time thread.** speakerd creates its PipeWire context with `client-rt.conf` (PipeWire's
`client.conf` plus `module-rt`), so the graph's data thread asks for real-time priority (RTKit,
or `RLIMIT_RTPRIO`) whatever the environment; `PIPEWIRE_CONFIG_NAME`, when set, still decides.
On the virtual pair the data thread runs `SCHED_FIFO` at priority 88, and with
`PIPEWIRE_CONFIG_NAME=client.conf` at normal priority. A user's own
`~/.config/pipewire/client-rt.conf` replaces PipeWire's (snapcast's routing file has no
`module-rt`), which is why the board's unit gives speakerd an empty `HOME`.

**Rate and block size.** At start speakerd reads `default.clock.rate`, `default.clock.quantum`
and `default.clock.quantum-limit` from the PipeWire core. It prepares the graph for blocks up
to the quantum limit, and primes it at the quantum: from then on the graph is only ever called
with that block size, so it never reallocates on the audio thread (see Real time). The filter
asks for `node.latency = <quantum>/<rate>`, so a player asking for more latency (pw-play asks
for 100 ms) does not raise the quantum. If the quantum changes while speakerd runs:

- to a whole multiple of the primed size, each cycle runs as that many blocks: no dropout, the
  same output;
- to anything else (a smaller quantum, or one that is not a multiple), the cycle is padded with
  zeros to whole blocks, its real samples fade out within the cycle, and the output is silent
  until speakerd primes the graph at the new size with one silent block, on the message thread,
  and fades back in: a gap of a few tens of milliseconds;
- to 4 or more times the primed size, it first runs as that many blocks, and if the quantum
  stays there for 2 s, speakerd primes the graph at the quantum through a normal 30 ms
  fade-out and fade-in (a dip of about 60 ms), so that the graph does not keep the per-call
  cost of a small block for good after the quantum came down and went back up (1024 -> 32 ->
  1024 would otherwise run 32 blocks of 32 per cycle from then on). Both quantum lines are
  logged then, the second 2 s after the first. Two or three blocks per cycle stay as they are.

If a cycle comes at a rate the graph is not prepared for, the output is silent until speakerd
re-prepares the graph at the new rate and primes it at the quantum the cycles bring, which
takes a fraction of a second. It logs that (no quantum line follows, whatever the quantum), and
fades back in.

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
every parameter value, every connection is possible, and there is no loop. It says on stderr
why each layer before it failed. If none loads, it plays **passthrough**.

## device.json

```json
{
  "version": 1,
  "role": "left",
  "amplifier": { "node": "amplifier", "woofer": "playback_FL", "tweeter": "playback_FR" },
  "limits": { "gain_db": -20.0, "tweeter": "muted" }
}
```

| Key | |
|---|---|
| `role` | `"left"`, `"right"` or `"mono"`: which channel this speaker plays |
| `amplifier` | the amplifier's PipeWire node: `{ "node": ..., "woofer": ..., "tweeter": ... }`, or just its name. The ports default to `playback_FL` (woofer) and `playback_FR` (tweeter); the two must differ |
| `limits` | what a deploy caps (`docs/autonomous-development.md`, section 2): `{ "gain_db": <dB, at most 0>, "tweeter": "muted" or "on" }`, both keys. `gain_db` is a fixed gain on both amplifier outputs and their ceiling; -100 or less is silence. `tweeter` `"muted"` makes it exact zeros. **Without `limits`, the output is limited all the same, at the fail-safe: -20 dB, tweeter muted.** No limit at all is written out: `{ "gain_db": 0, "tweeter": "on" }` |
| `version` | optional; 1 if present |

The amplifier is `amplifier` on a virtual speaker and `bpi-alsa-playback-pcm` on a board.
**The hardware convention** the defaults and the fail-safe rely on: the amplifier's
`playback_FL` drives the woofer and `playback_FR` the tweeter, unless `device.json` says
otherwise. A board wired the other way must say so in `amplifier`, or a muted "tweeter" is its
woofer.

**The file fails closed.** A key speakerd does not know, at any level (`"Limits"`, `"limts"`, a
`"trim"` before the speakerd that knows it), and a key given twice in one object (JUCE's JSON
parser would keep the last) make the file invalid: a typo or a newer setting must never be
heard as no limit. The file grows with the speakerd that reads it, through its version.

**The limits** apply after the graph and the fade, to whatever the graph is, passthrough
included: the woofer and the tweeter times the gain, then the tweeter zeros if muted, then NaN
and infinity to 0 and every sample clamped at the ceiling, plus or minus the gain (at most
1.0). The gain is a ceiling as well, so `"gain_db": -20` means no sample above -20 dBFS on
either output, however loud the stream (above full scale included) or however much the graph
boosts. They apply on top of passthrough's own -20 dB: passthrough with `"gain_db": -20` reaches
the woofer at -40 dB. The render mode applies them too, since it reads the same `device.json`.
They are constants for the life of the process (the Engine is made with them; there is no
Engine without limits): the audio thread only reads them, and a change needs a restart.

**A missing or invalid `device.json`** is reported (`device.json: FAILED: ...`), whatever is
wrong in it: invalid JSON, an unknown or repeated key, an invalid `limits` (a `gain_db` above 0
or not a number, a `tweeter` that is neither `"muted"` nor `"on"`, a key of it missing,
`null`), the woofer and the tweeter on one port. speakerd then plays with the defaults, limited
the way a deploy is: role `mono`, amplifier `bpi-alsa-playback-pcm`, output at -20 dB, tweeter
muted. So a speaker that cannot read its device file, or whose file says nothing about limits,
never drives its tweeter and plays its woofer at -20 dB at most.

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
    { "src": { "node": 16, "channel": 0 }, "dst": { "node": 2,  "channel": 0 } }
  ]
}
```

This plays FL through loudness to the woofer, and leaves the tweeter silent. **Only a crossover
may feed the tweeter** (output channel 1): a full-range signal can destroy it. A test on the
virtual pair may route a plugin to both channels, to measure both; a document for a board never
does.

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
| `state` | optional: base64 (RFC 4648, canonical, with its padding; whitespace is ignored) of the plugin's own state, what its processor's `getStateInformation()` returns; at most 16 MB. Base64 that does not encode back to itself fails the layer |
| `bypassed` | optional, `true` or `false` |

Anything else (for example the controller's canvas positions) is ignored.

The state goes in first, then the parameter values on top of it. A document written by hand
with parameter values and no state works. LV2 state carries the plugin's URI (finding 1 of
section 7), so `state` is never LV2 state. speakerd wraps the plugin's own state under
`<URI>:StateString` of the build it hosts, the key JUCE's LV2 wrapper keeps it under, so the
same document works whichever build its URI names (`LV2State.h`).

**Connections.** `src` is a node's output channel, `dst` a node's input channel. They are checked
against each plugin's real channel counts when it loads. A connection listed twice, or one the
graph refuses, fails the layer. So does a loop (a node that feeds itself, through any path:
`node <uid> (<name>) feeds itself: the graph has a loop`), which JUCE's graph would otherwise
run, giving the connection that closes it silence. So does an unknown plugin, an unknown
parameter ID, or a choice text the plugin does not have.

**What "takes its state" means.** JUCE's LV2 wrapper reports success for any state, and a JUCE
plugin ignores a state it cannot read. So speakerd reads the plugin's own state out before (S0)
and after (S1) restoring the document's state (G). If G differs from S0 but S1 still equals S0,
the layer fails: `the plugin did not take the state: its state did not change`. This proves the
state changed, not that every value in it arrived. Its limit: a G that restates the current
values in other bytes (another attribute order, say) is reported as not taken, which fails the
layer: the safe side.

Not in version 1: bus layouts (each plugin keeps its default), and the Mine pin.

## Passthrough

The last layer, used when no other loads. The speaker's own channel goes to the woofer only, at
**-20 dB** (and the `limits` of `device.json` on top of that), and the tweeter gets digital
silence. Left plays FL, right plays FR, and mono plays
(FL + FR) / 2. The woofer is then clamped to +-0.1, and NaN or infinity becomes 0, so the
woofer never sees more than -20 dBFS, even from a stream above full scale (a player's volume
above 1, or a float WAV over 0 dBFS). No plugin is involved, so it works even with no LV2 plugin
installed at all. With no crossover running, a full-range signal must never reach the tweeter.
speakerd announces passthrough loudly:

```
speakerd: *** PASSTHROUGH: no state layer loaded ***
speakerd: using layer passthrough: woofer = FL at -20 dB, tweeter muted
```

## The render mode

`--render IN.wav OUT.wav` runs the same graph, from the same layers, with no PipeWire. It
uses the same `Engine`, the same settling (below) and the same block size as the live node,
1024 unless `--block` says otherwise. Every block is a full one, as every cycle of the live node
is: the last is padded with zeros and only its real samples are written, so `OUT.wav` is as long
as `IN.wav`. `IN.wav` may be mono or stereo. A mono input feeds both FL and FR, which is what a
mono player gets through PipeWire. `OUT.wav` has two channels, the
woofer and the tweeter, at the input's rate, as 32-bit integer PCM. The render starts at full
level (the live node fades in once, at start, before anything plays). Pointing the same
document's URI at `.../loudness` instead of `.../loudness_headless` gives what the complete
build outputs: the reference a live capture is compared with.

## Real time

- **The audio thread never blocks, allocates or makes a syscall of its own.** Per cycle it
  copies the stream in, calls `AudioProcessorGraph::processBlock`, ramps the fade, applies the
  limits of `device.json` (a multiply and a clear, with constants fixed when the engine is
  made), caps the output at the ceiling and copies the two amplifier channels out.
- **Three locks JUCE takes on the audio thread, and why none of them waits.** (1) JUCE's graph
  takes each processor's callback lock around its `processBlock`; speakerd never takes those
  locks from another thread while the graph runs. (2) The graph hands its render sequence over
  under a `SpinLock` that the audio thread only try-locks. (3) JUCE's LV2 hosting, when a plugin
  echoes a parameter on its atom output (`patch:Set`), sets the hosted parameter on the audio
  thread (`processAtomPort` -> `PatchSetCallback` -> `setDenormalisedValueWithoutTriggeringUpdate`),
  which takes that parameter's `listenerLock` (`juce_AudioProcessorParameter.cpp`,
  `sendValueChangedMessageToListeners`); a `StateChanged` atom calls `updateHostDisplay`, which
  takes the processor's `listenerLock`. The message thread takes the same parameter lock when
  it sets a value. So nobody sets a running graph's parameters on the message thread. In step 2
  values are only set on a graph that is not running yet. **In step 3, parameter changes are
  applied on the audio thread, from the command queue, never on the message thread while the
  graph runs.**
- **Absolute caps.** NaN and infinity in the stream become 0 before any plugin sees them (so
  they cannot stick in a filter's state). After the graph, the fade and the limits' gain, NaN
  and infinity become 0 and every sample is clamped to the ceiling: plus or minus the limits'
  gain, at most 1.0, on both amplifier channels, whatever the graph did.
  The test reads the exponent bits, so it holds whatever the optimisation flags.
- **The graph is handed over through one atomic, after a fade handshake.** The message thread
  takes the graph only after the output has faded out. It turns the fade-in request off, then
  moves a serial number on; a cycle hands that serial back only if it began with the fade-in off
  and ended at gain 0, and the message thread waits for exactly its serial. While it holds the
  graph, cycles output silence instead of waiting.
- **A graph swap costs only the fades.** A new graph is built, prepared and settled beside the
  running one, on its own buffers; the audio thread does not know it yet. The swap is then a
  30 ms fade-out, a pointer swap and a 30 ms fade-in.
- **Fades.** The output fades in over 30 ms at every start, when the output gate opens (the
  links of both sides active: at start, and when the amplifier comes back), after a rate
  change, after a quantum the graph had to be primed for and after every graph swap. It fades out over 30 ms
  before them and at a stop. At a stop, two silent cycles go by after the fade-out before the
  node is destroyed: the cycle that ends the ramp has run by then, but its output may not have
  reached the amplifier, and destroying the node at once dropped it (a step to zero, measured on
  the virtual pair).
- **Values in force from the first audible sample.** JUCE's LV2 wrapper prepares a plugin's
  DSP when it is instantiated, with the default parameter values. A hosted LV2 plugin only
  takes parameter values while it processes (finding 2 of section 7). So every new graph runs
  on 1 s of silence, on the message thread, before it is heard. That is longer than loudness's
  longest glide.
- **Block size.** JUCE's graph reallocates its output buffer whenever a call's block size
  differs from the previous call's (`AudioBuffer::setSize`, in `RenderSequence::perform`). So the
  graph is only ever called with the block size it was primed at, on the message thread:
  settling primes it at the quantum, and a quantum that is not a whole number of primed blocks
  is primed anew (see Rate and block size). The audio thread never reallocates.

## Log lines

On stderr, one line each, `speakerd: ` first. A test can wait for these; their wording is
stable.

| Line | When |
|---|---|
| `speakerd <version> (<commit>), state dir <dir>` | always, first (not with `--import-filtergraph`). `speakerd --version` prints the same version and commit. 0.2.0 is the first speakerd that knows `limits`; 0.2.1 makes its own sink at `priority.driver = 0` and says so loudly, and asks for a real-time thread through `client-rt.conf` |
| `device.json: FAILED: <reason>; using the defaults` | `device.json` missing or invalid, its `limits` included; the defaults are limited (see device.json) |
| `device: role <role>, amplifier '<node>' (woofer <port>, tweeter <port>)` | always, at start |
| `device: limits: output at <gain> dB, tweeter <muted\|on>`, followed by ` (no limits in device.json: the fail-safe)` or ` (device.json not read: the fail-safe)` when they are the fail-safe | always, right after the line before. `<gain>` is printed as `%g` does: `-20`, `-6.5`, `0`. No limit at all reads `output at 0 dB, tweeter on` |
| `lv2 bundle <path>: <URI>` | a `--lv2-bundle` loaded |
| `--lv2-bundle <path>: not a directory`, `--lv2-bundle <path>: no LV2 plugin loaded from it: ...` | a `--lv2-bundle` that did not load; exit status 2 follows |
| `cannot connect to PipeWire: <reason>: trying again every second`, `PipeWire did not answer: trying again every second`, `cannot start the PipeWire client loop: trying again every second` | PipeWire is not there (yet); said once, however long it takes |
| `pipewire: <rate> Hz, quantum <n>, largest quantum <n>` | connected to PipeWire |
| `starting at layer <name> (--from)` | `--from` names a layer after `current` |
| `layer <name> (<file>): FAILED: <reason>` | a layer that did not load; `<name>` is `current`, `last-known-good` or `factory` |
| `layer <name> (<file>): warning: <text>` | a clamped value, or no connection to the amplifier |
| `layer <name> (<file>): loaded, revision <n>, <k> plugin node(s)` | the layer that loaded |
| `using layer <name> (<file>), revision <n>` | the layer speakerd plays from |
| `*** PASSTHROUGH: no state layer loaded ***` then `using layer passthrough: woofer = <channel> at -20 dB, tweeter muted` | no layer loaded |
| `pipewire: created the input sink '<name>'` or `pipewire: input sink '<name>' exists: using it` | at start |
| `*** NO INPUT SINK CONFIGURED: PipeWire had no node '<name>', so speakerd made it; a speaker must define it in its PipeWire configuration (on a board, the drop-in 60-speakerd-sink.conf is missing) ***` | right after `created the input sink`: no PipeWire configuration defines the sink. On a board the firmware's drop-in is missing, and between speakerd's restarts a stream could reach the amplifier |
| `pipewire: another speakerd already runs on this PipeWire (node '<name>-dsp' exists)` | at start, before any sink line: another speakerd runs on this PipeWire; this one makes nothing, the other is not touched; `stopping` and exit status 3 follow |
| `cannot create the input sink '<name>': <reason>`, `cannot create the PipeWire node: <reason>`, `cannot connect the PipeWire node: <reason>` | the sink or the filter could not be started; `stopping` and exit status 3 follow |
| `pipewire: node '<name>-dsp' registered as id <id>` | the filter is in PipeWire |
| `waiting for amplifier '<node>' and its ports (...)`, `waiting for input sink '<name>' and its ports (...)` | a peer is not there (yet) |
| `linked to amplifier '<node>' (output_woofer -> <port>, output_tweeter -> <port>)` | the outputs are linked |
| `linked to input sink '<name>' (monitor_FL -> input_FL, monitor_FR -> input_FR)` | the inputs are linked |
| `unlinked from amplifier '<node>': linking again as soon as it is back`, `unlinked from input sink '<name>': ...` | a peer went away; speakerd keeps running and links again when it is back |
| `playing: layer <layer>, <rate> Hz, quantum <n>` | both sides linked, every link active and the fade-in under way: from here a player is heard. Once per linking (not when the peer goes away again before two cycles have run) |
| `pipewire: the graph now runs at <rate> Hz: re-prepared the plugins` | after a rate change; no quantum line follows it |
| `pipewire: the quantum changed to <n>: the graph runs it as <k> block(s) of <p>` | the quantum (at the same rate) became a whole multiple of the size the graph is primed for (`<p>`): no dropout. With `<k>` of 4 or more, the next line follows if the quantum stays for 2 s |
| `pipewire: the quantum changed to <n>: faded out, primed the graph for it and faded back in` | any other quantum, after the gap; or a quantum of 4 or more primed blocks that stayed for 2 s, after a 30 ms fade-out and fade-in |
| `pipewire: a link failed: <reason> (<error>): <node>:<port> -> <node>:<port>` | a link speakerd made failed; at most once per 10 s per pair of ports. Not fatal |
| `pipewire: could not create a link: <error>` | the request for a link failed in the client. Not fatal |
| `pipewire: error, carrying on: <message> (<error>)` | PipeWire reported an error that is not a lost connection, such as one about a link that went away with its peer; at most once per 10 s. Not fatal |
| `pipewire: connection error: <message> (<error>)`, `pipewire: node '<name>-dsp' failed: <error>`, `pipewire: the input sink '<name>' failed: ...` | the connection to PipeWire was lost (the core reports `-EPIPE`), or the filter or the input sink failed; `lost PipeWire` follows |
| `pipewire: node '<name>-dsp' went away`, possibly followed by ` (<reason>)` | the filter node was destroyed (by PipeWire, or with `pw-cli destroy`); `lost PipeWire` follows, and often a `pipewire: error, carrying on: ...` line before it |
| `lost PipeWire` | after one of those; `stopping` and exit status 3 follow |
| `stopping` | the last line of the live mode, once, whatever the reason: after `lost PipeWire` (exit 3), after another speakerd was found or a sink or filter could not be started (exit 3), or on SIGINT, SIGTERM, SIGHUP at any point (exit 0). Nothing is logged after it; speakerd makes no new link once it has said it, and the output fades out through the links it has |
| `render: <n> samples at <rate> Hz in blocks of <b>, layer <layer>, written to <path>` | the render is done |
| `render: <reason>` | the render failed; exit status 4 follows |
| `import: <file>: an AudioPluginHost session, <n> node(s), <m> connection(s)` | `--import-filtergraph`, first; then one line per node and per connection, as in the next rows, then the warnings |
| `import: node <uid> (Audio Input) becomes node 1, the stream in: channel 0 = FL, 1 = FR`, `import: node <uid> (MIDI Input): nothing connected to it, left out` | how each internal node of the session was mapped |
| `import: node <uid> (Audio Output) becomes node 2, the amplifier: session channel 0 (playback_FL) = <woofer\|tweeter>, 1 (playback_FR) = <tweeter\|woofer>, ` then `as --device says (woofer <port>, tweeter <port>)` or `assumed: no --device, and playback_FL is the woofer unless device.json says otherwise` | which of the session's output channels is the woofer |
| `import: node <uid> (<name>), LV2 <URI>, becomes node <n>[, <URI'>_headless (--prefer-headless) \| , keeping <URI>: no <URI>_headless found], state: <k> bytes of the plugin's own`, or `..., state: empty, left out`, or `..., no STATE: the plugin starts from its defaults` | each plugin node |
| `import: connection <src>:<ch> -> <dst>:<ch> becomes <src'>:<ch> (<FL\|FR>) -> <dst'>:<ch> (<woofer\|tweeter>)` | each connection, in the session's uids and then the document's |
| `import: warning: <text>` | something the document does that is allowed but suspect: a plugin not connected, nothing reaching the amplifier; with `--allow-unsafe-routing`, the stream straight to the tweeter or a control build |
| `import: a graph document, version 1, revision 1: <k> plugin node(s), <m> connection(s)` | the import worked; the document is on stdout, exit status 0 |
| `import: FAILED: <reason>` | the import failed; exit status 2 follows, nothing is on stdout |

`<layer>` is `current (graph.json), revision 3`, `last-known-good (graph.lkg.json), revision 2`,
`factory (factory.json), revision 1` or `passthrough`.

## Importing an AudioPluginHost session

The tuning a board runs today is an AudioPluginHost session, a `.filtergraph` file.
`speakerd --import-filtergraph FILE` turns it into a graph document, version 1, on stdout, which
a state layer can hold, so that "the same sound as today" is a document speakerd plays. It
says on stderr what it mapped (see Log lines) and exits 0, or says why not and exits 2. It
reads the file as `PluginGraph::restoreFromXml` does
(`extras/AudioPluginHost/Source/Plugins/PluginGraph.cpp` of the pinned JUCE):

| In the session | In the document |
|---|---|
| the internal `Audio Input` node | the input node, uid 1: its channel 0 is FL, 1 is FR |
| the internal `Audio Output` node | the output node, uid 2 (see below for which channel is the woofer) |
| an LV2 node | a plugin node, uids from 16 in the session's order; `plugin.identifier` is its URI (the `file` attribute) |
| its `STATE` | `state`: the plugin's own state. The host saved the hosted instance's LV2 state (lilv's Turtle, in JUCE's `MemoryBlock` base64), which keeps the plugin's own state under `<URI>:StateString`; that is unwrapped, the inverse of what speakerd does when it loads a state (`LV2State.h`). The URI it is read under is the session's, so it must be the URI the state was saved by |
| `MIDI Input`, `MIDI Output` | left out, when nothing is connected to them (every new AudioPluginHost session has them) |
| node positions, editor windows | nothing |

**Which output channel is the woofer.** In AudioPluginHost, output channel `i` reached the
amplifier's position `i` (0 = FL, port `playback_FL`; 1 = FR, `playback_FR`) through its "ALSA
Playback". speakerd sends document channel 0 to `device.json`'s woofer port and 1 to its tweeter
port. With `--device DEVICE_JSON`, session channel `i` becomes the document channel of
whichever of the woofer and the tweeter that file puts on that port; it fails unless they are
exactly `playback_FL` and `playback_FR`, in either order, and on a `device.json` speakerd would
not read. Without `--device`, `playback_FL` is taken as the woofer (the hardware convention,
see device.json), and the log says it was assumed.

With `--prefer-headless`, a complete build's URI (one that ends neither in `_headless` nor in
`_control`) or a control build's becomes its `_headless` sibling's, when that plugin is found in
the default LV2 paths or a `--lv2-bundle`; the state stays the same, since all builds of a
plugin share their state's format. Without it, the document names the builds the session names.

**What is unsafe fails,** unless `--allow-unsafe-routing` (then it is a warning): the stream
from Audio Input straight to the tweeter (after the mapping above), and a control build, which
has no DSP and passes the stream through at full level (`--prefer-headless` replaces it by its
headless build when that is found). A plugin between the stream and the tweeter is not
checked: whether it is a crossover is not something the session says.

The import also fails, exit 2, on: a file larger than 64 MB, not XML or not a `<FILTERGRAPH>`;
a `<!DOCTYPE>`, `<!ENTITY>` or any other declaration (AudioPluginHost writes none, and JUCE's
parser would read the files an entity names), or elements nested deeper than 32; an element
other than `<FILTER>` and `<CONNECTION>`; a uid or channel attribute that is not digits only;
another plugin format (VST3, LADSPA); an internal plugin other than the four above; ARA; a MIDI
connection or a connection of a MIDI node; a channel the stream or the amplifier does not have;
a connection to a node that is not in the session; a `STATE` that is not exactly JUCE's base64
(its size prefix, digits only and at most 32 MB, must match its characters, and it must read
back as itself) or not the LV2 state of a JUCE plugin saved under its URI, whose `StateString`
is checked the same way (at most 16 MB); a disabled bus (speakerd enables every bus, so the
channels would move); two connections that map to one (a session with two `Audio Output`
nodes); and when the document cannot be written to stdout. It is parsed from the file's text,
never from the file, so it reads no other file. Any other error ends it with exit 2 too. It
warns, and goes on, when a plugin is not connected and when nothing reaches the amplifier. A
node without `STATE`, or whose plugin state is empty, gets no `state`: the plugin starts from
its defaults. The document is checked as a layer is (`GraphDocument::parse`) before it is
written; whether its plugins load is checked when a speaker loads it.

**The example.** `examples/loudness.filtergraph` is the session of the complete `loudness` build
between the stream's FL and the woofer, with the parameter values of `examples/loudness.params.json`.
`examples/make-loudness-filtergraph.sh` made it, with `examples/MakeFiltergraph.cpp` (the target
`speakerd-make-filtergraph`, built only on request): it hosts the complete build through JUCE's
LV2 hosting in a graph made the way AudioPluginHost makes a new document (Audio Input, MIDI Input,
Audio Output, MIDI Output, uids 1 to 4, the plugin 5), sets the values while the graph runs, and
writes the file the way `PluginGraph::createXml` and `saveDocument` do, CRLF line ends included.
Imported and rendered, it gives exactly (bit for bit) what the same values given as `params`
give, and so does its `--prefer-headless` import with `loudness_headless`.

```bash
speakerd --import-filtergraph dsp/juce_apps/speakerd/examples/loudness.filtergraph \
         --device ~/.local/state/speakerd/device.json --prefer-headless > graph.json
```

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
| `Engine.*` | the running graph, the audio-thread entry point, the fades, the hand-over between the threads, settling, priming |
| `Samples.h` | the absolute caps: NaN and infinity to 0, clamping |
| `PipeWireNode.*` | the input sink, the `pw_filter` node, and the links |
| `Render.*` | the offline render |
| `DeviceConfig.*` | `device.json`, its `limits` included |
| `BuildInfo.cmake` | writes `BuildInfo.h` at every build: the commit speakerd is built from |
| `FilterGraph.*` | `--import-filtergraph`: an AudioPluginHost session as a graph document |
| `examples/` | an AudioPluginHost session, `loudness.filtergraph`, and how it was made |
