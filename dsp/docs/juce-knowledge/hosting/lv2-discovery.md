# LV2 plugin discovery & identity (JUCE AudioPluginHost)

Two distinct failure modes have bitten the crossover / firConv plugins on the Banana Pi target.
Both are host/deployment issues, **not** DSP or plugin-logic bugs.

## 1. "Unable to locate plugin with the requested URI" on reopen (needs a rescan)

**Symptom:** reopening the JUCE Plug-In Host with a saved `.filtergraph` pops "Couldn't create
plugin — Unable to locate plugin with the requested URI" for *our* plugin, while system plugins
(e.g. LSP in `/usr/lib/lv2`) reload fine. A manual rescan makes it work for that session only.

**Cause:** JUCE's LV2 host builds its lilv world **once at startup** from a *hardcoded* path list
and then looks plugins up by URI in that world:

- `LV2PluginFormatHeadless::Pimpl()` → `loadAllPluginsFromPaths(getDefaultLocationsToSearch())`
  (`modules/juce_audio_processors_headless/format_types/juce_LV2PluginFormatImpl.h`).
- Linux defaults (`getDefaultLocationsToSearch`): `~/.lv2`, `/usr/lib/lv2`, `/usr/local/lib/lv2`
  (or the `lib64` variants). **`$LV2_PATH` is ignored** — `loadAllFromPaths` calls
  `lilv_world_set_option(LILV_OPTION_LV2_PATH, …)` with that hardcoded list, overriding the env.
- Loading a graph → `findPluginByUri(desc.fileOrIdentifier)` against that world; miss → the error.
  (For LV2, `PluginDescription::fileOrIdentifier` **is the plugin URI**, not a file path.)

A manual rescan calls `searchPathsForPlugins(paths)` → loads the extra dir into the world for the
running session, but the next launch rebuilds the world from the defaults only, so it's lost again.

**Fix:** deploy the `.lv2` bundle into a **default** search path — use `~/.lv2/` (the build
scripts' `-install` deploy to `~/$DEPLOY_LV2_DIR`, which defaults to `.lv2`; keep it that way
in `config/local.env`). Do **not** rely on `$LV2_PATH` or a custom
dir like `~/juce_lv2` for this host. Remove stale copies from non-default dirs to avoid confusion.

## 2. Only one of two plugins appears after a scan

**Cause:** two bundles sharing the same `LV2URI` (or the same VST3/AU `PLUGIN_CODE`). Hosts key
their database on the URI — `CMake API.md`: *"LV2 hosts will assume that any plugins with the same
URI are interchangeable."* Same URI ⇒ one overwrites the other.

**Fix:** give every plugin a unique `LV2URI` **and** `PLUGIN_CODE` in `juce_add_plugin`
(`PLUGIN_MANUFACTURER_CODE` may be shared). Current: every URI is `SPEAKER_LV2_URI_BASE/<plugin>` (juce_plugins/cmake/SpeakerPlugin.cmake), e.g. crossover = `…/bpi_amp_dsp_tuning/crossover` / `Cros`;
firConv = `…/bpi_amp_dsp_tuning/firConv` / `Fcnv`. Bundle-folder and `.so` names being unique is **not**
enough — the host dedupes by URI, not filename.
