#!/usr/bin/env bash
#
# setup.sh
#
# Installs the BPI AMP Platform's audio side on a speaker, component by component, in the
# order the speaker boots them (docs/remote-plugin-host.md, section 8, rule 2):
#
#   pipewire   the board's PipeWire configuration: the persistent speakerd sink
#              (pipewire.conf.d/60-speakerd-sink.conf); or, with --base-conf, the
#              repository's pipewire.conf (48000 Hz, quantum 512 by default and 64 to 2048 for
#              the controller's buffer size, the speakerd sink in it,
#              the drop-ins it contains removed), and with --rate another sample rate
#   snapcast   snapclient, routed into the speakerd sink (snapcast-setup.sh --route speakerd)
#   speakerd   the headless plugin host (speakerd-setup.sh install)
#   ampd       the speaker's network agent, and the hub on the left speaker (ampd-setup.sh install):
#              ampd.json and its unit; its program comes with speakerd's version
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
SERVER=""
SNAPCAST_ARGS=()
BASE_CONF=0
RATE=""
FACTORY=""
SECRET_FILE=""
PLUGINS=()
ARCH=aarch64
SPEAKERD_ARGS=()     # passed to speakerd-setup.sh install (and check)
AMPD_ARGS=()         # passed to ampd-setup.sh install (and check)
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
                              (the board's own is kept as .orig once). Off by default: a running board keeps
                              its own. The file is the measured board's own with the graph (default.clock.rate)
                              and the amplifier (its audio.rate) at 48000 Hz, the rate snapcast streams and
                              the shipped crossover and firConv filters are designed for, the quantum 512 by
                              default and 64 to 2048 (min-quantum, max-quantum: the buffer sizes the controller
                              may choose), resample.quality 15 and the speakerd sink; the drop-ins it contains
                              (pipewire.conf.d/20-resample.conf, 60-speakerd-sink.conf) are removed.
  --rate <44100|48000>        with --base-conf: the graph's default.clock.rate (and allowed-rates) and the
                              amplifier's audio.rate, changed together. Default: the
                              file's, 48000. At 44100 PipeWire resamples snapcast's 48000 Hz stream and the
                              shipped filters run with every frequency scaled by 44100/48000.

  snapcast:
  --route <speakerd|plugin-host>
                              where snapclient plays (default speakerd; plugin-host: Ap.cap, for an
                              AudioPluginHost).
  --server <host>             the snapserver, by name (example.local: it survives a change of network) or
                              address: a full client setup (snapcast-setup.sh --mode target --server, which
                              installs libnss-mdns on the board when a .local name needs it). Without it, an
                              installed snapclient is only re-routed (--routing-only).
  --server-ip <host>          the same as --server (the old name, kept).
  --snapcast-arg <arg>        one more argument for snapcast-setup.sh, repeatable.

  speakerd (see speakerd-setup.sh --help):
  --role <left|right|mono>    required on the first install.
  --amplifier <node>, --woofer-port <port>, --tweeter-port <port>
  --factory <file>            a graph document for factory.json (in target-remote mode, a local file).
  --import-filtergraph <file> an AudioPluginHost .filtergraph on the board, converted into factory.json.
  --stop-plugin-host          stop a running AudioPluginHost (and disable its systemd units) instead of
                              refusing. Checked for every component that may restart PipeWire into
                              the speakerd route (pipewire, speakerd, and snapcast --server, which
                              restarts it only to remove the old split fragment), not only speakerd.
  --plugin <plugin[:build]>   target-remote: a plugin bundle to copy, repeatable (default ${DEFAULT_PLUGINS[*]}).
  --arch <aarch64|native>     target-remote: the build tree the bundle comes from (default aarch64).
  --bundle <dir>              target: the bundle (default: the folder above this script, when it is one).

  ampd (see ampd-setup.sh --help):
  --name <name>               this speaker's name (default: device.json's role).
  --hub | --agent             the hub (the default for role left) or an agent of it.
  --hub-address <addr>        an agent's hub, until mDNS finds it (required for a new agent).
  --listen <addr>             the address ampd listens on (default 0.0.0.0).
  --secret-file <file>        the pair's secret (ampd-setup.sh --help), required on a first install. In
                              target-remote mode a file on this machine (default \$AMPD_SECRET_FILE, from the
                              environment or firmware/local.mk), copied with the bundle, never on a command line.

  --no-systemd, --home <dir>  for the virtual pair tests (see speakerd-setup.sh).
  -h, --help                  show this help.

Examples:
  # The first time, from the development machine, the board already running snapclient:
  tools/build.sh speakerd aarch64 && for p in loudness crossover firConv; do tools/build.sh \$p aarch64 headless; done
  ./setup.sh --mode target-remote --target-host user@bpi-amp.local --role left --stop-plugin-host --dry-run

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
        --server | --server-ip) SERVER="${2:?}"; remote "$1" "$2"; shift 2 ;;
        --snapcast-arg)    SNAPCAST_ARGS+=("${2:?}"); remote "$1" "$2"; shift 2 ;;
        --role)            SPEAKERD_ARGS+=("$1" "${2:?}"); AMPD_ARGS+=("$1" "$2"); remote "$1" "$2"; shift 2 ;;
        --amplifier | --woofer-port | --tweeter-port | --import-filtergraph)
                           SPEAKERD_ARGS+=("$1" "${2:?}"); remote "$1" "$2"; shift 2 ;;
        --stop-plugin-host) SPEAKERD_ARGS+=("$1"); remote "$1"; shift ;;
        --name | --hub-address | --listen)
                           AMPD_ARGS+=("$1" "${2:?}"); remote "$1" "$2"; shift 2 ;;
        --hub | --agent)   AMPD_ARGS+=("$1"); remote "$1"; shift ;;
        --secret-file)     SECRET_FILE="${2:?}"; shift 2 ;;
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
[ "$BASE_CONF" -eq 0 ] || selected pipewire || die "--base-conf${RATE:+ and --rate} belong to the pipewire component, which is not chosen: nothing would install pipewire.conf"

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
    [ -n "$SECRET_FILE" ] || ! selected ampd || SECRET_FILE="${AMPD_SECRET_FILE:-$(local_mk_value AMPD_SECRET_FILE)}"
    if [ -n "$SECRET_FILE" ]; then
        [ -r "$SECRET_FILE" ] || die "cannot read the secret file $SECRET_FILE"
        mkdir -p "$tmp/bundle/extra"
        (umask 077 && cp "$SECRET_FILE" "$tmp/bundle/extra/ampd.secret")
        REMOTE_ARGS+=(--secret-file extra/ampd.secret)
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

if [ -n "$SECRET_FILE" ]; then
    if [ -f "$SECRET_FILE" ]; then SECRET_FILE="$(cd "$(dirname "$SECRET_FILE")" && pwd)/$(basename "$SECRET_FILE")"
    elif [ -n "$BUNDLE" ] && [ -f "$BUNDLE/$SECRET_FILE" ]; then SECRET_FILE="$BUNDLE/$SECRET_FILE"
    else die "no secret file $SECRET_FILE"; fi
    AMPD_ARGS+=(--secret-file "$SECRET_FILE")
fi

common=()
[ "$DRY_RUN" -eq 0 ] || common+=(--dry-run)
[ "$NO_RESTART" -eq 0 ] || common+=(--no-restart)
test_hooks=()
[ "$NO_SYSTEMD" -eq 0 ] || test_hooks+=(--no-systemd)
[ -z "$HOME_OVERRIDE" ] || test_hooks+=(--home "$H")
speakerd_setup() { bash "$FIRMWARE_DIR/speakerd-setup.sh" "$@" ${BUNDLE:+--bundle "$BUNDLE"} "${common[@]}" "${test_hooks[@]}"; }
ampd_setup() { bash "$FIRMWARE_DIR/ampd-setup.sh" "$@" "${common[@]}" "${test_hooks[@]}"; }
snapcast_setup() {
    local extra=()
    [ "$DRY_RUN" -eq 0 ] || extra+=(--dry-run)
    if [ -n "$HOME_OVERRIDE" ] || [ "$NO_SYSTEMD" -eq 1 ] || [ "$NO_RESTART" -eq 1 ]; then extra+=(--no-restart); fi
    [ -z "$HOME_OVERRIDE" ] || extra+=(--client-home "$H")
    bash "$FIRMWARE_DIR/snapcast-setup.sh" "$@" "${extra[@]}" "${SNAPCAST_ARGS[@]}"
}
DROPIN="$H/.config/pipewire/pipewire.conf.d/60-speakerd-sink.conf"
MAIN_CONF="$H/.config/pipewire/pipewire.conf"
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

# --base-conf: the repository's pipewire.conf, at its own rate (48000) or at --rate's.
#
# The rates a pipewire.conf ($1) sets, one per line: "graph <rate>" (default.clock.rate),
# "allowed <rates>" (default.clock.allowed-rates when it is set, without spaces) and
# "object <factory.name> <node.name> <rate>" for each object that sets audio.rate, whatever the
# order of its keys. Commented lines are not read; the keys are one per line, as the
# repository's file has them (and as --rate rewrites them).
conf_rates() {
    awk '
        function value(line) {
            sub(/^[^=]*=[[:space:]]*/, "", line)
            if (line ~ /^"/) { sub(/^"/, "", line); sub(/".*$/, "", line) } else sub(/[[:space:]].*$/, "", line)
            return line
        }
        /^[[:space:]]*#/ { next }
        /\{[[:space:]]*factory[[:space:]]*=/ { n++; factory[n] = "?"; name[n] = "?" }
        /^[[:space:]]*factory\.name[[:space:]]*=/ { factory[n] = value($0) }
        /^[[:space:]]*node\.name[[:space:]]*=/ { name[n] = value($0) }
        /^[[:space:]]*default\.clock\.rate[[:space:]]*=/ { print "graph " value($0) }
        /^[[:space:]]*default\.clock\.allowed-rates[[:space:]]*=/ {
            v = $0; sub(/^[^=]*=[[:space:]]*/, "", v); sub(/\].*$/, "]", v); gsub(/[[:space:]]/, "", v); print "allowed " v
        }
        /^[[:space:]]*audio\.rate[[:space:]]*=/ { rate[n] = value($0) }
        END { for (i = 1; i <= n; i++) if (i in rate) print "object " factory[i] " " name[i] " " rate[i] }
    ' "$1"
}

# The rate PipeWire runs at now (its settings metadata), or nothing when it cannot be asked.
running_rate() {
    command -v pw-metadata > /dev/null 2>&1 || return 0
    { timeout 5 pw-metadata -n settings 0 clock.rate 2> /dev/null || true; } \
        | sed -n "s/.*key:'clock.rate' value:'\([0-9]*\)'.*/\1/p" | head -n 1
}

# Makes the file to install, BASE_FILE: the repository's pipewire.conf, with --rate in
# default.clock.rate, in default.clock.allowed-rates when it is set, and in the amplifier's
# (each api.alsa.pcm.sink object's) audio.rate; nothing else. Then checks that the graph and the amplifier run at one rate,
# BASE_RATE, and refuses otherwise: an amplifier at another rate than the graph resamples
# everything it plays. Called with the checks, before anything changes. Sets BASE_FILE,
# BASE_RATE and BASE_WHAT ("the graph and <amplifier> at <rate> Hz, <object> at <rate> Hz",
# for the messages).
BASE_FILE=""
BASE_RATE=""
BASE_WHAT=""
prepare_base_conf() {
    local src="$FIRMWARE_DIR/pipewire/pipewire.conf" rates bad
    [ -f "$src" ] || die "no $src"
    BASE_FILE="$(mktemp)"
    trap 'rm -f "$BASE_FILE"' EXIT
    # install-lib.sh's: status and smoke compare the installed file with the same rewrite.
    repository_conf_at_rate "$src" "$RATE" "$BASE_FILE"
    rates="$(conf_rates "$BASE_FILE")"
    BASE_RATE="$(awk '$1 == "graph" { r = $2 } END { print r }' <<< "$rates")"
    [ -n "$BASE_RATE" ] || die "$src sets no default.clock.rate: the graph's rate would be PipeWire's default, not the file's. Nothing was changed."
    [ -z "$RATE" ] || [ "$BASE_RATE" = "$RATE" ] || die "--rate $RATE did not reach default.clock.rate in $src (it says $BASE_RATE). Nothing was changed."
    bad="$(awk -v r="$BASE_RATE" '
        $1 == "graph" && $2 != r { printf "%sdefault.clock.rate %s", sep, $2; sep = ", " }
        $1 == "allowed" && $2 != "[" r "]" { printf "%sdefault.clock.allowed-rates %s", sep, $2; sep = ", " }
        $1 == "object" && $2 == "api.alsa.pcm.sink" && $4 != r { printf "%sthe amplifier %s at %s Hz", sep, $3, $4; sep = ", " }' <<< "$rates")"
    [ -z "$bad" ] || die "$src${RATE:+ at --rate $RATE} would run the graph at $BASE_RATE Hz but $bad: the amplifier runs at the graph's rate (firmware/CLAUDE.md, 'Rates'). Nothing was changed."
    BASE_WHAT="$(awk -v r="$BASE_RATE" '
        $1 == "object" && $2 == "api.alsa.pcm.sink" { amp[++a] = $3; next }
        $1 == "object" { other = other ", " $3 " at " $4 " Hz" }
        END { s = "the graph"; for (i = 1; i <= a; i++) s = s (i == a ? " and " : ", ") amp[i]; print s " at " r " Hz" other }' <<< "$rates")"
    ok "pipewire.conf to install: $BASE_WHAT"
    if [ "$BASE_RATE" != 48000 ]; then
        warn "at $BASE_RATE Hz PipeWire resamples snapcast's 48000 Hz stream, and speakerd's plugins run at $BASE_RATE Hz:"
        warn "the shipped crossover and firConv filters, designed for 48000 Hz, run with every frequency scaled by"
        warn "$BASE_RATE/48000 (firmware/CLAUDE.md, 'Rates')"
    fi
}

# A pipewire.conf that this script installed (is_repository_conf, install-lib.sh) is never
# kept as the board's own.

# Installs BASE_FILE as ~/.config/pipewire/pipewire.conf, keeping the board's own as .orig
# once. Sets BASE_CHANGED.
BASE_CHANGED=0
install_base_conf() {
    local dst="$H/.config/pipewire/pipewire.conf" now
    if [ -f "$dst" ] && cmp -s "$BASE_FILE" "$dst"; then ok "$dst is the repository's already: $BASE_WHAT"; return 0; fi
    now="$(running_rate)"
    if [ -n "$now" ] && [ "$now" != "$BASE_RATE" ]; then
        info "PipeWire runs at $now Hz now; with this pipewire.conf it runs at $BASE_RATE Hz once it restarts"
    fi
    if [ "$DRY_RUN" -eq 1 ]; then info "(dry-run) would install firmware/pipewire/pipewire.conf as $dst: $BASE_WHAT"; return 0; fi
    mkdir -p "$(dirname "$dst")"
    if [ -f "$dst" ] && [ ! -f "$dst.orig" ] && ! is_repository_conf "$dst"; then
        cp -a "$dst" "$dst.orig" && ok "Kept the board's own as $dst.orig"
    fi
    # Written whole: a temporary file beside it, then a rename.
    { cp "$BASE_FILE" "$dst.tmp.$$" && chmod 644 "$dst.tmp.$$" && mv -f "$dst.tmp.$$" "$dst"; } \
        || { rm -f "$dst.tmp.$$"; die "cannot write $dst"; }
    BASE_CHANGED=1
    ok "Wrote $dst: $BASE_WHAT"
}

# The drop-ins whose settings the repository's pipewire.conf has itself (REPO_CONF_HAS,
# install-lib.sh: the converter quality and the speakerd sink, which must not be there twice),
# removed from the user's pipewire.conf.d before PipeWire restarts. Sets BASE_CHANGED.
remove_contained_dropins() {
    local f
    for f in "${REPO_CONF_HAS[@]}"; do
        f="$H/.config/pipewire/pipewire.conf.d/$f"
        [ -e "$f" ] || [ -L "$f" ] || continue
        if [ "$DRY_RUN" -eq 1 ]; then info "(dry-run) would remove $f: the repository's pipewire.conf has what it sets"; continue; fi
        rm -f "$f" || die "cannot remove $f"
        ok "Removed $f: the repository's pipewire.conf has what it sets"
        BASE_CHANGED=1
    done
}

# ----------------------------------------------------------------------------
# Check every chosen component before changing any
# ----------------------------------------------------------------------------
info "Components, in boot order: ${CHOSEN[*]} (home $H)"
if selected pipewire && [ "$BASE_CONF" -eq 1 ]; then
    info "Checking the repository's pipewire.conf${RATE:+ at --rate $RATE}"
    prepare_base_conf
fi
if selected speakerd && [ "$ROUTE" = plugin-host ] && selected snapcast; then
    die "--route plugin-host with the speakerd component would leave the AudioPluginHost's rule pointing at the amplifier while speakerd plays it too: route into speakerd, or skip one of them"
fi
if selected speakerd; then
    info "Checking speakerd"
    [ -n "$BUNDLE" ] || die "speakerd needs a bundle (--bundle, or run the setup.sh of an unpacked bundle): nothing was changed"
    speakerd_setup check "${SPEAKERD_ARGS[@]}" || die "speakerd cannot be installed (see above): nothing was changed"
fi
if selected ampd; then
    info "Checking ampd"
    ampd_check=(check "${AMPD_ARGS[@]}")
    if selected speakerd; then ampd_check+=(--speakerd-too); fi
    ampd_setup "${ampd_check[@]}" || die "ampd cannot be installed (see above): nothing was changed"
fi
if selected snapcast; then
    if [ "$ROUTE" = speakerd ] && ! selected pipewire && [ ! -f "$DROPIN" ] && ! is_repository_conf "$MAIN_CONF"; then
        die "--route speakerd needs the speakerd sink, which is not configured ($DROPIN, or the repository's $MAIN_CONF): include the pipewire component"
    fi
    if [ -z "$SERVER" ] && [ ! -f "$SNAPCLIENT_UNIT" ]; then
        die "snapclient is not set up here ($SNAPCLIENT_UNIT): give --server for a full client setup, or --skip snapcast"
    fi
fi

# A running AudioPluginHost must not see PipeWire restart under it into the speakerd sink's
# configuration: it would be linked to the amplifier again beside speakerd, full range. The
# components that may restart PipeWire: pipewire, speakerd (its sink) and snapcast's full setup
# (--server, which restarts it only when it removes the old 60-snapcast-split.conf). With
# --route plugin-host and no speakerd, the plugin host is what plays: it stays.
STOP_PLUGIN_HOST=0
[[ " ${SPEAKERD_ARGS[*]} " != *" --stop-plugin-host "* ]] || STOP_PLUGIN_HOST=1
NO_PLUGIN_HOST=0
if { selected pipewire || selected speakerd || { selected snapcast && [ -n "$SERVER" ]; }; } \
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
                remove_contained_dropins
                [ "$BASE_CHANGED" -eq 0 ] || restart_pipewire
            fi
            if [ "$BASE_CONF" -eq 1 ] && [ "$DRY_RUN" -eq 1 ] && ! is_repository_conf "$MAIN_CONF"; then
                info "pipewire: (dry-run) the persistent speakerd sink would be the repository's pipewire.conf's: no drop-in"
            elif [ "$ROUTE" = speakerd ] || selected speakerd || [ -f "$DROPIN" ]; then
                # The drop-in (none with the repository's pipewire.conf, which has the sink),
                # a PipeWire restart if it changed, and the RUNNING PipeWire's speakerd node
                # checked: all fatal.
                info "pipewire: the persistent speakerd sink"
                speakerd_setup sink
            else
                info "pipewire: no speakerd sink (nothing plays into it with --route plugin-host and no speakerd)"
            fi
            ;;
        snapcast)
            if [ -n "$SERVER" ]; then
                info "snapcast: a snapclient of $SERVER, routed into $([ "$ROUTE" = speakerd ] && echo the speakerd sink || echo Ap.cap)"
                snapcast_setup --mode target --server "$SERVER" --route "$ROUTE"
            else
                info "snapcast: re-routing the installed snapclient into $([ "$ROUTE" = speakerd ] && echo the speakerd sink || echo Ap.cap)"
                snapcast_setup --mode target --routing-only --route "$ROUTE"
            fi
            ;;
        speakerd)
            info "speakerd: the headless plugin host"
            speakerd_setup install "${SPEAKERD_ARGS[@]}"
            ;;
        ampd)
            info "ampd: the network agent, after speakerd (the speaker plays without it)"
            if selected speakerd; then ampd_setup install "${AMPD_ARGS[@]}" --speakerd-too
            else ampd_setup install "${AMPD_ARGS[@]}"; fi
            ;;
    esac
done
echo
ok "Done: ${CHOSEN[*]}"
