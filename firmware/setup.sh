#!/usr/bin/env bash
#
# setup.sh
#
# Installs the BPI AMP Platform's audio side on a speaker, component by component, in the
# order the speaker boots them (docs/remote-plugin-host.md, section 8, rule 2):
#
#   pipewire   the board's PipeWire configuration: the persistent speakerd sink
#              (pipewire.conf.d/60-speakerd-sink.conf); with --base-conf also the
#              repository's pipewire.conf, and with --rate its sample rate
#   snapcast   snapclient, routed into the speakerd sink (snapcast-setup.sh --route speakerd)
#   speakerd   the headless plugin host (speakerd-setup.sh install), its output capped
#   ampd       the speaker's network agent: not yet (step 3 of docs/remote-plugin-host.md)
#
# Each component is checked before any is changed. It builds nothing: the remote mode copies
# one bundle (the aarch64 builds and these installers) to the board and runs this script's
# local mode there, and says which tools/build.sh commands make what is missing.
# See firmware/CLAUDE.md, "The whole architecture".
#
# Usage:
#   ./setup.sh [--only LIST | --skip LIST] [options]                          # on the board
#   ./setup.sh --mode target-remote [--target-host user@host] [options]      # from the development machine
#
# Run `./setup.sh --help` for the full option list.

set -euo pipefail

SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
# shellcheck source=install-lib.sh
. "$(dirname "$SELF")/install-lib.sh"

COMPONENTS=(pipewire snapcast speakerd ampd)

MODE=target
TARGET_HOST=""
ONLY=""
SKIP=""
DRY_RUN=0
NO_RESTART=0
NO_SYSTEMD=0
HOME_OVERRIDE=""
BUNDLE=""
ROUTE=speakerd
SERVER_IP=""
SNAPCAST_ARGS=()
BASE_CONF=0
RATE=""
FACTORY=""
PLUGINS=()
ARCH=aarch64
SPEAKERD_ARGS=()     # passed to speakerd-setup.sh install (and check)
REMOTE_ARGS=()       # passed to this script on the board

usage() {
    awk 'NR>1 { if ($0 ~ /^#/) { sub(/^# ?/, ""); print } else exit }' "$SELF"
    cat <<EOF

Options:
  --mode <target|target-remote>
                              target (default): this machine is the speaker.
                              target-remote: copy a bundle to the board and run setup.sh there.
  --target-host <user@host>   the board, as its audio user (default: \$BOARD_USER@\$BOARD_HOST, from the
                              environment or firmware/local.mk; else user@bpi-amp.local).
  --only <a,b,...>            install only these components (${COMPONENTS[*]}).
  --skip <a,b,...>            install every component but these.
  --dry-run                   say what each component would change; change nothing. With target-remote:
                              copy the bundle and run this dry run ON the board (it contacts the board,
                              and changes nothing there).
  --no-restart                write the files, restart nothing.

  pipewire:
  --base-conf                 also install firmware/pipewire/pipewire.conf as ~/.config/pipewire/pipewire.conf
                              (the one there is kept as .orig once). Off by default: a running board keeps
                              its own, which may name the amplifier differently.
  --rate <44100|48000>        with --base-conf: the graph's default.clock.rate and the amplifier's
                              audio.rate. The repository's pipewire.conf has 44100; snapcast streams 48000.

  snapcast:
  --route <speakerd|plugin-host>
                              where snapclient plays (default speakerd; plugin-host: Ap.cap, for an
                              AudioPluginHost).
  --server-ip <ip>            the snapserver: a full client setup (snapcast-setup.sh --mode target).
                              Without it, an installed snapclient is only re-routed (--routing-only).
  --snapcast-arg <arg>        one more argument for snapcast-setup.sh, repeatable.

  speakerd (see speakerd-setup.sh --help):
  --role <left|right|mono>    required on the first install.
  --amplifier <node>, --woofer-port <port>, --tweeter-port <port>
  --factory <file>            a graph document for factory.json (in target-remote mode, a local file).
  --import-filtergraph <file> an AudioPluginHost .filtergraph on the board, converted into factory.json.
  --stop-plugin-host          stop a running AudioPluginHost (and disable its systemd units) instead of
                              refusing. Checked whenever PipeWire restarts into the speakerd route
                              (pipewire, speakerd, snapcast --server-ip), not only for speakerd.
  --plugin <plugin[:build]>   target-remote: a plugin bundle to copy, repeatable (default ${DEFAULT_PLUGINS[*]}).
  --arch <aarch64|native>     target-remote: the build tree the bundle comes from (default aarch64).
  --bundle <dir>              target: the bundle (default: the folder above this script, when it is one).

  --no-systemd, --home <dir>  for the virtual pair tests (see speakerd-setup.sh).
  -h, --help                  show this help.

Examples:
  # The first time, from the development machine, the board already running snapclient:
  tools/build.sh speakerd aarch64 && tools/build.sh loudness aarch64 headless
  ./setup.sh --mode target-remote --target-host user@bpi-amp.local --role left \\
      --plugin loudness:headless --stop-plugin-host --dry-run

  # On the board, from an unpacked bundle: only re-route snapclient into the speakerd sink
  ./firmware/setup.sh --only pipewire,snapcast
EOF
}

remote() { REMOTE_ARGS+=("$@"); }
while [ $# -gt 0 ]; do
    case "$1" in
        --mode)            MODE="${2:?}"; shift 2 ;;
        --target-host)     TARGET_HOST="${2:?}"; shift 2 ;;
        --only)            ONLY="${2:?}"; remote "$1" "$2"; shift 2 ;;
        --skip)            SKIP="${2:?}"; remote "$1" "$2"; shift 2 ;;
        --dry-run)         DRY_RUN=1; remote "$1"; shift ;;
        --no-restart)      NO_RESTART=1; remote "$1"; shift ;;
        --no-systemd)      NO_SYSTEMD=1; shift ;;
        --home)            HOME_OVERRIDE="${2:?}"; shift 2 ;;
        --bundle)          BUNDLE="${2:?}"; shift 2 ;;
        --base-conf)       BASE_CONF=1; remote "$1"; shift ;;
        --rate)            RATE="${2:?}"; remote "$1" "$2"; shift 2 ;;
        --route)           ROUTE="${2:?}"; remote "$1" "$2"; shift 2 ;;
        --server-ip)       SERVER_IP="${2:?}"; remote "$1" "$2"; shift 2 ;;
        --snapcast-arg)    SNAPCAST_ARGS+=("${2:?}"); remote "$1" "$2"; shift 2 ;;
        --role | --amplifier | --woofer-port | --tweeter-port | --import-filtergraph)
                           SPEAKERD_ARGS+=("$1" "${2:?}"); remote "$1" "$2"; shift 2 ;;
        --stop-plugin-host) SPEAKERD_ARGS+=("$1"); remote "$1"; shift ;;
        --factory)         FACTORY="${2:?}"; shift 2 ;;
        --plugin)          PLUGINS+=("${2:?}"); shift 2 ;;
        --arch)            ARCH="${2:?}"; shift 2 ;;
        -h | -help | --help) usage; exit 0 ;;
        *)                 die "unknown option: $1 (try --help)" ;;
    esac
done
case "$MODE" in target | target-remote) ;; *) die "--mode is target or target-remote" ;; esac
case "$ROUTE" in speakerd | plugin-host) ;; *) die "--route is speakerd or plugin-host" ;; esac
case "$RATE" in "" | 44100 | 48000) ;; *) die "--rate is 44100 or 48000" ;; esac
case "$ARCH" in aarch64 | native) ;; *) die "--arch is aarch64 or native" ;; esac
[ -z "$RATE" ] || [ "$BASE_CONF" -eq 1 ] || die "--rate needs --base-conf: the rate is in the board's own pipewire.conf (default.clock.rate and the amplifier's audio.rate), which only --base-conf replaces. See firmware/CLAUDE.md, 'Rates'."

# The components, in boot order.
selected() {
    local c="$1"
    if [ -n "$ONLY" ]; then [[ ",$ONLY," == *",$c,"* ]] || return 1; fi
    if [ -n "$SKIP" ]; then [[ ",$SKIP," != *",$c,"* ]] || return 1; fi
    return 0
}
for c in ${ONLY//,/ } ${SKIP//,/ }; do
    case " ${COMPONENTS[*]} " in *" $c "*) ;; *) die "no component '$c' (${COMPONENTS[*]})" ;; esac
done
CHOSEN=()
for c in "${COMPONENTS[@]}"; do selected "$c" && CHOSEN+=("$c"); done
[ ${#CHOSEN[@]} -gt 0 ] || die "no component selected"

# ----------------------------------------------------------------------------
# The remote mode
# ----------------------------------------------------------------------------
if [ "$MODE" = target-remote ]; then
    [ -n "$TARGET_HOST" ] || TARGET_HOST="$(board_target)"
    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' EXIT
    if selected speakerd; then make_bundle "$tmp/bundle" "$ARCH" "${PLUGINS[@]}"
    else make_bundle "$tmp/bundle" none; fi
    if [ -n "$FACTORY" ]; then
        mkdir -p "$tmp/bundle/extra"
        cp "$FACTORY" "$tmp/bundle/extra/factory.json"
        REMOTE_ARGS+=(--factory extra/factory.json)
    fi
    info "Setting up ${CHOSEN[*]} on $TARGET_HOST, bundle $(version_value "$tmp/bundle/VERSION" id)"
    remote_run "$TARGET_HOST" "$tmp/bundle" setup.sh --mode target "${REMOTE_ARGS[@]}"
    exit $?
fi

# ----------------------------------------------------------------------------
# The local mode, on the board
# ----------------------------------------------------------------------------
[ "$(id -u)" -ne 0 ] || [ -n "$HOME_OVERRIDE" ] || die "run setup.sh as the board's audio user (the one whose PipeWire drives the amplifier), not as root: it uses sudo where it must"
if [ -n "$HOME_OVERRIDE" ]; then mkdir -p "$HOME_OVERRIDE"; H="$(cd "$HOME_OVERRIDE" && pwd)"
else H="$(getent passwd "$(id -un)" | cut -d: -f6)"; [ -n "$H" ] || H="$HOME"; fi
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
if [ -z "$BUNDLE" ] && [ -f "$FIRMWARE_DIR/../VERSION" ]; then BUNDLE="$FIRMWARE_DIR/.."; fi
[ -z "$BUNDLE" ] || BUNDLE="$(cd "$BUNDLE" && pwd)"
if [ -n "$FACTORY" ]; then
    if [ -f "$FACTORY" ]; then FACTORY="$(cd "$(dirname "$FACTORY")" && pwd)/$(basename "$FACTORY")"
    elif [ -n "$BUNDLE" ] && [ -f "$BUNDLE/$FACTORY" ]; then FACTORY="$BUNDLE/$FACTORY"
    else die "no factory graph $FACTORY"; fi
    SPEAKERD_ARGS+=(--factory "$FACTORY")
fi

common=()
[ "$DRY_RUN" -eq 0 ] || common+=(--dry-run)
[ "$NO_RESTART" -eq 0 ] || common+=(--no-restart)
test_hooks=()
[ "$NO_SYSTEMD" -eq 0 ] || test_hooks+=(--no-systemd)
[ -z "$HOME_OVERRIDE" ] || test_hooks+=(--home "$H")
speakerd_setup() { bash "$FIRMWARE_DIR/speakerd-setup.sh" "$@" ${BUNDLE:+--bundle "$BUNDLE"} "${common[@]}" "${test_hooks[@]}"; }
snapcast_setup() {
    local extra=()
    [ "$DRY_RUN" -eq 0 ] || extra+=(--dry-run)
    if [ -n "$HOME_OVERRIDE" ] || [ "$NO_SYSTEMD" -eq 1 ] || [ "$NO_RESTART" -eq 1 ]; then extra+=(--no-restart); fi
    [ -z "$HOME_OVERRIDE" ] || extra+=(--client-home "$H")
    bash "$FIRMWARE_DIR/snapcast-setup.sh" "$@" "${extra[@]}" "${SNAPCAST_ARGS[@]}"
}
DROPIN="$H/.config/pipewire/pipewire.conf.d/60-speakerd-sink.conf"
SNAPCLIENT_UNIT="$H/.config/systemd/user/snapclient.service"

restart_pipewire() {
    if [ "$DRY_RUN" -eq 1 ] || [ "$NO_RESTART" -eq 1 ] || [ "$NO_SYSTEMD" -eq 1 ]; then
        info "PipeWire needs a restart for the new configuration (not done: --dry-run/--no-restart/--no-systemd)"
        return 0
    fi
    info "Restarting PipeWire"
    # Fatal: what follows (the speakerd sink, the route into it) needs the new configuration.
    systemctl --user restart pipewire.service wireplumber.service \
        || die "could not restart PipeWire (systemctl --user restart pipewire wireplumber): stopping before anything is routed"
    systemctl --user restart pipewire-pulse.service 2> /dev/null || true
    if systemctl --user is-enabled snapclient.service > /dev/null 2>&1; then
        if systemctl --user restart snapclient.service; then ok "Restarted snapclient"; else warn "could not restart snapclient"; fi
    fi
}

# --base-conf: the repository's pipewire.conf, with --rate in default.clock.rate and in the
# amplifier's (the api.alsa.pcm.sink object's) audio.rate. Sets BASE_CHANGED.
BASE_CHANGED=0
install_base_conf() {
    local dst="$H/.config/pipewire/pipewire.conf" conf
    conf="$(mktemp)"
    if [ -n "$RATE" ]; then
        awk -v rate="$RATE" '
            /^[[:space:]]*default\.clock\.rate[[:space:]]*=/ { sub(/=[[:space:]]*[0-9]+/, "= " rate) }
            /factory\.name[[:space:]]*=[[:space:]]*api\.alsa\.pcm\.sink/ { alsa = 1 }
            alsa && /^[[:space:]]*audio\.rate[[:space:]]*=/ { sub(/=[[:space:]]*[0-9]+/, "= " rate); alsa = 0 }
            { print }' "$FIRMWARE_DIR/pipewire/pipewire.conf" > "$conf"
    else
        cp "$FIRMWARE_DIR/pipewire/pipewire.conf" "$conf"
    fi
    if [ -f "$dst" ] && cmp -s "$conf" "$dst"; then ok "$dst is the repository's already"; rm -f "$conf"; return 0; fi
    if [ "$DRY_RUN" -eq 1 ]; then info "(dry-run) would install firmware/pipewire/pipewire.conf${RATE:+ at $RATE Hz} as $dst"; rm -f "$conf"; return 0; fi
    mkdir -p "$(dirname "$dst")"
    if [ -f "$dst" ] && [ ! -f "$dst.orig" ]; then cp -a "$dst" "$dst.orig" && ok "Kept the board's own as $dst.orig"; fi
    mv -f "$conf" "$dst" && chmod 644 "$dst"
    BASE_CHANGED=1
    ok "Wrote $dst${RATE:+ ($RATE Hz)}"
}

# ----------------------------------------------------------------------------
# Check every chosen component before changing any
# ----------------------------------------------------------------------------
info "Components, in boot order: ${CHOSEN[*]} (home $H)"
if selected speakerd && [ "$ROUTE" = plugin-host ] && selected snapcast; then
    die "--route plugin-host with the speakerd component would leave the AudioPluginHost's rule pointing at the amplifier while speakerd plays it too: route into speakerd, or skip one of them"
fi
if selected speakerd; then
    info "Checking speakerd"
    [ -n "$BUNDLE" ] || die "speakerd needs a bundle (--bundle, or run the setup.sh of an unpacked bundle): nothing was changed"
    speakerd_setup check "${SPEAKERD_ARGS[@]}" || die "speakerd cannot be installed (see above): nothing was changed"
fi
if selected snapcast; then
    if [ "$ROUTE" = speakerd ] && ! selected pipewire && [ ! -f "$DROPIN" ]; then
        die "--route speakerd needs the speakerd sink, which is not configured ($DROPIN): include the pipewire component"
    fi
    if [ -z "$SERVER_IP" ] && [ ! -f "$SNAPCLIENT_UNIT" ]; then
        die "snapclient is not set up here ($SNAPCLIENT_UNIT): give --server-ip for a full client setup, or --skip snapcast"
    fi
fi

# A running AudioPluginHost must not see PipeWire restart under it into the speakerd sink's
# configuration: it would be linked to the amplifier again beside speakerd, full range. The
# components that restart PipeWire: pipewire, speakerd (its sink) and snapcast's full setup
# (--server-ip). With --route plugin-host and no speakerd, the plugin host is what plays: it
# stays.
STOP_PLUGIN_HOST=0
[[ " ${SPEAKERD_ARGS[*]} " != *" --stop-plugin-host "* ]] || STOP_PLUGIN_HOST=1
NO_PLUGIN_HOST=0
if { selected pipewire || selected speakerd || { selected snapcast && [ -n "$SERVER_IP" ]; }; } \
   && { [ "$ROUTE" = speakerd ] || selected speakerd; }; then
    NO_PLUGIN_HOST=1
    info "Checking for the old plugin host (PipeWire restarts)"
    if [ "$STOP_PLUGIN_HOST" -eq 1 ]; then
        speakerd_setup stop-plugin-host --stop-plugin-host --dry-run || die "the old plugin host cannot be stopped (see above): nothing was changed"
    else
        speakerd_setup stop-plugin-host --dry-run || die "the old plugin host runs (see above): nothing was changed"
    fi
fi

# ----------------------------------------------------------------------------
# Install, in boot order
# ----------------------------------------------------------------------------
# Every check has passed. The old plugin host goes first, before PipeWire restarts under it.
if [ "$NO_PLUGIN_HOST" -eq 1 ] && [ "$STOP_PLUGIN_HOST" -eq 1 ]; then
    echo
    info "The old plugin host"
    speakerd_setup stop-plugin-host --stop-plugin-host
fi
for c in "${CHOSEN[@]}"; do
    echo
    case "$c" in
        pipewire)
            if [ "$BASE_CONF" -eq 1 ]; then
                info "pipewire: the repository's pipewire.conf"
                install_base_conf
                [ "$BASE_CHANGED" -eq 0 ] || restart_pipewire
            fi
            if [ "$ROUTE" = speakerd ] || selected speakerd || [ -f "$DROPIN" ]; then
                # The drop-in, a PipeWire restart if it changed, and the RUNNING PipeWire's
                # speakerd node checked: all fatal.
                info "pipewire: the persistent speakerd sink"
                speakerd_setup sink
            else
                info "pipewire: no speakerd sink (nothing plays into it with --route plugin-host and no speakerd)"
            fi
            ;;
        snapcast)
            if [ -n "$SERVER_IP" ]; then
                info "snapcast: a snapclient of $SERVER_IP, routed into $([ "$ROUTE" = speakerd ] && echo the speakerd sink || echo Ap.cap)"
                snapcast_setup --mode target --server-ip "$SERVER_IP" --route "$ROUTE"
            else
                info "snapcast: re-routing the installed snapclient into $([ "$ROUTE" = speakerd ] && echo the speakerd sink || echo Ap.cap)"
                snapcast_setup --mode target --routing-only --route "$ROUTE"
            fi
            ;;
        speakerd)
            info "speakerd: the headless plugin host, capped"
            speakerd_setup install "${SPEAKERD_ARGS[@]}"
            ;;
        ampd)
            info "ampd: not yet. It is step 3 of docs/remote-plugin-host.md; speakerd plays without it."
            ;;
    esac
done
echo
ok "Done: ${CHOSEN[*]}"
