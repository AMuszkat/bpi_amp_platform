#!/bin/bash
# The controller's own tests, against the fake hub (README.md, "Tests"):
#
#     dsp/juce_apps/controller/tests/run.sh               # the bridge tests (test_controller.py)
#     dsp/juce_apps/controller/tests/run.sh --screenshot  # and the GUI under Xvfb, with screenshots
#
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

python3 -m unittest discover -s "$HERE" -v

[ "${1:-}" = "--screenshot" ] || exit 0

# The GUI: the hub's graph on the canvas, the speakers panel, and the loudness control editor
# open, on a virtual display (as plugin-check/run.sh does).
OUT="$ROOT/build/controller-check"
mkdir -p "$OUT"
pids=()
cleanup() { [ ${#pids[@]} -gt 0 ] && kill "${pids[@]}" 2> /dev/null || true; }
trap cleanup EXIT

if [ -z "${DISPLAY:-}" ]; then
    Xvfb :96 -screen 0 1720x1000x24 > "$OUT/xvfb.log" 2>&1 & pids+=($!)
    sleep 1
    export DISPLAY=:96
    openbox > "$OUT/openbox.log" 2>&1 & pids+=($!)
    sleep 0.5
fi

python3 "$HERE/fake_hub.py" --probe > "$OUT/hub.log" 2>&1 & pids+=($!)
sleep 0.5

cat > "$OUT/screenshot.script" <<EOF
main-window 0 0 1040 720
wait-synced
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
for _ in $(seq 1 100); do grep -q '"text":"capture"' "$OUT/screenshot.jsonl" 2> /dev/null && break; sleep 0.1; done
import -window root "$OUT/controller-screen.png"
wait $ctl
tail -1 "$OUT/screenshot.jsonl"
echo "Screenshots: $OUT/controller-screen.png (the X screen), $OUT/controller-juce.png (JUCE's own rendering)"
