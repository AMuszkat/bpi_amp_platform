#!/bin/bash
# Build the complete, control and headless targets of a plugin natively, build plugin-check,
# and run it. See README.md.
#
#     juce_apps/plugin-check/run.sh <plugin> [--no-editor]
#
# Environment:
#   JUCE_INSTALL_DIR   a JUCE install (cmake --install of juce_plugins/JUCE)   default /opt/juce
#   BUILD_DIR          where builds and the report go         default juce_apps/plugin-check/build-check
#
# The editor check needs an X display. With no DISPLAY set, it starts Xvfb and a window
# manager (openbox) itself when they are installed, and skips the editor check otherwise.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
PLUGIN="${1:?usage: $0 <plugin> [--no-editor]}"
EDITOR=1
[ "${2:-}" = "--no-editor" ] && EDITOR=0

JUCE_INSTALL_DIR="${JUCE_INSTALL_DIR:-/opt/juce}"
BUILD_DIR="$(mkdir -p "${BUILD_DIR:-$HERE/build-check}" && cd "${BUILD_DIR:-$HERE/build-check}" && pwd)"
SRC="$ROOT/juce_plugins/$PLUGIN"
[ -f "$SRC/CMakeLists.txt" ] || { echo "No plugin '$PLUGIN' in $ROOT/juce_plugins" >&2; exit 2; }

echo "=== Building $PLUGIN: complete, control, headless"
cmake -S "$SRC" -B "$BUILD_DIR/$PLUGIN" -G Ninja -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_PREFIX_PATH="$JUCE_INSTALL_DIR" > "$BUILD_DIR/$PLUGIN-configure.log"
cmake --build "$BUILD_DIR/$PLUGIN" -j"$(nproc)" \
      --target "${PLUGIN}_LV2" "${PLUGIN}_control_LV2" "${PLUGIN}_headless_LV2"

echo "=== Building plugin-check"
cmake -S "$HERE" -B "$BUILD_DIR/plugin-check" -G Ninja -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_PREFIX_PATH="$JUCE_INSTALL_DIR" > "$BUILD_DIR/plugin-check-configure.log"
cmake --build "$BUILD_DIR/plugin-check" -j"$(nproc)"

bundle() { echo "$BUILD_DIR/$PLUGIN/${1}_artefacts/Release/LV2/$1.lv2"; }

args=(--complete "$(bundle "$PLUGIN")"
      --control  "$(bundle "${PLUGIN}_control")"
      --headless "$(bundle "${PLUGIN}_headless")")

if [ -f "$SRC/plugin-check.probes" ]; then
    while read -r line; do
        line="${line%%#*}"; line="${line// /}"
        [ -n "$line" ] && args+=(--probe "$line")
    done < "$SRC/plugin-check.probes"
fi

# An empty home, so the LV2 host sees only the bundles named above, not the copies the build
# put in ~/.lv2 (which may be stale or from another branch).
export HOME="$BUILD_DIR/home"
mkdir -p "$HOME"

pids=()
cleanup() { [ ${#pids[@]} -gt 0 ] && kill "${pids[@]}" 2>/dev/null || true; }
trap cleanup EXIT

if [ "$EDITOR" = 1 ] && [ -z "${DISPLAY:-}" ]; then
    if command -v Xvfb > /dev/null && command -v openbox > /dev/null; then
        Xvfb :97 -screen 0 1280x800x24 > "$BUILD_DIR/xvfb.log" 2>&1 & pids+=($!)
        sleep 1
        export DISPLAY=:97
        openbox > "$BUILD_DIR/openbox.log" 2>&1 & pids+=($!)
        sleep 0.5
    else
        echo "(no DISPLAY and no Xvfb/openbox: skipping the editor check)"
        EDITOR=0
    fi
fi

[ "$EDITOR" = 1 ] && args+=(--show-editor 4)

echo "=== Running plugin-check"
REPORT="$BUILD_DIR/$PLUGIN-report.txt"
set +e
"$BUILD_DIR/plugin-check/plugin-check_artefacts/Release/plugin-check" "${args[@]}" 2>&1 \
    | grep --line-buffered -v 'attempt to map invalid URI' | tee "$REPORT" &
CHECK=$!

# Screenshot the control editor while it is up, when ImageMagick is there.
if [ "$EDITOR" = 1 ] && command -v import > /dev/null; then
    for _ in $(seq 1 60); do grep -q '^EDITOR ' "$REPORT" 2>/dev/null && break; sleep 0.5; done
    sleep 2
    import -window root "$BUILD_DIR/$PLUGIN-control-editor.png" 2>/dev/null \
        && echo "Screenshot: $BUILD_DIR/$PLUGIN-control-editor.png"
fi

wait $CHECK
grep -q '^ALL CHECKS PASSED' "$REPORT"
