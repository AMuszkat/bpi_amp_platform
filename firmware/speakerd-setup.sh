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
# Every install caps the output: device.json gets "limits": output at -20 dB, tweeter muted.
# Only the limits action lifts the cap, with a person at the speaker after a smoke test.
#
# Usage:
#   ./speakerd-setup.sh install --role left|right|mono [options]   # on the board, as its audio user
#   ./speakerd-setup.sh status | rollback | uninstall | smoke      # on the board
#   ./speakerd-setup.sh sink | check | limits [options]           # on the board
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
RC_LOCAL=/etc/rc.local   # private-ok: a file on the board, not a host name

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
  uninstall   restore snapclient's previous routing, then remove the service, the sink and
              the versions; device.json and the layers stay unless --purge
  smoke       check without making a sound that speakerd plays capped, through the speakerd
              sink, linked to the amplifier, driven by it, with no restart or xrun for a while
  sink        install only the persistent speakerd sink (setup.sh's PipeWire component)
  check       what install would refuse, without changing anything
  limits      change device.json's limits (the cap), then restart speakerd. No limit at all is
              --gain-db 0 --tweeter-limit on (written explicitly: without limits speakerd
              plays capped)
  bundle      assemble a bundle from the build tree (development machine; builds nothing)

Options:
  --mode <target|target-remote>
                              target (default): this machine is the speaker.
                              target-remote: copy a bundle to the board and run the action there.
  --target-host <user@host>   the board, as its audio user (default: \$BOARD_USER@\$BOARD_HOST, from the
                              environment or firmware/local.mk; else user@bpi-amp.local).
  --bundle <dir>              the bundle to install (default: the folder above this script, when it is one).
  --role <left|right|mono>    which channel this speaker plays. Required on the first install; a
                              reinstall keeps device.json's role, amplifier and ports unless given.
  --amplifier <node>          the amplifier's PipeWire node (default: the ALSA Audio/Sink found with
                              pw-dump; bpi-alsa-playback-pcm or alsa-playback-pcm when there are several).
  --woofer-port <port>        the amplifier port of the woofer (default playback_FL).
  --tweeter-port <port>       the amplifier port of the tweeter (default playback_FR). Which port is
                              which driver is a fact of the board's wiring: confirm it at the speaker.
  --factory <file>            a graph document (version 1) to install as factory.json. In target-remote
                              mode a file on this machine, copied with the bundle.
  --import-filtergraph <file> an AudioPluginHost .filtergraph on the board: speakerd converts it into
                              factory.json (speakerd --import-filtergraph --prefer-headless).
  --allow-tweeter-without-crossover
                              accept a factory graph in which the stream reaches the tweeter without
                              passing a crossover. For the virtual pair only: never on a board.
  --stop-plugin-host          stop a running AudioPluginHost (and disable a systemd unit that starts
                              it) instead of refusing to install while one runs.
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
  --arch <aarch64|native>     bundle: which build tree (default aarch64: build/aarch64/...).
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
  # On the development machine: build, then install on the board (copies one bundle, runs install there)
  tools/build.sh speakerd aarch64 && tools/build.sh loudness aarch64 headless
  ./speakerd-setup.sh install --mode target-remote --target-host user@bpi-amp.local --role left \\
      --plugin loudness:headless --import-filtergraph /home/user/session.filtergraph

  # On the board, from an unpacked bundle
  ./firmware/speakerd-setup.sh install --role left
  ./firmware/speakerd-setup.sh smoke
EOF
}

ORIG_ARGS=("$@")
[ $# -gt 0 ] || { usage; exit 2; }
case "$1" in
    -h | -help | --help) usage; exit 0 ;;
    install | status | rollback | uninstall | smoke | sink | check | limits | bundle) ACTION="$1"; shift ;;
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
        --allow-tweeter-without-crossover) ALLOW_FULL_RANGE_TWEETER=1; remote "$1"; shift ;;
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
    if [ "$ACTION" = install ]; then
        make_bundle "$tmp/bundle" aarch64 "${PLUGINS[@]}"
    else
        make_bundle "$tmp/bundle" none
    fi
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
if [ -n "$HOME_OVERRIDE" ]; then
    mkdir -p "$HOME_OVERRIDE"
    H="$(cd "$HOME_OVERRIDE" && pwd)"
    AUDIO_USER="$(id -un)"
elif [ "$(id -u)" -eq 0 ]; then
    # Everything belongs to the audio user and runs in that user's session: do it as that user.
    user="${AUDIO_USER:-${SUDO_USER:-${BOARD_USER:-}}}"
    [ -n "$user" ] && [ "$user" != root ] || die "Running as root: pass --user <name>, the user whose PipeWire drives the amplifier"
    uid="$(id -u "$user" 2> /dev/null)" || die "no user '$user' on this machine"
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
DROPIN_SOURCE="$FIRMWARE_DIR/pipewire/pipewire.conf.d/60-speakerd-sink.conf"
UNIT_SOURCE="$FIRMWARE_DIR/speakerd/speakerd.service"

if [ -z "$BUNDLE" ] && [ -f "$FIRMWARE_DIR/../VERSION" ] && [ -e "$FIRMWARE_DIR/../bin/speakerd" ]; then
    BUNDLE="$FIRMWARE_DIR/.."
fi
[ -z "$BUNDLE" ] || BUNDLE="$(cd "$BUNDLE" && pwd)"
if [ -n "$FACTORY" ] && [ ! -f "$FACTORY" ]; then
    if [ -n "$BUNDLE" ] && [ -f "$BUNDLE/$FACTORY" ]; then FACTORY="$BUNDLE/$FACTORY"; else die "no factory graph $FACTORY"; fi
fi

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
helper() { python3 "$HELPER" "$@"; }

# systemctl --user, unless --no-systemd.
sc() {
    if [ "$NO_SYSTEMD" -eq 1 ]; then info "(no systemd) systemctl --user $*"; return 0; fi
    systemctl --user "$@"
}

dry() { [ "$DRY_RUN" -eq 1 ]; }

pipewire_reachable() { timeout 5 pw-cli info 0 > /dev/null 2>&1; }

pw_dump() {   # $1 = file
    timeout 10 pw-dump > "$1" 2> /dev/null && [ -s "$1" ]
}

graph_rate() {
    { timeout 5 pw-metadata -n settings 0 clock.rate 2> /dev/null || true; } | sed -n "s/.*key:'clock.rate' value:'\([0-9]*\)'.*/\1/p" | head -n 1
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

# The version a speakerd binary says (speakerd --version), or nothing when it cannot say: a
# speakerd from before --version, which also predates the limits.
speakerd_version() {   # $1 = the binary
    local out empty
    empty="$(mktemp -d)"
    # Either stream: only a speakerd that exits 0 has a version to say.
    out="$(HOME="$empty" timeout 10 "$1" --version 2>&1)" || out=""
    rmdir "$empty" 2> /dev/null || true
    sed -n -E 's/^[^0-9]*([0-9]+\.[0-9]+(\.[0-9]+)?).*/\1/p' <<< "$out" | head -n 1
}

# Whether version $1 is $2 or newer.
version_at_least() { [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n 1)" = "$2" ]; }

# Refuses a speakerd older than SPEAKERD_MIN_VERSION. $1 = its version (may be empty), $2 = what.
require_speakerd_version() {
    if [ -z "$1" ] || ! version_at_least "$1" "$SPEAKERD_MIN_VERSION"; then
        die "$2 is ${1:-too old to say its version (no --version)}: a board needs speakerd $SPEAKERD_MIN_VERSION or newer, the first that enforces device.json's limits. An older one would ignore the cap."
    fi
}

# The AudioPluginHost processes that run (any user), by command name.
plugin_host_pids() {
    local f comm pid stat pids=()
    for f in /proc/[0-9]*/comm; do
        { read -r comm < "$f"; } 2> /dev/null || continue
        [ "$comm" = AudioPluginHost ] || continue
        pid="${f#/proc/}"; pid="${pid%/comm}"
        # A zombie (state Z) has exited already: it plays nothing.
        { stat="$(< "/proc/$pid/stat")"; } 2> /dev/null || continue
        stat="${stat##*) }"; [ "${stat%% *}" != Z ] || continue
        pids+=("$pid")
    done
    echo "${pids[*]}"
}

# Files that start an AudioPluginHost at boot or login.
plugin_host_autostarts() {
    local f
    for f in "$H"/.config/systemd/user/*.service /etc/systemd/user/*.service /etc/systemd/system/*.service \
             "$H"/.config/autostart/*.desktop /etc/xdg/autostart/*.desktop "$H"/.config/lxsession/*/autostart \
             "$H"/.profile "$H"/.bash_profile "$H"/.bashrc "$H"/.xsessionrc "$H"/.xinitrc "$H"/.xprofile "$RC_LOCAL"; do
        if [ -f "$f" ] && grep -q AudioPluginHost "$f" 2> /dev/null; then echo "$f"; fi
    done
    if [ -z "$HOME_OVERRIDE" ] && command -v crontab > /dev/null && crontab -l 2> /dev/null | grep -q AudioPluginHost; then
        echo "the crontab of $AUDIO_USER"
    fi
    return 0
}

# Refuses while an AudioPluginHost runs, unless --stop-plugin-host; says loudly what would
# start one again.
check_plugin_host() {
    local pids autostarts f
    pids="$(plugin_host_pids)"
    autostarts="$(plugin_host_autostarts)"
    if [ -n "$pids" ]; then
        if [ "$STOP_PLUGIN_HOST" -eq 0 ]; then
            loud "AudioPluginHost runs (pid $pids). It feeds the amplifier today, and speakerd would feed it" \
                 "too: two plugin hosts at once, the tweeter included. Refusing to install." \
                 "Close it, or pass --stop-plugin-host to have it stopped."
            exit 1
        fi
        if dry; then
            info "(dry-run) would stop AudioPluginHost (pid $pids)"
        else
            loud "Stopping AudioPluginHost (pid $pids): speakerd replaces it (--stop-plugin-host)."
            # shellcheck disable=SC2086  # one argument per pid
            kill -TERM $pids 2> /dev/null || sudo -n kill -TERM $pids 2> /dev/null || die "cannot stop AudioPluginHost (pid $pids)"
            for _ in $(seq 1 50); do [ -z "$(plugin_host_pids)" ] && break; sleep 0.1; done
            pids="$(plugin_host_pids)"
            # shellcheck disable=SC2086
            [ -z "$pids" ] || { kill -KILL $pids 2> /dev/null || true; sleep 0.5; }
            [ -z "$(plugin_host_pids)" ] || die "AudioPluginHost (pid $(plugin_host_pids)) is still running"
            ok "AudioPluginHost stopped"
        fi
    fi
    if [ -n "$autostarts" ]; then
        local lines=("AudioPluginHost is started at boot or login by:")
        while read -r f; do lines+=("    $f"); done <<< "$autostarts"
        lines+=("speakerd-run waits for it to exit before it starts speakerd, but remove it: two plugin"
                "hosts must never feed the amplifier.")
        loud "${lines[@]}"
        if [ "$STOP_PLUGIN_HOST" -eq 1 ]; then
            while read -r f; do
                case "$f" in
                    "$H"/.config/systemd/user/*.service)
                        if dry; then info "(dry-run) would disable the user unit $(basename "$f")"
                        elif sc disable --now "$(basename "$f")"; then ok "Disabled the user unit $(basename "$f")"
                        else warn "could not disable $(basename "$f")"; fi ;;
                    /etc/systemd/system/*.service)
                        if dry; then info "(dry-run) would disable the system unit $(basename "$f")"
                        elif [ "$NO_SYSTEMD" -eq 0 ] && sudo -n systemctl disable --now "$(basename "$f")" 2> /dev/null; then ok "Disabled the system unit $(basename "$f")"
                        else warn "disable it by hand: sudo systemctl disable --now $(basename "$f")"; fi ;;
                    *) warn "remove AudioPluginHost from $f by hand" ;;
                esac
            done <<< "$autostarts"
        fi
    fi
    return 0
}

# Checks the bundle; sets BUNDLE_ID.
check_bundle() {
    [ -n "$BUNDLE" ] || die "no bundle: pass --bundle <dir> (made by 'speakerd-setup.sh bundle' or setup.sh on the development machine)"
    [ -f "$BUNDLE/VERSION" ] || die "$BUNDLE is not a bundle: it has no VERSION"
    [ -x "$BUNDLE/bin/speakerd" ] || die "$BUNDLE has no bin/speakerd"
    BUNDLE_ID="$(version_value "$BUNDLE/VERSION" id)"
    [ -n "$BUNDLE_ID" ] && [ "$BUNDLE_ID" != none ] || die "$BUNDLE/VERSION has no id"
    local machine here hash
    machine="$(elf_machine "$BUNDLE/bin/speakerd")"
    here="$(uname -m)"; [ "$here" = arm64 ] && here=aarch64
    [ "$machine" = "$here" ] || die "the bundle's speakerd is built for $machine, this machine is $here"
    hash="$(contents_hash "$BUNDLE")"
    [ "${BUNDLE_ID##*-}" = "$hash" ] || die "the bundle's contents do not match its VERSION (hash $hash, id $BUNDLE_ID): copied incompletely?"
    check_libraries
    BUNDLE_SPEAKERD_VERSION="$(speakerd_version "$BUNDLE/bin/speakerd")"
    require_speakerd_version "$BUNDLE_SPEAKERD_VERSION" "the bundle's speakerd"
    ok "speakerd $BUNDLE_SPEAKERD_VERSION (at least $SPEAKERD_MIN_VERSION)"
}

# Every shared library speakerd and the plugins need is on this machine. speakerd needs
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

# Decides the amplifier and its ports for device.json. Sets AMP_ARGS (for the helper) and
# says what it chose and why.
choose_amplifier() {
    AMP_ARGS=()
    local have_device=0 dump node="" ports="" priority="" alsa="" rate="" woofer tweeter p node_id=""
    if [ -f "$STATE/device.json" ] && helper device "$STATE/device.json" --show 2> /dev/null | grep -q '"amplifier"'; then
        have_device=1
    fi
    if [ $have_device = 1 ] && [ -z "$AMPLIFIER$WOOFER_PORT$TWEETER_PORT" ]; then
        info "Keeping the amplifier of $STATE/device.json"
        return 0
    fi
    dump="$(mktemp)"
    if pw_dump "$dump"; then
        local out
        if out="$(helper amp "$dump" ${AMPLIFIER:+--node "$AMPLIFIER"})"; then
            eval "$out"
        else
            rm -f "$dump"
            die "cannot tell which node is the amplifier (see above)"
        fi
    else
        [ -n "$AMPLIFIER" ] || { rm -f "$dump"; die "cannot reach PipeWire to find the amplifier: run this in the audio user's session, or name it with --amplifier"; }
        warn "cannot reach PipeWire: the amplifier '$AMPLIFIER' and its ports are not checked"
        node="$AMPLIFIER"
    fi
    rm -f "$dump"
    : "$node_id"
    woofer="${WOOFER_PORT:-playback_FL}"; tweeter="${TWEETER_PORT:-playback_FR}"
    if [ -n "$ports" ]; then
        for p in "$woofer" "$tweeter"; do
            case " $ports " in *" $p "*) ;; *) die "the amplifier '$node' has no port $p (its ports: $ports): name them with --woofer-port and --tweeter-port" ;; esac
        done
    fi
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
# The persistent sink (the "sink" action, and part of install)
# ----------------------------------------------------------------------------
DROPIN_CHANGED=0
ensure_sink() {
    if install_file "$DROPIN_SOURCE" "$DROPIN"; then DROPIN_CHANGED=1; else ok "The speakerd sink is configured ($DROPIN)"; fi
}

restart_pipewire() {
    [ "$DROPIN_CHANGED" -eq 1 ] || return 0
    if dry || [ "$NO_RESTART" -eq 1 ]; then info "PipeWire needs a restart for the speakerd sink (not done: --dry-run/--no-restart)"; return 0; fi
    info "Restarting PipeWire, so that the speakerd sink exists"
    sc restart pipewire.service pipewire-pulse.service wireplumber.service 2> /dev/null \
        || sc restart pipewire.service wireplumber.service \
        || warn "could not restart PipeWire: systemctl --user restart pipewire wireplumber"
    # snapclient's stream went with the old PipeWire.
    if [ "$NO_SYSTEMD" -eq 0 ] && systemctl --user is-enabled snapclient.service > /dev/null 2>&1; then
        if sc restart snapclient.service; then ok "Restarted snapclient"; else warn "could not restart snapclient"; fi
    fi
}

# After a (re)start under systemd: waits for speakerd to say what device.json gave it, and
# fails loudly when that is not the limits device.json names (broken file, fail-safe limits).
check_started() {   # $1 = epoch seconds of the restart
    [ "$NO_SYSTEMD" -eq 0 ] || return 0
    local log="" line
    for _ in $(seq 1 30); do
        log="$(journalctl --user -u speakerd.service --since "@$1" -o cat --no-pager 2> /dev/null || true)"
        grep -qE '^speakerd: device(: limits|\.json: FAILED)' <<< "$log" && break
        sleep 0.5
    done
    if line="$(grep -m1 '^speakerd: device.json: FAILED' <<< "$log")"; then
        loud "speakerd could not read device.json and plays with its fail-safe defaults:" "${line#speakerd: }"
        exit 1
    fi
    if line="$(grep -m1 '^speakerd: device: limits: ' <<< "$log")"; then
        if [[ ! "$line" =~ $LIMITS_LINE ]]; then
            loud "speakerd does not play device.json's limits:" "${line#speakerd: }"
            exit 1
        fi
        ok "${line#speakerd: }"
    else
        warn "speakerd has not said what limits it enforces within 15 s: journalctl --user -u speakerd -b"
    fi
}

# ----------------------------------------------------------------------------
# install
# ----------------------------------------------------------------------------
install_version() {
    local dst="$LIB/$BUNDLE_ID" tmpdst
    if [ -d "$dst" ] && [ "$(version_value "$dst/VERSION" id)" = "$BUNDLE_ID" ]; then
        ok "Version $BUNDLE_ID is installed already"
        return 0
    fi
    if dry; then info "(dry-run) would install version $BUNDLE_ID in $dst"; return 0; fi
    tmpdst="$LIB/.$BUNDLE_ID.tmp.$$"
    rm -rf "$tmpdst"; mkdir -p "$tmpdst/bin" "$tmpdst/setup/speakerd" "$tmpdst/setup/pipewire/pipewire.conf.d"
    cp -p "$BUNDLE/bin/speakerd" "$tmpdst/bin/speakerd"
    cp -p "$FIRMWARE_DIR/speakerd/speakerd-run" "$tmpdst/bin/speakerd-run"
    chmod 755 "$tmpdst/bin/speakerd" "$tmpdst/bin/speakerd-run"
    if [ -d "$BUNDLE/lv2" ]; then cp -a "$BUNDLE/lv2" "$tmpdst/lv2"; else mkdir -p "$tmpdst/lv2"; fi
    cp -p "$BUNDLE/VERSION" "$tmpdst/VERSION"
    echo "speakerd_version=$BUNDLE_SPEAKERD_VERSION" >> "$tmpdst/VERSION"
    # This installer, so that status, rollback and uninstall work on the board without a bundle.
    cp -p "$FIRMWARE_DIR"/{setup.sh,install-lib.sh,speakerd-setup.sh,snapcast-setup.sh} "$tmpdst/setup/"
    cp -p "$FIRMWARE_DIR"/speakerd/{speakerd-run,speakerd.service,setup-helper.py} "$tmpdst/setup/speakerd/"
    cp -p "$FIRMWARE_DIR"/pipewire/pipewire.conf "$tmpdst/setup/pipewire/"
    cp -p "$FIRMWARE_DIR"/pipewire/pipewire.conf.d/*.conf "$tmpdst/setup/pipewire/pipewire.conf.d/"
    rm -rf "$dst"; mv "$tmpdst" "$dst"
    printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$BUNDLE_ID" >> "$LIB/installed.log"
    ok "Installed version $BUNDLE_ID in $dst"
}

install_factory() {   # sets FACTORY_CHANGED
    FACTORY_CHANGED=0
    local doc out allow=()
    [ "$ALLOW_FULL_RANGE_TWEETER" -eq 0 ] || allow=(--allow-tweeter-without-crossover)
    if [ -n "$IMPORT_FILTERGRAPH" ]; then
        [ -f "$IMPORT_FILTERGRAPH" ] || die "no .filtergraph at $IMPORT_FILTERGRAPH"
        doc="$(mktemp)"
        local binary="$LIB/$BUNDLE_ID/bin/speakerd" bundles=() b
        if dry; then binary="$BUNDLE/bin/speakerd"; fi
        for b in "$(dirname "$(dirname "$binary")")"/lv2/*.lv2; do [ -d "$b" ] && bundles+=(--lv2-bundle "$b"); done
        info "Converting $IMPORT_FILTERGRAPH into a graph document (speakerd --import-filtergraph)"
        mkdir -p "$LIB/home"
        local device=()
        [ ! -f "$STATE/device.json" ] || device=(--device "$STATE/device.json")
        if ! HOME="$LIB/home" "$binary" --import-filtergraph "$IMPORT_FILTERGRAPH" --prefer-headless "${device[@]}" "${bundles[@]}" > "$doc" 2> "$doc.err"; then
            sed 's/^/    /' "$doc.err" >&2
            rm -f "$doc" "$doc.err"
            die "speakerd could not convert $IMPORT_FILTERGRAPH (a speakerd without --import-filtergraph says 'bad command line')"
        fi
        rm -f "$doc.err"
    elif [ -n "$FACTORY" ]; then
        doc="$FACTORY"
    else
        if [ -f "$STATE/factory.json" ]; then ok "Keeping $STATE/factory.json"
        else
            loud "No factory graph: until there is one (--factory or --import-filtergraph), speakerd plays" \
                 "PASSTHROUGH: the own channel to the woofer only, at -20 dB, the tweeter muted."
        fi
        return 0
    fi
    if ! out="$(helper graph "$doc" "${allow[@]}")"; then
        [ "$doc" = "$FACTORY" ] || rm -f "$doc"
        die "refusing this factory graph (see above)"
    fi
    local summary="" warning=""
    eval "$out"
    [ -z "$warning" ] || loud "$warning"
    if [ -f "$STATE/factory.json" ] && cmp -s "$doc" "$STATE/factory.json"; then
        ok "factory.json is that graph already ($summary)"
    elif dry; then
        info "(dry-run) would write $STATE/factory.json: $summary"
    else
        helper write "$STATE/factory.json" < "$doc"
        FACTORY_CHANGED=1
        ok "Wrote $STATE/factory.json: $summary"
    fi
    [ "$doc" = "$FACTORY" ] || rm -f "$doc"
}

# An existing device.json speakerd would reject (a key it does not know, a key twice) is
# refused: it would play with its fail-safe defaults. A broken one is rewritten only with --role.
check_device_json() {
    [ -f "$STATE/device.json" ] || return 0
    local why
    why="$(helper device "$STATE/device.json" --check 2>&1)" && return 0
    # A file that is not JSON at all is rewritten with --role (and kept as device.json.broken);
    # keys speakerd would reject are a person's to fix.
    if [ -n "$ROLE" ] && ! grep -q 'key(s)' <<< "$why"; then warn "$why"; return 0; fi
    printf '%s\n' "$why" >&2
    die "fix $STATE/device.json first"
}

write_device() {   # sets DEVICE_CHANGED
    DEVICE_CHANGED=0
    local out changed="" role="" amplifier="" woofer="" tweeter="" limits="" note=""
    if dry; then
        info "(dry-run) would write $STATE/device.json: role ${ROLE:-(kept)}, ${AMP_ARGS[*]:-amplifier kept}, limits output at $CAP_GAIN_DB dB, tweeter $CAP_TWEETER"
        return 0
    fi
    mkdir -p "$STATE"
    out="$(helper device "$STATE/device.json" ${ROLE:+--role "$ROLE"} "${AMP_ARGS[@]}" --gain-db "$CAP_GAIN_DB" --tweeter-limit "$CAP_TWEETER")" \
        || die "cannot write $STATE/device.json (see above)"
    eval "$out"
    [ -z "$note" ] || warn "$note"
    [ "$changed" = yes ] && DEVICE_CHANGED=1
    ok "device.json: role $role, amplifier '$amplifier' (woofer $woofer, tweeter $tweeter), limits $limits"
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

do_install() {
    check_bundle
    info "Installing speakerd $BUNDLE_ID for $AUDIO_USER (home $H)"
    command -v python3 > /dev/null || die "python3 is needed (for JSON)"
    check_plugin_host
    check_device_json
    if [ ! -f "$STATE/device.json" ] && [ -z "$ROLE" ]; then
        die "first install: --role left|right|mono is required (which channel this speaker plays)"
    fi
    choose_amplifier
    note_rates

    ensure_sink
    install_version
    if ! dry; then mkdir -p "$LIB/home" "$STATE"; fi
    [ ! -e "$LIB/home/.lv2" ] || loud "$LIB/home/.lv2 exists: it may shadow the bundles. Remove it."
    # device.json first: the import maps the session's outputs by its ports.
    write_device
    install_factory
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

    if dry; then ok "Dry run: nothing changed"; return 0; fi
    [ "$unit_changed" -eq 0 ] || sc daemon-reload
    restart_pipewire
    sc enable speakerd.service > /dev/null 2>&1 || warn "could not enable speakerd.service"
    if [ "$NO_RESTART" -eq 1 ]; then
        info "Not (re)starting speakerd (--no-restart)"
    elif [ "$old" != "$BUNDLE_ID" ] || [ "$DEVICE_CHANGED$FACTORY_CHANGED$unit_changed$DROPIN_CHANGED" != 0000 ] \
         || ! { [ "$NO_SYSTEMD" -eq 1 ] || systemctl --user is-active --quiet speakerd.service; }; then
        local restarted; restarted="$(date +%s)"
        if sc restart speakerd.service; then ok "speakerd (re)started"; check_started "$restarted"
        else warn "could not start speakerd: journalctl --user -u speakerd"; fi
    else
        ok "Nothing changed: speakerd keeps running"
    fi
    if [ "$NO_SYSTEMD" -eq 0 ] && [ -z "$HOME_OVERRIDE" ]; then
        [ "$(loginctl show-user "$AUDIO_USER" --property=Linger --value 2> /dev/null)" = yes ] \
            || loginctl enable-linger "$AUDIO_USER" 2> /dev/null \
            || warn "linger is off: speakerd starts only when $AUDIO_USER logs in. sudo loginctl enable-linger $AUDIO_USER"
    fi
    cat <<EOF

${c_green}speakerd $BUNDLE_ID installed.${c_off} The output is capped: -20 dB, tweeter muted.
  - Check it, without a sound:    $(dirname "$SELF")/speakerd-setup.sh smoke
  - Its log:                      journalctl --user -u speakerd -b
  - Back to the previous version: $(dirname "$SELF")/speakerd-setup.sh rollback
EOF
}

# ----------------------------------------------------------------------------
# status, rollback, uninstall, limits, check
# ----------------------------------------------------------------------------
do_status() {
    local id current previous mark
    current="$(link_target "$LIB/current")"; previous="$(link_target "$LIB/previous")"
    echo "speakerd on $AUDIO_USER@$(hostname) (home $H)"
    if [ -d "$LIB" ]; then
        echo "  versions in $LIB:"
        for id in "$LIB"/*/; do
            id="$(basename "$id")"
            [ -f "$LIB/$id/VERSION" ] || continue
            mark=""; [ "$id" = "$previous" ] && mark=previous; [ "$id" = "$current" ] && mark=current
            printf '    %-9s %s  (speakerd %s, commit %s, dirty %s, bundled %s)\n' "$mark" "$id" \
                "$(version_value "$LIB/$id/VERSION" speakerd_version)" \
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
    for id in graph.json graph.lkg.json factory.json; do
        if [ -f "$STATE/$id" ]; then
            echo "  $id: $(helper graph "$STATE/$id" --allow-tweeter-without-crossover 2> /dev/null | sed -n "s/^summary='\(.*\)'$/\1/p")"
        else
            echo "  $id: none"
        fi
    done
    if [ -f "$DROPIN" ]; then echo "  sink: $DROPIN"; else echo "  sink: not configured ($DROPIN)"; fi
    if pipewire_reachable; then
        local links sink=ABSENT dsp=absent
        links="$(timeout 5 pw-link -l 2> /dev/null || true)"
        timeout 5 pw-link -i 2> /dev/null | grep -q '^speakerd:playback_FL$' && sink=present
        grep -q '^speakerd-dsp:' <<< "$links" && dsp=present
        echo "  PipeWire: $(graph_rate) Hz; speakerd sink $sink, speakerd-dsp $dsp"
        grep -A2 '^speakerd-dsp:output_' <<< "$links" | sed 's/^/    /' || true
    else
        echo "  PipeWire: not reachable from here"
    fi
    if [ -f "$CLIENT_RT" ]; then
        echo "  snapclient routing ($CLIENT_RT): $(awk '/application.name = "Snapclient"/ { s = 1 } s && /target.object/ { print $3; exit }' "$CLIENT_RT")"
    fi
    local pids; pids="$(plugin_host_pids)"
    [ -z "$pids" ] || echo "  *** AudioPluginHost runs: pid $pids ***"
    plugin_host_autostarts | sed 's/^/  AudioPluginHost starts from: /'
}

do_rollback() {
    local current previous
    current="$(link_target "$LIB/current")"; previous="$(link_target "$LIB/previous")"
    [ -n "$previous" ] && [ -d "$LIB/$previous" ] || die "no previous version to roll back to (current: ${current:-none})"
    local version
    version="$(version_value "$LIB/$previous/VERSION" speakerd_version)"
    [ -n "$version" ] || version="$(speakerd_version "$LIB/$previous/bin/speakerd")"
    require_speakerd_version "$version" "the previous version's speakerd ($previous)"
    if dry; then info "(dry-run) would make $previous current and $current previous"; return 0; fi
    point "$LIB/current" "$previous"
    point "$LIB/previous" "$current"
    ok "current -> $previous (previous -> $current; rollback again to go back)"
    if [ "$NO_RESTART" -eq 0 ]; then
        local restarted; restarted="$(date +%s)"
        sc restart speakerd.service && ok "speakerd restarted" && check_started "$restarted"
    fi
}

do_uninstall() {
    local route=""
    if [ -f "$CLIENT_RT" ] && grep -q 'target.object *= *"speakerd"' "$CLIENT_RT"; then
        info "snapclient plays into the speakerd sink: restoring the plugin-host routing first (snapcast-setup.sh --route plugin-host)"
        local args=(--mode target --routing-only --route plugin-host)
        if dry; then args+=(--dry-run); fi
        if [ "$NO_RESTART" -eq 1 ] || [ "$NO_SYSTEMD" -eq 1 ]; then args+=(--no-restart); fi
        [ -z "$HOME_OVERRIDE" ] || args+=(--client-home "$H")
        bash "$FIRMWARE_DIR/snapcast-setup.sh" "${args[@]}" || die "could not restore snapclient's routing: not uninstalling"
        route=restored
        warn "snapclient now plays into Ap.cap (the AudioPluginHost route). If this PipeWire has no Ap.cap,"
        warn "WirePlumber sends it to the default sink: the amplifier, full range."
    fi
    local other
    other="$(grep -rls 'target.object *= *"speakerd"' "$H/.config/pipewire" "$H/.config/wireplumber" 2> /dev/null | grep -vxF "$CLIENT_RT" || true)"
    [ -z "$other" ] || warn "these still send something to the speakerd sink, which is going: $other"
    if dry; then
        info "(dry-run) would stop and disable speakerd, remove $UNIT, $DROPIN and $LIB$([ "$PURGE" = 1 ] && echo " and $STATE")"
        return 0
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
    if [ "$PURGE" -eq 1 ]; then rm -rf "$STATE" && ok "Removed $STATE"
    else info "Kept $STATE (device.json and the layers): --purge removes it"; fi
    ok "speakerd uninstalled${route:+, and the routing of snapclient restored}"
}

do_limits() {
    local gain tweeter out changed="" limits="" role="" amplifier="" woofer="" note=""
    [ -f "$STATE/device.json" ] || die "no $STATE/device.json: install first"
    if [ "$CAP" -eq 1 ]; then gain="$CAP_GAIN_DB"; tweeter="$CAP_TWEETER"
    else
        [ -n "$GAIN_DB" ] && [ -n "$TWEETER_LIMIT" ] || die "limits: --cap, or --gain-db <dB> --tweeter-limit <muted|on>"
        gain="$GAIN_DB"; tweeter="$TWEETER_LIMIT"
        if [ "$tweeter" != muted ] || awk -v g="$gain" -v c="$CAP_GAIN_DB" 'BEGIN { exit !(g > c) }'; then
            [ "$PERSON_PRESENT" -eq 1 ] || die "output at $gain dB, tweeter $tweeter is above the cap ($CAP_GAIN_DB dB, tweeter $CAP_TWEETER): only with --person-present, after a smoke test, with a person at the speaker"
            loud "Lifting the cap: output at $gain dB, tweeter $tweeter. A person is at the speaker (--person-present)."
        fi
    fi
    if dry; then info "(dry-run) would set the limits to output at $gain dB, tweeter $tweeter"; return 0; fi
    out="$(helper device "$STATE/device.json" --gain-db "$gain" --tweeter-limit "$tweeter")" || die "cannot write device.json"
    eval "$out"
    : "$role$amplifier$woofer$note"
    ok "device.json limits: $limits"
    if [ "$changed" = yes ] && [ "$NO_RESTART" -eq 0 ]; then
        local restarted; restarted="$(date +%s)"
        sc restart speakerd.service && ok "speakerd restarted with them" && check_started "$restarted"
    fi
}

do_check() {
    if [ -n "$BUNDLE" ]; then check_bundle; ok "The bundle $BUNDLE_ID is good for this machine"; fi
    # A check changes nothing: with --stop-plugin-host it says what install would stop.
    local dry_run="$DRY_RUN"
    DRY_RUN=1
    check_plugin_host
    DRY_RUN="$dry_run"
    check_device_json
    if [ ! -f "$STATE/device.json" ] && [ -z "$ROLE" ]; then
        die "first install: --role left|right|mono is required"
    fi
    choose_amplifier
    note_rates
    ok "Nothing in the way of an install"
}

# ----------------------------------------------------------------------------
# smoke: checks without making a sound
# ----------------------------------------------------------------------------
SMOKE_FAILS=0; SMOKE_WARNS=0
pass()   { printf '  %spass%s %s\n' "$c_green" "$c_off" "$*"; }
failed() { printf '  %sFAIL%s %s\n' "$c_red" "$c_off" "$*"; SMOKE_FAILS=$((SMOKE_FAILS + 1)); }
noted()  { printf '  %swarn%s %s\n' "$c_yellow" "$c_off" "$*"; SMOKE_WARNS=$((SMOKE_WARNS + 1)); }
said()   { printf '  info %s\n' "$*"; }

speakerd_log() {   # everything speakerd and speakerd-run said since boot
    if [ "$NO_SYSTEMD" -eq 1 ]; then
        [ -n "$LOG_FILE" ] || die "smoke --no-systemd needs --log <file>, speakerd's output"
        cat "$LOG_FILE"
    else
        journalctl --user -u speakerd.service -b -o cat --no-pager 2> /dev/null
    fi
}

json_get() {   # $1 = JSON text, $2 = a Python expression of d (the document)
    python3 -c "import json,sys; d=json.load(sys.stdin); v=($2); print('' if v is None else v)" <<< "$1"
}

do_smoke() {
    local log since t0 lines0 restarts0="" pid0="" amp woofer tweeter device dump out line
    echo "smoke: speakerd on $AUDIO_USER@$(hostname), version $(link_target "$LIB/current")"
    [ -f "$STATE/device.json" ] || { failed "no $STATE/device.json"; return 1; }

    # The service.
    if [ "$NO_SYSTEMD" -eq 0 ]; then
        if systemctl --user is-active --quiet speakerd.service; then pass "speakerd.service is active"
        else failed "speakerd.service is $(systemctl --user is-active speakerd.service 2> /dev/null)"; fi
        restarts0="$(systemctl --user show speakerd.service -p NRestarts --value 2> /dev/null)"
        pid0="$(systemctl --user show speakerd.service -p MainPID --value 2> /dev/null)"
    fi

    # What it said since its last start.
    log="$(speakerd_log)"
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
    grep '^speakerd: layer .*FAILED' <<< "$since" | sed 's/^/  info /' || true
    # A broken or missing device.json plays mono into the default amplifier name: never right.
    if line="$(grep -m1 '^speakerd: device.json: FAILED' <<< "$since")"; then failed "${line#speakerd: }"; fi

    # The limits it enforces, against device.json's.
    device="$(helper device "$STATE/device.json" --show)"
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
        elif [ -z "$gain" ] || ! awk -v a="$gain" -v b="$want_gain" 'BEGIN { exit !(a - b < 0.001 && b - a < 0.001) }' || [ "$tweeter_on" != "$want_tweeter" ]; then
            failed "speakerd says '${line#speakerd: }'; device.json says output at $want_gain dB, tweeter $want_tweeter"
        elif [ "$tweeter_on" = muted ] && awk -v g="$gain" -v c="$CAP_GAIN_DB" 'BEGIN { exit !(g <= c) }'; then
            pass "capped: output at $gain dB, tweeter muted"
        else
            noted "the cap is lifted: output at $gain dB, tweeter $tweeter_on (only a person does that, after a smoke test)"
        fi
    else
        failed "speakerd did not say which limits it enforces ('device: limits: ...'): a speakerd without the limits must not play on a board"
    fi

    # The links: the sink into speakerd-dsp, speakerd-dsp into the amplifier, and nothing else into it.
    amp="$(json_get "$device" 'd["amplifier"]["node"] if isinstance(d.get("amplifier"), dict) else d.get("amplifier")')"
    woofer="$(json_get "$device" '(d.get("amplifier") if isinstance(d.get("amplifier"), dict) else {}).get("woofer", "playback_FL")')"
    tweeter="$(json_get "$device" '(d.get("amplifier") if isinstance(d.get("amplifier"), dict) else {}).get("tweeter", "playback_FR")')"
    local links pair missing=() feeders snap
    links="$(timeout 5 pw-link -l 2> /dev/null | awk '!/^ / { port = $0 } /^ *\|->/ { sub(/^ *\|-> */, ""); print port " -> " $0 }')" || links=""
    for pair in "speakerd:monitor_FL -> speakerd-dsp:input_FL" "speakerd:monitor_FR -> speakerd-dsp:input_FR" \
                "speakerd-dsp:output_woofer -> $amp:$woofer" "speakerd-dsp:output_tweeter -> $amp:$tweeter"; do
        grep -qxF "$pair" <<< "$links" || missing+=("$pair")
    done
    if [ ${#missing[@]} -eq 0 ]; then pass "linked: speakerd sink -> speakerd-dsp -> $amp ($woofer woofer, $tweeter tweeter)"
    else failed "links missing: $(printf '%s; ' "${missing[@]}")"; fi
    feeders="$(grep -F " -> $amp:" <<< "$links" | grep -v '^speakerd-dsp:' || true)"
    if [ -z "$feeders" ]; then pass "nothing but speakerd-dsp plays into $amp"
    else failed "something else plays into the amplifier: $(tr '\n' ';' <<< "$feeders")"; fi
    snap="$(grep -E '^[^ ]*([Ss]napcast|[Ss]napclient)[^ ]*:output_' <<< "$links" || true)"
    if [ -z "$snap" ]; then said "no snapclient stream in PipeWire now"
    elif ! grep -qv ' -> speakerd:playback_' <<< "$snap"; then pass "snapclient plays into the speakerd sink"
    else noted "snapclient plays into: $(awk '{ printf "%s ", $3 }' <<< "$snap")"; fi

    # No AudioPluginHost.
    local pids; pids="$(plugin_host_pids)"
    if [ -z "$pids" ]; then pass "no AudioPluginHost runs"; else failed "AudioPluginHost runs (pid $pids)"; fi

    # Who drives: the amplifier, never the speakerd sink.
    dump="$(mktemp)"
    if ! pw_dump "$dump"; then
        noted "cannot reach PipeWire for pw-dump"
    elif ! command -v pw-profiler > /dev/null; then
        noted "no pw-profiler here: cannot tell who drives the graph"
    else
        local prof drivers="" followers=""
        prof="$(mktemp -d)"
        (cd "$prof" && timeout -s INT 2 pw-profiler -o "$prof/profiler.log" > "$prof/out" 2>&1 || true)
        if out="$(helper drivers "$prof/out" "$dump")"; then
            eval "$out"
            if [[ " $drivers " == *" speakerd "* ]]; then failed "the speakerd sink drives the graph (drivers: $drivers): it must never clock the amplifier"
            elif [[ " $drivers " == *" $amp "* ]] && [[ " $followers " == *" speakerd-dsp "* ]]; then pass "$amp drives the graph (followers: $followers)"
            else noted "could not tell who drives speakerd-dsp (drivers: ${drivers:-none}; followers: ${followers:-none})"; fi
        else
            noted "pw-profiler saw no running graph (is the profiler module loaded? does anything play?)"
        fi
        rm -rf "$prof"
    fi

    # Real-time threads.
    local pid=""
    pid="$(helper node "$dump" speakerd-dsp 2> /dev/null | sed -n "s/^pid='\([0-9]*\)'$/\1/p")" || true
    [ -n "$pid" ] || pid="$pid0"
    if [ -n "$pid" ] && [ "$pid" != 0 ]; then
        local rt
        rt="$(ps -L -o cls=,rtprio=,comm= -p "$pid" 2> /dev/null | awk '$1 == "FF" || $1 == "RR"' | wc -l)"
        if [ "$rt" -gt 0 ]; then pass "speakerd (pid $pid) has $rt real-time thread(s)"
        else noted "speakerd (pid $pid) has no real-time thread: xruns are likely under load (RTKit, or RLIMIT_RTPRIO for $AUDIO_USER)"; fi
    fi
    rm -f "$dump"
    said "default sink: $(timeout 5 pw-metadata -n default 0 default.audio.sink 2> /dev/null | sed -n "s/.*value:'\([^']*\)'.*/\1/p" | head -n 1)"

    # A while without a restart, an xrun or a lost PipeWire.
    said "watching for ${WINDOW} s"
    sleep "$WINDOW"
    local after starts
    if [ "$NO_SYSTEMD" -eq 0 ]; then
        after="$(journalctl --user -u speakerd.service -u pipewire.service -u wireplumber.service --since "@$t0" -o cat --no-pager 2> /dev/null || true)"
        if [ "$(systemctl --user show speakerd.service -p NRestarts --value 2> /dev/null)" != "$restarts0" ] \
           || [ "$(systemctl --user show speakerd.service -p MainPID --value 2> /dev/null)" != "$pid0" ]; then
            failed "speakerd restarted while watched (restarts $restarts0 -> $(systemctl --user show speakerd.service -p NRestarts --value 2> /dev/null))"
        fi
    else
        after="$(speakerd_log | tail -n +$((lines0 + 1)))"
    fi
    starts="$(awk -v t="$t0" '$1 == "start" && $2 >= t' "$RUNTIME/history" 2> /dev/null | wc -l)"
    if [ "$starts" -eq 0 ]; then pass "no restart in ${WINDOW} s"; else failed "speakerd-run started speakerd $starts time(s) while watched"; fi
    if grep -qi 'xrun' <<< "$after"; then failed "xruns while watched: $(grep -i -m3 'xrun' <<< "$after" | tr '\n' ';')"
    else pass "no xrun in ${WINDOW} s"; fi
    if grep -qE '^speakerd: (lost PipeWire|stopping)' <<< "$after"; then failed "speakerd stopped or lost PipeWire while watched"; fi

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
    sink)      ensure_sink; restart_pipewire ;;
    smoke)     do_smoke ;;
esac
