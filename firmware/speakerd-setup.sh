#!/usr/bin/env bash
#
# speakerd-setup.sh
#
# Installs speakerd, the headless plugin host of the remote plugin host design
# (docs/remote-plugin-host.md), on a speaker, as a systemd user service of the user whose
# PipeWire drives the amplifier. It replaces the AudioPluginHost that does the DSP today.
# See firmware/CLAUDE.md, "speakerd on a board", for the layout and the reasons.
#
#   ~/.local/lib/speakerd/<version>/   bin/speakerd, bin/speakerd-run, lv2/*.lv2, VERSION, setup/
#   ~/.local/lib/speakerd/current      -> the version that runs; previous -> the one before
#   ~/.local/lib/speakerd/home/        speakerd's HOME: empty, so no ~/.lv2 shadows the bundles
#   ~/.local/state/speakerd/           device.json and factory.json (graph.json and
#                                      graph.lkg.json are ampd's, left alone)
#   ~/.config/systemd/user/speakerd.service
#   ~/.config/pipewire/pipewire.conf.d/60-speakerd-sink.conf    the persistent speakerd sink
#
# A version also holds ampd (bin/ampd), which ampd-setup.sh runs as its own service; status,
# smoke, rollback and uninstall include it when it is installed.
#
# Every install caps the output: device.json gets "limits": output at -20 dB, tweeter muted.
# Only the limits action lifts the cap, with a person at the speaker after a smoke test.
# install refuses everything it can refuse before it changes anything.
#
# Usage:
#   ./speakerd-setup.sh install --role left|right|mono [options]   # on the board, as its audio user
#   ./speakerd-setup.sh status | rollback | uninstall | smoke      # on the board
#   ./speakerd-setup.sh sink | check | limits | stop-plugin-host   # on the board
#   ./speakerd-setup.sh bundle --out DIR [--arch aarch64|native]  # on the development machine
#   ./speakerd-setup.sh <action> --mode target-remote [--target-host user@host] [options]
#
# Run `./speakerd-setup.sh --help` for the full option list.

set -euo pipefail

SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
# shellcheck source=install-lib.sh
. "$(dirname "$SELF")/install-lib.sh"
HELPER="$FIRMWARE_DIR/speakerd/setup-helper.py"

CAP_GAIN_DB=-20.0
CAP_TWEETER=muted
# The oldest speakerd that may play on a board: 0.2.0 is the first that enforces device.json's
# limits (and fails closed without them) and says its version (speakerd --version). An older one
# would ignore the cap, so install and rollback refuse it (dsp/juce_apps/speakerd/README.md).
SPEAKERD_MIN_VERSION=0.2.0
# speakerd's line for the limits it plays, exactly; with the fail-safe it adds a suffix, such as
# " (no limits in device.json: the fail-safe)", which is never device.json's limits.
LIMITS_LINE='^speakerd: device: limits: output at (-?[0-9.]+(e[-+]?[0-9]+)?) dB, tweeter (muted|on)$'
# What speakerd says when PipeWire had no speakerd sink and it made one itself: on a board, the
# drop-in is not loaded.
NO_SINK_LINE='^speakerd: \*\*\* NO INPUT SINK CONFIGURED'
RC_LOCAL=/etc/rc.local   # private-ok: a file on the board, not a host name
# The process name of the old plugin host, and the system LV2 folders JUCE reads before any
# --lv2-bundle. Both can be changed only for the virtual pair tests, which must not touch this
# machine's own AudioPluginHost or system folders.
PLUGIN_HOST="${SPEAKERD_PLUGIN_HOST_NAME:-AudioPluginHost}"
SYSTEM_LV2_DIRS="${SPEAKERD_SYSTEM_LV2_DIRS:-/usr/lib/lv2 /usr/local/lib/lv2}"

# ----------------------------------------------------------------------------
# Options
# ----------------------------------------------------------------------------
ACTION=""
MODE=target
TARGET_HOST=""
BUNDLE=""
ROLE=""
AMPLIFIER=""
WOOFER_PORT=""
TWEETER_PORT=""
FACTORY=""
IMPORT_FILTERGRAPH=""
ALLOW_FULL_RANGE_TWEETER=0
STOP_PLUGIN_HOST=0
KEEP_VERSIONS=3
PURGE=0
DRY_RUN=0
NO_SYSTEMD=0
NO_RESTART=0
HOME_OVERRIDE=""
AUDIO_USER=""
OUT=""
ARCH=aarch64
PLUGINS=()
GAIN_DB=""
TWEETER_LIMIT=""
CAP=0
PERSON_PRESENT=0
WINDOW=10
LOG_FILE=""
REMOTE_ARGS=()

usage() {
    awk 'NR>1 { if ($0 ~ /^#/) { sub(/^# ?/, ""); print } else exit }' "$SELF"
    cat <<EOF

Actions:
  install     install the bundle's speakerd as a new version, make it current and (re)start it
  status      what is installed, what runs, from which layer, linked to what
  rollback    make the previous version current again (and the current one previous)
  uninstall   restore snapclient's routing from before speakerd and re-enable the AudioPluginHost
              units install disabled, then remove the service, the sink and the versions;
              device.json and the layers stay unless --purge
  smoke       check without making a sound that speakerd plays capped, through the drop-in's
              speakerd sink, linked to the amplifier, driven by it, with no restart or xrun for a
              while. Run it with a quiet stream playing: who drives is measured only when the
              graph runs
  sink        install the persistent speakerd sink and check that the running PipeWire has it
  check       what install would refuse, without changing anything
  limits      change device.json's limits (the cap), then restart speakerd. No limit at all is
              --gain-db 0 --tweeter-limit on (written explicitly: without limits speakerd
              plays capped)
  stop-plugin-host
              stop a running AudioPluginHost and disable the systemd units that start it
              (setup.sh does this before it restarts PipeWire); uninstall re-enables them
  bundle      assemble a bundle from the build tree (development machine; builds nothing)

Options:
  --mode <target|target-remote>
                              target (default): this machine is the speaker.
                              target-remote: copy a bundle to the board and run the action there
                              (with --dry-run too: the board-side dry run changes nothing).
  --target-host <user@host>   the board, as its audio user, never root (default: \$BOARD_USER@\$BOARD_HOST,
                              from the environment or firmware/local.mk; else user@bpi-amp.local).
  --bundle <dir>              the bundle to install (default: the folder above this script, when it is one).
  --role <left|right|mono>    which channel this speaker plays. Required on the first install; a
                              reinstall keeps device.json's role, amplifier and ports unless given.
  --amplifier <node>          the amplifier's PipeWire node (default: device.json's, else the ALSA
                              Audio/Sink found with pw-dump; bpi-alsa-playback-pcm or alsa-playback-pcm
                              when there are several).
  --woofer-port <port>        the amplifier port of the woofer (default: device.json's, else playback_FL).
  --tweeter-port <port>       the amplifier port of the tweeter (default: device.json's, else playback_FR).
                              Which port is which driver is a fact of the board's wiring: confirm it at
                              the speaker.
  --factory <file>            a graph document (version 1) to install as factory.json. In target-remote
                              mode a file on this machine, copied with the bundle.
  --import-filtergraph <file> an AudioPluginHost .filtergraph on the board: speakerd converts it into
                              factory.json (speakerd --import-filtergraph --prefer-headless --device).
  --allow-tweeter-without-crossover
                              accept a factory graph in which the stream reaches the tweeter other than
                              through a crossover's high band. Only with --home (the virtual pair tests):
                              refused on a board and in target-remote mode.
  --stop-plugin-host          stop a running AudioPluginHost (and disable a systemd unit that starts
                              it) instead of refusing to install while one runs. It is stopped only
                              once every other check has passed.
  --keep <n>                  versions to keep, current and previous included (default $KEEP_VERSIONS).
  --purge                     uninstall: also remove ~/.local/state/speakerd.
  --cap                       limits: back to the cap (output at $CAP_GAIN_DB dB, tweeter $CAP_TWEETER).
  --gain-db <dB> --tweeter-limit <muted|on>
                              limits: the output gain limit (0 or below) and the tweeter.
  --person-present            limits: required for anything above the cap. ONLY after a smoke test has
                              passed, with a person at the speaker: these lift the protection of the
                              drivers. No agent and no script passes this.
  --window <seconds>          smoke: how long to watch for restarts and xruns (default $WINDOW).
  --out <dir>                 bundle: where to assemble it.
  --arch <aarch64|native>     bundle and target-remote: which build tree (default aarch64: build/aarch64/...).
  --plugin <plugin[:build]>   bundle: a plugin bundle to include, repeatable (default:
                              ${DEFAULT_PLUGINS[*]}).
  --user <name>               the board's audio user, when run as root (default: \$SUDO_USER, \$BOARD_USER).
  --dry-run                   say what would change, change nothing.
  --no-restart                write the files, but do not restart PipeWire or speakerd.
  --no-systemd, --home <dir>, --log <file>
                              for the virtual pair tests: no systemctl, journalctl or loginctl; install
                              into <dir> as if it were the user's home; smoke reads speakerd's log from
                              <file>.
  -h, --help                  show this help.

Examples:
  # On the development machine: build, then check on the board what install would do
  tools/build.sh speakerd aarch64 && tools/build.sh loudness aarch64 headless
  ./speakerd-setup.sh install --mode target-remote --target-host user@bpi-amp.local --role left \\
      --plugin loudness:headless --import-filtergraph /home/user/session.filtergraph --dry-run

  # On the board, from an unpacked bundle in the home (not in a noexec /tmp)
  ./firmware/speakerd-setup.sh install --role left
  ./firmware/speakerd-setup.sh smoke
EOF
}

ORIG_ARGS=("$@")
[ $# -gt 0 ] || { usage; exit 2; }
case "$1" in
    -h | -help | --help) usage; exit 0 ;;
    install | status | rollback | uninstall | smoke | sink | check | limits | stop-plugin-host | bundle) ACTION="$1"; shift ;;
    *) die "unknown action '$1' (try --help)" ;;
esac
remote() { REMOTE_ARGS+=("$@"); }
while [ $# -gt 0 ]; do
    case "$1" in
        --mode)            MODE="${2:?}"; shift 2 ;;
        --target-host)     TARGET_HOST="${2:?}"; shift 2 ;;
        --bundle)          BUNDLE="${2:?}"; shift 2 ;;
        --role)            ROLE="${2:?}"; remote "$1" "$2"; shift 2 ;;
        --amplifier)       AMPLIFIER="${2:?}"; remote "$1" "$2"; shift 2 ;;
        --woofer-port)     WOOFER_PORT="${2:?}"; remote "$1" "$2"; shift 2 ;;
        --tweeter-port)    TWEETER_PORT="${2:?}"; remote "$1" "$2"; shift 2 ;;
        --factory)         FACTORY="${2:?}"; shift 2 ;;
        --import-filtergraph) IMPORT_FILTERGRAPH="${2:?}"; remote "$1" "$2"; shift 2 ;;
        --allow-tweeter-without-crossover) ALLOW_FULL_RANGE_TWEETER=1; shift ;;
        --stop-plugin-host) STOP_PLUGIN_HOST=1; remote "$1"; shift ;;
        --keep)            KEEP_VERSIONS="${2:?}"; remote "$1" "$2"; shift 2 ;;
        --purge)           PURGE=1; remote "$1"; shift ;;
        --cap)             CAP=1; remote "$1"; shift ;;
        --gain-db)         GAIN_DB="${2:?}"; remote "$1" "$2"; shift 2 ;;
        --tweeter-limit)   TWEETER_LIMIT="${2:?}"; remote "$1" "$2"; shift 2 ;;
        --person-present)  PERSON_PRESENT=1; remote "$1"; shift ;;
        --window)          WINDOW="${2:?}"; remote "$1" "$2"; shift 2 ;;
        --out)             OUT="${2:?}"; shift 2 ;;
        --arch)            ARCH="${2:?}"; shift 2 ;;
        --plugin)          PLUGINS+=("${2:?}"); shift 2 ;;
        --user)            AUDIO_USER="${2:?}"; remote "$1" "$2"; shift 2 ;;
        --dry-run)         DRY_RUN=1; remote "$1"; shift ;;
        --no-restart)      NO_RESTART=1; remote "$1"; shift ;;
        --no-systemd)      NO_SYSTEMD=1; shift ;;
        --home)            HOME_OVERRIDE="${2:?}"; shift 2 ;;
        --log)             LOG_FILE="${2:?}"; shift 2 ;;
        -h | -help | --help) usage; exit 0 ;;
        *)                 die "unknown option: $1 (try --help)" ;;
    esac
done
case "$MODE" in target | target-remote) ;; *) die "--mode is target or target-remote" ;; esac
case "$ARCH" in aarch64 | native) ;; *) die "--arch is aarch64 or native" ;; esac
case "$ROLE" in "" | left | right | mono) ;; *) die "--role is left, right or mono" ;; esac
case "$KEEP_VERSIONS" in [2-9] | [1-9][0-9]) ;; *) die "--keep takes a number of versions, 2 or more" ;; esac
case "$WINDOW" in [0-9] | [0-9][0-9] | [0-9][0-9][0-9]) ;; *) die "--window takes whole seconds, 0 to 999" ;; esac
# A factory graph that feeds the tweeter other than through a crossover's high band is for the
# virtual pair tests only (--home), never for a board.
if [ "$ALLOW_FULL_RANGE_TWEETER" -eq 1 ] && { [ "$MODE" = target-remote ] || [ -z "$HOME_OVERRIDE" ]; }; then
    die "--allow-tweeter-without-crossover is for the virtual pair tests (--home) only, never for a board"
fi

# ----------------------------------------------------------------------------
# bundle (development machine) and the remote mode
# ----------------------------------------------------------------------------
if [ "$ACTION" = bundle ]; then
    [ -n "$OUT" ] || die "bundle needs --out <dir>"
    make_bundle "$OUT" "$ARCH" "${PLUGINS[@]}"
    ok "Bundle $(version_value "$OUT/VERSION" id) in $OUT"
    sed 's/^/    /' "$OUT/VERSION"
    exit 0
fi

if [ "$MODE" = target-remote ]; then
    [ -n "$TARGET_HOST" ] || TARGET_HOST="$(board_target)"
    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' EXIT
    # install and check need the artifacts: check runs every check install does.
    case "$ACTION" in
        install | check) make_bundle "$tmp/bundle" "$ARCH" "${PLUGINS[@]}" ;;
        *)               make_bundle "$tmp/bundle" none ;;
    esac
    if [ -n "$FACTORY" ]; then
        mkdir -p "$tmp/bundle/extra"
        cp "$FACTORY" "$tmp/bundle/extra/factory.json"
        REMOTE_ARGS+=(--factory extra/factory.json)
    fi
    info "speakerd $ACTION on $TARGET_HOST, bundle $(version_value "$tmp/bundle/VERSION" id)"
    remote_run "$TARGET_HOST" "$tmp/bundle" speakerd-setup.sh "$ACTION" "${REMOTE_ARGS[@]}"
    exit $?
fi

# ----------------------------------------------------------------------------
# The user and the paths (local mode)
# ----------------------------------------------------------------------------
if [ -z "$BUNDLE" ] && [ -f "$FIRMWARE_DIR/../VERSION" ] && [ -e "$FIRMWARE_DIR/../bin/speakerd" ]; then
    BUNDLE="$FIRMWARE_DIR/.."
fi
[ -z "$BUNDLE" ] || BUNDLE="$(cd "$BUNDLE" && pwd)"

if [ -n "$HOME_OVERRIDE" ]; then
    mkdir -p "$HOME_OVERRIDE"
    H="$(cd "$HOME_OVERRIDE" && pwd)"
    AUDIO_USER="$(id -un)"
elif [ "$(id -u)" -eq 0 ]; then
    # Everything belongs to the audio user and runs in that user's session: do it as that user.
    user="${AUDIO_USER:-${SUDO_USER:-${BOARD_USER:-}}}"
    [ -n "$user" ] && [ "$user" != root ] || die "Running as root: pass --user <name>, the user whose PipeWire drives the amplifier"
    uid="$(id -u "$user" 2> /dev/null)" || die "no user '$user' on this machine"
    for path in "$SELF" ${BUNDLE:+"$BUNDLE/VERSION"}; do
        runuser -u "$user" -- test -r "$path" \
            || die "$user cannot read $path: run this as $user from a folder $user can read (the remote mode: ssh as $user, not root)"
    done
    if [ "$DRY_RUN" -eq 0 ] && [ "$NO_SYSTEMD" -eq 0 ]; then
        if loginctl enable-linger "$user" 2> /dev/null; then ok "Linger is on for $user"; else warn "could not enable linger for $user"; fi
    fi
    exec runuser -u "$user" -- env "XDG_RUNTIME_DIR=/run/user/$uid" "HOME=$(getent passwd "$user" | cut -d: -f6)" \
        bash "$SELF" "${ORIG_ARGS[@]}"
else
    [ -z "$AUDIO_USER" ] || [ "$AUDIO_USER" = "$(id -un)" ] || die "run this as $AUDIO_USER (or as root with --user $AUDIO_USER)"
    AUDIO_USER="$(id -un)"
    H="$(getent passwd "$AUDIO_USER" | cut -d: -f6)"
    [ -n "$H" ] || H="$HOME"
fi
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

LIB="$H/.local/lib/speakerd"
STATE="$H/.local/state/speakerd"
UNIT="$H/.config/systemd/user/speakerd.service"
DROPIN="$H/.config/pipewire/pipewire.conf.d/60-speakerd-sink.conf"
CLIENT_RT="$H/.config/pipewire/client-rt.conf"
RUNTIME="$XDG_RUNTIME_DIR/speakerd"
PLUGIN_HOST_UNITS="$STATE/plugin-host-units"
DROPIN_SOURCE="$FIRMWARE_DIR/pipewire/pipewire.conf.d/60-speakerd-sink.conf"
UNIT_SOURCE="$FIRMWARE_DIR/speakerd/speakerd.service"
AMPD_UNIT="$H/.config/systemd/user/ampd.service"

if [ -n "$FACTORY" ] && [ ! -f "$FACTORY" ]; then
    if [ -n "$BUNDLE" ] && [ -f "$BUNDLE/$FACTORY" ]; then FACTORY="$BUNDLE/$FACTORY"; else die "no factory graph $FACTORY"; fi
fi
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
helper() { python3 "$HELPER" "$@"; }

# ampd (ampd-setup.sh), with this run's test hooks; installed when its unit or ampd.json is.
ampd_installed() { [ -f "$AMPD_UNIT" ] || [ -f "$STATE/ampd.json" ]; }
ampd_setup() {
    local hooks=()
    [ "$NO_SYSTEMD" -eq 0 ] || hooks+=(--no-systemd)
    [ -z "$HOME_OVERRIDE" ] || hooks+=(--home "$H")
    bash "$FIRMWARE_DIR/ampd-setup.sh" "$@" "${hooks[@]}"
}

# systemctl --user, unless --no-systemd.
sc() {
    if [ "$NO_SYSTEMD" -eq 1 ]; then info "(no systemd) systemctl --user $*"; return 0; fi
    systemctl --user "$@"
}

dry() { [ "$DRY_RUN" -eq 1 ]; }
# The virtual pair tests: the live checks are skipped when no PipeWire is reachable.
test_mode() { [ -n "$HOME_OVERRIDE" ]; }

pipewire_reachable() { timeout 5 pw-cli info 0 > /dev/null 2>&1; }

pw_dump() {   # $1 = file
    timeout 10 pw-dump > "$1" 2> /dev/null && [ -s "$1" ]
}

graph_rate() {
    { timeout 5 pw-metadata -n settings 0 clock.rate 2> /dev/null || true; } | sed -n "s/.*key:'clock.rate' value:'\([0-9]*\)'.*/\1/p" | head -n 1
}

# A value from a JSON text: $1 = the text, $2 = a Python expression of d (the document).
json_get() {
    python3 -c "import json,sys; d=json.load(sys.stdin); v=($2); print('' if v is None else v)" <<< "$1" 2> /dev/null || true
}

# Installs $1 as $2 when the content differs (atomically: a temporary file, then a rename).
# Returns 0 when it changed something, 1 when $2 was already the same.
install_file() {
    local src="$1" dst="$2" mode="${3:-644}"
    if [ -f "$dst" ] && cmp -s "$src" "$dst"; then return 1; fi
    if dry; then info "(dry-run) would write $dst"; return 0; fi
    mkdir -p "$(dirname "$dst")"
    cp "$src" "$dst.tmp.$$" && chmod "$mode" "$dst.tmp.$$" && mv -f "$dst.tmp.$$" "$dst"
    ok "Wrote $dst"
    return 0
}

# Points the symlink $1 at $2, atomically.
point() {
    ln -sfn "$2" "$1.tmp.$$" && mv -Tf "$1.tmp.$$" "$1"
}

link_target() { if [ -L "$1" ]; then basename "$(readlink "$1")"; fi; }

# What a speakerd binary says about its version (speakerd --version): sets FOUND_VERSION (empty
# when it cannot say: a speakerd from before --version, which also predates the limits) and
# VERSION_STATUS (its exit status; 126 when it cannot be executed).
speakerd_version() {   # $1 = the binary
    local out
    mkdir -p "$WORK/empty-home"
    # Either stream: only a speakerd that exits 0 has a version to say.
    VERSION_STATUS=0
    out="$(HOME="$WORK/empty-home" timeout 10 "$1" --version 2>&1)" || VERSION_STATUS=$?
    [ "$VERSION_STATUS" -eq 0 ] || out=""
    FOUND_VERSION="$(sed -n -E 's/^[^0-9]*([0-9]+\.[0-9]+(\.[0-9]+)?).*/\1/p' <<< "$out" | head -n 1)"
    # The whole line, with the build's commit when speakerd says it ("speakerd 0.2.1 (<commit>)").
    FOUND_BUILD="$(head -n 1 <<< "$out")"
}

# Whether version $1 is $2 or newer.
version_at_least() { [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n 1)" = "$2" ]; }

# Refuses a speakerd older than SPEAKERD_MIN_VERSION, or one that cannot run. $1 = the binary,
# $2 = what it is. Sets FOUND_VERSION.
require_speakerd_version() {
    speakerd_version "$1"
    if [ "$VERSION_STATUS" -eq 126 ]; then
        die "$2 cannot be executed ($1: permission denied): is its file system mounted noexec, as /tmp often is? Put the bundle in the home and run it from there."
    fi
    if [ -z "$FOUND_VERSION" ] || ! version_at_least "$FOUND_VERSION" "$SPEAKERD_MIN_VERSION"; then
        die "$2 is ${FOUND_VERSION:-too old to say its version (no --version)}: a board needs speakerd $SPEAKERD_MIN_VERSION or newer, the first that enforces device.json's limits. An older one would ignore the cap."
    fi
}

# Whether systemd says a unit is enabled. $1 = user|system, $2 = the unit. Without systemd
# (--no-systemd) nothing is known: not enabled.
unit_enabled() {
    [ "$NO_SYSTEMD" -eq 0 ] || return 1
    if [ "$1" = user ]; then systemctl --user is-enabled --quiet "$2" 2> /dev/null
    else systemctl is-enabled --quiet "$2" 2> /dev/null; fi
}

# The AudioPluginHost processes that run (any user), by command name.
plugin_host_pids() {
    local f comm pid stat pids=()
    for f in /proc/[0-9]*/comm; do
        { read -r comm < "$f"; } 2> /dev/null || continue
        [ "$comm" = "$PLUGIN_HOST" ] || continue
        pid="${f#/proc/}"; pid="${pid%/comm}"
        # A zombie (state Z) has exited already: it plays nothing.
        { stat="$(< "/proc/$pid/stat")"; } 2> /dev/null || continue
        stat="${stat##*) }"; [ "${stat%% *}" != Z ] || continue
        pids+=("$pid")
    done
    echo "${pids[*]}"
}

# Files that start an AudioPluginHost at boot or login. The virtual pair tests look in their
# home only: this machine's /etc is not theirs.
plugin_host_autostarts() {
    local f files=("$H"/.config/systemd/user/*.service "$H"/.config/autostart/*.desktop "$H"/.config/lxsession/*/autostart
                   "$H"/.profile "$H"/.bash_profile "$H"/.bashrc "$H"/.xsessionrc "$H"/.xinitrc "$H"/.xprofile)
    test_mode || files+=(/etc/systemd/user/*.service /etc/systemd/system/*.service /etc/xdg/autostart/*.desktop "$RC_LOCAL")
    for f in "${files[@]}"; do
        if [ -f "$f" ] && grep -q "$PLUGIN_HOST" "$f" 2> /dev/null; then echo "$f"; fi
    done
    if ! test_mode && command -v crontab > /dev/null && crontab -l 2> /dev/null | grep -q "$PLUGIN_HOST"; then
        echo "the crontab of $AUDIO_USER"
    fi
    return 0
}

# Refuses while an AudioPluginHost runs, unless --stop-plugin-host (then stop_plugin_host does
# it, once nothing else can refuse). $1 = install also refuses while anything would start one
# again at boot or login, beside speakerd: a desktop autostart, a profile, rc.local or the
# crontab always (a person removes them), a systemd unit unless --stop-plugin-host disables it
# (or did). $1 = running checks only the running one (setup.sh before it restarts PipeWire).
refuse_plugin_host() {
    local mode="${1:-install}" pids autostarts f unit blocking=()
    pids="$(plugin_host_pids)"
    if [ -n "$pids" ]; then
        if [ "$STOP_PLUGIN_HOST" -eq 0 ]; then
            loud "$PLUGIN_HOST runs (pid $pids). It feeds the amplifier today, and speakerd would feed it" \
                 "too: two plugin hosts at once, the tweeter included. Refusing." \
                 "Close it, or pass --stop-plugin-host to have it stopped."
            exit 1
        fi
        info "$PLUGIN_HOST runs (pid $pids): it will be stopped once every check has passed (--stop-plugin-host)"
    fi
    [ "$mode" = install ] || return 0
    autostarts="$(plugin_host_autostarts)"
    [ -n "$autostarts" ] || return 0
    while read -r f; do
        [ -n "$f" ] || continue
        unit="$(basename "$f")"
        case "$f" in
            "$H"/.config/systemd/user/*.service)
                if [ "$STOP_PLUGIN_HOST" -eq 1 ]; then info "$f: will be disabled (--stop-plugin-host)"; continue; fi
                if grep -qxF "user $unit" "$PLUGIN_HOST_UNITS" 2> /dev/null && ! unit_enabled user "$unit"; then
                    continue    # an earlier install disabled it, and it stayed so
                fi
                blocking+=("$f: a user unit; pass --stop-plugin-host, or: systemctl --user disable --now $unit") ;;
            /etc/systemd/system/*.service)
                if grep -qxF "system $unit" "$PLUGIN_HOST_UNITS" 2> /dev/null && ! unit_enabled system "$unit"; then
                    continue
                fi
                if [ "$STOP_PLUGIN_HOST" -eq 1 ] && sudo -n true 2> /dev/null; then
                    info "$f: will be disabled (--stop-plugin-host)"; continue
                fi
                blocking+=("$f: a system unit; run: sudo systemctl disable --now $unit") ;;
            /etc/systemd/user/*.service)
                blocking+=("$f: a unit for every user; run: sudo systemctl --global disable $unit") ;;
            "the crontab of "*)
                blocking+=("$f: remove the line that starts $PLUGIN_HOST (crontab -e)") ;;
            *.desktop)
                blocking+=("$f: a desktop autostart; delete the file") ;;
            *)
                blocking+=("$f: remove the line that starts $PLUGIN_HOST") ;;
        esac
    done <<< "$autostarts"
    if [ ${#blocking[@]} -gt 0 ]; then
        loud "$PLUGIN_HOST would start again at boot or login, and feed the amplifier beside speakerd:" \
             "${blocking[@]/#/    }" "Refusing to install until these are gone."
        exit 1
    fi
}

# Notes a unit stop_plugin_host disabled, once, for uninstall.
record_unit() {   # $1 = user|system, $2 = the unit
    mkdir -p "$STATE"
    grep -qxF "$1 $2" "$PLUGIN_HOST_UNITS" 2> /dev/null || echo "$1 $2" >> "$PLUGIN_HOST_UNITS"
}

# Stops the running AudioPluginHost and disables the systemd units that start it; uninstall
# re-enables the units recorded in $PLUGIN_HOST_UNITS.
stop_plugin_host() {
    [ "$STOP_PLUGIN_HOST" -eq 1 ] || return 0
    local pids f unit
    pids="$(plugin_host_pids)"
    if [ -n "$pids" ]; then
        if dry; then
            info "(dry-run) would stop $PLUGIN_HOST (pid $pids)"
        else
            loud "Stopping $PLUGIN_HOST (pid $pids): speakerd replaces it (--stop-plugin-host)."
            # shellcheck disable=SC2086  # one argument per pid
            kill -TERM $pids 2> /dev/null || sudo -n kill -TERM $pids 2> /dev/null || die "cannot stop $PLUGIN_HOST (pid $pids)"
            for _ in $(seq 1 50); do [ -z "$(plugin_host_pids)" ] && break; sleep 0.1; done
            pids="$(plugin_host_pids)"
            # shellcheck disable=SC2086
            [ -z "$pids" ] || { kill -KILL $pids 2> /dev/null || sudo -n kill -KILL $pids 2> /dev/null || true; sleep 0.5; }
            [ -z "$(plugin_host_pids)" ] || die "$PLUGIN_HOST (pid $(plugin_host_pids)) is still running"
            ok "$PLUGIN_HOST stopped"
        fi
    fi
    while read -r f; do
        [ -n "$f" ] || continue
        unit="$(basename "$f")"
        case "$f" in
            "$H"/.config/systemd/user/*.service)
                if grep -qxF "user $unit" "$PLUGIN_HOST_UNITS" 2> /dev/null && ! unit_enabled user "$unit"; then continue
                elif dry; then info "(dry-run) would disable the user unit $unit"
                elif sc disable --now "$unit"; then
                    record_unit user "$unit"; ok "Disabled the user unit $unit"
                else die "could not disable the user unit $unit: systemctl --user disable --now $unit"; fi ;;
            /etc/systemd/system/*.service)
                if grep -qxF "system $unit" "$PLUGIN_HOST_UNITS" 2> /dev/null && ! unit_enabled system "$unit"; then continue
                elif dry; then info "(dry-run) would disable the system unit $unit"
                elif [ "$NO_SYSTEMD" -eq 0 ] && sudo -n systemctl disable --now "$unit" 2> /dev/null; then
                    record_unit system "$unit"; ok "Disabled the system unit $unit"
                else die "could not disable the system unit $unit: sudo systemctl disable --now $unit"; fi ;;
            *) ;;   # refuse_plugin_host refused these already
        esac
    done <<< "$(plugin_host_autostarts)"
}

# Every library the bundle's speakerd and plugins need is on this machine. speakerd needs
# libpipewire-0.3, libstdc++ (GLIBCXX_3.4.30) and glibc (2.34); the plugins also libfreetype
# and libfontconfig, ampScope libasound. ldd says "not found" for a missing library or symbol
# version.
check_libraries() {
    command -v ldd > /dev/null || { warn "no ldd here: the libraries speakerd needs are not checked"; return 0; }
    local f missing=() line
    for f in "$BUNDLE/bin/speakerd" "$BUNDLE"/lv2/*.lv2/*.so; do
        [ -f "$f" ] || continue
        while read -r line; do
            missing+=("${f#"$BUNDLE"/}: $line")
        done < <(ldd "$f" 2>&1 | grep -E 'not found|not a dynamic' | sed 's/^[[:space:]]*//')
    done
    if [ ${#missing[@]} -gt 0 ]; then
        printf '    %s\n' "${missing[@]}" >&2
        die "libraries missing on this machine (Debian: libpipewire-0.3-0, libstdc++6, libfreetype6, libfontconfig1, libasound2)"
    fi
    ok "Every library speakerd and its plugins need is here"
}

# A plugin bundle with one of the bundle's URIs in a system LV2 folder: JUCE reads those before
# any --lv2-bundle, so speakerd would refuse to start (status 2) and the speaker stays silent.
check_lv2_shadows() {
    local manifest uri dir other hits=()
    for manifest in "$BUNDLE"/lv2/*.lv2/manifest.ttl; do
        [ -f "$manifest" ] || continue
        while read -r uri; do
            [ -n "$uri" ] || continue
            for dir in $SYSTEM_LV2_DIRS; do
                for other in "$dir"/*/manifest.ttl; do
                    [ -f "$other" ] || continue
                    grep -qF "<$uri>" "$other" 2> /dev/null && hits+=("$uri: $(dirname "$other")")
                done
            done
        done < <(grep -oE '^<[^>]+>' "$manifest" | tr -d '<>' | sort -u)
    done
    if [ ${#hits[@]} -gt 0 ]; then
        loud "A system LV2 folder has a plugin with the URI of one of the bundle's:" "${hits[@]/#/    }" \
             "JUCE reads it before --lv2-bundle, so speakerd will refuse to start (status 2) and the speaker" \
             "stays silent. Remove it (it is not this installer's to remove)."
    fi
}

# Checks the bundle; sets BUNDLE_ID and BUNDLE_SPEAKERD_VERSION.
check_bundle() {
    [ -n "$BUNDLE" ] || die "no bundle: pass --bundle <dir> (made by 'speakerd-setup.sh bundle' or setup.sh on the development machine)"
    [ -f "$BUNDLE/VERSION" ] || die "$BUNDLE is not a bundle: it has no VERSION"
    [ -f "$BUNDLE/bin/speakerd" ] || die "$BUNDLE has no bin/speakerd"
    BUNDLE_ID="$(version_value "$BUNDLE/VERSION" id)"
    [ -n "$BUNDLE_ID" ] && [ "$BUNDLE_ID" != none ] || die "$BUNDLE/VERSION has no id: a bundle without speakerd (made for status or rollback)"
    local machine here hash
    machine="$(elf_machine "$BUNDLE/bin/speakerd")"
    here="$(uname -m)"; [ "$here" = arm64 ] && here=aarch64
    [ "$machine" = "$here" ] || die "the bundle's speakerd is built for $machine, this machine is $here"
    hash="$(contents_hash "$BUNDLE")"
    [ "${BUNDLE_ID##*-}" = "$hash" ] || die "the bundle's contents do not match its VERSION (hash $hash, id $BUNDLE_ID): copied incompletely?"
    check_libraries
    require_speakerd_version "$BUNDLE/bin/speakerd" "the bundle's speakerd"
    BUNDLE_SPEAKERD_VERSION="$FOUND_VERSION"
    BUNDLE_SPEAKERD_BUILD="$FOUND_BUILD"
    ok "$BUNDLE_SPEAKERD_BUILD (at least $SPEAKERD_MIN_VERSION)"
    # speakerd says the commit it was built from; the bundle, the commit it was made at. Apart,
    # the artifacts were built before the deployed commit was checked out.
    local built_from bundled_at
    built_from="$(sed -n -E 's/.*\(([0-9a-f]{7,40})(-dirty)?\).*/\1/p' <<< "$BUNDLE_SPEAKERD_BUILD")"
    bundled_at="$(version_value "$BUNDLE/VERSION" commit)"
    if [ -n "$built_from" ] && [ -n "$bundled_at" ] && [[ "$bundled_at" != "$built_from"* ]]; then
        warn "speakerd was built from commit $built_from, the bundle made at ${bundled_at:0:12}: rebuild after checking out the commit you deploy (tools/build.sh speakerd aarch64)"
    fi
    check_lv2_shadows
}

# An existing device.json speakerd would reject (a key it does not know, a key twice) is
# refused: it would play with its fail-safe defaults. A file that is not JSON is replaced with
# --role (kept as device.json.broken). Sets DEVICE_BROKEN.
DEVICE_BROKEN=0
check_device_json() {
    [ -f "$STATE/device.json" ] || return 0
    local why
    why="$(helper device "$STATE/device.json" --check 2>&1)" && return 0
    if [ -n "$ROLE" ] && ! grep -q 'key(s)' <<< "$why"; then warn "$why"; DEVICE_BROKEN=1; return 0; fi
    printf '%s\n' "$why" >&2
    die "fix $STATE/device.json first"
}

# The amplifier and its ports for device.json: an option, else device.json's, else (the node)
# what pw-dump shows. Sets AMP_ARGS (for the helper) and says what it chose and why.
choose_amplifier() {
    AMP_ARGS=()
    local cur="" cur_node="" cur_woofer="" cur_tweeter="" dump node="" ports="" priority="" alsa="" rate="" node_id=""
    local woofer tweeter p out
    if [ -f "$STATE/device.json" ] && [ "$DEVICE_BROKEN" -eq 0 ]; then
        cur="$(helper device "$STATE/device.json" --show 2> /dev/null || true)"
        cur_node="$(json_get "$cur" 'd["amplifier"]["node"] if isinstance(d.get("amplifier"), dict) else d.get("amplifier")')"
        cur_woofer="$(json_get "$cur" '(d.get("amplifier") if isinstance(d.get("amplifier"), dict) else {}).get("woofer")')"
        cur_tweeter="$(json_get "$cur" '(d.get("amplifier") if isinstance(d.get("amplifier"), dict) else {}).get("tweeter")')"
    fi
    if [ -n "$cur_node" ] && [ -z "$AMPLIFIER$WOOFER_PORT$TWEETER_PORT" ]; then
        info "Keeping the amplifier of $STATE/device.json: '$cur_node' (woofer ${cur_woofer:-playback_FL}, tweeter ${cur_tweeter:-playback_FR})"
        return 0
    fi
    dump="$WORK/amp.json"
    local which=()
    if [ -n "$AMPLIFIER" ]; then which=(--node "$AMPLIFIER"); elif [ -n "$cur_node" ]; then which=(--node "$cur_node"); fi
    if pw_dump "$dump"; then
        if out="$(helper amp "$dump" "${which[@]}")"; then
            eval "$out"
        else
            die "cannot tell which node is the amplifier (see above)"
        fi
    else
        node="${AMPLIFIER:-$cur_node}"
        [ -n "$node" ] || die "cannot reach PipeWire to find the amplifier: run this in the audio user's session, or name it with --amplifier"
        warn "cannot reach PipeWire: the amplifier '$node' and its ports are not checked"
    fi
    : "$node_id"
    woofer="${WOOFER_PORT:-${cur_woofer:-playback_FL}}"; tweeter="${TWEETER_PORT:-${cur_tweeter:-playback_FR}}"
    if [ -n "$ports" ]; then
        for p in "$woofer" "$tweeter"; do
            case " $ports " in *" $p "*) ;; *) die "the amplifier '$node' has no port $p (its ports: $ports): name them with --woofer-port and --tweeter-port" ;; esac
        done
    fi
    [ "$woofer" != "$tweeter" ] || die "the woofer and the tweeter cannot both be $woofer"
    AMP_ARGS=(--amplifier "$node" --woofer "$woofer" --tweeter "$tweeter")
    info "The amplifier: '$node'${alsa:+ (ALSA: $alsa)}${rate:+, $rate Hz}${ports:+, ports $ports}"
    loud "woofer = $node:$woofer, tweeter = $node:$tweeter" \
         "Which port drives which speaker is the board's wiring, which this script cannot know." \
         "Confirm it at the speaker before the cap is lifted; until then the tweeter stays muted."
    if [ "$alsa" = no ]; then
        info "('$node' is not an ALSA sink: a virtual speaker's amplifier?)"
    elif [ "$alsa" = yes ] && { [ -z "$priority" ] || [ "${priority:-0}" -le 0 ] 2> /dev/null; }; then
        warn "the amplifier has no priority.driver above 0. The speakerd sink (priority.driver 0) ties with it,"
        warn "and the amplifier drives their graph only because PipeWire created it first. Give it one in"
        warn "pipewire.conf (priority.driver = 1000, as firmware/pipewire/pipewire.conf does)."
    fi
}

note_rates() {
    local rate
    rate="$(graph_rate)"
    [ -n "$rate" ] || return 0
    if [ "$rate" != 48000 ]; then
        info "PipeWire runs at $rate Hz; snapcast streams 48000 Hz (snapcast-setup.sh --sampleformat), so PipeWire"
        info "resamples the stream, and speakerd's plugins run at $rate Hz: the factory graph's filters must be"
        info "designed for $rate Hz. Nothing here changes the rate (firmware/CLAUDE.md, 'Rates')."
    fi
}

# ----------------------------------------------------------------------------
# The persistent sink: the drop-in, and the running PipeWire's speakerd node
# ----------------------------------------------------------------------------
DROPIN_CHANGED=0
ensure_sink() {
    if install_file "$DROPIN_SOURCE" "$DROPIN"; then DROPIN_CHANGED=1; else ok "The speakerd sink is configured ($DROPIN)"; fi
}

# What the running PipeWire has: LIVE_SINK yes (the drop-in's speakerd sink), no, or unknown
# (no PipeWire reachable); LIVE_SINK_WHY; DEFAULT_SINK.
read_live_sink() {
    local out present="" dropin="" differs="" default_sink=""
    LIVE_SINK=unknown; LIVE_SINK_WHY="no PipeWire is reachable from here"; DEFAULT_SINK=""
    if pw_dump "$WORK/sink.json" && out="$(helper sink "$WORK/sink.json")"; then
        eval "$out"
        DEFAULT_SINK="$default_sink"
        if [ "$present" = no ]; then
            LIVE_SINK=no; LIVE_SINK_WHY="the running PipeWire has no speakerd sink"
        elif [ "$dropin" = yes ]; then
            LIVE_SINK=yes; LIVE_SINK_WHY=""
        else
            LIVE_SINK=no
            LIVE_SINK_WHY="the running PipeWire's speakerd sink is not the drop-in's ($differs): this PipeWire does not load $DROPIN (a config name other than pipewire.conf, or PIPEWIRE_CONFIG_DIR?), and the sink is one speakerd made itself or another config's"
        fi
    fi
}

# Fails unless the running PipeWire has the drop-in's speakerd sink: a file that exists is not a
# sink that exists. Skipped with --dry-run and --no-restart (said), and in the tests when no
# PipeWire is reachable.
require_live_sink() {
    if dry || [ "$NO_RESTART" -eq 1 ]; then
        read_live_sink
        [ "$LIVE_SINK" = yes ] || warn "now: ${LIVE_SINK_WHY} (not checked further: --dry-run/--no-restart)"
        return 0
    fi
    for _ in $(seq 1 20); do
        read_live_sink
        [ "$LIVE_SINK" = no ] && [ "$DROPIN_CHANGED" -eq 1 ] || break
        sleep 0.5
    done
    case "$LIVE_SINK" in
        yes) ok "The running PipeWire has the drop-in's speakerd sink (priority.session 1500, priority.driver 0)" ;;
        unknown)
            if test_mode; then warn "$LIVE_SINK_WHY: the live speakerd sink is not checked (test mode)"
            else die "$LIVE_SINK_WHY: cannot check that the running PipeWire has the drop-in's speakerd sink"; fi ;;
        no) die "$LIVE_SINK_WHY. Nothing may be routed into speakerd until it does." ;;
    esac
}

# Restarts PipeWire when the drop-in changed. A PipeWire that does not restart has no speakerd
# sink: fatal.
restart_pipewire() {
    [ "$DROPIN_CHANGED" -eq 1 ] || return 0
    if dry || [ "$NO_RESTART" -eq 1 ]; then info "PipeWire needs a restart for the speakerd sink (not done: --dry-run/--no-restart)"; return 0; fi
    info "Restarting PipeWire, for the speakerd sink's drop-in ($([ -f "$DROPIN" ] && echo added || echo removed))"
    sc restart pipewire.service wireplumber.service || die "could not restart PipeWire (systemctl --user restart pipewire wireplumber): the running PipeWire does not have the new configuration"
    sc restart pipewire-pulse.service 2> /dev/null || true
    # snapclient's stream went with the old PipeWire.
    if [ "$NO_SYSTEMD" -eq 0 ] && systemctl --user is-enabled snapclient.service > /dev/null 2>&1; then
        if sc restart snapclient.service; then ok "Restarted snapclient"; else warn "could not restart snapclient"; fi
    fi
}

# ----------------------------------------------------------------------------
# install: everything that can refuse first, then the changes
# ----------------------------------------------------------------------------

# device.json as it will be, in $WORK/device.json (nothing written yet). Sets DEVICE_LIMITS.
prepare_device() {
    local out changed="" role="" amplifier="" woofer="" tweeter="" limits="" note=""
    rm -f "$WORK/device.json"
    if [ -f "$STATE/device.json" ] && [ "$DEVICE_BROKEN" -eq 0 ]; then cp "$STATE/device.json" "$WORK/device.json"; fi
    out="$(helper device "$WORK/device.json" ${ROLE:+--role "$ROLE"} "${AMP_ARGS[@]}" --gain-db "$CAP_GAIN_DB" --tweeter-limit "$CAP_TWEETER")" \
        || die "cannot write device.json (see above)"
    eval "$out"
    : "$changed$note"
    info "device.json: role $role, amplifier '$amplifier' (woofer $woofer, tweeter $tweeter), limits $limits"
}

# The factory graph, converted and checked, in $WORK/factory.json (nothing written yet), or
# none. speakerd converts a .filtergraph from the bundle, with the device.json to be.
prepare_factory() {
    local doc="$WORK/factory.json" out allow=() summary="" warning="" b bundles=()
    rm -f "$doc"
    [ "$ALLOW_FULL_RANGE_TWEETER" -eq 0 ] || allow=(--allow-tweeter-without-crossover)
    if [ -n "$IMPORT_FILTERGRAPH" ]; then
        [ -f "$IMPORT_FILTERGRAPH" ] || die "no .filtergraph at $IMPORT_FILTERGRAPH"
        for b in "$BUNDLE"/lv2/*.lv2; do [ -d "$b" ] && bundles+=(--lv2-bundle "$b"); done
        info "Converting $IMPORT_FILTERGRAPH into a graph document (speakerd --import-filtergraph)"
        mkdir -p "$WORK/empty-home"
        if ! HOME="$WORK/empty-home" "$BUNDLE/bin/speakerd" --import-filtergraph "$IMPORT_FILTERGRAPH" --prefer-headless \
                --device "$WORK/device.json" "${bundles[@]}" > "$doc" 2> "$WORK/import.err"; then
            sed 's/^/    /' "$WORK/import.err" >&2
            die "speakerd could not convert $IMPORT_FILTERGRAPH (see above)"
        fi
    elif [ -n "$FACTORY" ]; then
        cp "$FACTORY" "$doc"
    else
        if [ -f "$STATE/factory.json" ]; then
            helper graph "$STATE/factory.json" "${allow[@]}" > /dev/null \
                || die "the factory.json there is (kept) is refused (see above): replace it with --factory or --import-filtergraph"
            ok "Keeping $STATE/factory.json"
        else
            loud "No factory graph: until there is one (--factory or --import-filtergraph), speakerd plays" \
                 "PASSTHROUGH: the own channel to the woofer only, at -20 dB, the tweeter muted."
        fi
        return 0
    fi
    out="$(helper graph "$doc" "${allow[@]}")" || die "refusing this factory graph (see above)"
    eval "$out"
    [ -z "$warning" ] || loud "$warning"
    info "factory.json: $summary"
}

install_version() {
    local dst="$LIB/$BUNDLE_ID" tmpdst
    if [ -d "$dst" ] && [ "$(version_value "$dst/VERSION" id)" = "$BUNDLE_ID" ]; then
        ok "Version $BUNDLE_ID is installed already"
        return 0
    fi
    if dry; then info "(dry-run) would install version $BUNDLE_ID in $dst"; return 0; fi
    tmpdst="$LIB/.$BUNDLE_ID.tmp.$$"
    rm -rf "$tmpdst"; mkdir -p "$tmpdst/bin"
    cp -p "$BUNDLE/bin/speakerd" "$tmpdst/bin/speakerd"
    cp -p "$FIRMWARE_DIR/speakerd/speakerd-run" "$tmpdst/bin/speakerd-run"
    # ampd, the network agent, comes with the version: a rollback takes both back together.
    cp -p "$FIRMWARE_DIR/ampd/ampd.py" "$tmpdst/bin/ampd"
    # The tweeter rule's one definition, which ampd imports from beside itself.
    cp -p "$FIRMWARE_DIR/speakerd/tweeter_rule.py" "$tmpdst/bin/tweeter_rule.py"
    chmod 755 "$tmpdst/bin/speakerd" "$tmpdst/bin/speakerd-run" "$tmpdst/bin/ampd"
    if [ -d "$BUNDLE/lv2" ]; then cp -a "$BUNDLE/lv2" "$tmpdst/lv2"; else mkdir -p "$tmpdst/lv2"; fi
    cp -p "$BUNDLE/VERSION" "$tmpdst/VERSION"
    { echo "speakerd_version=$BUNDLE_SPEAKERD_VERSION"; echo "speakerd_build=$BUNDLE_SPEAKERD_BUILD"; } >> "$tmpdst/VERSION"
    # This installer, so that status, rollback and uninstall work on the board without a bundle.
    # firmware/CLAUDE.md calls its scripts directly: executable, whatever the copy they came from kept.
    copy_installers "$tmpdst/setup"
    rm -rf "$dst"; mv "$tmpdst" "$dst"
    printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$BUNDLE_ID" >> "$LIB/installed.log"
    ok "Installed version $BUNDLE_ID in $dst"
}

# The tweeter rule over every layer speakerd may load: graph.json and graph.lkg.json (ampd's)
# load before factory.json. $1 = refuse (any layer that breaks it refuses) | warn.
check_layers() {
    local mode="$1" f out bad=()
    for f in graph.json graph.lkg.json factory.json; do
        [ -f "$STATE/$f" ] || continue
        out="$(helper graph "$STATE/$f" 2>&1)" || bad+=("$f: ${out#setup-helper: }")
    done
    [ ${#bad[@]} -gt 0 ] || return 0
    if [ "$mode" = refuse ]; then
        loud "Refusing: a layer speakerd may load feeds the tweeter other than through a crossover's high band:" \
             "${bad[@]/#/    }"
        exit 1
    fi
    loud "A layer speakerd may load feeds the tweeter other than through a crossover's high band (the" \
         "tweeter is muted now; turning it on will be refused):" "${bad[@]/#/    }"
}

# Writes what prepare_device and prepare_factory made. Sets DEVICE_CHANGED, FACTORY_CHANGED.
write_state() {
    DEVICE_CHANGED=0; FACTORY_CHANGED=0
    if dry; then
        info "(dry-run) would write $STATE/device.json$([ -f "$WORK/factory.json" ] && echo " and $STATE/factory.json")"
        return 0
    fi
    mkdir -p "$STATE"
    if [ "$DEVICE_BROKEN" -eq 1 ]; then cp -p "$STATE/device.json" "$STATE/device.json.broken" && warn "kept the broken device.json as device.json.broken"; fi
    if ! cmp -s "$WORK/device.json" "$STATE/device.json"; then
        helper write "$STATE/device.json" < "$WORK/device.json"
        DEVICE_CHANGED=1; ok "Wrote $STATE/device.json"
    fi
    if [ -f "$WORK/factory.json" ] && ! cmp -s "$WORK/factory.json" "$STATE/factory.json"; then
        helper write "$STATE/factory.json" < "$WORK/factory.json"
        FACTORY_CHANGED=1; ok "Wrote $STATE/factory.json"
    fi
}

prune_versions() {
    local current previous id keep=() kept=0
    current="$(link_target "$LIB/current")"; previous="$(link_target "$LIB/previous")"
    [ -f "$LIB/installed.log" ] || return 0
    # Newest first, each once.
    while read -r _ id; do
        case " ${keep[*]} " in *" $id "*) continue ;; esac
        keep+=("$id")
    done < <(tac "$LIB/installed.log")
    for id in "${keep[@]}"; do
        [ -d "$LIB/$id" ] || continue
        if [ "$id" = "$current" ] || [ "$id" = "$previous" ]; then continue; fi
        if [ $kept -lt $((KEEP_VERSIONS - 2)) ]; then kept=$((kept + 1)); continue; fi
        if dry; then info "(dry-run) would remove the old version $id"
        else rm -rf "${LIB:?}/$id"; ok "Removed the old version $id"; fi
    done
}

# After a (re)start under systemd: speakerd must still run and say exactly the limits
# device.json gives it. Anything else fails loudly: a broken device.json, the fail-safe, no
# line at all (which is also what a journal this user cannot read looks like), a speakerd that
# stopped (status 2: a bundle shadowed by a system LV2 folder).
check_started() {   # $1 = epoch seconds of the restart
    [ "$NO_SYSTEMD" -eq 0 ] || return 0
    local log="" run="" line wait="${SPEAKERD_START_WAIT:-15}"
    # Only what follows the first start in the window counts: restarting a running speakerd
    # records the OLD one's end ("speakerd ended: clean") after the moment of the restart. An end
    # after that start is the new one's: a crash.
    for _ in $(seq 1 $((wait * 2))); do
        log="$(journalctl --user -u speakerd.service --since "@$1" -o cat --no-pager 2> /dev/null || true)"
        run="$(awk '/^speakerd-run: starting speakerd/ { found = 1 } found' <<< "$log")"
        grep -qE '^speakerd: device(: limits|\.json: FAILED)' <<< "$run" && break
        sleep 0.5
    done
    # A speakerd that refuses its bundles (status 2) has said its device line already: give it
    # the time to go.
    if [ "$wait" -ge 2 ]; then sleep 2; else sleep 1; fi
    log="$(journalctl --user -u speakerd.service --since "@$1" -o cat --no-pager 2> /dev/null || true)"
    run="$(awk '/^speakerd-run: starting speakerd/ { found = 1 } found' <<< "$log")"
    if [ -z "$run" ]; then
        loud "speakerd-run did not start speakerd within $wait s (see the journal: an AudioPluginHost it waits for?)." \
             "If 'journalctl --user -n 5' shows no entries, $AUDIO_USER cannot read the journal (Armbian: a volatile" \
             "or unsplit journal, or $AUDIO_USER not in the systemd-journal group): fix that first, nothing here can be checked."
        exit 1
    fi
    if ! systemctl --user is-active --quiet speakerd.service || grep -qE '^speakerd-run: speakerd ended' <<< "$run"; then
        loud "speakerd is not running after its start (systemctl --user status speakerd):" \
             "$(grep -E '^speakerd(-run)?: ' <<< "$run" | tail -n 5)"
        exit 1
    fi
    if line="$(grep -m1 '^speakerd: device.json: FAILED' <<< "$run")"; then
        loud "speakerd could not read device.json and plays with its fail-safe defaults:" "${line#speakerd: }"
        exit 1
    fi
    if line="$(grep -m1 -E "$NO_SINK_LINE" <<< "$run")"; then
        loud "PipeWire had no speakerd sink, so speakerd made one: the drop-in is not loaded." "${line#speakerd: }"
        exit 1
    fi
    if line="$(grep -m1 '^speakerd: device: limits: ' <<< "$run")"; then
        if [[ ! "$line" =~ $LIMITS_LINE ]]; then
            loud "speakerd does not play device.json's limits:" "${line#speakerd: }"
            exit 1
        fi
        ok "${line#speakerd: }"
    else
        loud "speakerd started but has said nothing about its limits within $wait s: a speakerd that does not" \
             "say them must not play on a board (journalctl --user -u speakerd -b)."
        exit 1
    fi
}

do_install() {
    check_bundle
    info "Installing speakerd $BUNDLE_ID for $AUDIO_USER (home $H)"
    command -v python3 > /dev/null || die "python3 is needed (for JSON)"
    refuse_plugin_host
    check_device_json
    if [ ! -f "$STATE/device.json" ] && [ -z "$ROLE" ]; then
        die "first install: --role left|right|mono is required (which channel this speaker plays)"
    fi
    choose_amplifier
    note_rates
    prepare_device
    prepare_factory
    [ ! -e "$LIB/home/.lv2" ] || die "$LIB/home/.lv2 exists: it would shadow the bundles. Remove it."

    # Nothing has changed so far. From here on it changes things.
    stop_plugin_host
    ensure_sink
    restart_pipewire
    require_live_sink
    install_version
    if ! dry; then mkdir -p "$LIB/home" "$STATE"; fi
    write_state
    local unit_changed=0
    if install_file "$UNIT_SOURCE" "$UNIT"; then unit_changed=1; fi

    local old; old="$(link_target "$LIB/current")"
    if [ "$old" != "$BUNDLE_ID" ]; then
        if dry; then info "(dry-run) would make $BUNDLE_ID current${old:+, $old previous}"
        else
            [ -z "$old" ] || point "$LIB/previous" "$old"
            point "$LIB/current" "$BUNDLE_ID"
            ok "current -> $BUNDLE_ID${old:+ (previous -> $old)}"
        fi
    fi
    prune_versions
    dry || check_layers warn

    if dry; then ok "Dry run: nothing changed"; return 0; fi
    [ "$unit_changed" -eq 0 ] || sc daemon-reload
    sc enable speakerd.service > /dev/null 2>&1 || warn "could not enable speakerd.service"
    if [ "$NO_RESTART" -eq 1 ]; then
        info "Not (re)starting speakerd (--no-restart)"
    elif [ "$old" != "$BUNDLE_ID" ] || [ "$DEVICE_CHANGED$FACTORY_CHANGED$unit_changed$DROPIN_CHANGED" != 0000 ] \
         || ! { [ "$NO_SYSTEMD" -eq 1 ] || systemctl --user is-active --quiet speakerd.service; }; then
        local restarted; restarted="$(date +%s)"
        sc restart speakerd.service || die "could not start speakerd: journalctl --user -u speakerd"
        ok "speakerd (re)started"
        check_started "$restarted"
    else
        ok "Nothing changed: speakerd keeps running"
    fi
    if [ "$NO_SYSTEMD" -eq 0 ] && ! test_mode; then
        [ "$(loginctl show-user "$AUDIO_USER" --property=Linger --value 2> /dev/null)" = yes ] \
            || loginctl enable-linger "$AUDIO_USER" 2> /dev/null \
            || warn "linger is off: speakerd starts only when $AUDIO_USER logs in. sudo loginctl enable-linger $AUDIO_USER"
    fi
    cat <<EOF

${c_green}speakerd $BUNDLE_ID installed.${c_off} The output is capped: -20 dB, tweeter muted.
  - Check it, without a sound, with a quiet stream playing: $(dirname "$SELF")/speakerd-setup.sh smoke
  - Its log:                      journalctl --user -u speakerd -b
  - Back to the previous version: $(dirname "$SELF")/speakerd-setup.sh rollback
EOF
}

# ----------------------------------------------------------------------------
# status, rollback, uninstall, limits, check, sink
# ----------------------------------------------------------------------------
do_status() {
    local id current previous mark build
    current="$(link_target "$LIB/current")"; previous="$(link_target "$LIB/previous")"
    echo "speakerd on $AUDIO_USER@$(hostname) (home $H)"
    if [ -d "$LIB" ]; then
        echo "  versions in $LIB:"
        for id in "$LIB"/*/; do
            id="$(basename "$id")"
            [ -f "$LIB/$id/VERSION" ] || continue
            mark=""; [ "$id" = "$previous" ] && mark=previous; [ "$id" = "$current" ] && mark=current
            # The whole --version line (with the commit speakerd was built from); an install
            # from before it recorded only the number.
            build="$(version_value "$LIB/$id/VERSION" speakerd_build)"
            [ -n "$build" ] || build="speakerd $(version_value "$LIB/$id/VERSION" speakerd_version)"
            printf '    %-9s %s  (%s, commit %s, dirty %s, bundled %s)\n' "$mark" "$id" "$build" \
                "$(version_value "$LIB/$id/VERSION" commit | cut -c1-12)" "$(version_value "$LIB/$id/VERSION" dirty)" \
                "$(version_value "$LIB/$id/VERSION" bundled)"
        done
    else
        echo "  not installed ($LIB does not exist)"
    fi
    if [ "$NO_SYSTEMD" -eq 0 ]; then
        echo "  service: $(systemctl --user is-enabled speakerd.service 2> /dev/null || echo not-installed), $(systemctl --user is-active speakerd.service 2> /dev/null || true), restarts $(systemctl --user show speakerd.service -p NRestarts --value 2> /dev/null || echo ?)"
    fi
    if [ -f "$UNIT" ]; then echo "  unit: $UNIT"; else echo "  unit: none"; fi
    if [ -x "$LIB/current/bin/speakerd-run" ]; then
        SPEAKERD_RUNTIME_DIR="$RUNTIME" "$LIB/current/bin/speakerd-run" --status | sed 's/^/  history: /'
    fi
    if [ -f "$STATE/device.json" ]; then
        echo "  device.json:"; sed 's/^/    /' "$STATE/device.json"
    else
        echo "  device.json: none"
    fi
    local out
    for id in graph.json graph.lkg.json factory.json; do
        if [ ! -f "$STATE/$id" ]; then echo "  $id: none"
        elif out="$(helper graph "$STATE/$id" 2>&1)"; then echo "  $id: $(sed -n "s/^summary='\(.*\)'$/\1/p" <<< "$out")"
        else echo "  $id: *** ${out#setup-helper: } ***"; fi
    done
    if [ -f "$DROPIN" ]; then echo "  sink: $DROPIN"; else echo "  sink: not configured ($DROPIN)"; fi
    read_live_sink
    case "$LIVE_SINK" in
        yes) echo "  live sink: the drop-in's (priority.session 1500, priority.driver 0); default sink ${DEFAULT_SINK:-?}" ;;
        *)   echo "  live sink: NOT the drop-in's: $LIVE_SINK_WHY; default sink ${DEFAULT_SINK:-?}" ;;
    esac
    if pipewire_reachable; then
        local links dsp=absent
        links="$(timeout 5 pw-link -l 2> /dev/null || true)"
        grep -q '^speakerd-dsp:' <<< "$links" && dsp=present
        echo "  PipeWire: $(graph_rate) Hz; speakerd-dsp $dsp"
        grep -A2 '^speakerd-dsp:output_' <<< "$links" | sed 's/^/    /' || true
    fi
    if [ -f "$CLIENT_RT" ]; then
        echo "  snapclient routing ($CLIENT_RT): $(awk '/application.name = "Snapclient"/ { s = 1 } s && /target.object/ { print $3; exit }' "$CLIENT_RT")"
    fi
    local pids; pids="$(plugin_host_pids)"
    [ -z "$pids" ] || echo "  *** $PLUGIN_HOST runs: pid $pids ***"
    plugin_host_autostarts | sed "s/^/  $PLUGIN_HOST starts from: /"
    if ampd_installed; then ampd_setup status | sed 's/^/  /'; fi
}

do_rollback() {
    local current previous version
    current="$(link_target "$LIB/current")"; previous="$(link_target "$LIB/previous")"
    [ -n "$previous" ] && [ -d "$LIB/$previous" ] || die "no previous version to roll back to (current: ${current:-none})"
    version="$(version_value "$LIB/$previous/VERSION" speakerd_version)"
    if [ -n "$version" ]; then
        version_at_least "$version" "$SPEAKERD_MIN_VERSION" \
            || die "the previous version's speakerd ($previous) is $version: a board needs speakerd $SPEAKERD_MIN_VERSION or newer, the first that enforces device.json's limits. An older one would ignore the cap."
    else
        require_speakerd_version "$LIB/$previous/bin/speakerd" "the previous version's speakerd ($previous)"
    fi
    if dry; then info "(dry-run) would make $previous current and $current previous, and cap device.json"; return 0; fi
    # Other plugin builds (another crossover) play from here: under the cap, as after an
    # install, until a person lifts it again. The cap first, then the switch.
    if [ -f "$STATE/device.json" ]; then
        helper device "$STATE/device.json" --gain-db "$CAP_GAIN_DB" --tweeter-limit "$CAP_TWEETER" > /dev/null \
            || die "cannot cap device.json: not rolling back"
        ok "device.json capped: output at $CAP_GAIN_DB dB, tweeter $CAP_TWEETER"
    fi
    point "$LIB/current" "$previous"
    point "$LIB/previous" "$current"
    ok "current -> $previous (previous -> $current; rollback again to go back)"
    if [ "$NO_RESTART" -eq 0 ]; then
        local restarted; restarted="$(date +%s)"
        sc restart speakerd.service || die "could not restart speakerd"
        ok "speakerd restarted"
        check_started "$restarted"
        # ampd is the version's too.
        if [ -f "$AMPD_UNIT" ]; then
            if sc restart ampd.service; then ok "ampd restarted"; else warn "could not restart ampd"; fi
        fi
    fi
}

# snapclient's routing from before speakerd: the client-rt.conf that snapcast-setup.sh saved as
# client-rt.conf.before-speakerd when it first routed into speakerd; without it, the generated
# AudioPluginHost route (into Ap.cap).
restore_routing() {
    if [ -f "$CLIENT_RT.before-speakerd" ]; then
        if dry; then info "(dry-run) would restore $CLIENT_RT from $CLIENT_RT.before-speakerd and restart snapclient"; return 0; fi
        cp -p "$CLIENT_RT.before-speakerd" "$CLIENT_RT.tmp.$$" && mv -f "$CLIENT_RT.tmp.$$" "$CLIENT_RT"
        rm -f "$CLIENT_RT.before-speakerd"
        ok "Restored snapclient's routing from before speakerd ($CLIENT_RT)"
        check_snapclient_target
        if [ "$NO_RESTART" -eq 0 ] && [ "$NO_SYSTEMD" -eq 0 ] && systemctl --user is-enabled snapclient.service > /dev/null 2>&1; then
            if sc restart snapclient.service; then ok "Restarted snapclient"; else warn "could not restart snapclient"; fi
        fi
        return 0
    fi
    warn "no saved routing from before speakerd ($CLIENT_RT.before-speakerd): writing the AudioPluginHost route"
    local args=(--mode target --routing-only --route plugin-host)
    if dry; then args+=(--dry-run); fi
    if [ "$NO_RESTART" -eq 1 ] || [ "$NO_SYSTEMD" -eq 1 ]; then args+=(--no-restart); fi
    if test_mode; then args+=(--client-home "$H"); fi
    bash "$FIRMWARE_DIR/snapcast-setup.sh" "${args[@]}" || die "could not restore snapclient's routing: not uninstalling"
    dry || check_snapclient_target
}

# After the routing is restored: the node snapclient's rule names must exist in the running
# PipeWire, or WirePlumber sends snapclient to the default sink, the amplifier, full range and
# uncapped (a --base-conf pipewire.conf, say, has no Ap.cap). If it does not, snapclient is
# stopped and disabled, and said so.
check_snapclient_target() {
    local target why
    target="$(awk '/application\.name *= *"Snapclient"/ { s = 1 }
                   s && /^[^#]*target\.object *= *"/ { match($0, /target\.object *= *"[^"]*"/); v = substr($0, RSTART, RLENGTH)
                                                         sub(/^[^"]*"/, "", v); sub(/"$/, "", v); print v; exit }' \
              "$CLIENT_RT" 2> /dev/null)"
    if [ -z "$target" ]; then
        why="names no target for snapclient"
    elif pw_dump "$WORK/route.json"; then
        if helper node "$WORK/route.json" "$target" > /dev/null 2>&1; then ok "snapclient's target '$target' exists"; return 0; fi
        why="sends it to '$target', which the running PipeWire does not have"
    elif test_mode; then
        warn "no PipeWire reachable: snapclient's target '$target' is not checked (test mode)"; return 0
    else
        why="sends it to '$target', and the running PipeWire cannot be reached to check that it exists"
    fi
    if dry; then warn "(dry-run) snapclient's restored routing $why: snapclient would be stopped and disabled"; return 0; fi
    loud "snapclient's restored routing $why: WirePlumber would send it to the default sink, the" \
         "amplifier, full range and uncapped. Stopping and disabling snapclient. Give it a target that" \
         "exists (firmware/snapcast-setup.sh --mode target --routing-only --route ...), then:" \
         "systemctl --user enable --now snapclient"
    sc stop snapclient.service 2> /dev/null || true
    sc disable snapclient.service 2> /dev/null || true
}

# Re-enables the AudioPluginHost units install disabled (--stop-plugin-host).
reenable_plugin_host() {
    [ -f "$PLUGIN_HOST_UNITS" ] || return 0
    local scope unit
    while read -r scope unit; do
        [ -n "$unit" ] || continue
        if dry; then info "(dry-run) would re-enable the $scope unit $unit"; continue; fi
        case "$scope" in
            user)   if sc enable --now "$unit"; then ok "Re-enabled the user unit $unit"; else warn "could not re-enable $unit"; fi ;;
            system) if [ "$NO_SYSTEMD" -eq 0 ] && sudo -n systemctl enable --now "$unit" 2> /dev/null; then ok "Re-enabled the system unit $unit"
                    else warn "re-enable it by hand: sudo systemctl enable --now $unit"; fi ;;
        esac
    done < "$PLUGIN_HOST_UNITS"
    dry || rm -f "$PLUGIN_HOST_UNITS"
}

do_uninstall() {
    if [ -f "$CLIENT_RT" ] && grep -q 'target.object *= *"speakerd"' "$CLIENT_RT"; then
        info "snapclient plays into the speakerd sink: restoring its routing first"
        restore_routing
    fi
    local other
    other="$(grep -rls 'target.object *= *"speakerd"' "$H/.config/pipewire" "$H/.config/wireplumber" 2> /dev/null | grep -vxF "$CLIENT_RT" || true)"
    [ -z "$other" ] || warn "these still send something to the speakerd sink, which is going: $other"
    if dry; then
        info "(dry-run) would stop and disable speakerd, remove $UNIT, $DROPIN and $LIB$([ "$PURGE" = 1 ] && echo " and $STATE")"
        reenable_plugin_host
        return 0
    fi
    # ampd's program is in the versions that go: its service goes first.
    if [ -f "$AMPD_UNIT" ]; then
        if [ "$PURGE" -eq 1 ]; then ampd_setup uninstall --purge; else ampd_setup uninstall; fi
    fi
    sc stop speakerd.service 2> /dev/null || true
    sc disable speakerd.service 2> /dev/null || true
    rm -f "$UNIT" && ok "Removed $UNIT"
    sc daemon-reload || true
    if [ -f "$DROPIN" ]; then
        rm -f "$DROPIN" && ok "Removed $DROPIN"
        DROPIN_CHANGED=1
        restart_pipewire
    fi
    rm -rf "$LIB" && ok "Removed $LIB"
    reenable_plugin_host
    if [ "$PURGE" -eq 1 ]; then rm -rf "$STATE" && ok "Removed $STATE"
    else info "Kept $STATE (device.json and the layers): --purge removes it"; fi
    ok "speakerd uninstalled"
}

do_limits() {
    local gain tweeter out changed="" limits="" role="" amplifier="" woofer="" note=""
    [ -f "$STATE/device.json" ] || die "no $STATE/device.json: install first"
    check_device_json
    if [ "$CAP" -eq 1 ]; then gain="$CAP_GAIN_DB"; tweeter="$CAP_TWEETER"
    else
        [ -n "$GAIN_DB" ] && [ -n "$TWEETER_LIMIT" ] || die "limits: --cap, or --gain-db <dB> --tweeter-limit <muted|on>"
        gain="$GAIN_DB"; tweeter="$TWEETER_LIMIT"
        if [ "$tweeter" != muted ] || awk -v g="$gain" -v c="$CAP_GAIN_DB" 'BEGIN { exit !(g > c) }'; then
            [ "$PERSON_PRESENT" -eq 1 ] || die "output at $gain dB, tweeter $tweeter is above the cap ($CAP_GAIN_DB dB, tweeter $CAP_TWEETER): only with --person-present, after a smoke test, with a person at the speaker"
            loud "Lifting the cap: output at $gain dB, tweeter $tweeter. A person is at the speaker (--person-present)."
        fi
    fi
    # The tweeter goes on only if no layer speakerd may load feeds it other than through a
    # crossover's high band: what it plays now may be ampd's graph.json, not the factory.
    if [ "$tweeter" = on ] && [ "$ALLOW_FULL_RANGE_TWEETER" -eq 0 ]; then check_layers refuse; fi
    if dry; then info "(dry-run) would set the limits to output at $gain dB, tweeter $tweeter"; return 0; fi
    out="$(helper device "$STATE/device.json" --gain-db "$gain" --tweeter-limit "$tweeter")" || die "cannot write device.json"
    eval "$out"
    : "$role$amplifier$woofer$note"
    ok "device.json limits: $limits"
    if [ "$changed" = yes ] && [ "$NO_RESTART" -eq 0 ]; then
        local restarted; restarted="$(date +%s)"
        sc restart speakerd.service || die "could not restart speakerd"
        ok "speakerd restarted with them"
        check_started "$restarted"
    fi
}

do_check() {
    if [ -n "$BUNDLE" ]; then check_bundle; ok "The bundle $BUNDLE_ID is good for this machine"; fi
    refuse_plugin_host
    check_device_json
    if [ ! -f "$STATE/device.json" ] && [ -z "$ROLE" ]; then
        die "first install: --role left|right|mono is required"
    fi
    choose_amplifier
    note_rates
    prepare_device
    [ -z "$BUNDLE" ] || prepare_factory
    read_live_sink
    case "$LIVE_SINK" in
        yes) ok "The running PipeWire has the drop-in's speakerd sink" ;;
        *)   info "The running PipeWire: $LIVE_SINK_WHY (install adds the drop-in, restarts PipeWire and checks again)" ;;
    esac
    ok "Nothing in the way of an install"
}

do_sink() {
    ensure_sink
    restart_pipewire
    require_live_sink
}

# ----------------------------------------------------------------------------
# smoke: checks without making a sound; every check reports, the summary always prints
# ----------------------------------------------------------------------------
SMOKE_FAILS=0; SMOKE_WARNS=0
pass()   { printf '  %spass%s %s\n' "$c_green" "$c_off" "$*"; }
failed() { printf '  %sFAIL%s %s\n' "$c_red" "$c_off" "$*"; SMOKE_FAILS=$((SMOKE_FAILS + 1)); }
noted()  { printf '  %swarn%s %s\n' "$c_yellow" "$c_off" "$*"; SMOKE_WARNS=$((SMOKE_WARNS + 1)); }
said()   { printf '  info %s\n' "$*"; }

speakerd_log() {   # everything speakerd and speakerd-run said since boot
    if [ "$NO_SYSTEMD" -eq 1 ]; then
        if [ -n "$LOG_FILE" ]; then cat "$LOG_FILE" 2> /dev/null; fi
    else
        journalctl --user -u speakerd.service -b -o cat --no-pager 2> /dev/null
    fi
}

do_smoke() {
    set +e
    local log since t0 lines0 restarts0="" pid0="" amp="" woofer="" tweeter="" device="{}" line
    echo "smoke: speakerd on $AUDIO_USER@$(hostname), version $(link_target "$LIB/current")"
    [ "$NO_SYSTEMD" -eq 0 ] || [ -n "$LOG_FILE" ] || failed "--no-systemd needs --log <file>, speakerd's output"
    if [ ! -f "$STATE/device.json" ]; then failed "no $STATE/device.json"
    elif ! device="$(helper device "$STATE/device.json" --show 2>&1)"; then failed "device.json cannot be read: $device"; device="{}"
    fi

    # The service.
    if [ "$NO_SYSTEMD" -eq 0 ]; then
        if systemctl --user is-active --quiet speakerd.service; then pass "speakerd.service is active"
        else failed "speakerd.service is $(systemctl --user is-active speakerd.service 2> /dev/null)"; fi
        restarts0="$(systemctl --user show speakerd.service -p NRestarts --value 2> /dev/null)"
        pid0="$(systemctl --user show speakerd.service -p MainPID --value 2> /dev/null)"
    fi

    # What it said since its last start.
    log="$(speakerd_log)"
    [ -n "$log" ] || failed "speakerd's log is empty: if 'journalctl --user -n 5' shows nothing, $AUDIO_USER cannot read the journal (Armbian: volatile or unsplit journal, or not in the systemd-journal group)"
    since="$(awk '/^speakerd-run: starting speakerd/ { buf = "" } { buf = buf $0 "\n" } END { printf "%s", buf }' <<< "$log")"
    lines0="$(wc -l <<< "$log")"
    t0="$(date +%s)"
    if line="$(grep -m1 '^speakerd: playing: ' <<< "$since")"; then pass "it plays: ${line#speakerd: playing: }"
    else failed "speakerd has not said 'playing:' since its last start"; fi
    if line="$(grep -m1 '^speakerd: using layer ' <<< "$since")"; then
        case "$line" in
            *passthrough*) noted "PASSTHROUGH: no layer loaded (${line#speakerd: using layer })" ;;
            *) said "layer: ${line#speakerd: using layer }" ;;
        esac
    else
        noted "speakerd did not say which layer it uses"
    fi
    grep '^speakerd: layer .*FAILED' <<< "$since" | sed 's/^/  info /'
    # A broken or missing device.json plays mono into the default amplifier name: never right.
    if line="$(grep -m1 '^speakerd: device.json: FAILED' <<< "$since")"; then failed "${line#speakerd: }"; fi
    # speakerd made its own sink: the drop-in is not loaded.
    if line="$(grep -m1 -E "$NO_SINK_LINE" <<< "$since")"; then failed "${line#speakerd: }"; fi

    # The limits it enforces, exactly the line, against device.json's.
    local want_gain want_tweeter gain tweeter_on
    want_gain="$(json_get "$device" '(d.get("limits") or {}).get("gain_db")')"
    want_tweeter="$(json_get "$device" '(d.get("limits") or {}).get("tweeter")')"
    if line="$(grep -m1 '^speakerd: device: limits: ' <<< "$since")"; then
        gain=""; tweeter_on=""
        if [[ "$line" =~ $LIMITS_LINE ]]; then gain="${BASH_REMATCH[1]}"; tweeter_on="${BASH_REMATCH[3]}"; fi
        if [ -z "$gain" ]; then
            failed "speakerd does not play device.json's limits: '${line#speakerd: }'"
        elif [ -z "$want_gain" ]; then
            failed "device.json has no limits (speakerd then plays with its fail-safe ones; say them explicitly): ${line#speakerd: }"
        elif ! awk -v a="$gain" -v b="$want_gain" 'BEGIN { exit !(a - b < 0.001 && b - a < 0.001) }' || [ "$tweeter_on" != "$want_tweeter" ]; then
            failed "speakerd says '${line#speakerd: }'; device.json says output at $want_gain dB, tweeter $want_tweeter"
        elif [ "$tweeter_on" = muted ] && awk -v g="$gain" -v c="$CAP_GAIN_DB" 'BEGIN { exit !(g <= c) }'; then
            pass "capped: output at $gain dB, tweeter muted"
        else
            noted "the cap is lifted: output at $gain dB, tweeter $tweeter_on (only a person does that, after a smoke test)"
        fi
    else
        failed "speakerd did not say which limits it enforces ('device: limits: ...'): a speakerd without the limits must not play on a board"
    fi

    # The sink: the drop-in's, live, and the default.
    read_live_sink
    case "$LIVE_SINK" in
        yes) pass "the speakerd sink is the drop-in's (priority.session 1500, priority.driver 0)" ;;
        *)   failed "the speakerd sink is not the drop-in's: $LIVE_SINK_WHY" ;;
    esac

    # The links: the sink into speakerd-dsp, speakerd-dsp into the amplifier, and nothing else into it.
    amp="$(json_get "$device" 'd["amplifier"]["node"] if isinstance(d.get("amplifier"), dict) else d.get("amplifier")')"
    woofer="$(json_get "$device" '(d.get("amplifier") if isinstance(d.get("amplifier"), dict) else {}).get("woofer", "playback_FL")')"
    tweeter="$(json_get "$device" '(d.get("amplifier") if isinstance(d.get("amplifier"), dict) else {}).get("tweeter", "playback_FR")')"
    local links pair missing=() feeders snap
    links="$(timeout 5 pw-link -l 2> /dev/null | awk '!/^ / { port = $0 } /^ *\|->/ { sub(/^ *\|-> */, ""); print port " -> " $0 }')"
    if [ -z "$amp" ]; then failed "device.json names no amplifier"
    else
        for pair in "speakerd:monitor_FL -> speakerd-dsp:input_FL" "speakerd:monitor_FR -> speakerd-dsp:input_FR" \
                    "speakerd-dsp:output_woofer -> $amp:$woofer" "speakerd-dsp:output_tweeter -> $amp:$tweeter"; do
            grep -qxF "$pair" <<< "$links" || missing+=("$pair")
        done
        if [ ${#missing[@]} -eq 0 ]; then pass "linked: speakerd sink -> speakerd-dsp -> $amp ($woofer woofer, $tweeter tweeter)"
        else failed "links missing: $(printf '%s; ' "${missing[@]}")"; fi
        feeders="$(grep -F " -> $amp:" <<< "$links" | grep -v '^speakerd-dsp:')"
        if [ -z "$feeders" ]; then pass "nothing but speakerd-dsp plays into $amp"
        else failed "something else plays into the amplifier: $(tr '\n' ';' <<< "$feeders")"; fi
    fi
    if [ "$DEFAULT_SINK" = speakerd ]; then pass "the default sink is speakerd"
    elif [ -n "$amp" ] && [ "$DEFAULT_SINK" = "$amp" ]; then
        failed "the default sink is the amplifier '$amp' (chosen with wpctl?): a stream with no target plays straight into it, full range and uncapped. wpctl set-default <the id of 'speakerd input' in wpctl status>"
    else noted "the default sink is '${DEFAULT_SINK:-unknown}', not speakerd: a stream with no target plays into it"; fi
    snap="$(grep -E '^[^ ]*([Ss]napcast|[Ss]napclient)[^ ]*:output_' <<< "$links")"
    if [ -z "$snap" ]; then said "no snapclient stream in PipeWire now"
    elif ! grep -qv ' -> speakerd:playback_' <<< "$snap"; then pass "snapclient plays into the speakerd sink"
    else noted "snapclient plays into: $(awk '{ printf "%s ", $3 }' <<< "$snap")"; fi

    # No AudioPluginHost.
    local pids; pids="$(plugin_host_pids)"
    if [ -z "$pids" ]; then pass "no $PLUGIN_HOST runs"; else failed "$PLUGIN_HOST runs (pid $pids)"; fi

    # Who drives: the amplifier, never the speakerd sink. Measured only while the graph runs.
    local dump="$WORK/smoke-dump.json" out
    if ! pw_dump "$dump"; then
        noted "who drives the graph was NOT measured: PipeWire is not reachable"
    elif ! command -v pw-profiler > /dev/null; then
        noted "who drives the graph was NOT measured: no pw-profiler here"
    else
        local prof="$WORK/profiler" drivers="" followers=""
        mkdir -p "$prof"
        (cd "$prof" && timeout -s INT 2 pw-profiler -o "$prof/profiler.log" > "$prof/out" 2>&1)
        if out="$(helper drivers "$prof/out" "$dump")"; then
            eval "$out"
            if [[ " $drivers " == *" speakerd "* ]]; then failed "the speakerd sink drives the graph (drivers: $drivers): it must never clock the amplifier"
            elif [[ " $drivers " == *" $amp "* ]] && [[ " $followers " == *" speakerd-dsp "* ]]; then pass "$amp drives the graph (followers: $followers)"
            else noted "who drives speakerd-dsp was NOT measured (drivers: ${drivers:-none}; followers: ${followers:-none}): play a quiet stream into the speakerd sink while smoke runs"; fi
        else
            noted "who drives the graph was NOT measured: nothing ran. Either nothing plays (play a quiet stream into the speakerd sink while smoke runs), or PipeWire has no profiler module (pw-profiler needs libpipewire-module-profiler in context.modules: firmware/CLAUDE.md, 'First deploy')"
        fi
    fi

    # Real-time threads.
    local pid=""
    pid="$(helper node "$dump" speakerd-dsp 2> /dev/null | sed -n "s/^pid='\([0-9]*\)'$/\1/p")"
    [ -n "$pid" ] || pid="$pid0"
    if [ -n "$pid" ] && [ "$pid" != 0 ] && [ -d "/proc/$pid" ]; then
        local rt
        rt="$(ps -L -o cls=,rtprio=,comm= -p "$pid" 2> /dev/null | awk '$1 == "FF" || $1 == "RR"' | wc -l)"
        if [ "${rt:-0}" -gt 0 ]; then pass "speakerd (pid $pid) has $rt real-time thread(s)"
        else noted "speakerd (pid $pid) has no real-time thread: xruns are likely under load (RTKit, or RLIMIT_RTPRIO for $AUDIO_USER)"; fi
    else
        noted "speakerd's process was not found: its threads are not checked"
    fi

    # A while without a restart, an xrun or a lost PipeWire.
    said "watching for ${WINDOW} s"
    sleep "$WINDOW"
    local after starts
    if [ "$NO_SYSTEMD" -eq 0 ]; then
        after="$(journalctl --user -u speakerd.service -u pipewire.service -u wireplumber.service --since "@$t0" -o cat --no-pager 2> /dev/null)"
        if [ "$(systemctl --user show speakerd.service -p NRestarts --value 2> /dev/null)" != "$restarts0" ] \
           || [ "$(systemctl --user show speakerd.service -p MainPID --value 2> /dev/null)" != "$pid0" ]; then
            failed "speakerd restarted while watched (restarts $restarts0 -> $(systemctl --user show speakerd.service -p NRestarts --value 2> /dev/null))"
        fi
    else
        after="$(speakerd_log | tail -n +$((lines0 + 1)))"
    fi
    starts=0
    [ ! -f "$RUNTIME/history" ] || starts="$(awk -v t="$t0" '$1 == "start" && $2 >= t' "$RUNTIME/history" | wc -l)"
    if [ "$starts" -eq 0 ]; then pass "no restart in ${WINDOW} s"; else failed "speakerd-run started speakerd $starts time(s) while watched"; fi
    if grep -qi 'xrun' <<< "$after"; then failed "xruns while watched: $(grep -i -m3 'xrun' <<< "$after" | tr '\n' ';')"
    else pass "no xrun in ${WINDOW} s"; fi
    if grep -qE '^speakerd: (lost PipeWire|stopping)' <<< "$after"; then failed "speakerd stopped or lost PipeWire while watched"; fi

    # ampd, when it is installed: running, connected to speakerd, an agent to the hub.
    if ampd_installed; then
        if ampd_setup smoke | sed 's/^/  /'; [ "${PIPESTATUS[0]}" -eq 0 ]; then pass "ampd (above)"
        else failed "ampd (above)"; fi
    fi

    echo "smoke: $SMOKE_FAILS failed, $SMOKE_WARNS warning(s)"
    [ "$SMOKE_FAILS" -eq 0 ]
}

# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
case "$ACTION" in
    install)   do_install ;;
    status)    do_status ;;
    rollback)  do_rollback ;;
    uninstall) do_uninstall ;;
    limits)    do_limits ;;
    check)     do_check ;;
    sink)      do_sink ;;
    smoke)     do_smoke ;;
    stop-plugin-host) refuse_plugin_host running; stop_plugin_host ;;
esac
