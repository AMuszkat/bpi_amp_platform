# Running the JUCE AudioPluginHost on Linux (the `-debug-linux` flow)

Notes from wiring `tools/build-podman.sh -setup-host` / `<plugin> -debug-linux` into the plugins. All of
this was verified against JUCE 8.0.12 sources and by running it.

## 1. Building it needs `ladspa.h`

`extras/AudioPluginHost/CMakeLists.txt` sets `JUCE_PLUGINHOST_LADSPA=1` via
`target_compile_definitions(... PRIVATE ...)`. That **cannot be overridden from the CMake
command line** — a `-D` cache entry does not beat a target compile definition, and defining
the macro twice with different values is worse than useless. So the builder image needs the
`ladspa-sdk` package or the build dies with:

    juce_LADSPAPluginFormatHeadless.cpp:39:10: fatal error: ladspa.h: No such file or directory

Build with `-DJUCE_BUILD_EXTRAS=ON` and target `AudioPluginHost`; the artefact lands in
`<build>/extras/AudioPluginHost/AudioPluginHost_artefacts/Release/AudioPluginHost`.

## 2. Hand-authoring a `.filtergraph`

Generating a session with a plugin pre-loaded is safe once you know three things — each
checked in `PluginGraph.cpp` / `InternalPlugins.cpp` rather than guessed:

- **Node IDs come from the XML.** `createNodeFromXml` calls
  `graph.addNode (..., NodeID ((uint32) xml.getIntAttribute ("uid")))`, so `<CONNECTION
  srcFilter=… dstFilter=…>` refers to the `uid` attributes you wrote.
- **Internal I/O nodes are matched by NAME only.** `InternalPluginFormat::createPluginInstance`
  → `factory.createInstance (desc.name)` → `name.equalsIgnoreCase (desc.name)`. So
  `format="Internal" name="Audio Input"` (or `"Audio Output"`, `"MIDI Input"`, `"MIDI Output"`)
  is enough; there is no need to reproduce JUCE's `uniqueId` hash.
- **For LV2, `file` is the plugin URI**, not a path — `PluginDescription::fileOrIdentifier`
  holds the URI for that format, and `format` must be the string `"LV2"`.

`uiopen_Normal="1"` on the `<FILTER>` element makes the host open that plugin's editor as
soon as the session loads (`PluginWindow::getOpenProp (Type::normal)` → `"uiopen_" + "Normal"`).

A failed plugin instantiation is **silent** — `createNodeFromXml` simply does not add the
node — so "the host started" proves nothing about whether your graph is right.

## 3. Verifying a session headlessly

Because of that silence (and because the GUI may not start at all, see §4), the reliable
check is a small console app (about 60 lines; not kept in the repo, the core is below) that
runs the *same* restore path:

```cpp
juce::AudioPluginFormatManager formats;
juce::addHeadlessDefaultFormatsToManager (formats);   // JUCE 8 DELETED addDefaultFormats()
juce::PluginDescription pd;
pd.loadFromXml (*pluginElement);                      // the <PLUGIN> child of <FILTER>
auto instance = formats.createPluginInstance (pd, 48000.0, 512, error);
```

It reports the real channel counts and parameter count, needs no GUI and no audio device.
Skip `format == "Internal"` nodes — those are supplied by AudioPluginHost itself, not by any
`AudioPluginFormat`, so they can never instantiate outside it.

## 4. It can SIGSEGV on startup inside PipeWire

On a PipeWire desktop the host may crash **before drawing anything**, with a backtrace that
contains none of your code:

    Thread "alsa-pipewire" received signal SIGSEGV
    #0 libspa-audioconvert.so … #6 pw_impl_node_set_state … libpipewire-0.3.so

Seen with PipeWire 1.6.2, where `aplay -L` offers only `null`, `pipewire`, `default` and raw
`hw:*` — i.e. `default` *is* the crashing path, and `libasound_module_pcm_pipewire.so` is the
only alsa-lib plugin installed. Confirm it is the audio backend and not your plugin by
running the host with **no session file**; if that also crashes, nothing of yours is involved.

**It is not a broken PipeWire bridge.** `aplay -D pipewire`, `arecord -D pipewire` and
`alsaloop -C pipewire -P pipewire` all succeed on the same machine. `thread apply all bt`
shows the JUCE side (the crash is on a PipeWire-owned thread, so a single-thread backtrace
hides it):

    juce::AudioDeviceManager::setAudioDeviceSetup
      -> juce::ALSAAudioIODevice::open
        -> juce::ALSADevice::setParameters
          -> snd_pcm_prepare()            [libasound]
            -> libasound_module_pcm_pipewire.so   -> boom, on thread "alsa-pipewire"

So it is that specific `snd_pcm_prepare` with JUCE's hw params, not ALSA-over-PipeWire in
general. **Selecting "pipewire" inside the host's Audio Settings crashes it just the same** —
and because the settings are only written on a clean exit, the bad choice is at least not
persisted.

### The fix, and the near-identical thing that silently does nothing

`-debug-linux` starts the host with a private `XDG_CONFIG_HOME`
(`<plugin>/plugin_host_debug/config`) holding an `alsa/asoundrc` that points `default`
straight at a sound card:

    pcm.!default {
        type asym
        playback.pcm { type plug slave.pcm "hw:CARD=Generic_1,DEV=0" }
        capture.pcm  { type plug slave.pcm "hw:CARD=Generic_1,DEV=0" }
        hint { show on description "Default (direct ALSA - PipeWire bypassed …)" }
    }
    ctl.!default { type hw card "Generic_1" }

**Where that file lives is the whole trick.** The obvious version of the same idea — point
`ALSA_CONFIG_PATH` at a file that includes the stock config and then overrides `default` —

    </usr/share/alsa/alsa.conf>
    pcm.!default { type plug   slave.pcm "sysdefault" }

is a **no-op, and a silent one**: alsa.conf's `@hooks` block loads `/etc/alsa/conf.d/`
*after* the including file's own text has been parsed, so PipeWire's
`99-pipewire-default.conf` (`pcm.!default { type pipewire … }`) just overrode the override.
It cost a debugging session because nothing says so — the host crashes in the identical
frame with the "workaround" enabled. Always check an ALSA override with

    ALSA_CONFIG_PATH=… aplay -L | sed -n '/^default$/{n;p;}'

which kept answering `Default ALSA Output (currently PipeWire Media Server)`.

The last entry of that same `@hooks` list is `"$XDG_CONFIG_HOME/alsa/asoundrc"` — the only
config file loaded *after* `conf.d`, so it is the only place an override wins. Setting
`XDG_CONFIG_HOME` for the host process alone also keeps the change off the rest of the
desktop, and drops the host's own `Juce Audio Plugin Host.settings` (audio device, plugin
list) in the same folder, so the debug session is self-contained and per-plugin.

`sysdefault` would not have worked even from the right place: it resolves to the *first*
card, which on this machine is HDMI-only and has no capture side. The script reads
`/proc/asound` (no dependency on alsa-utils) and prefers a card that can do **both**
directions, since the debug graph is input → plugin → output; whichever direction a card
lacks gets `null`. `PLUGIN_HOST_ALSA_CARD=<id|index>` overrides the pick;
`PLUGIN_HOST_PIPEWIRE=1` skips the override, to re-check the crash after a PipeWire update.

Proof it took effect, read off the running host's `/proc/<pid>/`:

    fd/   -> /dev/snd/pcmC1D0p and /dev/snd/pcmC1D0c   (the card, opened both ways)
    maps  -> no libpipewire / libspa mapped at all

Do **not** just point `ALSA_CONFIG_PATH` at `/dev/null`. That stops the crash too, but it
discards every PCM definition along with it, so the host starts with **zero** audio devices
(`aplay -L` lists 0) and is only good for GUI/parameter work. The override above keeps all
the hw/plughw devices enumerable and playable.

Avoiding the ALSA bridge entirely and using JACK remains the clean long-term alternative:
install `pipewire-jack` (so `libjack.so.0` is PipeWire's shim — Ubuntu's stock
`libjack-jackd2-0` is *not*, it wants a real `jackd`) and build the host with `JUCE_JACK=1`.
AudioPluginHost does not set that flag, and the module header guards it with `#ifndef`, so it
*can* be injected from the command line — unlike `JUCE_PLUGINHOST_LADSPA` in §1, which the
target sets explicitly.

Note this is a *host-system* fault: every library in that backtrace belongs to the desktop,
not to the build container.

## 5. Hosts really do prepare plugins with sampleRate 0

Related trap found the same way. With no audio device, the graph has no sample rate, and
JUCE's LV2 client wrapper instantiates the plugin with **`sampleRate = 0`, `blockSize = 0`**:

    LV2PluginInstance::LV2PluginInstance (sampleRate=0, maxBlockSize=0)
      -> LV2PluginInstance::prepare (0, 0)
        -> YourProcessor::prepareToPlay (0, 0)

Any `prepareToPlay` that divides by the sample rate (every filter coefficient does) will
produce inf/NaN and trip JUCE's own assertions. Clamp defensively:

```cpp
currentSampleRate       = sampleRate > 0.0 ? sampleRate : 48000.0;
const int preparedBlock = samplesPerBlock > 0 ? samplesPerBlock : 512;
```

(Name it something other than `blockSize` — that is a member of `juce::AudioProcessor` and
`-Wshadow` is on via `juce_recommended_warning_flags`.)

## 6. Debug builds surface text bugs Release hides

`juce::String`'s `const char*` constructor **assumes ASCII** and asserts on any byte > 127
(`juce_String.cpp:327`). A UTF-8 literal like `"\xe2\x86\x92"` (→) therefore asserts in Debug
and renders as mojibake in Release, where the assertion is compiled out. Wrap it:

```cpp
juce::String (juce::CharPointer_UTF8 ("\xe2\x86\x92"))
```

## Editor windows

Fixed-size editors being maximised, floating panels from an embedded editor, and repaint
cost of a Debug build at HiDPI are in [`../gui/x11-plugin-windows.md`](../gui/x11-plugin-windows.md).
