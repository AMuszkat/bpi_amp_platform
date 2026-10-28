#!/bin/bash
# The virtual pair: two virtual speakers, "left" and "right", each with its own PipeWire,
# WirePlumber and D-Bus session, on this machine. See README.md.
#
#     tests/vpair/vpair.sh up | down | status
#     tests/vpair/vpair.sh up right               # one speaker only (also for down)
#     tests/vpair/vpair.sh run left pw-link -l    # run a pw-* tool against one speaker
#     eval "$(tests/vpair/vpair.sh env left)"     # the same, for a shell in the same place
#
# On a machine with Podman the pair runs in the development container (tools/container.sh),
# and this script moves there itself; `env` is then only useful inside the container.
#
# VPAIR_DIR is where the speakers keep their sockets and state (default /tmp/vpair). Keep it
# short: a PipeWire socket path must fit in 108 bytes.
#
# For `up`: VPAIR_SPEAKER_CONF is the PipeWire configuration instead of speaker.conf, and
# VPAIR_PIPEWIRE_DROPINS a folder of drop-ins (*.conf) PipeWire loads with it, as a board loads
# ~/.config/pipewire/pipewire.conf.d. Name one speaker to give it its own.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/../../tools/container.sh"
container_or_native "$0" "$@"
BASE="${VPAIR_DIR:-/tmp/vpair}"
SPEAKERS=(left right)

need() { command -v "$1" > /dev/null || { echo "vpair: '$1' is not installed ($2, or install Podman to use the container)" >&2; exit 2; }; }

speaker_env() {
    local d="$BASE/$1"
    echo "export XDG_RUNTIME_DIR='$d/run' XDG_CONFIG_HOME='$d/config' XDG_STATE_HOME='$d/state'"
    [ -f "$d/dbus.address" ] && echo "export DBUS_SESSION_BUS_ADDRESS='$(cat "$d/dbus.address")'"
    return 0
}

# shellcheck disable=SC2046  # one argument per pid
is_up() { [ -f "$BASE/$1/pids" ] && kill -0 $(cat "$BASE/$1/pids") 2> /dev/null; }

# The speakers named on the command line, or both.
speakers() {
    [ $# -gt 0 ] || set -- "${SPEAKERS[@]}"
    local s
    for s; do
        case " ${SPEAKERS[*]} " in *" $s "*) echo "$s" ;; *) echo "vpair: no speaker '$s' (left or right)" >&2; exit 2 ;; esac
    done
}

start_speaker() {
    local s="$1" d="$BASE/$1"
    if is_up "$s"; then echo "$s: already up"; return; fi
    rm -rf "$d"; mkdir -p "$d/run" "$d/config" "$d/state"
    [ ${#d} -lt 80 ] || { echo "vpair: VPAIR_DIR is too long for PipeWire's socket path" >&2; exit 2; }

    # A virtual speaker has no Bluetooth. For it WirePlumber 0.4 (bookworm's, as on the
    # boards) watches logind, and exits where there is no systemd, as in the container.
    mkdir -p "$d/config/wireplumber/bluetooth.lua.d"
    echo 'bluez_monitor.enabled = false' > "$d/config/wireplumber/bluetooth.lua.d/80-no-bluetooth.lua"

    # Its configuration, and the drop-ins next to it: PipeWire loads <config>.d/*.conf.
    cp "${VPAIR_SPEAKER_CONF:-$HERE/speaker.conf}" "$d/pipewire.conf"
    if [ -n "${VPAIR_PIPEWIRE_DROPINS:-}" ]; then
        [ -d "$VPAIR_PIPEWIRE_DROPINS" ] || { echo "vpair: no folder $VPAIR_PIPEWIRE_DROPINS (VPAIR_PIPEWIRE_DROPINS)" >&2; exit 2; }
        ln -s "$(cd "$VPAIR_PIPEWIRE_DROPINS" && pwd)" "$d/pipewire.conf.d"
    fi

    # WirePlumber needs a session bus; each speaker gets its own.
    dbus-daemon --session --fork --print-address=3 --print-pid=4 3> "$d/dbus.address" 4> "$d/dbus.pid"
    (
        eval "$(speaker_env "$s")"
        pipewire -c "$d/pipewire.conf" > "$d/pipewire.log" 2>&1 < /dev/null &
        echo $! > "$d/pipewire.pid"
        sleep 0.5
        wireplumber > "$d/wireplumber.log" 2>&1 < /dev/null &
        echo $! > "$d/wireplumber.pid"
    )
    cat "$d/dbus.pid" "$d/pipewire.pid" "$d/wireplumber.pid" | tr '\n' ' ' > "$d/pids"

    # Up when the amplifier exists and WirePlumber has given it its ports.
    for _ in $(seq 1 40); do
        if (eval "$(speaker_env "$s")"; timeout 2 pw-link -i 2> /dev/null | grep -q '^amplifier:playback_FL$'); then
            echo "$s: up"
            return
        fi
        sleep 0.25
    done
    echo "$s: did not come up, see $d/*.log" >&2
    exit 1
}

stop_speaker() {
    local d="$BASE/$1"
    if [ -f "$d/pids" ]; then
        # shellcheck disable=SC2046  # one argument per pid
        kill $(cat "$d/pids") 2> /dev/null || true
        sleep 0.3
    fi
    rm -rf "$d"
    echo "$1: down"
}

case "${1:-}" in
    up)
        need pipewire "apt install pipewire"
        need wireplumber "apt install wireplumber"
        need dbus-daemon "apt install dbus"
        need pw-link "apt install pipewire-bin"
        shift
        chosen="$(speakers "$@")"
        mkdir -p "$BASE"
        for s in $chosen; do start_speaker "$s"; done
        ;;
    down)
        shift
        chosen="$(speakers "$@")"
        for s in $chosen; do stop_speaker "$s"; done
        ;;
    status)
        for s in "${SPEAKERS[@]}"; do is_up "$s" && echo "$s: up" || echo "$s: down"; done
        ;;
    env)
        speaker_env "${2:?usage: $0 env <left|right>}"
        ;;
    run)
        [ $# -ge 3 ] || { echo "usage: $0 run <left|right> <command...>" >&2; exit 2; }
        eval "$(speaker_env "$2")"
        shift 2
        exec "$@"
        ;;
    *)
        echo "usage: $0 up [speaker...] | down [speaker...] | status | env <speaker> | run <speaker> <command...>" >&2
        exit 2
        ;;
esac
