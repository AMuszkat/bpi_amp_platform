#!/bin/bash
# The controller's own tests, against the fake hub (README.md, "Tests"):
#
#     dsp/juce_apps/controller/tests/run.sh               # the bridge tests (test_controller.py)
#     dsp/juce_apps/controller/tests/run.sh --screenshot  # and the GUI under Xvfb, with screenshots
#     dsp/juce_apps/controller/tests/run.sh --jobs N      # N tests at a time (runner.py)
#
# The tests run several at a time, each worker on its own display from :300 up and its own
# fake hub addresses (runner.py); those marked @alone after the others, one at a time. Where CI
# is set, one at a time; $BPI_CONTROLLER_JOBS sets the width too.
# Runs in the development container when Podman is installed (tools/container.sh at the
# repository root; BPI_CONTAINER=0 runs it natively). Builds the controller first when it is
# missing. Screenshots go to build/controller-check/.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../../.." && pwd)"
. "$ROOT/tools/container.sh"
container_or_native "$0" "$@"
cd "$ROOT"

CONTROLLER="${CONTROLLER:-$ROOT/build/controller/controller_artefacts/Release/controller}"
[ -x "$CONTROLLER" ] || tools/build.sh controller
export CONTROLLER

screenshot=0 jobs=()
while [ $# -gt 0 ]; do
    case "$1" in
        --screenshot) screenshot=1 ;;
        --jobs) jobs=(--jobs "${2:?--jobs N}"); shift ;;
        --jobs=*) jobs=(--jobs "${1#--jobs=}") ;;
        *) echo "usage: $0 [--jobs N] [--screenshot]" >&2; exit 2 ;;
    esac
    shift
done

# A SIGTERM or SIGHUP to the session (tools/schedule.py passes them on) reaches the runner too,
# which stops its tests and displays and exits 143 or 129; this script waits for it (a trap
# runs once the runner has ended) and exits with its status, rather than dying first.
trap : TERM HUP
python3 "$HERE/runner.py" "${jobs[@]}"
trap - TERM HUP

[ "$screenshot" = 1 ] || exit 0

# The GUI: the hub's graph on the canvas, the speakers panel (a buffer size of 256 chosen), and
# the loudness control editor open, on a virtual display (as plugin-check/run.sh does).
OUT="$ROOT/build/controller-check"
mkdir -p "$OUT"
pids=()
cleanup() { [ ${#pids[@]} -gt 0 ] && kill "${pids[@]}" 2> /dev/null || true; }
trap cleanup EXIT

# wait_for SECONDS COMMAND...: until COMMAND succeeds, at most SECONDS.
wait_for() {
    local deadline=$((SECONDS + $1)); shift
    until "$@"; do
        [ $SECONDS -lt $deadline ] || return 1
        sleep 0.05
    done
}

if [ -z "${DISPLAY:-}" ]; then
    # :96, never :0 (a JUCE program with no DISPLAY opens :0.0). Xvfb writes the number to fd 3
    # once it takes connections, and openbox runs its --startup command once it manages the screen.
    Xvfb :96 -displayfd 3 -screen 0 1720x1000x24 -nolisten tcp 3> "$OUT/xvfb.display" > "$OUT/xvfb.log" 2>&1 & pids+=($!)
    if ! wait_for 10 grep -qx 96 "$OUT/xvfb.display"; then
        echo "Xvfb did not start on :96 within 10 s ($OUT/xvfb.log):" >&2; tail -20 "$OUT/xvfb.log" >&2; exit 1
    fi
    export DISPLAY=:96
    rm -f "$OUT/openbox.started"
    openbox --startup "touch $(printf %q "$OUT/openbox.started")" > "$OUT/openbox.log" 2>&1 & pids+=($!)
    if ! wait_for 10 test -e "$OUT/openbox.started"; then
        echo "openbox did not start within 10 s ($OUT/openbox.log)" >&2; exit 1
    fi
fi

python3 -u "$HERE/fake_hub.py" --probe > "$OUT/hub.log" 2>&1 & pids+=($!)
if ! wait_for 10 grep -q '^fake hub on' "$OUT/hub.log"; then
    echo "the fake hub did not start within 10 s ($OUT/hub.log):" >&2; tail -20 "$OUT/hub.log" >&2; exit 1
fi

cat > "$OUT/screenshot.script" <<EOF
main-window 0 0 1040 720
wait-synced
buffer-size 256
expect-applied
expect-buffer-size left 256
open 16 1060 30
open 20 1060 560
sleep 2500
screenshot $OUT/controller-juce.png
print capture
sleep 2500
state
EOF

export HOME="$OUT/home"   # its own settings file, never the user's
mkdir -p "$HOME"
# The tests' pair secret (fake_hub.SECRET), in a file of mode 600 as the controller wants it.
python3 -c "import sys; sys.path.insert(0, '$HERE'); import fake_hub; print(fake_hub.SECRET)" > "$OUT/secret"
chmod 600 "$OUT/secret"
"$CONTROLLER" --hub 127.0.3.1 --name ctl-screenshot --speaker right=127.0.3.2 --test-probe --gui \
    --secret-file "$OUT/secret" \
    --script "$OUT/screenshot.script" > "$OUT/screenshot.jsonl" &
ctl=$!
wait_for 30 grep -q '"text":"capture"' "$OUT/screenshot.jsonl" || echo "(no capture step within 30 s: the screen shot is of whatever is there)" >&2
import -window root "$OUT/controller-screen.png"
wait $ctl
tail -1 "$OUT/screenshot.jsonl"
echo "Screenshots: $OUT/controller-screen.png (the X screen), $OUT/controller-juce.png (JUCE's own rendering)"
