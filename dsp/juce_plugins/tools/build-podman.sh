#!/bin/bash
# Build the JUCE plugins in juce_plugins/ for Debian ARM64 inside a Podman container, and
# debug them locally. ONE script for every plugin: the plugin is the first argument.
#
#     tools/build-podman.sh <plugin|all> [steps...]
#
# WHY PODMAN
#   * Linux host  -> the container runs natively (no virtualisation), so the cross-compile
#                    runs at full host speed. This is the priority case.
#   * macOS / Win -> Podman runs the same container inside its managed Linux VM
#                    ("podman machine"). Works identically, just slower.
#
# The host is amd64, the target is aarch64, so CMake CROSS-compiles using
# tools/toolchain-aarch64-linux.cmake. The container image (tools/Containerfile) carries
# the aarch64 toolchain and the :arm64 JUCE dependencies. One image and one JUCE install
# (juce_plugins/build-podman/) are shared by every plugin; each plugin's own build lands in
# <plugin>/build-podman/.
#
# QUICK START
#   tools/build-podman.sh -install-podman     # one-time: install Podman on this host
#   tools/build-podman.sh loudness            # image (first run) + JUCE + plugin
#   tools/build-podman.sh all                 # ... every plugin
#   tools/build-podman.sh loudness -install   # deploy the .lv2 to DEPLOY_HOST (config/local.env)
#
# STEPS
#   global (no plugin needed):
#     -install-podman   install Podman on this host
#     -image            build/refresh the shared builder image
#     -setup-juce       build + install JUCE for the builds (one-time, shared)
#     -clean-juce       remove the shared JUCE build/install
#     -setup-host       build the JUCE AudioPluginHost into juce_plugins/pluginHost
#     -shell            interactive shell in the builder container (in the plugin's folder
#                       when one is given)
#   per plugin (<plugin> or all):
#     -setup            configure the plugin's CMake build (aarch64 cross)
#     -build            compile the LV2 plugin
#     -install          deploy the .lv2 to $DEPLOY_HOST:~/$DEPLOY_LV2_DIR over ssh
#     -clean            remove the plugin's build outputs (keeps the image and JUCE)
#     -debug-linux      native Debug build for THIS Linux host, opened in the plugin host
#                       (one plugin at a time)
#   With a plugin and no steps: -setup-juce -setup -build.
#
# LOCAL DEBUGGING ON THIS MACHINE
#   tools/build-podman.sh -setup-host            # one-time
#   tools/build-podman.sh loudness -debug-linux
#
#   -debug-linux is the local debug loop: same container (so dependency versions stay
#   pinned), but a NATIVE x86_64 Debug build instead of the aarch64 Release cross-build,
#   installed into ~/.lv2 on this machine and opened in the JUCE AudioPluginHost with a
#   persistent session (<plugin>/plugin_host_debug/). The host is run with a private
#   XDG_CONFIG_HOME (inside <plugin>/plugin_host_debug/config) that holds its settings and,
#   on a PipeWire desktop, an ALSA override keeping the host's "default" device on real
#   hardware - opening PipeWire's ALSA PCM SIGSEGVs the host before it draws anything.
#   Env knobs:
#       PLUGIN_HOST_ALSA_CARD=<id|index>   pick the card (see /proc/asound/cards)
#       PLUGIN_HOST_PIPEWIRE=1             do not override; use the desktop default anyway
#   It is deliberately Linux-only — the container produces a Linux binary, so it can only be
#   run by a Linux host.
#
# CONFIGURATION
#   Machine-specific values come from config/local.env at the repo root (copy
#   config/local.env.example); an environment variable of the same name wins:
#       DEPLOY_HOST     ssh destination for -install, e.g. user@my-board.local
#       DEPLOY_LV2_DIR  LV2 folder on the target, relative to its home (default .lv2)
#
# RUNNING THE COMMANDS YOURSELF, INSIDE THE CONTAINER (optional)
#   tools/build-podman.sh loudness -shell
#   You land in /work/loudness (juce_plugins/ is bind-mounted at /work). The manual commands
#   are just what -setup / -build run, e.g.:
#       cmake -S . -B build-podman/plugin -G Ninja -DCMAKE_BUILD_TYPE=Release \
#             -DCMAKE_TOOLCHAIN_FILE=/work/tools/toolchain-aarch64-linux.cmake \
#             -DCMAKE_PREFIX_PATH=/work/build-podman/juce-install \
#             -DCMAKE_FIND_ROOT_PATH="/;/work/build-podman/juce-install"
#       cmake --build build-podman/plugin --target loudness_LV2 -j"$(nproc)"

set -euo pipefail

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
load_local_config

SELF="$0"
CONTAINERFILE="$TOOLS_DIR/Containerfile"
IMAGE="localhost/juce-plugins-arm64-builder:bookworm"

# juce_plugins/ is bind-mounted at /work, so the container sees every path below as /work/...
C_ROOT="/work"
C_TOOLCHAIN="$C_ROOT/tools/toolchain-aarch64-linux.cmake"
C_JUCE_SRC="$C_ROOT/JUCE"

# Shared by every plugin (gitignored via the build-*/ rule): JUCE is built once.
H_SHARED="$PLUGINS_DIR/build-podman"
C_JUCE_BUILD="$C_ROOT/build-podman/juce-build"
C_JUCE_INSTALL="$C_ROOT/build-podman/juce-install"

# The plugin host lives in its own folder next to the plugins. -setup-host builds it there;
# -debug-linux only looks for it and fails with instructions if it is missing.
HOST_APP_DIR="$PLUGINS_DIR/pluginHost"
C_HOSTAPP_BUILD="$C_ROOT/pluginHost/build"

# The host is built from a clean export of the pinned JUCE commit with every patch in
# juce_plugins/patches applied -- fixes to JUCE bugs that bite the host, kept out of the
# submodule itself. HOST_STAMP fingerprints that source (commit + patches), so -setup-host
# rebuilds a host that predates a patch, and -debug-linux can warn about one.
PATCH_DIR="$PLUGINS_DIR/patches"
HOST_JUCE_SRC="$HOST_APP_DIR/juce-src"
C_HOSTAPP_SRC="$C_ROOT/pluginHost/juce-src"
HOST_STAMP="$HOST_APP_DIR/.juce-source-fingerprint"

# JUCE's LV2 host builds its lilv world once at startup from HARDCODED paths and ignores
# $LV2_PATH, so a debug build has to land in one of them for the saved session to find it.
LOCAL_LV2_DIR="$HOME/.lv2"

# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------
usage() {
    cat <<EOF
Usage: $SELF [<plugin>|all] [steps...]

Plugins: $(list_plugins | tr '\n' ' ')

Global steps (no plugin needed):
  -install-podman  Install Podman on this host (Linux/macOS)
  -image           Build/refresh the shared builder container image
  -setup-juce      Build + install JUCE for the builds (one-time, shared by all plugins)
  -clean-juce      Remove the shared JUCE build/install
  -setup-host      Build the JUCE AudioPluginHost into juce_plugins/pluginHost
  -shell           Interactive shell in the builder container

Per-plugin steps (<plugin> or all):
  -setup           Configure the plugin CMake build (aarch64 cross)
  -build           Compile the LV2 plugin
  -install         Deploy the .lv2 to \$DEPLOY_HOST (config/local.env) over ssh
  -clean           Remove the plugin's build outputs (keeps the image and JUCE)
  -debug-linux     Debug-build for THIS Linux host and open it in the plugin host

With a plugin and no steps: -setup-juce -setup -build.
EOF
}

TARGET=""
if [ $# -gt 0 ] && [ "${1#-}" = "$1" ]; then
    TARGET="$1"
    shift
fi

DO_INSTALL_PODMAN=false; DO_IMAGE=false; DO_SETUP_JUCE=false; DO_CLEAN_JUCE=false
DO_SETUP_HOST=false; DO_SHELL=false
DO_SETUP=false; DO_BUILD=false; DO_INSTALL=false; DO_CLEAN=false; DO_DEBUG_LINUX=false

if [ $# -eq 0 ]; then
    if [ -z "$TARGET" ]; then
        usage
        exit 0
    fi
    # Default: produce the plugin locally (the image is auto-built on first use).
    # Deploy (-install) is intentionally NOT part of the default — it is explicit.
    DO_SETUP_JUCE=true; DO_SETUP=true; DO_BUILD=true
fi
while [ $# -gt 0 ]; do
    case "$1" in
        -install-podman) DO_INSTALL_PODMAN=true ;;
        -image)          DO_IMAGE=true ;;
        -setup-juce)     DO_SETUP_JUCE=true ;;
        -clean-juce)     DO_CLEAN_JUCE=true ;;
        -setup-host)     DO_SETUP_HOST=true ;;
        -shell)          DO_SHELL=true ;;
        -setup)          DO_SETUP=true ;;
        -build)          DO_BUILD=true ;;
        -install)        DO_INSTALL=true ;;
        -clean)          DO_CLEAN=true ;;
        -debug-linux)    DO_DEBUG_LINUX=true ;;
        -h|--help)       usage; exit 0 ;;
        *)
            echo "Unknown option: $1"
            echo "Use -h or --help for usage information"
            exit 1
            ;;
    esac
    shift
done

PLUGINS=()
if [ -n "$TARGET" ]; then
    mapfile -t PLUGINS < <(resolve_plugins "$TARGET")
    [ ${#PLUGINS[@]} -gt 0 ] || exit 1
fi

if { $DO_SETUP || $DO_BUILD || $DO_INSTALL || $DO_CLEAN || $DO_DEBUG_LINUX; } && [ -z "$TARGET" ]; then
    echo "Error: that step needs a plugin (or 'all'). Plugins: $(list_plugins | tr '\n' ' ')"
    exit 1
fi
if { $DO_DEBUG_LINUX || $DO_SHELL; } && [ ${#PLUGINS[@]} -gt 1 ]; then
    echo "Error: -debug-linux and -shell take a single plugin, not 'all'."
    exit 1
fi

# ---------------------------------------------------------------------------
# Per-plugin identity. Everything is derived from the plugin folder name, the one
# convention speaker_add_plugin() enforces: target = product = LV2 binary = URI tail.
# ---------------------------------------------------------------------------
select_plugin() {
    PLUGIN_NAME="$1"
    PLUGIN_LV2_URI="$(plugin_lv2_uri "$PLUGIN_NAME")"
    read -r PLUGIN_NUM_IN PLUGIN_NUM_OUT < <(plugin_channels "$PLUGIN_NAME")

    H_OUT="$PLUGINS_DIR/$PLUGIN_NAME/build-podman"      # host view (for -install / -clean)
    C_SRC="$C_ROOT/$PLUGIN_NAME"                        # container view of the plugin folder
    C_PLUGIN_BUILD="$C_SRC/build-podman/plugin"
    C_PLUGIN_BUILD_DEBUG="$C_SRC/build-podman/plugin-debug"

    # Persistent plugin-host session for this plugin. Created on the first -debug-linux run
    # with the plugin already wired in; after that it is left alone, so whatever you set up
    # in the host (extra plugins, connections, window positions) survives a rebuild.
    DEBUG_SESSION_DIR="$PLUGINS_DIR/$PLUGIN_NAME/plugin_host_debug"
    DEBUG_SESSION="$DEBUG_SESSION_DIR/$PLUGIN_NAME.filtergraph"

    # The plugin host is started with XDG_CONFIG_HOME pointed here, so the whole debug loop
    # is self-contained and ~/.config is left alone. Two things live in it:
    #   * alsa/asoundrc                     - keeps ALSA's "default" PCM off PipeWire, which
    #                                         crashes the host on open (see setup_host_config).
    #   * "Juce Audio Plugin Host.settings" - the host's own prefs (chosen audio device,
    #                                         scanned plugin list), written on a clean exit.
    HOST_CONFIG_HOME="$DEBUG_SESSION_DIR/config"
    HOST_ALSA_NOTE=""
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Run a bash script (fed on stdin) inside the builder container, with juce_plugins/
# bind-mounted at /work and $1 as the working directory. Files created land on the host;
# with rootless Podman the container's root maps to your user, so outputs are owned by you.
# (":z" relabels the mount for SELinux hosts such as Fedora; it is ignored elsewhere.)
in_container() {
    podman run --rm -i \
        -v "$PLUGINS_DIR":/work:z \
        -w "${1:-$C_ROOT}" \
        "$IMAGE" bash -euo pipefail
}

require_podman() {
    if ! command -v podman >/dev/null 2>&1; then
        echo "Error: podman is not installed. Run:  $SELF -install-podman"
        exit 1
    fi
    # On macOS/Windows a Linux VM ('podman machine') must be running for anything to work.
    if ! podman info >/dev/null 2>&1; then
        echo "Error: Podman is installed but not ready."
        echo "  On macOS/Windows start its Linux VM:  podman machine init && podman machine start"
        exit 1
    fi
}

build_image() {
    require_podman
    echo ">>> Building builder image $IMAGE (first time takes a few minutes)..."
    # Feed the Containerfile on stdin so no build context is uploaded (it has no COPY).
    podman build -t "$IMAGE" - < "$CONTAINERFILE"
    echo "Image ready."
    echo ""
}

ensure_image() {
    require_podman
    if ! podman image exists "$IMAGE"; then
        echo ">>> Builder image not found."
        build_image
    fi
}

require_juce_src() {
    if [ ! -f "$PLUGINS_DIR/JUCE/CMakeLists.txt" ]; then
        echo "Error: JUCE sources not found at $PLUGINS_DIR/JUCE"
        echo "  JUCE is a git submodule. Fetch it once from the repo root with:"
        echo "      git submodule update --init juce_plugins/JUCE"
        exit 1
    fi
}

juce_is_installed() {
    find "$H_SHARED/juce-install" -name JUCEConfig.cmake 2>/dev/null | grep -q .
}

# Native (no toolchain): builds a juceaide that RUNS on the host and is reused, unchanged,
# by the cross plugin builds and by the native debug builds. Shared by every plugin.
setup_juce() {
    ensure_image
    require_juce_src
    if juce_is_installed; then
        echo ">>> JUCE already installed at juce_plugins/build-podman/juce-install (-clean-juce to rebuild)."
    else
        echo ">>> Building + installing JUCE (host-native juceaide helper + CMake package)..."
        in_container <<EOF
cmake -S "$C_JUCE_SRC" -B "$C_JUCE_BUILD" -G Ninja \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX="$C_JUCE_INSTALL" \
      -DJUCE_BUILD_EXTRAS=OFF -DJUCE_BUILD_EXAMPLES=OFF
cmake --build "$C_JUCE_BUILD" --target install -j"\$(nproc)"
EOF
        echo "JUCE installed to juce_plugins/build-podman/juce-install"
    fi
    echo ""
}

# A build dir configured against another JUCE install (e.g. the per-plugin installs this
# script used to make) keeps that path cached in JUCE_DIR; such a cache is started fresh.
cache_is_stale() {
    local cache="$1/CMakeCache.txt"
    [ -f "$cache" ] && ! grep -q "^JUCE_DIR:PATH=$C_JUCE_INSTALL/" "$cache"
}

# --- local debugging helpers ------------------------------------------------

require_linux_host() {
    if [ "$(uname -s)" != "Linux" ]; then
        echo "Error: -debug-linux only works on a Linux host."
        echo "  The container produces a Linux binary; on $(uname -s) Podman runs inside its own"
        echo "  Linux VM, so neither the plugin nor the plugin host can run on your desktop."
        exit 1
    fi
}

# Path of the AudioPluginHost executable, or empty if it has not been built. Only the
# build dir is searched: the patched JUCE export next to it is source, not a host.
find_plugin_host() {
    [ -d "$HOST_APP_DIR/build" ] || return 0
    find "$HOST_APP_DIR/build" -type f -name AudioPluginHost -perm -u+x 2>/dev/null | head -1
}

# Fingerprint of what the host is built from: the pinned JUCE commit plus every patch.
host_source_fingerprint() {
    {
        git -C "$PLUGINS_DIR/JUCE" rev-parse HEAD 2>/dev/null
        for p in "$PATCH_DIR"/*.patch; do
            [ -f "$p" ] && sha256sum "$p" | cut -d' ' -f1
        done
    } | sha256sum | cut -d' ' -f1
}

host_is_current() {
    [ -n "$(find_plugin_host)" ] && [ "$(cat "$HOST_STAMP" 2>/dev/null)" = "$(host_source_fingerprint)" ]
}

# --- keeping the plugin host off PipeWire's ALSA PCM ------------------------
#
# On a PipeWire desktop the host SIGSEGVs while opening the audio device, before it draws
# anything. The fault is inside PipeWire's own spa-audioconvert (thread "alsa-pipewire"),
# reached from snd_pcm_prepare() inside JUCE's ALSAAudioIODevice::open; it happens with no
# session file and no plugin loaded, while plain aplay/arecord on the very same PCM are fine.
# Confirmed with PipeWire 1.6.2. See docs/juce-knowledge/hosting/audiopluginhost-linux.md.
#
# JUCE opens the PCM literally named "default" on startup (ALSAAudioIODeviceType::
# getDefaultDeviceIndex looks for exactly that id), and PipeWire's
# /etc/alsa/conf.d/99-pipewire-default.conf is what makes "default" a PipeWire PCM. So the
# host is started with "default" redefined to point at a real sound card.
#
# WHERE the override has to live: setting ALSA_CONFIG_PATH to a file that includes
# /usr/share/alsa/alsa.conf and then redefines pcm.!default does NOTHING, silently -
# alsa.conf's @hooks load /etc/alsa/conf.d/ *after* the including file's own text has been
# parsed, so 99-pipewire-default.conf overrides the override again. The LAST entry in that
# same @hooks list is "$XDG_CONFIG_HOME/alsa/asoundrc", so that is the one file that wins -
# hence the private XDG_CONFIG_HOME, which also keeps the host's settings out of ~/.config.

# Does ALSA's "default" PCM currently route to PipeWire?
alsa_default_is_pipewire() {
    local hits
    hits="$(grep -rlsi 'pcm\.!default' /etc/alsa/conf.d /usr/share/alsa/alsa.conf.d 2>/dev/null || true)"
    if [ -n "$hits" ]; then
        if printf '%s\n' "$hits" | xargs grep -lsiE 'type[[:space:]]+pipewire' >/dev/null 2>&1; then
            return 0
        fi
    fi
    # Fall back to asking ALSA itself (alsa-utils is not guaranteed to be installed).
    if command -v aplay >/dev/null 2>&1; then
        if LC_ALL=C aplay -L 2>/dev/null | sed -n '/^default$/{n;p;}' | grep -qi pipewire; then
            return 0
        fi
    fi
    return 1
}

# Echo "<playback PCM>|<capture PCM>" for the card the plugin host should use, read straight
# out of /proc/asound (always present on Linux, unlike alsa-utils). A card that can do BOTH
# directions wins, because the debug graph is audio in -> plugin -> audio out; failing that,
# the first card of each kind is used. PLUGIN_HOST_ALSA_CARD=<id|index> forces a card.
pick_alsa_hw_devices() {
    local want="${PLUGIN_HOST_ALSA_CARD:-}"
    local idx id pb cap
    local both_pb="" both_cap="" any_pb="" any_cap=""

    while read -r idx id; do
        [ -n "$id" ] || continue
        if [ -n "$want" ] && [ "$want" != "$id" ] && [ "$want" != "$idx" ]; then
            continue
        fi

        # /proc/asound/cardN/pcm<M>p and pcm<M>c exist per playback / capture device.
        pb="$(ls -d /proc/asound/card"$idx"/pcm*p 2>/dev/null | sed 's#.*/pcm##; s#p$##' | sort -n | head -1)"
        cap="$(ls -d /proc/asound/card"$idx"/pcm*c 2>/dev/null | sed 's#.*/pcm##; s#c$##' | sort -n | head -1)"

        if [ -n "$pb" ] && [ -z "$any_pb" ]; then
            any_pb="hw:CARD=$id,DEV=$pb"
        fi
        if [ -n "$cap" ] && [ -z "$any_cap" ]; then
            any_cap="hw:CARD=$id,DEV=$cap"
        fi
        if [ -n "$pb" ] && [ -n "$cap" ] && [ -z "$both_pb" ]; then
            both_pb="hw:CARD=$id,DEV=$pb"
            both_cap="hw:CARD=$id,DEV=$cap"
        fi
    done < <(sed -n 's/^ *\([0-9][0-9]*\) \[\([^]]*\)\].*/\1 \2/p' /proc/asound/cards 2>/dev/null)

    if [ -n "$both_pb" ]; then
        echo "$both_pb|$both_cap"
    else
        echo "$any_pb|$any_cap"
    fi
}

# Prepare $HOST_CONFIG_HOME for this run and describe what was done in $HOST_ALSA_NOTE.
#   PLUGIN_HOST_PIPEWIRE=1    use the desktop's ALSA default as-is (i.e. PipeWire). This is
#                             the configuration that crashes; it exists so the crash can be
#                             re-checked after a PipeWire update.
#   PLUGIN_HOST_NO_PIPEWIRE=1 force the hardware override even if "default" does not look
#                             like PipeWire.
#   PLUGIN_HOST_ALSA_CARD=..  pick the card by id (as in /proc/asound/cards) or index.
setup_host_config() {
    mkdir -p "$HOST_CONFIG_HOME/alsa"
    local rc="$HOST_CONFIG_HOME/alsa/asoundrc"
    HOST_ALSA_NOTE=""

    if [ "${PLUGIN_HOST_PIPEWIRE:-0}" = "1" ]; then
        rm -f "$rc"
        HOST_ALSA_NOTE="PLUGIN_HOST_PIPEWIRE=1 - using the desktop's ALSA default (PipeWire); this is the path that SIGSEGVs"
        return
    fi

    # Only step in when "default" really is PipeWire. On a plain-ALSA box the stock default
    # (dmix and friends) is the better device and is left alone.
    if [ "${PLUGIN_HOST_NO_PIPEWIRE:-0}" != "1" ] && ! alsa_default_is_pipewire; then
        rm -f "$rc"
        HOST_ALSA_NOTE="ALSA 'default' does not route to PipeWire - audio config left alone"
        return
    fi

    local devs pb cap card
    devs="$(pick_alsa_hw_devices)"
    pb="${devs%%|*}"
    cap="${devs##*|}"

    if [ -z "$pb" ] && [ -z "$cap" ]; then
        rm -f "$rc"
        HOST_ALSA_NOTE="no sound card found in /proc/asound - audio config left alone (the host may crash on PipeWire)"
        return
    fi

    card="${pb:-$cap}"
    card="${card#hw:CARD=}"
    card="${card%%,*}"

    cat > "$rc" <<ALSARC
# Written by $(basename "$SELF") -debug-linux - rewritten on every run, do not edit.
#
# ALSA loads "\$XDG_CONFIG_HOME/alsa/asoundrc" LAST of all its config files (the @hooks list
# at the top of /usr/share/alsa/alsa.conf), which is the only point at which a pcm.!default
# override beats PipeWire's /etc/alsa/conf.d/99-pipewire-default.conf. Only the plugin host
# reads it: it is started with XDG_CONFIG_HOME set to this folder, so nothing else on the
# desktop is affected and PipeWire keeps the rest of the machine's audio.
pcm.!default {
    type asym
    playback.pcm { type plug slave.pcm "${pb:-null}" }
    capture.pcm  { type plug slave.pcm "${cap:-null}" }
    hint { show on description "Default (direct ALSA - PipeWire bypassed for plugin debugging)" }
}
ctl.!default { type hw card "$card" }
ALSARC

    HOST_ALSA_NOTE="ALSA 'default' -> playback ${pb:-none}, capture ${cap:-none} (PipeWire bypassed)"
}

# Write a plugin-host session with this plugin already instantiated and wired
# between the audio input and output, and its editor set to open on load.
#
# The format is what PluginGraph::createXml() emits. Two details make this work:
#   * node IDs come straight from the FILTER "uid" attribute
#     (PluginGraph.cpp: graph.addNode (..., NodeID (xml.getIntAttribute ("uid")))),
#     so the CONNECTION uids below refer to the nodes declared here;
#   * the internal I/O nodes are matched by NAME alone
#     (InternalPlugins.cpp: name.equalsIgnoreCase (desc.name)), so "Audio Input" /
#     "Audio Output" with format="Internal" is enough — no need to reproduce JUCE's
#     uniqueId hash. For the LV2 node, "file" is the plugin URI, which is what
#     PluginDescription::fileOrIdentifier means for LV2.
# "uiopen_Normal" makes the host pop the plugin's own editor as soon as it loads.
write_debug_session() {
    mkdir -p "$DEBUG_SESSION_DIR"

    # Wire the plugin between the device input and output. A mono-out plugin is fanned to
    # both output channels so it is audible on both speakers; a plugin with 2+ outputs
    # (the crossover emits its two bands) maps its channels straight across instead.
    local conns="  <CONNECTION srcFilter=\"1\" srcChannel=\"0\" dstFilter=\"3\" dstChannel=\"0\"/>
  <CONNECTION srcFilter=\"3\" srcChannel=\"0\" dstFilter=\"2\" dstChannel=\"0\"/>"

    if [ "$PLUGIN_NUM_OUT" -ge 2 ]; then
        conns="$conns
  <CONNECTION srcFilter=\"3\" srcChannel=\"1\" dstFilter=\"2\" dstChannel=\"1\"/>"
    else
        conns="$conns
  <CONNECTION srcFilter=\"3\" srcChannel=\"0\" dstFilter=\"2\" dstChannel=\"1\"/>"
    fi

    cat > "$DEBUG_SESSION" <<XML
<?xml version="1.0" encoding="UTF-8"?>

<FILTERGRAPH>
  <FILTER uid="1" x="0.5" y="0.1">
    <PLUGIN name="Audio Input" descriptiveName="" format="Internal" category="I/O devices"
            manufacturer="JUCE" version="1.0" file="" isInstrument="0"
            numInputs="0" numOutputs="2" isShell="0" uid="0" uniqueId="0"/>
  </FILTER>
  <FILTER uid="2" x="0.5" y="0.9">
    <PLUGIN name="Audio Output" descriptiveName="" format="Internal" category="I/O devices"
            manufacturer="JUCE" version="1.0" file="" isInstrument="0"
            numInputs="2" numOutputs="0" isShell="0" uid="0" uniqueId="0"/>
  </FILTER>
  <FILTER uid="3" x="0.5" y="0.5" uiopen_Normal="1" uiLastX_Normal="200" uiLastY_Normal="120">
    <PLUGIN name="$PLUGIN_NAME" descriptiveName="" format="LV2" category=""
            manufacturer="Juce" version="0.0.1" file="$PLUGIN_LV2_URI" isInstrument="0"
            numInputs="$PLUGIN_NUM_IN" numOutputs="$PLUGIN_NUM_OUT" isShell="0" uid="0" uniqueId="0"/>
  </FILTER>
$conns
</FILTERGRAPH>
XML
}

install_podman() {
    if command -v podman >/dev/null 2>&1; then
        echo ">>> Podman already installed: $(podman --version)"
        return
    fi
    local os; os="$(uname -s)"
    echo ">>> Installing Podman for $os ..."
    case "$os" in
        Linux)
            if   command -v apt-get >/dev/null 2>&1; then sudo apt-get update && sudo apt-get install -y podman
            elif command -v dnf     >/dev/null 2>&1; then sudo dnf install -y podman
            elif command -v pacman  >/dev/null 2>&1; then sudo pacman -S --noconfirm podman
            elif command -v zypper  >/dev/null 2>&1; then sudo zypper install -y podman
            else echo "Could not detect a package manager. Please install 'podman' manually."; exit 1
            fi
            echo "Podman installed. On Linux it runs containers natively — no VM needed."
            ;;
        Darwin)
            if ! command -v brew >/dev/null 2>&1; then
                echo "Homebrew not found. Install it from https://brew.sh and re-run,"
                echo "or install Podman Desktop from https://podman.io."
                exit 1
            fi
            brew install podman
            echo ">>> Initialising the Podman Linux VM (one-time)..."
            podman machine init || true
            podman machine start
            ;;
        *)
            echo "Automatic install is not supported on '$os'."
            echo "  Windows: 'winget install RedHat.Podman' or Podman Desktop (https://podman.io),"
            echo "           then run:  podman machine init && podman machine start"
            exit 1
            ;;
    esac
    echo ""
}

# ---------------------------------------------------------------------------
# Per-plugin steps
# ---------------------------------------------------------------------------

step_clean() {
    echo ">>> Cleaning $PLUGIN_NAME build outputs..."
    # plugin/ and plugin-debug/ are this script's; juce-build/juce-install are leftovers of
    # the per-plugin JUCE installs used before JUCE was shared. Anything else you keep in
    # build-podman/ (e.g. a test harness) is left alone.
    rm -rf "$H_OUT/plugin" "$H_OUT/plugin-debug" "$H_OUT/juce-build" "$H_OUT/juce-install"
    echo "  Removed $H_OUT/{plugin,plugin-debug}"
    echo ""
}

step_setup() {
    ensure_image
    if ! juce_is_installed; then
        echo "Error: JUCE is not installed. Run:  $SELF -setup-juce   first."
        exit 1
    fi
    echo ">>> Configuring $PLUGIN_NAME (aarch64 cross-compile)..."
    # PKG_CONFIG_LIBDIR points pkg-config at the :arm64 .pc files so JUCE resolves the
    # target's libraries. CMAKE_FIND_ROOT_PATH="/" lets the toolchain's strict find
    # modes still see the whole (multi-arch) container root + the JUCE install.
    # CMAKE_CROSSCOMPILING_EMULATOR lets CMake run the ARM64 LV2/VST3 manifest helpers
    # that JUCE builds and invokes post-build (see Containerfile) under qemu.
    local fresh=""
    if cache_is_stale "$H_OUT/plugin"; then fresh="--fresh"; fi
    in_container "$C_SRC" <<EOF
export PKG_CONFIG_LIBDIR=/usr/lib/aarch64-linux-gnu/pkgconfig:/usr/share/pkgconfig
cmake $fresh -S "$C_SRC" -B "$C_PLUGIN_BUILD" -G Ninja \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_TOOLCHAIN_FILE="$C_TOOLCHAIN" \
      -DCMAKE_PREFIX_PATH="$C_JUCE_INSTALL" \
      -DCMAKE_FIND_ROOT_PATH="/;$C_JUCE_INSTALL" \
      -DCMAKE_CROSSCOMPILING_EMULATOR=/usr/bin/qemu-aarch64-static
EOF
    echo "Configured."
    echo ""
}

step_build() {
    ensure_image
    if [ ! -f "$H_OUT/plugin/CMakeCache.txt" ] || cache_is_stale "$H_OUT/plugin"; then
        echo "Error: $PLUGIN_NAME is not configured (for the shared JUCE). Run:  $SELF $PLUGIN_NAME -setup"
        exit 1
    fi
    echo ">>> Building $PLUGIN_NAME (LV2, aarch64 Release)..."
    in_container "$C_SRC" <<EOF
export PKG_CONFIG_LIBDIR=/usr/lib/aarch64-linux-gnu/pkgconfig:/usr/share/pkgconfig
cmake --build "$C_PLUGIN_BUILD" --target ${PLUGIN_NAME}_LV2 -j"\$(nproc)"
EOF
    echo "Build complete."
    echo "  Plugin: juce_plugins/$PLUGIN_NAME/build-podman/plugin/${PLUGIN_NAME}_artefacts/Release/LV2/$PLUGIN_NAME.lv2"
    echo ""
}

step_install() {
    # Runs on the HOST: the artefact is on the bind-mounted output dir, and ssh keys
    # live on the host, so there's no reason to deploy from inside the container.
    local lv2="$H_OUT/plugin/${PLUGIN_NAME}_artefacts/Release/LV2/$PLUGIN_NAME.lv2"
    if [ ! -d "$lv2" ]; then
        echo "Error: $lv2 not found. Run:  $SELF $PLUGIN_NAME -build   first."
        exit 1
    fi
    echo ">>> Deploying $PLUGIN_NAME to $DEPLOY_HOST ..."
    # Stream the bundle with tar over ssh instead of `scp -r`: newer OpenSSH scp (SFTP mode)
    # fails to recursively upload a directory into an existing remote dir ("scp: stat remote:
    # No such file or directory"). tar works on every ssh version.
    if tar -C "$(dirname "$lv2")" -cf - "$(basename "$lv2")" \
         | ssh "$DEPLOY_HOST" "tar -C '$DEPLOY_LV2_DIR' -xf -"; then
        echo "Deployed to $DEPLOY_HOST:~/$DEPLOY_LV2_DIR/$PLUGIN_NAME.lv2 (a default LV2 path — no rescan needed)"
    else
        echo ""
        echo "!!! -install FAILED to write into '~/$DEPLOY_LV2_DIR' on $DEPLOY_HOST."
        echo "    Most likely a PERMISSION issue: '~/$DEPLOY_LV2_DIR' is a default LV2 search path but is"
        echo "    commonly root-owned (it may already hold a system-installed lsp-plugins.lv2), so your"
        echo "    user can't write bundles into it (tar reports 'Cannot mkdir: Permission denied')."
        echo "    Take ownership once (needs your sudo password), then re-run with -install:"
        echo "        ssh -t $DEPLOY_HOST 'sudo chown -R \$(id -un):\$(id -gn) ~/$DEPLOY_LV2_DIR'"
        exit 1
    fi
    echo ""
}

# Checked once per run, before any plugin is deployed.
check_deploy_target() {
    require_deploy_host
    # Require the remote LV2 dir to already exist AND be writable by you — do NOT create it
    # or chown it here (avoid needing root during deploy). Fail with a precise message.
    if ! ssh "$DEPLOY_HOST" "test -d '$DEPLOY_LV2_DIR'"; then
        echo "Error: remote directory '~/$DEPLOY_LV2_DIR' does not exist on $DEPLOY_HOST."
        echo "  Create it on the target first (it's a default LV2 search path):"
        echo "      ssh $DEPLOY_HOST 'mkdir -p ~/$DEPLOY_LV2_DIR'"
        exit 1
    fi
    if ! ssh "$DEPLOY_HOST" "test -w '$DEPLOY_LV2_DIR'"; then
        echo "Error: remote '~/$DEPLOY_LV2_DIR' on $DEPLOY_HOST is not writable by you (likely root-owned)."
        echo "  It's a default LV2 search path the host scans, so take ownership once (needs your sudo password):"
        echo "      ssh -t $DEPLOY_HOST 'sudo chown -R \$(id -un):\$(id -gn) ~/$DEPLOY_LV2_DIR'"
        exit 1
    fi
}

step_debug_linux() {
    require_linux_host
    ensure_image
    require_juce_src

    # Fail before doing any work if the plugin host is missing — it is assumed to exist.
    local host_bin
    host_bin="$(find_plugin_host)"
    if [ -z "$host_bin" ]; then
        echo "Error: no AudioPluginHost executable found under $HOST_APP_DIR"
        echo "  The debug flow expects the JUCE plugin host to live in its own folder next to"
        echo "  the plugins (juce_plugins/pluginHost). Build it once with:"
        echo "      $SELF -setup-host"
        exit 1
    fi

    if ! host_is_current; then
        echo "Warning: this AudioPluginHost was built without the current juce_plugins/patches."
        echo "  Among other things the plugin window can then be maximised by the window manager,"
        echo "  which stretches a fixed-size editor across the screen. Rebuild it with:"
        echo "      $SELF -setup-host"
        echo ""
    fi

    if ! juce_is_installed; then
        echo ">>> JUCE is not installed yet — running -setup-juce first..."
        setup_juce
    fi

    # Native x86_64 Debug build, in its own dir so it never clobbers the aarch64 Release
    # one. No toolchain file => the container's native compiler. The JUCE install is
    # reused as-is: it is a source distribution plus a host-native juceaide, so the same
    # install serves both the cross build and this one.
    if [ ! -f "$H_OUT/plugin-debug/CMakeCache.txt" ] || cache_is_stale "$H_OUT/plugin-debug"; then
        echo ">>> Configuring the native Debug build..."
        in_container "$C_SRC" <<EOF
cmake --fresh -S "$C_SRC" -B "$C_PLUGIN_BUILD_DEBUG" -G Ninja \
      -DCMAKE_BUILD_TYPE=Debug \
      -DCMAKE_PREFIX_PATH="$C_JUCE_INSTALL"
EOF
    fi

    echo ">>> Building $PLUGIN_NAME (native x86_64, Debug)..."
    in_container "$C_SRC" <<EOF
cmake --build "$C_PLUGIN_BUILD_DEBUG" --target ${PLUGIN_NAME}_LV2 -j"\$(nproc)"
EOF

    local lv2_debug
    lv2_debug="$(find "$H_OUT/plugin-debug" -maxdepth 5 -type d -name "$PLUGIN_NAME.lv2" 2>/dev/null | head -1)"
    if [ -z "$lv2_debug" ]; then
        echo "Error: the Debug build did not produce $PLUGIN_NAME.lv2 under $H_OUT/plugin-debug"
        exit 1
    fi

    # Install into ~/.lv2 on THIS machine. The plugin host rebuilds its LV2 world from the
    # default paths at startup and ignores $LV2_PATH, so pointing it at the build dir would
    # not work — the bundle has to actually be in one of those directories.
    mkdir -p "$LOCAL_LV2_DIR"
    rm -rf "${LOCAL_LV2_DIR:?}/$PLUGIN_NAME.lv2"
    cp -a "$lv2_debug" "$LOCAL_LV2_DIR/"
    echo "Installed debug build to $LOCAL_LV2_DIR/$PLUGIN_NAME.lv2"

    if [ -f "$DEBUG_SESSION" ]; then
        echo "Reusing existing plugin-host session: $DEBUG_SESSION"
    else
        write_debug_session
        echo "Created plugin-host session with $PLUGIN_NAME loaded: $DEBUG_SESSION"
    fi

    # Audio device: give the host a private XDG_CONFIG_HOME holding an ALSA override that
    # keeps "default" off PipeWire's crashing PCM (see setup_host_config above), plus its own
    # settings file, so the debug loop is reproducible and self-contained.
    setup_host_config
    if [ -n "$HOST_ALSA_NOTE" ]; then
        echo "Audio: $HOST_ALSA_NOTE"
    fi

    echo ""
    echo ">>> Opening the plugin host. Close its window (or press Ctrl-C) to return."
    echo "    Anything you change and SAVE in the host persists in the session file above."
    echo "    Its own settings (audio device, plugin list) live in $HOST_CONFIG_HOME."
    echo ""

    # Foreground on purpose: this is a Debug build, so DBG()/assertion output goes to this
    # terminal, which is the whole point of the step.
    local host_rc=0
    XDG_CONFIG_HOME="$HOST_CONFIG_HOME" "$host_bin" "$DEBUG_SESSION" || host_rc=$?

    if [ "$host_rc" -eq 139 ]; then
        echo ""
        echo "!!! The plugin host crashed (SIGSEGV) while opening the audio device."
        echo "    This is NOT your plugin: the fault is inside PipeWire's own ALSA path"
        echo "    (libspa-audioconvert, on a thread named 'alsa-pipewire'), reached from"
        echo "    snd_pcm_prepare() in ALSAAudioIODevice::open. It happens identically with no"
        echo "    session file and no plugin loaded, and plain aplay/arecord on the same device"
        echo "    are fine, so it is that specific open and not the PipeWire bridge as a whole."
        if [ "${PLUGIN_HOST_PIPEWIRE:-0}" = "1" ]; then
            echo "    You asked for PipeWire with PLUGIN_HOST_PIPEWIRE=1. Drop it and re-run:"
            echo "        $SELF $PLUGIN_NAME -debug-linux"
        else
            echo "    ALSA's 'default' was already redirected to hardware for this run:"
            echo "        $HOST_CONFIG_HOME/alsa/asoundrc"
            echo "    so something still reached PipeWire. Check that file resolves the way you"
            echo "    expect (XDG_CONFIG_HOME=$HOST_CONFIG_HOME aplay -L), and try another card:"
            echo "        PLUGIN_HOST_ALSA_CARD=<id or index from /proc/asound/cards> $SELF $PLUGIN_NAME -debug-linux"
            echo "    Also do NOT pick 'pipewire' in Options > Audio Settings - it is the same"
            echo "    crashing path, whatever 'default' points at."
        fi
        exit 1
    fi
    echo ""
}

# ---------------------------------------------------------------------------
# Global steps, then per-plugin steps in a fixed order
# ---------------------------------------------------------------------------

echo "=== JUCE plugin build via Podman (target: Debian ARM64)${TARGET:+ — $TARGET} ==="
echo ""

if $DO_INSTALL_PODMAN; then
    install_podman
fi

if $DO_CLEAN_JUCE; then
    echo ">>> Removing the shared JUCE build..."
    rm -rf "$H_SHARED"
    echo "  Removed $H_SHARED"
    echo "  (builder image kept; remove it with:  podman image rm $IMAGE)"
    echo ""
fi

if $DO_IMAGE; then
    build_image
fi

if $DO_SHELL; then
    ensure_image
    workdir="$C_ROOT"
    [ ${#PLUGINS[@]} -eq 1 ] && workdir="$C_ROOT/${PLUGINS[0]}"
    echo ">>> Opening a shell in the builder container. juce_plugins/ is at /work; you are in"
    echo "    $workdir. Run cmake by hand or type 'exit' to leave."
    echo ""
    podman run --rm -it -v "$PLUGINS_DIR":/work:z -w "$workdir" "$IMAGE" bash
    exit 0
fi

if $DO_SETUP_JUCE; then
    setup_juce
fi

if $DO_SETUP_HOST; then
    ensure_image
    require_juce_src
    if host_is_current; then
        echo ">>> AudioPluginHost already built (with juce_plugins/patches) at $(find_plugin_host)"
        echo "    (delete $HOST_APP_DIR to rebuild it)"
    else
        if [ -n "$(find_plugin_host)" ]; then
            echo ">>> The AudioPluginHost predates the current juce_plugins/patches -- rebuilding it"
        fi
        echo ">>> Building the JUCE AudioPluginHost into juce_plugins/pluginHost ..."
        echo "    (a few minutes -- it is shared by every plugin in this repo)"

        # Export the pinned commit (git archive: tracked files only, no .git) and patch the
        # copy. Done on the host because the submodule's .git points outside the /work
        # bind mount, so git cannot read it from inside the container.
        rm -rf "$HOST_JUCE_SRC" "$HOST_APP_DIR/build" "$HOST_STAMP"
        mkdir -p "$HOST_JUCE_SRC"
        git -C "$PLUGINS_DIR/JUCE" archive HEAD | tar -x -C "$HOST_JUCE_SRC"

        for p in "$PATCH_DIR"/*.patch; do
            [ -f "$p" ] || continue
            echo "    applying $(basename "$p")"
            patch -d "$HOST_JUCE_SRC" -p1 --forward --quiet < "$p"
        done

        # Built in the same container as the plugins so both link against the same
        # library versions. JUCE_BUILD_EXTRAS=ON is what adds the AudioPluginHost target;
        # it already sets JUCE_PLUGINHOST_LV2=1 in its own CMakeLists.
        in_container <<EOF
cmake -S "$C_HOSTAPP_SRC" -B "$C_HOSTAPP_BUILD" -G Ninja \
      -DCMAKE_BUILD_TYPE=Release \
      -DJUCE_BUILD_EXTRAS=ON -DJUCE_BUILD_EXAMPLES=OFF
cmake --build "$C_HOSTAPP_BUILD" --target AudioPluginHost -j"\$(nproc)"
EOF
        host_source_fingerprint > "$HOST_STAMP"
        echo "AudioPluginHost built: $(find_plugin_host)"
    fi
    echo ""
fi

if $DO_INSTALL; then
    check_deploy_target
fi

for p in ${PLUGINS[@]+"${PLUGINS[@]}"}; do
    select_plugin "$p"
    if $DO_CLEAN;       then step_clean; fi
    if $DO_SETUP;       then step_setup; fi
    if $DO_BUILD;       then step_build; fi
    if $DO_INSTALL;     then step_install; fi
    if $DO_DEBUG_LINUX; then step_debug_linux; fi
done

echo "=== Done ==="
