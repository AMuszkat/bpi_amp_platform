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
#
# `up` starts both speakers at once and returns once WirePlumber has given each amplifier its
# ports; it fails when either speaker does not come up. `down` stops both at once and returns
# once their processes have exited (SIGKILL after 5 s). Neither waits a fixed time.

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

# Whether process $1 runs: it exists and is not a zombie (a killed child is one until its
# parent waits for it, and kill -0 still finds it).
alive() {
    local stat
    { read -r stat < "/proc/$1/stat"; } 2> /dev/null || return 1
    stat="${stat##*) }"
    [ "${stat%% *}" != Z ]
}

# Up while all three of its daemons run.
is_up() {
    local daemon pid
    for daemon in dbus pipewire wireplumber; do
        pid="$(cat "$BASE/$1/$daemon.pid" 2> /dev/null)" && alive "$pid" || return 1
    done
}

# Microseconds since the epoch, for the deadlines of the waits below.
usec() { echo "${EPOCHREALTIME/[.,]/}"; }

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
    # What is left of an earlier start goes first (the folder alone would leave its daemons).
    if [ -d "$d" ]; then stop_speaker "$s" > /dev/null; fi
    mkdir -p "$d/run" "$d/config" "$d/state"
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
    local started=0 deadline daemon
    (
        eval "$(speaker_env "$s")"
        pipewire -c "$d/pipewire.conf" > "$d/pipewire.log" 2>&1 < /dev/null &
        echo $! > "$d/pipewire.pid"
        # WirePlumber once PipeWire takes connections: its socket exists (10 s at most).
        deadline=$(( $(usec) + 10000000 ))
        until [ -S "$d/run/pipewire-0" ]; do
            alive "$(< "$d/pipewire.pid")" && [ "$(usec)" -lt $deadline ] || exit 1
            sleep 0.05
        done
        wireplumber > "$d/wireplumber.log" 2>&1 < /dev/null &
        echo $! > "$d/wireplumber.pid"
    ) || started=$?
    # Whatever started is in the pids, for `down`.
    cat "$d/dbus.pid" "$d/pipewire.pid" "$d/wireplumber.pid" 2> /dev/null | tr '\n' ' ' > "$d/pids" || true
    [ $started = 0 ] || { echo "$s: PipeWire did not take connections within 10 s, see $d/pipewire.log" >&2; exit 1; }

    # Up when the amplifier exists and WirePlumber has given it its ports (20 s at most).
    deadline=$(( $(usec) + 20000000 ))
    while :; do
        if (eval "$(speaker_env "$s")"; timeout 2 pw-link -i 2> /dev/null | grep -q '^amplifier:playback_FL$'); then
            echo "$s: up"
            return
        fi
        for daemon in pipewire wireplumber; do
            alive "$(< "$d/$daemon.pid")" || { echo "$s: $daemon exited, see $d/$daemon.log" >&2; exit 1; }
        done
        [ "$(usec)" -lt $deadline ] || break
        sleep 0.05
    done
    echo "$s: did not come up, see $d/*.log" >&2
    exit 1
}

# Waits up to $1 seconds for the processes $2... to exit; fails if one still runs then.
wait_gone() {
    local deadline=$(( $(usec) + $1 * 1000000 )) pid
    shift
    while :; do
        for pid; do alive "$pid" && break; pid=""; done
        [ -n "$pid" ] || return 0
        [ "$(usec)" -lt $deadline ] || return 1
        sleep 0.02
    done
}

stop_speaker() {
    local d="$BASE/$1" pid comm pids=()
    if [ -f "$d/pids" ]; then
        # Only the speaker's own daemons: a pid of a pair from before a restart of this machine or
        # container may be another process's now.
        for pid in $(cat "$d/pids"); do
            { read -r comm < "/proc/$pid/comm"; } 2> /dev/null || continue
            case "$comm" in dbus-daemon | pipewire | wireplumber) pids+=("$pid") ;; esac
        done
    fi
    if [ ${#pids[@]} -gt 0 ]; then
        kill "${pids[@]}" 2> /dev/null || true
        # Until they have exited: 5 s, then SIGKILL and 1 s more.
        if ! wait_gone 5 "${pids[@]}"; then
            echo "$1: still running 5 s after SIGTERM, killed: $(for pid in "${pids[@]}"; do alive "$pid" && cat "/proc/$pid/comm"; done | tr '\n' ' ')" >&2
            kill -KILL "${pids[@]}" 2> /dev/null || true
            wait_gone 1 "${pids[@]}" || true
        fi
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
        # Both at once; up fails when either does.
        starts=() rc=0
        for s in $chosen; do start_speaker "$s" & starts+=($!); done
        for pid in "${starts[@]}"; do wait "$pid" || rc=$?; done
        exit $rc
        ;;
    down)
        shift
        chosen="$(speakers "$@")"
        for s in $chosen; do stop_speaker "$s" & done
        wait
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
