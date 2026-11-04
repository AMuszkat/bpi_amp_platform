#!/bin/bash
# Build the complete, control and headless targets of a plugin natively, build plugin-check,
# and run it. See README.md.
#
#     juce_apps/plugin-check/run.sh <plugin> [--no-editor]
#
# Runs in the development container when Podman is installed (tools/container.sh at the
# repository root; BPI_CONTAINER=0 runs it natively).
#
# Environment:
#   JUCE_INSTALL_DIR   a JUCE install (cmake --install of juce_plugins/JUCE)
#                      default build/juce-install at the repository root, made when missing
#   PLUGIN_CHECK_LTO   1: the plugin's builds with link-time optimisation, as they ship
#                      0: without (SPEAKER_LTO=OFF): a faster check after an edit       default 1
#   BUILD_DIR          where builds and the report go
#                      default juce_apps/plugin-check/build-check, and build-check-nolto
#                      with PLUGIN_CHECK_LTO=0, so that neither tree is rebuilt for the other
#   BPI_CCACHE         1: ccache as the compiler launcher, when it is on PATH; otherwise none
#                      (tools/test.sh sets it, and CCACHE_DIR and CCACHE_BASEDIR, with ccache_env
#                      of tools/container.sh; this script has no policy of its own)  default unset
#
# The editor check needs an X display. With no DISPLAY set, it starts Xvfb (on the first free
# display number, so that several checks can run at once) and a window manager (openbox)
# itself when they are installed, and skips the editor check otherwise.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
. "$ROOT/../tools/container.sh"
# container_or_native, handing PLUGIN_CHECK_LTO on: the container is given BUILD_DIR,
# JUCE_INSTALL_DIR and the BPI_* variables (tools/container.sh), and not that one.
if container_wanted; then
    container_exec env ${PLUGIN_CHECK_LTO+"PLUGIN_CHECK_LTO=$PLUGIN_CHECK_LTO"} "$HERE/run.sh" "$@"
fi
PLUGIN="${1:?usage: $0 <plugin> [--no-editor]}"
EDITOR=1
[ "${2:-}" = "--no-editor" ] && EDITOR=0

# SPEAKER_LTO on every configure of the plugin's builds, so that a tree is built one way only
# (juce_plugins/cmake/SpeakerPlugin.cmake, speaker_add_lto_flags).
case "${PLUGIN_CHECK_LTO:-1}" in
    1) LTO=ON;  WITH=with;    DEFAULT_BUILD_DIR="$HERE/build-check" ;;
    0) LTO=OFF; WITH=without; DEFAULT_BUILD_DIR="$HERE/build-check-nolto" ;;
    *) echo "PLUGIN_CHECK_LTO is 1 (with link-time optimisation) or 0 (without), not '$PLUGIN_CHECK_LTO'" >&2; exit 2 ;;
esac

ensure_juce
BUILD_DIR="$(mkdir -p "${BUILD_DIR:-$DEFAULT_BUILD_DIR}" && cd "${BUILD_DIR:-$DEFAULT_BUILD_DIR}" && pwd)"
SRC="$ROOT/juce_plugins/$PLUGIN"
[ -f "$SRC/CMakeLists.txt" ] || { echo "No plugin '$PLUGIN' in $ROOT/juce_plugins" >&2; exit 2; }

# The compiler launcher, on both configures: ccache when the caller asked for it (BPI_CCACHE=1)
# and it is on PATH, otherwise none (empty), which also takes it off a tree an earlier run gave
# it to, so that the tree still builds where ccache is missing. Switching rebuilds the tree (the
# compile commands change).
launcher=
[ "${BPI_CCACHE:-}" = 1 ] && command -v ccache > /dev/null && launcher=ccache
LAUNCHER=(-DCMAKE_C_COMPILER_LAUNCHER="$launcher" -DCMAKE_CXX_COMPILER_LAUNCHER="$launcher")

# One build tree for the plugin's three targets and the in-process checker linked against each
# (in-process/CMakeLists.txt), so the plugin is compiled once for both.
PLUGIN_BUILD="$BUILD_DIR/$PLUGIN-builds"
echo "=== Building $PLUGIN: complete, control, headless, and the in-process checks" \
     "($WITH link-time optimisation${launcher:+, through ccache})"
cmake -S "$HERE/in-process" -B "$PLUGIN_BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release \
      -DPLUGIN_DIR="$SRC" -DCMAKE_PREFIX_PATH="$JUCE_INSTALL_DIR" -DSPEAKER_LTO=$LTO "${LAUNCHER[@]}" \
      > "$BUILD_DIR/$PLUGIN-configure.log"
cmake --build "$PLUGIN_BUILD" -j"$(nproc)" \
      --target "${PLUGIN}_LV2" "${PLUGIN}_control_LV2" "${PLUGIN}_headless_LV2" \
               "${PLUGIN}_in_process" "${PLUGIN}_control_in_process" "${PLUGIN}_headless_in_process"

# The checker itself, one build per BUILD_DIR for every plugin: locked, for the checks of two
# plugins running at once.
echo "=== Building plugin-check"
(
    flock 9
    cmake -S "$HERE" -B "$BUILD_DIR/plugin-check" -G Ninja -DCMAKE_BUILD_TYPE=Release \
          -DCMAKE_PREFIX_PATH="$JUCE_INSTALL_DIR" "${LAUNCHER[@]}" > "$BUILD_DIR/plugin-check-configure.log"
    cmake --build "$BUILD_DIR/plugin-check" -j"$(nproc)"
) 9> "$BUILD_DIR/plugin-check.lock"

bundle() { echo "$PLUGIN_BUILD/plugin/${1}_artefacts/Release/LV2/$1.lv2"; }

args=(--complete "$(bundle "$PLUGIN")"
      --control  "$(bundle "${PLUGIN}_control")"
      --headless "$(bundle "${PLUGIN}_headless")")

probes=()
if [ -f "$SRC/plugin-check.probes" ]; then
    while read -r line; do
        line="${line%%#*}"; line="${line// /}"
        [ -n "$line" ] && probes+=(--probe "$line")
    done < "$SRC/plugin-check.probes"
fi
args+=("${probes[@]}")

# An empty home, so the LV2 host sees only the bundles named above, not the copies the build
# put in ~/.lv2 (which may be stale or from another branch).
export HOME="$BUILD_DIR/home"
mkdir -p "$HOME"

pids=()
cleanup() { [ ${#pids[@]} -gt 0 ] && kill "${pids[@]}" 2>/dev/null || true; }
trap cleanup EXIT

# Runs "${@:3}" until it succeeds, for at most $2 seconds and while process $1 runs.
wait_until() {
    local pid="$1" deadline=$((SECONDS + $2))
    shift 2
    until "$@"; do
        kill -0 "$pid" 2> /dev/null && [ $SECONDS -lt $deadline ] || return 1
        sleep 0.02
    done
}

# The display number Xvfb wrote (a line), into $display_number.
read_display() { read -r display_number < "$BUILD_DIR/$PLUGIN-xvfb.display" && [[ "$display_number" =~ ^[0-9]+$ ]]; }

if [ "$EDITOR" = 1 ] && [ -z "${DISPLAY:-}" ]; then
    if command -v Xvfb > /dev/null && command -v openbox > /dev/null; then
        # Xvfb takes the first free display number and writes it, once it accepts connections,
        # to fd 3 (-displayfd); openbox runs its --startup command once it manages the screen.
        started="$BUILD_DIR/$PLUGIN-openbox.started"
        rm -f "$started"
        : > "$BUILD_DIR/$PLUGIN-xvfb.display"
        Xvfb -displayfd 3 -screen 0 1280x800x24 -nolisten tcp \
            3> "$BUILD_DIR/$PLUGIN-xvfb.display" > "$BUILD_DIR/$PLUGIN-xvfb.log" 2>&1 & pids+=($!)
        if ! wait_until "${pids[-1]}" 10 read_display; then
            echo "Xvfb did not start within 10 s ($BUILD_DIR/$PLUGIN-xvfb.log):" >&2
            tail -20 "$BUILD_DIR/$PLUGIN-xvfb.log" >&2
            exit 1
        fi
        export DISPLAY=":$display_number"
        openbox --startup "touch $(printf %q "$started")" > "$BUILD_DIR/$PLUGIN-openbox.log" 2>&1 & pids+=($!)
        if ! wait_until "${pids[-1]}" 10 test -e "$started"; then
            echo "openbox did not start on $DISPLAY within 10 s ($BUILD_DIR/$PLUGIN-openbox.log):" >&2
            tail -20 "$BUILD_DIR/$PLUGIN-openbox.log" >&2
            exit 1
        fi
    else
        echo "(no DISPLAY and no Xvfb/openbox: skipping the editor check)"
        EDITOR=0
    fi
fi

# With ImageMagick, the editor stays up until the screenshot is taken (at most 60 s); without
# it, for 8 s.
SHOT_DONE="$BUILD_DIR/$PLUGIN-editor-shot.done"
rm -f "$SHOT_DONE" "$SHOT_DONE.nudge"
if [ "$EDITOR" = 1 ]; then
    if command -v import > /dev/null; then
        args+=(--show-editor 60 --editor-until "$SHOT_DONE")
    else
        args+=(--show-editor 8)
    fi
fi

REPORT="$BUILD_DIR/$PLUGIN-report.txt"
: > "$REPORT"
set +e

# Each build linked in directly: its monitors, its getters, its own state. The complete build
# says what each monitor reads after the tone (EXPECT lines), which the hosted headless build
# must then publish.
echo "=== Running the in-process checks"
in_process_ok=1
for build in "$PLUGIN" "${PLUGIN}_control" "${PLUGIN}_headless"; do
    "$PLUGIN_BUILD/${build}_in_process" "${probes[@]}" 2>&1 | tee -a "$REPORT"
    [ "${PIPESTATUS[0]}" = 0 ] || in_process_ok=0
done
while read -r expect; do
    args+=(--expect-monitor "$expect")
done < <(sed -n 's/^EXPECT //p' "$REPORT")

echo "=== Running plugin-check"
"$BUILD_DIR/plugin-check/plugin-check_artefacts/Release/plugin-check" "${args[@]}" 2>&1 \
    | grep --line-buffered -v 'attempt to map invalid URI' | tee -a "$REPORT" &
CHECK=$!

# Screenshot the control editor while it is up, when ImageMagick is there.
if [ "$EDITOR" = 1 ] && command -v import > /dev/null; then
    # The editor opens after every other check, which under load takes a while: wait for it
    # for as long as plugin-check runs.
    while kill -0 $CHECK 2> /dev/null && ! grep -q '^EDITOR ' "$REPORT"; do sleep 0.05; done
    shot="$BUILD_DIR/$PLUGIN-control-editor.png"
    # The screen is black apart from the editor, and a window that has not painted yet is a
    # plain rectangle of one or two colours; under load that can last seconds, and so can each
    # capture. So capture until one shows a painted editor (or 40 s have gone), keeping the
    # most colourful, and only then tell plugin-check to close the editor.
    # An editor still unpainted after 3 s is nudged (plugin-check hides and shows its window,
    # see showEditor in Main.cpp), and again every 3 s after that.
    best=0
    until=$((SECONDS + 40))
    nudge_at=$((SECONDS + 3))
    while grep -q '^EDITOR ' "$REPORT" && kill -0 $CHECK 2> /dev/null && [ $SECONDS -lt $until ]; do
        sleep 0.1
        import -window root "$shot.next.png" 2> /dev/null || continue
        colours="$(convert "$shot.next.png" -format '%k' info: 2> /dev/null || echo 0)"
        if [ "${colours:-0}" -gt "$best" ]; then best="$colours"; mv -f "$shot.next.png" "$shot"; fi
        [ "$best" -gt 16 ] && break
        if [ $SECONDS -ge $nudge_at ]; then touch "$SHOT_DONE.nudge"; nudge_at=$((SECONDS + 3)); fi
    done
    touch "$SHOT_DONE"
    rm -f "$shot.next.png"
    if [ "$best" -gt 16 ]; then
        echo "Screenshot: $shot"
    elif [ -f "$shot" ]; then
        echo "Screenshot: $shot (the editor had not painted: $best colours on the screen)"
    fi
fi

wait $CHECK
[ $in_process_ok = 1 ] && grep -q '^ALL CHECKS PASSED' "$REPORT"
