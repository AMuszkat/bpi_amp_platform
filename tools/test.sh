#!/bin/bash
# Runs the test rungs and records their results for the deploy gate. See .claude/skills/test.
#
#     tools/test.sh unit | vpair | all | affected [--no-record]
#
# affected runs only the virtual pair modules that cover the files changed since the last
# commit green in full (tools/status.py plan, by tests/impact.json), and refuses when a changed
# file needs both rungs in full. --no-record runs the tests without writing the status file (the
# Stop hook uses it on a tree with uncommitted changes).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
. tools/container.sh
container_or_native "$0" "$@"
what="${1:-all}"
record=1
[ "${2:-}" = "--no-record" ] && record=0
rc=0

record() { [ $record = 0 ] || tools/status.py set "$@"; }
record_affected() { [ $record = 0 ] || tools/status.py affected "$@"; }

# The version and source identity in the LV2 bundles plugin-check built for plugin $1
# (dsp/juce_plugins/tests/buildinfo). Not in the copies JUCE made of them in ~/.lv2: that folder
# is outside the tree, and any other build in the same HOME (another tree's, build-podman's
# local debug loop) may have written it since. That the copy step copies buildinfo.ttl too is
# proven below, on the aarch64 build's copy.
check_build_info() {
    local p="$1" bundles
    bundles=("${BUILD_DIR:-dsp/juce_apps/plugin-check/build-check}/$p-builds/plugin/"*_artefacts/Release/LV2/*.lv2)
    dsp/juce_plugins/tests/buildinfo/check.sh --arch x86_64 "$p" "${bundles[@]}"
}

# One aarch64 bundle and its copy: loudness_headless, what the board installer deploys
# (firmware/speakerd-setup.sh), and the cheapest target to cross-build (no editor sources).
# tools/build.sh copies it into the build's own copied/lv2, never into a ~/.lv2, so only this
# build tree writes that copy: it proves that the copy step, which SpeakerPlugin.cmake orders
# after buildinfo.ttl is written, copies the bundle with it (one function there adds both steps
# to every target, on every machine).
AARCH64_PLUGIN=loudness
AARCH64_TARGET=headless
check_build_info_aarch64() {
    local out="build/aarch64/$AARCH64_PLUGIN" name="${AARCH64_PLUGIN}_$AARCH64_TARGET"
    tools/build.sh "$AARCH64_PLUGIN" aarch64 "$AARCH64_TARGET" \
        && dsp/juce_plugins/tests/buildinfo/check.sh --arch aarch64 "$AARCH64_PLUGIN" \
               "$out/${name}_artefacts/Release/LV2/$name.lv2" "$out/copied/lv2/$name.lv2"
}

run_unit() {
    local plugins=() p
    for f in dsp/juce_plugins/*/CMakeLists.txt; do
        grep -q '^[[:space:]]*TARGETS' "$f" && plugins+=("$(basename "$(dirname "$f")")")
    done
    if [ ${#plugins[@]} -eq 0 ]; then
        echo "unit: no plugin declares TARGETS yet"; record unit skipped "no plugin with TARGETS"; return 0
    fi
    if ! ensure_juce; then
        echo "unit: no JUCE install (see CLAUDE.md)"
        record unit skipped "no JUCE install"; return 1
    fi
    local failed=0
    for p in "${plugins[@]}"; do
        echo "=== /test unit: $p"
        # plugin-check, then the version and source identity in the bundles it built.
        if dsp/juce_apps/plugin-check/run.sh "$p" && check_build_info "$p"; then
            echo "unit: $p passed"
        else
            echo "unit: $p FAILED"; failed=1
        fi
    done
    local checked="${plugins[*]}"
    # The version and source identity in an aarch64 build and its copy.
    echo "=== /test unit: buildinfo-aarch64"
    if check_build_info_aarch64; then echo "unit: buildinfo-aarch64 passed"; else echo "unit: buildinfo-aarch64 FAILED"; failed=1; fi
    checked+=" buildinfo-aarch64"
    # The shared block FIR of firConv and crossover, bit for bit on x86_64 and on aarch64
    # under qemu (dsp/juce_plugins/tests/blockfir).
    echo "=== /test unit: blockfir"
    if dsp/juce_plugins/tests/blockfir/run.sh; then echo "unit: blockfir passed"; else echo "unit: blockfir FAILED"; failed=1; fi
    checked+=" blockfir"
    # plugin-versions.sh check and list on a scratch repository (dsp/juce_plugins/tests/plugin-versions).
    echo "=== /test unit: plugin-versions"
    if dsp/juce_plugins/tests/plugin-versions/run.sh; then echo "unit: plugin-versions passed"; else echo "unit: plugin-versions FAILED"; failed=1; fi
    checked+=" plugin-versions"
    # The controller's bridge against its fake hub (dsp/juce_apps/controller/tests).
    if [ -f dsp/juce_apps/controller/CMakeLists.txt ]; then
        echo "=== /test unit: controller"
        if tools/build.sh controller && dsp/juce_apps/controller/tests/run.sh; then
            echo "unit: controller passed"
        else
            echo "unit: controller FAILED"; failed=1
        fi
        checked+=" controller"
    fi
    # The deploy gate's definition of green, full and affected (tools/status.py gate).
    echo "=== /test unit: status-gate"
    if python3 tools/tests/test_status_gate.py; then echo "unit: status-gate passed"; else echo "unit: status-gate FAILED"; failed=1; fi
    checked+=" status-gate"
    if [ $failed = 0 ]; then record unit pass "$checked"; else record unit fail "$checked"; fi
    return $failed
}

have_pipewire() {
    command -v pipewire > /dev/null && command -v wireplumber > /dev/null && return 0
    echo "$1: pipewire and wireplumber are needed: install Podman (tools/container.sh), or see CLAUDE.md"
    return 1
}

run_vpair() {
    echo "=== /test vpair"
    have_pipewire vpair || { record vpair skipped "no pipewire"; return 1; }
    # python3 -m unittest discover -s tests/vpair -v, which also writes the note: how many tests
    # ran, which failed, and which were skipped and why, so that a pass with skips shows.
    local note result=pass
    note="$(mktemp)"
    python3 -c "$VPAIR_RUNNER" "$note" || result=fail
    [ -s "$note" ] || echo "the test run wrote no note" > "$note"
    record vpair "$result" "$(cat "$note")"
    rm -f "$note"
    [ "$result" = pass ]
}

# Runs no part of rung 1: every file rung 1 covers needs the full run, so the base's stands.
run_affected() {
    echo "=== /test affected"
    local plan base tests note result=pass
    if ! plan="$(tools/status.py plan)"; then
        echo "affected: needs both rungs in full: tools/test.sh all"; return 1
    fi
    base="$(sed -n 1p <<< "$plan")"
    tests="$(sed -n 2p <<< "$plan")"
    if [ -z "$tests" ]; then
        echo "affected: no test covers what changed since ${base:0:12}: nothing to run"
        record_affected pass "$base" "" "nothing to run: no test covers the files changed since ${base:0:12}"
        return 0
    fi
    have_pipewire affected || { record_affected skipped "$base" "" "no pipewire"; return 1; }
    note="$(mktemp)"
    # shellcheck disable=SC2086 # the module names, one word each
    python3 -c "$VPAIR_RUNNER" "$note" $tests || result=fail
    [ -s "$note" ] || echo "the test run wrote no note" > "$note"
    record_affected "$result" "$base" "$tests" "$(cat "$note")"
    rm -f "$note"
    [ "$result" = pass ]
}

# python3 -c "$VPAIR_RUNNER" NOTE [MODULE ...]: the modules of tests/vpair named, or all of them.
VPAIR_RUNNER='
import sys, unittest
if sys.argv[2:]:
    sys.path.insert(0, "tests/vpair")
    suite = unittest.defaultTestLoader.loadTestsFromNames(sys.argv[2:])
else:
    suite = unittest.defaultTestLoader.discover("tests/vpair")
result = unittest.TextTestRunner(verbosity=2).run(suite)
one_line = lambda text: " ".join(str(text).split())
note = [f"{result.testsRun} tests"]
broken = [test.id() for test, _ in result.failures + result.errors]
if broken:
    note.append(f"{len(broken)} failed: " + ", ".join(broken))
note.append(f"{len(result.skipped)} skipped" + (": " if result.skipped else "")
            + "; ".join(f"{test.id()} ({one_line(reason)})" for test, reason in result.skipped))
with open(sys.argv[1], "w") as f:
    f.write(", ".join(note))
sys.exit(0 if result.wasSuccessful() else 1)
'

case "$what" in
    unit)  run_unit || rc=1 ;;
    vpair) run_vpair || rc=1 ;;
    all)   run_unit || rc=1; run_vpair || rc=1 ;;
    affected) run_affected || rc=1 ;;
    *) echo "usage: $0 unit | vpair | all | affected [--no-record]" >&2; exit 2 ;;
esac
[ $record = 0 ] || { echo "=== status"; tools/status.py show; }
exit $rc
