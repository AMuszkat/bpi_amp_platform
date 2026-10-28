#!/usr/bin/env bash
#
# snapcast-setup.sh
#
# Set up Snapcast synchronized audio streaming over the network on top of
# PipeWire. Snapcast gives tight (~1 ms) inter-client sync at the cost of a
# buffered end-to-end latency -- the right trade-off for a synchronized
# multi-room / stereo-pair setup (e.g. two BananaPi boards as L and R).
#
#   host   (server)   : runs `snapserver`, which captures audio natively as a
#                       PipeWire client (source = pipewire://) -- more direct
#                       than a FIFO, no loopback. A virtual sink "Snapcast
#                       (multi-room)" is created; route audio to it and the
#                       server streams it. The host always uses PipeWire.
#   target (client)   : a board running `snapclient` with its NATIVE PipeWire
#                       player (--player pipewire), so it appears as a node
#                       ("Snapcast") in the board's PipeWire graph. Its
#                       client-rt.conf routes that node (--route): into the
#                       speakerd sink (speakerd, the remote plugin host), or into
#                       Ap.cap for an AudioPluginHost (plugin-host, the default).
#                       If the board's packaged snapclient is too old for
#                       --player pipewire (e.g. 0.26.0), a newer release is
#                       installed automatically.
#
# Both server and client run as systemd *user* services so they share the
# session's PipeWire instance (that is what the native sources/players need;
# the packaged system services can't reach /run/user/UID). The server writes a
# managed /etc/snapserver.conf (backed up once); snapserver's pipewire source makes
# its own single capture sink. The client writes a per-user snapclient.service.
#
# Usage:
#   ./snapcast-setup.sh --mode host           [options]   # configure THIS machine as the server
#   ./snapcast-setup.sh --mode target         [options]   # configure THIS machine as a client (--server-ip required)
#   ./snapcast-setup.sh --mode target-remote  [options]   # ssh to a board, configure a client there
#   ./snapcast-setup.sh --mode both           [options]   # server here + ssh to board for a client
#   ./snapcast-setup.sh --mode target --routing-only --route speakerd|plugin-host
#                                                         # only re-route snapclient on this board
#
# Run `./snapcast-setup.sh --help` for the full option list.

set -euo pipefail

# ----------------------------------------------------------------------------
# Defaults
# ----------------------------------------------------------------------------
MODE=""
TARGET_HOST="root@${BOARD_HOST:-bpi-amp.local}"  # ssh destination of the board; set BOARD_HOST (for target-remote/both)
SERVER_IP=""                             # address clients connect to; auto-detected for target-remote/both
CLIENT_USER=""                           # board user whose PipeWire session runs snapclient (auto; else $BOARD_USER)
CLIENT_BUFFER_TIME=""                    # snapclient pipewire buffer_time ms (empty => let PipeWire decide)
CLIENT_LATENCY=0                         # snapclient fine-tune latency in ms (per-client trim)
SNAPCLIENT_VERSION="0.34.0"              # version to install on the board if its snapclient lacks the pipewire player

# Server stream parameters
SAMPLEFORMAT="48000:32:2"                # rate:bits:channels of the snapserver stream
CODEC="pcm"                              # flac | pcm | opus | ogg  (pcm = lowest latency, most bandwidth; required for 32-bit)
BUFFER_MS=1000                           # snapserver total buffer; lower = less latency, more dropout risk
CHUNK_MS=20                              # read chunk size
SOURCE_NAME="multi-room"                 # snapserver stream name (sink shows as "Snapcast/<name>")

# Server ports (Snapcast defaults)
STREAM_PORT=1704
CONTROL_PORT=1705
HTTP_PORT=1780

SCOPE="auto"                             # auto | system | user  (where the PipeWire fragment goes)
INSTALL_DEPS=1
OPEN_FIREWALL=0
DO_RESTART=1
HOST_QUANTUM=1                           # host: write ~/.config/pipewire/pipewire.conf.d/10-quantum.conf
TARGET_CLIENT_RT=1                        # target: deploy ~/.config/pipewire/client-rt.conf (auto-routing rules) + point snapclient at it
ROUTE="plugin-host"                      # target: where client-rt.conf sends snapclient: speakerd | plugin-host
ROUTING_ONLY=0                           # target: only rewrite client-rt.conf and restart snapclient
CLIENT_HOME_OVERRIDE=""                  # target: write into this folder as the user's home (tests: no root, no services)
DRY_RUN=0

SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"

# ----------------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------------
c_blue=$'\033[1;34m'; c_yellow=$'\033[1;33m'; c_red=$'\033[1;31m'; c_green=$'\033[1;32m'; c_off=$'\033[0m'
info()  { printf '%s==>%s %s\n' "$c_blue"   "$c_off" "$*"; }
ok()    { printf '%s ok%s %s\n' "$c_green"  "$c_off" "$*"; }
warn()  { printf '%swarn%s %s\n' "$c_yellow" "$c_off" "$*" >&2; }
die()   { printf '%serr%s  %s\n' "$c_red"    "$c_off" "$*" >&2; exit 1; }

usage() {
    # Print the leading comment block (everything after the shebang up to the first non-comment line).
    awk 'NR>1 { if ($0 ~ /^#/) { sub(/^# ?/, ""); print } else exit }' "$SELF"
    cat <<EOF

Options:
  --mode <host|target|target-remote|both>
                              Role to configure (required):
                                host          - configure THIS machine as the snapserver
                                target        - configure THIS machine as a snapclient (needs --server-ip)
                                target-remote - ssh to the board and configure a client there
                                both          - server here + client on the board over ssh
  --target-host <[user@]host> SSH destination of the board (default: $TARGET_HOST). Used by target-remote/both.
                              If given as user@host, that user is also used as --client-user (the board's
                              PipeWire/audio user) unless --client-user is set explicitly.
  --server-ip <ip>            Address clients connect to (default: auto-detected host IP for remote/both modes).
  --client-user <name>        Board user whose PipeWire session runs snapclient (default: auto; else \$BOARD_USER).
  --snapclient-version <v>    Version to install on the board if its snapclient lacks the pipewire player (default: $SNAPCLIENT_VERSION).
  --client-buffer-time <ms>   snapclient PipeWire buffer_time (default: PipeWire decides).
  --client-latency <ms>       Per-client latency trim passed to snapclient (default: $CLIENT_LATENCY).
  --codec <flac|pcm|opus|ogg> Stream codec; pcm = lowest latency (default: $CODEC).
  --buffer <ms>               Server buffer; lower = less latency, more dropout risk (default: $BUFFER_MS).
  --chunk-ms <ms>             Server read chunk size (default: $CHUNK_MS).
  --sampleformat <r:b:c>      Stream rate:bits:channels (default: $SAMPLEFORMAT).
  --scope <auto|system|user>  Where the host's PipeWire fragment goes (default: auto).
  --no-quantum                Host only: do NOT write the PipeWire quantum/rate drop-in
                              (~/.config/pipewire/pipewire.conf.d/10-quantum.conf).
  --no-client-rt              Target only: do NOT deploy the client-rt.conf auto-routing config
                              (~/.config/pipewire/client-rt.conf + snapclient PIPEWIRE_CONFIG_NAME).
  --route <speakerd|plugin-host>
                              Target only: where client-rt.conf sends snapclient (default: $ROUTE).
                                speakerd    - into the speakerd sink: speakerd, the remote plugin host,
                                              plays it. Refused unless the board's PipeWire config defines
                                              that sink (firmware/speakerd-setup.sh sink): a missing target
                                              sends snapclient to the amplifier, full range.
                                plugin-host - into Ap.cap, for an AudioPluginHost (its output to the amp).
  --routing-only              Target only: just rewrite client-rt.conf for --route and restart snapclient
                              (no install, no --server-ip needed).
  --client-home <dir>         Target only, for tests: write into <dir> as if it were the user's home
                              (no root, no chown, no services).
  --no-install                Skip dependency installation.
  --open-firewall             Open server TCP ports ($STREAM_PORT,$CONTROL_PORT,$HTTP_PORT) with ufw.
  --no-restart                Do not (re)start services after writing config.
  --dry-run                   Print actions and config without applying them.
  -h, -help, --help           Show this help.

Examples:
  # Server on the host:
  ./snapcast-setup.sh --mode host

  # Client on the BananaPi, driven from the host over ssh. user@host sets
  # both the ssh login and the board's PipeWire/audio user. Installs a newer
  # snapclient if the board's one lacks the pipewire player:
  ./snapcast-setup.sh --mode target-remote --target-host user@bpi-amp.local

  # Client configured locally on a board (you supply the server's IP):
  sudo ./snapcast-setup.sh --mode target --server-ip 192.0.2.10

  # Only re-route an installed client into speakerd (the speakerd sink must be configured):
  ./snapcast-setup.sh --mode target --routing-only --route speakerd
EOF
}

# ----------------------------------------------------------------------------
# Argument parsing
# ----------------------------------------------------------------------------
while [ $# -gt 0 ]; do
    case "$1" in
        --mode)            MODE="${2:?}"; shift 2 ;;
        --target-host)     TARGET_HOST="${2:?}"; shift 2 ;;
        --server-ip)       SERVER_IP="${2:?}"; shift 2 ;;
        --client-user)     CLIENT_USER="${2:?}"; shift 2 ;;
        --snapclient-version) SNAPCLIENT_VERSION="${2:?}"; shift 2 ;;
        --client-buffer-time) CLIENT_BUFFER_TIME="${2:?}"; shift 2 ;;
        --client-latency)  CLIENT_LATENCY="${2:?}"; shift 2 ;;
        --codec)           CODEC="${2:?}"; shift 2 ;;
        --buffer)          BUFFER_MS="${2:?}"; shift 2 ;;
        --chunk-ms)        CHUNK_MS="${2:?}"; shift 2 ;;
        --sampleformat)    SAMPLEFORMAT="${2:?}"; shift 2 ;;
        --scope)           SCOPE="${2:?}"; shift 2 ;;
        --no-install)      INSTALL_DEPS=0; shift ;;
        --open-firewall)   OPEN_FIREWALL=1; shift ;;
        --no-restart)      DO_RESTART=0; shift ;;
        --no-quantum)      HOST_QUANTUM=0; shift ;;
        --no-client-rt)    TARGET_CLIENT_RT=0; shift ;;
        --route)           ROUTE="${2:?}"; shift 2 ;;
        --routing-only)    ROUTING_ONLY=1; shift ;;
        --client-home)     CLIENT_HOME_OVERRIDE="${2:?}"; shift 2 ;;
        --dry-run)         DRY_RUN=1; shift ;;
        -h|-help|--help)   usage; exit 0 ;;
        *)                 die "Unknown option: $1 (try --help)" ;;
    esac
done

[ -n "$MODE" ] || { usage; die "--mode is required"; }
case "$MODE" in host|target|target-remote|both) ;; *) die "--mode must be host, target, target-remote or both" ;; esac
case "$ROUTE" in speakerd|plugin-host) ;; *) die "--route must be speakerd or plugin-host" ;; esac
# Without client-rt.conf (and the unit's PIPEWIRE_CONFIG_NAME) nothing routes snapclient: it
# plays into the default sink, which a wpctl set-default may have made the amplifier.
if [ "$ROUTE" = speakerd ] && [ "$TARGET_CLIENT_RT" -eq 0 ]; then
    die "--route speakerd needs client-rt.conf: not with --no-client-rt"
fi


# ----------------------------------------------------------------------------
# Privilege / scope helpers
# ----------------------------------------------------------------------------
SUDO=""
if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then SUDO="sudo"; fi
run_root() { if [ -n "$SUDO" ]; then $SUDO "$@"; else "$@"; fi; }

resolve_scope() {
    if [ "$SCOPE" = "auto" ]; then
        if [ "$(id -u)" -eq 0 ]; then SCOPE="system"; else SCOPE="user"; fi
    fi
}
confd_dir() {
    if [ "$SCOPE" = "system" ]; then echo "/etc/pipewire/pipewire.conf.d"
    else echo "${XDG_CONFIG_HOME:-$HOME/.config}/pipewire/pipewire.conf.d"; fi
}

detect_host_ip() {
    local ip
    ip="$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}')"
    [ -z "$ip" ] && ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
    echo "$ip"
}

# --- Board PipeWire session helpers (snapclient runs as a systemd *user* service) ---
CLIENT_UID=""; CLIENT_HOME=""
resolve_client_user() {
    if [ -n "$CLIENT_HOME_OVERRIDE" ]; then
        mkdir -p "$CLIENT_HOME_OVERRIDE"
        CLIENT_USER="$(id -un)"; CLIENT_UID="$(id -u)"; CLIENT_HOME="$(cd "$CLIENT_HOME_OVERRIDE" && pwd)"
        return 0
    fi
    if [ -z "$CLIENT_USER" ]; then
        if [ "$(id -u)" -ne 0 ]; then CLIENT_USER="$(id -un)"
        elif [ -n "${SUDO_USER:-}" ]; then CLIENT_USER="$SUDO_USER"
        elif [ -n "${BOARD_USER:-}" ]; then CLIENT_USER="$BOARD_USER"
        else die "Running as root: pass --client-user <name> (or set BOARD_USER) for the board's audio user"; fi
    fi
    CLIENT_UID="$(id -u "$CLIENT_USER" 2>/dev/null || true)"
    CLIENT_HOME="$(getent passwd "$CLIENT_USER" 2>/dev/null | cut -d: -f6)"
    [ -n "$CLIENT_UID" ] && [ -n "$CLIENT_HOME" ] || die "User '$CLIENT_USER' not found on this machine (set --client-user)"
}

# Run a command inside CLIENT_USER's user-session environment (so systemctl --user works).
as_user() {
    if [ "$(id -un)" = "$CLIENT_USER" ]; then XDG_RUNTIME_DIR="/run/user/$CLIENT_UID" "$@"
    elif [ "$(id -u)" -eq 0 ]; then runuser -u "$CLIENT_USER" -- env "XDG_RUNTIME_DIR=/run/user/$CLIENT_UID" "$@"
    else sudo -u "$CLIENT_USER" env "XDG_RUNTIME_DIR=/run/user/$CLIENT_UID" "$@"; fi
}

# Write a file under CLIENT_HOME, owned by CLIENT_USER.
write_user_file() {  # $1 = path relative to CLIENT_HOME   $2 = content
    local rel="$1" content="$2" path="$CLIENT_HOME/$1"
    if [ "$DRY_RUN" -eq 1 ]; then
        info "(dry-run) would write $path (owner $CLIENT_USER):"; printf '%s\n' "$content" | sed 's/^/    /'; return 0
    fi
    # Whole or not at all: a temporary file beside it, then a rename (a reader, or a power cut,
    # never sees half a routing).
    local tmp="$path.tmp.$$"
    # Into the test's folder, or into our own home: no root needed.
    if [ -n "$CLIENT_HOME_OVERRIDE" ] || { [ "$(id -u)" -ne 0 ] && [ "$(id -un)" = "$CLIENT_USER" ]; }; then
        mkdir -p "$(dirname "$path")"
        { printf '%s\n' "$content" > "$tmp" && mv -f "$tmp" "$path"; } || { rm -f "$tmp"; die "cannot write $path"; }
        ok "Wrote $path"; return 0
    fi
    run_root mkdir -p "$(dirname "$path")"
    { printf '%s\n' "$content" | run_root tee "$tmp" >/dev/null && run_root mv -f "$tmp" "$path"; } \
        || { run_root rm -f "$tmp"; die "cannot write $path"; }
    run_root chown -R "$CLIENT_USER:$CLIENT_USER" "$CLIENT_HOME/$(echo "$rel" | cut -d/ -f1)"
    ok "Wrote $path"
}

# ----------------------------------------------------------------------------
# Dependency installation
# ----------------------------------------------------------------------------
install_pkgs() {
    [ "$INSTALL_DEPS" -eq 1 ] || { info "Skipping dependency install (--no-install)"; return 0; }
    local pkgs="$*"
    if command -v apt-get >/dev/null 2>&1; then
        info "Installing via apt: $pkgs"
        [ "$DRY_RUN" -eq 1 ] && { echo "  (dry-run) apt-get install -y $pkgs"; return 0; }
        run_root apt-get update -y || warn "apt-get update failed; continuing"
        # shellcheck disable=SC2086  # one word per package
        run_root apt-get install -y $pkgs || warn "apt install of '$pkgs' incomplete"
    elif command -v dnf >/dev/null 2>&1; then
        info "Installing via dnf: $pkgs"
        [ "$DRY_RUN" -eq 1 ] && { echo "  (dry-run) dnf install -y $pkgs"; return 0; }
        # shellcheck disable=SC2086  # one word per package
        run_root dnf install -y $pkgs || warn "dnf install incomplete"
    elif command -v pacman >/dev/null 2>&1; then
        info "Installing via pacman: snapcast"
        [ "$DRY_RUN" -eq 1 ] && { echo "  (dry-run) pacman -S --noconfirm snapcast"; return 0; }
        run_root pacman -S --needed --noconfirm snapcast || warn "pacman install incomplete"
    else
        warn "No supported package manager found. Install Snapcast manually: $pkgs"
    fi
}

# True if the installed snapclient has the native PipeWire player backend.
snapclient_has_pipewire() {
    command -v snapclient >/dev/null 2>&1 || return 1
    strings "$(command -v snapclient)" 2>/dev/null | grep -q 'pipewire_player'
}

# Distro snapclient may be too old for --player pipewire (e.g. 0.26.0 on bullseye).
# If so, install a newer official release that includes the PipeWire player.
ensure_snapclient_with_pipewire() {
    if snapclient_has_pipewire; then
        ok "snapclient already supports --player pipewire ($(snapclient --version 2>/dev/null | awk '{print $NF}'))"
        return 0
    fi
    if [ "$INSTALL_DEPS" -eq 0 ]; then
        warn "Current snapclient lacks the pipewire player and --no-install was given; --player pipewire will fail."
        return 0
    fi
    local codename arch url tmp deb
    codename="$( . /etc/os-release 2>/dev/null; echo "${VERSION_CODENAME:-}" )"
    arch="$(dpkg --print-architecture 2>/dev/null)"
    info "Installing snapclient v$SNAPCLIENT_VERSION ($arch/$codename) with PipeWire player from GitHub"
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "  (dry-run) fetch snapclient_${SNAPCLIENT_VERSION}-*_${arch}_${codename}_with-pipewire.deb and apt-install it"
        return 0
    fi
    [ -n "$codename" ] && [ -n "$arch" ] || die "Could not detect distro codename/arch for the snapclient download"
    command -v curl >/dev/null 2>&1 || die "curl is required to download the snapclient package"
    # The release ships variants per arch/codename; pick the one built WITH pipewire.
    url="$(curl -fsSL "https://api.github.com/repos/badaix/snapcast/releases/tags/v$SNAPCLIENT_VERSION" 2>/dev/null \
            | grep -oE '"browser_download_url": *"[^"]+"' | cut -d'"' -f4 \
            | grep -E "snapclient_.*_${arch}_${codename}_with-pipewire\.deb$" | head -1)"
    [ -n "$url" ] || die "No 'with-pipewire' snapclient .deb for $arch/$codename in release v$SNAPCLIENT_VERSION. See https://github.com/badaix/snapcast/releases (try a different --snapclient-version)."
    tmp="$(mktemp -d)"; deb="$tmp/snapclient.deb"
    info "Downloading $url"
    curl -fsSL "$url" -o "$deb" || { rm -rf "$tmp"; die "Download failed"; }
    run_root apt-get update -y || warn "apt-get update failed; continuing"
    run_root apt-get install -y "$deb" || run_root dpkg -i "$deb" || { rm -rf "$tmp"; die "Failed to install snapclient .deb"; }
    rm -rf "$tmp"
    if snapclient_has_pipewire; then
        ok "Installed snapclient $(snapclient --version 2>/dev/null | awk '{print $NF}') with PipeWire player"
    else
        warn "Installed snapclient $SNAPCLIENT_VERSION but it lacks the pipewire player (built without libpipewire on $codename)."
        warn "Options: use a newer board OS, or a build with PipeWire enabled. --player pipewire will not work."
    fi
}

# ----------------------------------------------------------------------------
# File writing helpers
# ----------------------------------------------------------------------------
backup_once() {  # back up a file to .orig the first time we manage it
    local f="$1"
    [ "$DRY_RUN" -eq 1 ] && return 0
    if run_root test -f "$f" && ! run_root test -f "$f.orig"; then
        run_root cp -a "$f" "$f.orig" && ok "Backed up $f -> $f.orig"
    fi
}

write_root_file() {  # $1=path  $2=content  (written as root)
    local path="$1" content="$2"
    if [ "$DRY_RUN" -eq 1 ]; then
        info "(dry-run) would write $path:"; printf '%s\n' "$content" | sed 's/^/    /'; return 0
    fi
    run_root mkdir -p "$(dirname "$path")"
    printf '%s\n' "$content" | run_root tee "$path" >/dev/null
    ok "Wrote $path"
}


# ----------------------------------------------------------------------------
# Config generators
# ----------------------------------------------------------------------------
snapserver_conf() {
    cat <<EOF
# Managed by snapcast-setup.sh -- Snapcast SERVER.
# Native PipeWire capture: with capture_sink=true the source creates its OWN
# capturable sink (one node, shown as "Snapcast/$SOURCE_NAME"); route audio to it
# to stream. No FIFO, no loopback, no separate virtual sink.
# NOTE: do NOT set send_silence=true here -- with the pipewire capture source it
# feeds clients silence instead of the captured audio (board plays nothing).
[stream]
source = pipewire://?name=$SOURCE_NAME&capture_sink=true
sampleformat = $SAMPLEFORMAT
codec = $CODEC
buffer = $BUFFER_MS
chunk_ms = $CHUNK_MS

[http]
enabled = true
bind_to_address = 0.0.0.0
port = $HTTP_PORT

[tcp]
enabled = true
bind_to_address = 0.0.0.0
port = $CONTROL_PORT

[server]
EOF
}

snapclient_exec() {
    # Native PipeWire player: snapclient appears as a node in the graph. No --soundcard;
    # routing the node to a playback device is left to the user / WirePlumber.
    local spec="pipewire"
    [ -n "$CLIENT_BUFFER_TIME" ] && spec="pipewire:buffer_time=$CLIENT_BUFFER_TIME"
    echo "/usr/bin/snapclient --host $SERVER_IP --player $spec --latency $CLIENT_LATENCY"
}

# Target client-rt.conf. snapclient (native pipewire player) loads client.conf by
# default, so routing rules placed only in client-rt.conf never reach it -- we
# point it here with PIPEWIRE_CONFIG_NAME=client-rt.conf (see snapclient_unit).
# The stream.rules auto-connect nodes as they appear. --route plugin-host:
#   Snapclient                    -> Ap.cap  (persistent loopback; see note below)
#   AudioPluginHost "ALSA Playback" -> the amp sink (bpi-alsa-playback-pcm)
#   AudioPluginHost "ALSA Capture"  <- AirPlay playout (Ap.play)
#   shairport-sync "ALSA Playback"  -> AirPlay capture (Ap.cap)
# --route speakerd: the same players go into the speakerd sink, which the board's
# PipeWire config defines (persistent, like Ap.cap), and speakerd plays it to the amp:
#   Snapclient                      -> speakerd
#   shairport-sync "ALSA Playback"  -> speakerd
#   AudioPluginHost "ALSA Playback" -> speakerd  (never straight into the amp while
#                                                 speakerd plays it: two plugin hosts)
# Adjust the target.object values to match your own graph's node names.
#
# KEY: snapclient must target a PERSISTENT node. Ap.cap/Ap.play are created by a
# module-loopback in pipewire.conf, so they exist from boot -- like the amp. The
# AudioPluginHost's own input ("ALSA Capture") is TRANSIENT (only exists while
# that app is open) and appears AFTER snapclient, so targeting it directly makes
# snapclient fall back to the amp at boot. Targeting Ap.cap (the same loopback
# shairport feeds) links at boot and reaches the plugin host via Ap.play ->
# AudioPluginHost input, exactly like AirPlay.
target_client_rt_conf() {
    if [ "$ROUTE" = speakerd ]; then target_client_rt_conf_speakerd; return; fi
    cat <<'CLIENTRT'
# Real-time Client config file for PipeWire (managed by snapcast-setup.sh) #
#
# snapclient is pointed at this file via PIPEWIRE_CONFIG_NAME=client-rt.conf.
# Drop-in overrides go in ~/.config/pipewire/client-rt.conf.d/.

context.properties = {
    log.level = 3
}

context.spa-libs = {
    audio.convert.* = audioconvert/libspa-audioconvert
    support.*       = support/libspa-support
}

context.modules = [
    { name = libpipewire-module-protocol-native }
    { name = libpipewire-module-client-node }
    { name = libpipewire-module-client-device }
    { name = libpipewire-module-adapter }
    { name = libpipewire-module-metadata }
    { name = libpipewire-module-session-manager }
]

# Auto-routing rules: connect nodes to their targets as they appear.
stream.rules = [
    {   matches = [
            { application.name = "Snapclient" } ]
        actions = {
            update-props = {
                # Ap.cap is persistent (module-loopback) -> links at boot, never
                # the amp. Audio reaches the plugin host via Ap.cap -> Ap.play.
                target.object = "Ap.cap"
            }
        }
    }
    {   matches = [
            { application.name = "PipeWire ALSA [AudioPluginHost]",
              node.name        = "ALSA Playback" } ]
        actions = {
            update-props = {
                target.object = "bpi-alsa-playback-pcm"
            }
        }
    }
    {   matches = [
            { application.name = "PipeWire ALSA [AudioPluginHost]",
              node.name        = "ALSA Capture" } ]
        actions = {
            update-props = {
                target.object = "Ap.play"
            }
        }
    }
    {   matches = [
            { application.name = "PipeWire ALSA [shairport-sync]",
              node.name        = "ALSA Playback" } ]
        actions = {
            update-props = {
                target.object = "Ap.cap"
            }
        }
    }
]
CLIENTRT
}

# --route speakerd: every player plays into the speakerd sink (firmware/CLAUDE.md,
# "speakerd on a board"). Like Ap.cap it exists from boot, defined by the board's
# PipeWire config (pipewire.conf.d/60-speakerd-sink.conf), so snapclient links to it at
# boot and between speakerd's restarts, never to the amplifier.
target_client_rt_conf_speakerd() {
    cat <<'CLIENTRT'
# Real-time Client config file for PipeWire (managed by snapcast-setup.sh --route speakerd) #
#
# snapclient is pointed at this file via PIPEWIRE_CONFIG_NAME=client-rt.conf.
# Drop-in overrides go in ~/.config/pipewire/client-rt.conf.d/.

context.properties = {
    log.level = 3
}

context.spa-libs = {
    audio.convert.* = audioconvert/libspa-audioconvert
    support.*       = support/libspa-support
}

context.modules = [
    { name = libpipewire-module-protocol-native }
    { name = libpipewire-module-client-node }
    { name = libpipewire-module-client-device }
    { name = libpipewire-module-adapter }
    { name = libpipewire-module-metadata }
    { name = libpipewire-module-session-manager }
]

# Auto-routing rules: every player plays into the speakerd sink, and speakerd (the
# remote plugin host) plays to the amplifier.
stream.rules = [
    {   matches = [
            { application.name = "Snapclient" } ]
        actions = {
            update-props = {
                # The speakerd sink is persistent (the board's PipeWire config):
                # snapclient links to it at boot, never to the amplifier.
                target.object = "speakerd"
            }
        }
    }
    {   matches = [
            { application.name = "PipeWire ALSA [shairport-sync]",
              node.name        = "ALSA Playback" } ]
        actions = {
            update-props = {
                target.object = "speakerd"
            }
        }
    }
    {   matches = [
            { application.name = "PipeWire ALSA [AudioPluginHost]",
              node.name        = "ALSA Playback" } ]
        actions = {
            update-props = {
                # Never straight into the amplifier: speakerd plays it already.
                target.object = "speakerd"
            }
        }
    }
]
CLIENTRT
}

# --route speakerd is only safe when the speakerd sink is in the board's PipeWire
# configuration: a target that does not exist sends snapclient to the default sink, the
# amplifier, full range and at full level. A sink speakerd made itself is not enough: it
# lingers only until PipeWire restarts.
speakerd_sink_configured() {
    local f
    for f in "$CLIENT_HOME/.config/pipewire/pipewire.conf" "$CLIENT_HOME"/.config/pipewire/pipewire.conf.d/*.conf \
             /etc/pipewire/pipewire.conf /etc/pipewire/pipewire.conf.d/*.conf; do
        if [ -f "$f" ] && grep -Eq '^[[:space:]]*node\.name[[:space:]]*=[[:space:]]*"?speakerd"?[[:space:]]*(#.*)?$' "$f"; then
            echo "$f"; return 0
        fi
    done
    return 1
}

check_route_target() {
    [ "$ROUTE" = speakerd ] || return 0
    local where
    if where="$(speakerd_sink_configured)"; then
        ok "The speakerd sink is defined in $where"
    elif [ "$DRY_RUN" -eq 1 ]; then
        warn "(dry-run) no PipeWire config defines the speakerd sink yet: --route speakerd would be refused now"
    else
        die "--route speakerd: no PipeWire config of $CLIENT_USER defines the speakerd sink, so snapclient would fall back to the amplifier (full range, tweeter included). Install it first: firmware/speakerd-setup.sh sink (or firmware/setup.sh)."
    fi
    check_live_speakerd_sink
}

# A config file is not a sink: the RUNNING PipeWire must have the speakerd node with the
# drop-in's properties (priority.session 1500, priority.driver 0). A PipeWire that does not
# load ~/.config/pipewire/pipewire.conf.d (another config name, PIPEWIRE_CONFIG_DIR) has at
# most a sink speakerd made itself, which is gone after the next restart of PipeWire: then
# snapclient would fall back to the amplifier. With --dry-run this only says what it finds;
# the tests (--client-home) skip it when no PipeWire is reachable.
check_live_speakerd_sink() {
    local helper dump out present="" dropin="" differs="" default_sink=""
    helper="$(cd "$(dirname "$SELF")" && pwd)/speakerd/setup-helper.py"
    [ -f "$helper" ] || die "--route speakerd: cannot check the running PipeWire's speakerd sink ($helper is missing)"
    dump="$(mktemp)"
    if [ -n "$CLIENT_HOME_OVERRIDE" ]; then
        timeout 10 pw-dump > "$dump" 2> /dev/null || true
    else
        as_user timeout 10 pw-dump > "$dump" 2> /dev/null || true
    fi
    if [ ! -s "$dump" ]; then
        rm -f "$dump"
        if [ "$DRY_RUN" -eq 1 ]; then warn "(dry-run) cannot reach $CLIENT_USER's PipeWire: the live speakerd sink is not checked"; return 0; fi
        if [ -n "$CLIENT_HOME_OVERRIDE" ]; then warn "no PipeWire reachable: the live speakerd sink is not checked (test mode)"; return 0; fi
        die "--route speakerd: cannot reach $CLIENT_USER's PipeWire to check that its speakerd sink exists"
    fi
    out="$(python3 "$helper" sink "$dump")" || { rm -f "$dump"; die "--route speakerd: cannot read the running PipeWire's nodes"; }
    rm -f "$dump"
    eval "$out"
    : "$default_sink"
    if [ "$dropin" = yes ]; then
        ok "The running PipeWire has the drop-in's speakerd sink"
        return 0
    fi
    local why="the running PipeWire has no speakerd sink"
    [ "$present" = no ] || why="the running PipeWire's speakerd sink is not the drop-in's ($differs): it does not load ~/.config/pipewire/pipewire.conf.d"
    if [ "$DRY_RUN" -eq 1 ]; then warn "(dry-run) $why: --route speakerd would be refused now"; return 0; fi
    die "--route speakerd: $why, so snapclient could fall back to the amplifier (full range, tweeter included). Not routing it."
}

# Before the first route into speakerd, keeps the routing there was as
# client-rt.conf.before-speakerd, which the speakerd uninstall restores.
save_routing_before_speakerd() {
    local crt="$CLIENT_HOME/.config/pipewire/client-rt.conf" as=run_root
    [ "$ROUTE" = speakerd ] && [ "$DRY_RUN" -eq 0 ] || return 0
    # Our own home (or the test's) needs no root.
    if [ -n "$CLIENT_HOME_OVERRIDE" ] || [ "$(id -un)" = "$CLIENT_USER" ]; then as=""; fi
    $as test -f "$crt" || return 0
    $as grep -q 'target.object *= *"speakerd"' "$crt" && return 0
    $as cp -a "$crt" "$crt.before-speakerd" && ok "Kept the routing from before speakerd as $crt.before-speakerd"
}

route_target() { if [ "$ROUTE" = speakerd ]; then echo speakerd; else echo Ap.cap; fi; }

# --routing-only: rewrite client-rt.conf for --route and restart snapclient.
setup_routing_only() {
    resolve_client_user
    info "Routing snapclient into $(route_target) (--route $ROUTE; client-rt.conf only, user $CLIENT_USER)"
    check_route_target
    local crt="$CLIENT_HOME/.config/pipewire/client-rt.conf" unit="$CLIENT_HOME/.config/systemd/user/snapclient.service" as=run_root
    if [ -n "$CLIENT_HOME_OVERRIDE" ] || [ "$(id -un)" = "$CLIENT_USER" ]; then as=""; fi
    # The rules reach snapclient only through its unit's PIPEWIRE_CONFIG_NAME=client-rt.conf.
    # Into speakerd that is not optional: without it snapclient plays into the default sink.
    if $as test -f "$unit"; then
        if ! $as grep -q 'PIPEWIRE_CONFIG_NAME=client-rt.conf' "$unit"; then
            local why="$unit does not point snapclient at client-rt.conf (no Environment=PIPEWIRE_CONFIG_NAME=client-rt.conf), so these rules would not reach it"
            if [ "$ROUTE" = speakerd ] && [ "$DRY_RUN" -eq 0 ]; then
                die "--route speakerd: $why. Not routing: re-run --mode target with --server-ip (it rewrites the unit), or add that line."
            fi
            warn "$why: re-run --mode target with --server-ip"
        fi
    else
        warn "no snapclient user unit ($unit): these rules apply only to a snapclient started with PIPEWIRE_CONFIG_NAME=client-rt.conf"
    fi
    if [ "$DRY_RUN" -eq 0 ] && [ -f "$crt" ] && [ ! -f "$crt.orig" ]; then
        cp -a "$crt" "$crt.orig" && ok "Backed up existing client-rt.conf -> $crt.orig"
    fi
    save_routing_before_speakerd
    write_user_file ".config/pipewire/client-rt.conf" "$(target_client_rt_conf)"
    if [ "$DO_RESTART" -eq 1 ] && [ "$DRY_RUN" -eq 0 ] && [ -z "$CLIENT_HOME_OVERRIDE" ]; then
        # A stream's target is read when it is created: restart snapclient.
        if as_user systemctl --user restart snapclient 2>/dev/null; then
            ok "snapclient restarted: it plays into $(route_target)"
        else
            warn "Could not restart snapclient; check: systemctl --user --machine=$CLIENT_USER@ status snapclient"
        fi
    else
        info "snapclient not restarted (--no-restart/--dry-run): the new routing applies at its next start"
    fi
}

snapclient_unit() {
    # Point snapclient at client-rt.conf so its stream.rules apply (it loads
    # client.conf by default). Trailing newline kept via ANSI-C quoting so the
    # Environment= and ExecStart= lines don't collapse.
    local envline=""
    [ "$TARGET_CLIENT_RT" -eq 1 ] && envline=$'Environment=PIPEWIRE_CONFIG_NAME=client-rt.conf\n'
    cat <<EOF
# Managed by snapcast-setup.sh -- snapclient as a PipeWire-native user service.
[Unit]
Description=Snapcast client (user session, PipeWire)
After=pipewire.service pipewire-pulse.service wireplumber.service
Wants=pipewire.service
[Service]
${envline}ExecStart=$(snapclient_exec)
Restart=on-failure
RestartSec=2
[Install]
WantedBy=default.target
EOF
}

# ----------------------------------------------------------------------------
# Firewall + service control
# ----------------------------------------------------------------------------
open_firewall() {
    [ "$OPEN_FIREWALL" -eq 1 ] || return 0
    command -v ufw >/dev/null 2>&1 || { warn "--open-firewall: ufw not installed, skipping"; return 0; }
    info "Opening TCP ports $STREAM_PORT,$CONTROL_PORT,$HTTP_PORT via ufw"
    [ "$DRY_RUN" -eq 1 ] && { echo "  (dry-run) ufw allow ..."; return 0; }
    run_root ufw allow "$STREAM_PORT"/tcp  || true
    run_root ufw allow "$CONTROL_PORT"/tcp || true
    run_root ufw allow "$HTTP_PORT"/tcp    || true
}

# snapserver must share the desktop session's PipeWire, so it runs as a systemd
# *user* service (not the packaged system service, which can't reach /run/user/UID).
install_user_snapserver_service() {
    if [ "$(id -u)" -eq 0 ]; then
        warn "Running as root: the native PipeWire source needs a user session."
        warn "Re-run the host setup as your desktop user (sudo is used internally for /etc),"
        warn "or arrange for snapserver to run inside the PipeWire user's session manually."
        return 0
    fi
    info "Installing snapserver as a systemd user service (shares your PipeWire)"
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "  (dry-run) systemctl disable --now snapserver   # stop packaged system service"
        echo "  (dry-run) write ~/.config/systemd/user/snapserver.service + enable --now"
        echo "  (dry-run) loginctl enable-linger $USER         # survive logout (headless)"
        return 0
    fi
    # Avoid a port clash with the packaged system service.
    run_root systemctl disable --now snapserver 2>/dev/null || true
    local unit="$HOME/.config/systemd/user/snapserver.service"
    mkdir -p "$(dirname "$unit")"
    cat > "$unit" <<UNIT
[Unit]
Description=Snapcast server (user session, PipeWire-integrated)
After=pipewire.service wireplumber.service
Wants=pipewire.service
[Service]
ExecStart=/usr/bin/snapserver --config /etc/snapserver.conf
Restart=on-failure
[Install]
WantedBy=default.target
UNIT
    ok "Wrote $unit"
    [ "$DO_RESTART" -eq 1 ] || { info "Service written but not started (--no-restart)"; return 0; }
    systemctl --user daemon-reload
    # enable, then restart -- 'enable --now' is a no-op if it is already running, so a
    # re-run after a config change would otherwise keep the stale config.
    systemctl --user enable snapserver 2>/dev/null || true
    if systemctl --user restart snapserver 2>/dev/null; then
        ok "snapserver (re)started as a user service"
    else
        warn "Could not start user snapserver; check: systemctl --user status snapserver"
    fi
    # Keep the user service alive when nobody is logged in (headless host).
    run_root loginctl enable-linger "$USER" 2>/dev/null && ok "Enabled linger for $USER" || true
}

# Host transmitter: a PipeWire quantum/rate drop-in. Bigger quantum = more
# headroom against xruns/clicks in the captured Snapcast stream (latency grows,
# which does not matter for a buffered multi-room stream). Merges over
# /usr/share/pipewire/pipewire.conf; delete the file to return to defaults.
host_quantum_conf() {
    cat <<'QUANTUMCONF'
# ---------------------------------------------------------------------------
# PipeWire quantum (buffer-size) sandbox  —  user drop-in override
# File:  ~/.config/pipewire/pipewire.conf.d/10-quantum.conf
#
# This merges over /usr/share/pipewire/pipewire.conf. Delete this file to
# return to defaults. It only sets clock/quantum knobs — nothing else.
#
# APPLY after editing:
#     systemctl --user restart pipewire pipewire-pulse wireplumber snapserver
# VERIFY what's actually live:
#     pw-metadata -n settings | grep -E 'quantum|rate'
#     pw-top            # watch the QUANT column on the 'multi-room' driver row
#
# QUANTUM = frames processed per cycle (the buffer). Buffer time = quantum / rate.
# Your graph is driven by snapcast at 48000 Hz, so:
#     256  -> 5.3 ms      1024 -> 21.3 ms      4096 -> 85.3 ms
#     512  -> 10.7 ms     2048 -> 42.7 ms      8192 -> 170.7 ms
# Bigger quantum = more headroom against xruns/clicks (latency grows, but that
# does not matter for a measurement).
# ---------------------------------------------------------------------------

context.properties = {

    # --- The knobs to play with ---------------------------------------------

    # Default quantum PipeWire aims for, and the range it may pick within.
    # Raise min-quantum to stop apps from forcing tiny (click-prone) buffers.
   # default.clock.quantum        = 8192
    default.clock.min-quantum    = 64
    default.clock.max-quantum    = 1024

    # PIN a single fixed quantum for the whole graph (best for clean A/B tests).
    # Uncomment and set one value; this overrides the range above.
    #default.clock.force-quantum = 2048

    # --- Rate (usually leave commented; the driver/snapcast sets 48000) ------
    default.clock.rate          = 48000
    #default.clock.allowed-rates = [ 44100 48000 ]
}
QUANTUMCONF
}

configure_host_quantum() {
    [ "$HOST_QUANTUM" -eq 1 ] || { info "Skipping host PipeWire quantum drop-in (--no-quantum)"; return 0; }
    local dir file; dir="$(confd_dir)"; file="$dir/10-quantum.conf"
    if [ "$DRY_RUN" -eq 1 ]; then
        info "(dry-run) would write $file:"; host_quantum_conf | sed 's/^/    /'; return 0
    fi
    if [ "$SCOPE" = "system" ]; then
        run_root mkdir -p "$dir"; host_quantum_conf | run_root tee "$file" >/dev/null
    else
        mkdir -p "$dir"; host_quantum_conf > "$file"
    fi
    ok "Wrote $file (PipeWire quantum/rate drop-in)"
}

restart_pipewire() {
    [ "$DO_RESTART" -eq 1 ] || { info "Skipping PipeWire restart (--no-restart)"; return 0; }
    [ "$DRY_RUN" -eq 1 ] && { info "(dry-run) would restart PipeWire"; return 0; }
    info "Restarting PipeWire (virtual sink)"
    if [ "$SCOPE" = "user" ] && [ "$(id -u)" -ne 0 ]; then
        if systemctl --user restart pipewire pipewire-pulse wireplumber 2>/dev/null; then
            ok "Restarted user PipeWire services"
        else
            warn "Could not restart user PipeWire; run: systemctl --user restart pipewire wireplumber"
        fi
    else
        run_root systemctl restart pipewire pipewire-pulse wireplumber 2>/dev/null \
            || warn "Could not restart system PipeWire; restart it in the user's session instead"
    fi
}

# ----------------------------------------------------------------------------
# Role setups
# ----------------------------------------------------------------------------
setup_host() {
    info "Configuring this machine as the Snapcast SERVER (native PipeWire capture)"
    resolve_scope   # host runs snapserver in the user's PipeWire session -> user scope
    install_pkgs snapserver
    # Migration: older versions of this script created a redundant null-audio-sink
    # that snapserver never captured (a non-working decoy). Remove it if present.
    local stale
    stale="$(confd_dir)/60-snapcast-sink.conf"
    if [ "$DRY_RUN" -eq 0 ] && { [ -f "$stale" ] || run_root test -f "$stale" 2>/dev/null; }; then
        run_root rm -f "$stale" && ok "Removed stale redundant sink: $stale"
    fi
    # snapserver config: the native pipewire:// source creates its OWN capture sink
    # (a single node, shown as "Snapcast/$SOURCE_NAME" in pavucontrol/qpwgraph).
    backup_once /etc/snapserver.conf
    write_root_file /etc/snapserver.conf "$(snapserver_conf)"
    # PipeWire quantum/rate drop-in for the capture graph (host = transmitter).
    configure_host_quantum
    open_firewall
    # Apply the quantum drop-in before snapserver starts against the graph.
    restart_pipewire
    # Run snapserver in the user's PipeWire session (user service, not the system one).
    install_user_snapserver_service
    cat <<EOF

${c_green}Server ready.${c_off}
  - Route audio to the "Snapcast/$SOURCE_NAME" sink (pavucontrol / qpwgraph), or make it default:
        wpctl set-default \$(wpctl status | grep -iE 'snapcast|/$SOURCE_NAME' | grep -oE '[0-9]+' | head -1)
  - Web UI / control: http://$(detect_host_ip):$HTTP_PORT
  - Clients should connect to this host at: ${SERVER_IP:-$(detect_host_ip)}
EOF
}

setup_target_local() {
    if [ "$ROUTING_ONLY" -eq 1 ]; then setup_routing_only; return; fi
    [ -n "$SERVER_IP" ] || die "--mode target requires --server-ip <snapserver host IP>"
    resolve_client_user
    info "Configuring this machine as a PipeWire-native Snapcast CLIENT (server $SERVER_IP, user $CLIENT_USER, route $ROUTE)"
    [ "$TARGET_CLIENT_RT" -eq 0 ] || check_route_target
    ensure_snapclient_with_pipewire
    # Migration: older versions wrote a channel-split fragment (null-sink + loopback)
    # that now just clutters the graph and loops audio. Remove it if present.
    local stale="$CLIENT_HOME/.config/pipewire/pipewire.conf.d/60-snapcast-split.conf"
    if [ "$DRY_RUN" -eq 0 ] && run_root test -f "$stale"; then
        run_root rm -f "$stale" && ok "Removed stale channel-split fragment: $stale"
    fi
    # snapclient plays through PipeWire (--player pipewire) and runs in the user's
    # session, so disable the packaged system service and run a per-user one instead.
    if [ "$DRY_RUN" -eq 0 ]; then
        run_root systemctl disable --now snapclient 2>/dev/null || true
        run_root loginctl enable-linger "$CLIENT_USER" 2>/dev/null && ok "Enabled linger for $CLIENT_USER" || true
    fi
    # Deploy the client-rt.conf auto-routing config (snapclient is pointed at it
    # via PIPEWIRE_CONFIG_NAME in the unit). Back up any existing one to .orig.
    if [ "$TARGET_CLIENT_RT" -eq 1 ]; then
        local crt="$CLIENT_HOME/.config/pipewire/client-rt.conf"
        if [ "$DRY_RUN" -eq 0 ] && run_root test -f "$crt" && ! run_root test -f "$crt.orig"; then
            run_root cp -a "$crt" "$crt.orig" && ok "Backed up existing client-rt.conf -> $crt.orig"
        fi
        save_routing_before_speakerd
        write_user_file ".config/pipewire/client-rt.conf" "$(target_client_rt_conf)"
    fi
    write_user_file ".config/systemd/user/snapclient.service" "$(snapclient_unit)"
    if [ "$DO_RESTART" -eq 1 ] && [ "$DRY_RUN" -eq 0 ]; then
        as_user systemctl --user daemon-reload 2>/dev/null || true
        # restart the audio stack so snapclient attaches to a fresh PipeWire graph.
        if as_user systemctl --user restart pipewire pipewire-pulse wireplumber 2>/dev/null; then
            ok "Restarted PipeWire/WirePlumber for $CLIENT_USER"
        else
            warn "Could not restart PipeWire/WirePlumber for $CLIENT_USER (check: systemctl --user --machine=$CLIENT_USER@ status pipewire)"
        fi
        as_user systemctl --user enable snapclient 2>/dev/null || true
        # restart (not just enable --now) so a re-run picks up config changes.
        if as_user systemctl --user restart snapclient 2>/dev/null; then
            ok "snapclient (re)started as $CLIENT_USER's user service"
        else
            warn "Could not start snapclient; check: systemctl --user --machine=$CLIENT_USER@ status snapclient"
        fi
    else
        info "Service written but not started (--no-restart/--dry-run)"
    fi
    cat <<EOF

${c_green}Client ready.${c_off}
  - snapclient runs as a PipeWire node ("Snapcast") in $CLIENT_USER's session.
  - client-rt.conf routes it into $(route_target) (--route $ROUTE); see wpctl status / qpwgraph.
  - Verify:  systemctl --user --machine=$CLIENT_USER@ status snapclient
EOF
}

setup_target_remote() {
    [ -n "$SERVER_IP" ] || SERVER_IP="$(detect_host_ip)"
    [ -n "$SERVER_IP" ] || die "Could not auto-detect the server IP; pass --server-ip"
    # If --target-host is given as user@host and --client-user wasn't set, that ssh
    # user is also the board's PipeWire/audio user (e.g. user@bpi-amp.local -> user).
    # Skip 'root' -- root has no PipeWire session, so fall back to $BOARD_USER if set.
    if [ -z "$CLIENT_USER" ]; then
        local ruser="${TARGET_HOST%@*}"
        [ "$ruser" != "$TARGET_HOST" ] && [ "$ruser" != "root" ] && CLIENT_USER="$ruser"
    fi
    if [ -z "$CLIENT_USER" ]; then CLIENT_USER="${BOARD_USER:-}"; fi
    info "Configuring the board ($TARGET_HOST) as a Snapcast CLIENT over ssh (server $SERVER_IP${CLIENT_USER:+, user $CLIENT_USER})"
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "  (dry-run) scp $SELF $TARGET_HOST:/tmp/snapcast-setup.sh"
        echo "  (dry-run) ssh -t $TARGET_HOST sudo bash /tmp/snapcast-setup.sh --mode target --server-ip $SERVER_IP ..."
        return 0
    fi
    command -v ssh >/dev/null 2>&1 || die "ssh not found; cannot configure the remote board"
    scp -q "$SELF" "$TARGET_HOST:/tmp/snapcast-setup.sh" || die "Failed to copy script to $TARGET_HOST"
    local rargs="--mode target --server-ip $SERVER_IP --client-latency $CLIENT_LATENCY --snapclient-version $SNAPCLIENT_VERSION --scope system --route $ROUTE"
    [ -n "$CLIENT_USER" ]        && rargs="$rargs --client-user $CLIENT_USER"
    [ -n "$CLIENT_BUFFER_TIME" ] && rargs="$rargs --client-buffer-time $CLIENT_BUFFER_TIME"
    [ "$INSTALL_DEPS" -eq 0 ]    && rargs="$rargs --no-install"
    [ "$DO_RESTART" -eq 0 ]      && rargs="$rargs --no-restart"
    [ "$TARGET_CLIENT_RT" -eq 0 ] && rargs="$rargs --no-client-rt"
    [ "$ROUTING_ONLY" -eq 1 ]    && rargs="$rargs --routing-only"
    # -t allocates a TTY so sudo on the board can prompt for a password; run the whole
    # remote script under sudo (it needs root for /etc + services; --client-user keeps
    # snapclient in the right user's PipeWire session).
    # shellcheck disable=SC2029
    ssh -t "$TARGET_HOST" "sudo bash /tmp/snapcast-setup.sh $rargs" || die "Remote client setup failed on $TARGET_HOST"
    ok "Board configured as client"
}

# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
resolve_scope
if [ "$ROUTING_ONLY" -eq 1 ]; then
    info "Mode: $MODE | routing only: --route $ROUTE"
else
    info "Mode: $MODE | codec: $CODEC | buffer: ${BUFFER_MS}ms | format: $SAMPLEFORMAT | route: $ROUTE"
fi

case "$MODE" in
    host)          setup_host ;;
    target)        setup_target_local ;;
    target-remote) setup_target_remote ;;
    both)          setup_host; echo; setup_target_remote ;;
esac

ok "Done."
