#!/bin/bash
# The virtual pair: two virtual speakers, "left" and "right", each with its own PipeWire,
# WirePlumber and D-Bus session, on this machine. See README.md.
#
#     tests/vpair/vpair.sh up | down | status
#     eval "$(tests/vpair/vpair.sh env left)"     # point pw-* tools at one speaker
#
# VPAIR_DIR is where the speakers keep their sockets and state (default /tmp/vpair). Keep it
# short: a PipeWire socket path must fit in 108 bytes.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="${VPAIR_DIR:-/tmp/vpair}"
SPEAKERS=(left right)

need() { command -v "$1" > /dev/null || { echo "vpair: '$1' is not installed ($2)" >&2; exit 2; }; }

speaker_env() {
    local d="$BASE/$1"
    echo "export XDG_RUNTIME_DIR='$d/run' XDG_CONFIG_HOME='$d/config' XDG_STATE_HOME='$d/state'"
    [ -f "$d/dbus.address" ] && echo "export DBUS_SESSION_BUS_ADDRESS='$(cat "$d/dbus.address")'"
    return 0
}

is_up() { [ -f "$BASE/$1/pids" ] && kill -0 $(cat "$BASE/$1/pids") 2> /dev/null; }

start_speaker() {
    local s="$1" d="$BASE/$1"
    if is_up "$s"; then echo "$s: already up"; return; fi
    rm -rf "$d"; mkdir -p "$d/run" "$d/config" "$d/state"
    [ ${#d} -lt 80 ] || { echo "vpair: VPAIR_DIR is too long for PipeWire's socket path" >&2; exit 2; }

    # WirePlumber needs a session bus; each speaker gets its own.
    dbus-daemon --session --fork --print-address=3 --print-pid=4 3> "$d/dbus.address" 4> "$d/dbus.pid"
    (
        eval "$(speaker_env "$s")"
        pipewire -c "$HERE/speaker.conf" > "$d/pipewire.log" 2>&1 < /dev/null &
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
        mkdir -p "$BASE"
        for s in "${SPEAKERS[@]}"; do start_speaker "$s"; done
        ;;
    down)
        for s in "${SPEAKERS[@]}"; do stop_speaker "$s"; done
        ;;
    status)
        for s in "${SPEAKERS[@]}"; do is_up "$s" && echo "$s: up" || echo "$s: down"; done
        ;;
    env)
        speaker_env "${2:?usage: $0 env <left|right>}"
        ;;
    *)
        echo "usage: $0 up | down | status | env <speaker>" >&2
        exit 2
        ;;
esac
