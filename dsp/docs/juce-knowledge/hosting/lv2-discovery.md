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

## 3. Two bundles of one URI: which one runs, and what lilv parses

lilv (`juce_audio_processors_headless/format_types/LV2_SDK/lilv/src/world.c`), on loading a
bundle whose manifest declares a URI it already has from another bundle:

- `lilv_world_load_bundle` compares versions: it reads the manifest of each bundle and every
  file the manifest names for the plugin with `rdfs:seeAlso` (`load_plugin_model`; JUCE's
  manifest names `dsp.ttl`, where the version is), takes `lv2:minorVersion` and
  `lv2:microVersion` (`get_version`; 0.0 when either is missing), and **replaces** the loaded
  plugin when the new bundle's version is greater (`Replacing version`), **ignores** the new
  bundle when it is smaller (`Ignoring bundle`).
- On equal versions `lilv_world_add_plugin` keeps the first (`Duplicate plugin ... (ignored)`).
  JUCE loads the default paths first (section 1), so a bundle loaded later at the same version
  (`speakerd --lv2-bundle`) loses to a copy in `~/.lv2`.

**JUCE drops the major version.** `juce_audio_plugin_client_LV2.cpp` (the `dsp.ttl` writer)
writes the plugin's version `x.y.z` as `lv2:minorVersion y` and `lv2:microVersion z`; `x` is not
written anywhere lilv looks. So lilv orders 0.0.1 < 0.0.2 < 0.1.0 as intended, but sees 1.0.0 as
0.0, older than 0.9.9. A major raise (juce_plugins/README.md, "The version rule") does not make
a later bundle win.

**Every `rdfs:seeAlso` file is parsed, and a parse error disables the plugin.** Besides the
version comparison, `lilv_plugin_load` (`plugin.c`) parses all of a plugin's `rdfs:seeAlso`
files the first time the plugin is asked about; one that fails to parse sets `parse_errors`,
and `lilv_plugin_instantiate` then returns null (`instance.c`; what JUCE's LV2 hosting calls) and
`lilv_plugin_verify` false. So a file that is only diagnostic must not be named in the manifest:
`buildinfo.ttl` is not (`../build-system/cmake-reference.md`).
