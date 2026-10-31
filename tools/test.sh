#!/bin/bash
# Runs the test rungs and records their results for the deploy gate. See .claude/skills/test.
#
#     tools/test.sh unit | vpair | all [--no-record]
#
# --no-record runs the tests without writing the status file (the Stop hook uses it on a
# tree with uncommitted changes).
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

# The version and source identity in the LV2 bundles plugin-check built for plugin $1, and in
# the copies JUCE's copy step made of them, into ~/.lv2 of the HOME the build ran with: this
# one, as plugin-check builds before it sets a HOME of its own (dsp/juce_plugins/tests/buildinfo).
# The copy is what SpeakerPlugin.cmake orders after buildinfo.ttl is written.
check_build_info() {
    local p="$1" bundles copies=() b so
    bundles=("${BUILD_DIR:-dsp/juce_apps/plugin-check/build-check}/$p-builds/plugin/"*_artefacts/Release/LV2/*.lv2)
    for b in "${bundles[@]}"; do copies+=("$HOME/.lv2/$(basename "$b")"); done
    dsp/juce_plugins/tests/buildinfo/check.sh --arch x86_64 "$p" "${bundles[@]}" "${copies[@]}" && return 0
    # JUCE copies a bundle only when it links its binary, so a copy whose binary is not this
    # build's was written after it by another build in this HOME (tools/build.sh of another tree).
    for b in "${bundles[@]}"; do
        so="lib$(basename "$b" .lv2).so"
        if [ -e "$HOME/.lv2/$(basename "$b")/$so" ] && ! cmp -s "$b/$so" "$HOME/.lv2/$(basename "$b")/$so"; then
            echo "buildinfo: ~/.lv2/$(basename "$b") holds another build's binary, not a copy of this one: rm $b/$so and rerun, to link it and copy it again"
        fi
    done
    return 1
}

# One aarch64 bundle and its copy: loudness_headless, what the board installer deploys
# (firmware/speakerd-setup.sh), and the cheapest target to cross-build (no editor sources).
# tools/build.sh copies it into the build's own copied/lv2, never into a ~/.lv2.
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
    if [ $failed = 0 ]; then record unit pass "$checked"; else record unit fail "$checked"; fi
    return $failed
}

run_vpair() {
    echo "=== /test vpair"
    if ! command -v pipewire > /dev/null || ! command -v wireplumber > /dev/null; then
        echo "vpair: pipewire and wireplumber are needed: install Podman (tools/container.sh), or see CLAUDE.md"
        record vpair skipped "no pipewire"; return 1
    fi
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

VPAIR_RUNNER='
import sys, unittest
result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover("tests/vpair"))
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
    *) echo "usage: $0 unit | vpair | all [--no-record]" >&2; exit 2 ;;
esac
[ $record = 0 ] || { echo "=== status"; tools/status.py show; }
exit $rc
