#!/bin/bash
# The tests. See .claude/skills/test.
#
#   Checks, minutes each, run while working (several at once: tools/test.sh speakerd installers):
#     tools/test.sh plugin <name>      plugin-check of one plugin, and its build identity
#     tools/test.sh plugins            every plugin's plugin-check, the block FIR, plugin-versions
#     tools/test.sh speakerd           speakerd alone on the virtual pair: sound, layers, restarts
#     tools/test.sh ampd               ampd against a stand-in speakerd: the protocol, no audio
#     tools/test.sh pair               speakerd and ampd together: knobs, edits, meters, failover
#     tools/test.sh controller         the controller's own tests, and end to end on the pair
#     tools/test.sh installers         the installers and the board script, on a virtual speaker
#     tools/test.sh chain              the board's chain: its cost and the builds' identity
#     tools/test.sh tools              this script's tools: status.py, impact.py, the lab lock
#     tools/test.sh module <name>...   named modules of tests/vpair
#     tools/test.sh changed [REF]      the checks that cover the files changed (tools/impact.py)
#
#   Levels, end to end, run when asked and before a push (recorded by tools/status.py):
#     tools/test.sh unit | vpair | all [--no-record]
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
. tools/container.sh
container_or_native "$0" "$@"
record=1
rc=0

record() { [ $record = 0 ] || tools/status.py set "$@"; }

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

have_pipewire() {
    command -v pipewire > /dev/null && command -v wireplumber > /dev/null && return 0
    echo "$1: pipewire and wireplumber are needed: install Podman (tools/container.sh), or see CLAUDE.md"
    return 1
}

# Runs the named modules of tests/vpair (none: all of them); prints the note; the note's text in
# $NOTE.
modules() {
    local what="$1" file
    shift
    have_pipewire "$what" || { NOTE="skipped: no pipewire"; return 1; }
    file="$(mktemp)"
    python3 -c "$VPAIR_RUNNER" "$file" "$@"
    local rc=$?
    NOTE="$(cat "$file")"
    [ -n "$NOTE" ] || NOTE="the test run wrote no note"
    rm -f "$file"
    echo "$what: $NOTE"
    return $rc
}

plugins_with_targets() {
    local f
    for f in dsp/juce_plugins/*/CMakeLists.txt; do
        grep -q '^[[:space:]]*TARGETS' "$f" && basename "$(dirname "$f")"
    done
}

# plugin-check of plugin $1, then the version and source identity in the bundles it built
# (dsp/juce_plugins/tests/buildinfo). Not in the copies JUCE made of them in ~/.lv2: any other
# build in the same HOME may have written those since.
check_plugin() {
    ensure_juce || { echo "plugin $1: no JUCE install (see CLAUDE.md)"; return 1; }
    dsp/juce_apps/plugin-check/run.sh "$1" || return 1
    local bundles=("${BUILD_DIR:-dsp/juce_apps/plugin-check/build-check}/$1-builds/plugin/"*_artefacts/Release/LV2/*.lv2)
    dsp/juce_plugins/tests/buildinfo/check.sh --arch x86_64 "$1" "${bundles[@]}"
}

# One aarch64 bundle and its copy: loudness_headless, the cheapest target to cross-build.
# tools/build.sh copies it into the build's own copied/lv2, so only this build tree writes that
# copy: it proves that the copy step, ordered after buildinfo.ttl is written, copies it too.
check_build_info_aarch64() {
    local out="build/aarch64/loudness" name="loudness_headless"
    tools/build.sh loudness aarch64 headless \
        && dsp/juce_plugins/tests/buildinfo/check.sh --arch aarch64 loudness \
               "$out/${name}_artefacts/Release/LV2/$name.lv2" "$out/copied/lv2/$name.lv2"
}

# --- The checks -----------------------------------------------------------------------------------

SPEAKERD_MODULES="test_audio_path test_speakerd_same_sound test_speakerd_state_layers test_speakerd_start_order
    test_speakerd_restart test_speakerd_second_instance test_speakerd_quantum_change test_passthrough_safety
    test_speakerd_device_limits test_speakerd_import"
AMPD_MODULES="test_ampd_protocol test_two_controllers"
PAIR_MODULES="test_knob_latency test_graph_edit test_hub_down test_drag_during_revision test_ampd_restart
    test_ampd_persistence test_meter_values test_monitor_fanout test_board_cpu test_buffer_size"
INSTALLER_MODULES="test_firmware_speakerd_install test_firmware_ampd_install test_speakerd_installed
    test_speakerd_crash_restart"
CHAIN_MODULES="test_chain_cost test_plugin_identity"

# shellcheck disable=SC2086 # the module lists, one word each
check() {
    local what="$1"
    shift
    echo "=== /test $what${*:+ $*}"
    case "$what" in
        plugin)     check_plugin "$1" ;;
        plugins)    local p ok=0
                    for p in $(plugins_with_targets); do check_plugin "$p" || ok=1; done
                    dsp/juce_plugins/tests/blockfir/run.sh || ok=1
                    dsp/juce_plugins/tests/plugin-versions/run.sh || ok=1
                    return $ok ;;
        speakerd)   modules speakerd $SPEAKERD_MODULES ;;
        ampd)       modules ampd $AMPD_MODULES ;;
        pair)       modules pair $PAIR_MODULES ;;
        controller) tools/build.sh controller && dsp/juce_apps/controller/tests/run.sh \
                        && modules controller test_controller_end_to_end ;;
        installers) modules installers $INSTALLER_MODULES ;;
        chain)      modules chain $CHAIN_MODULES ;;
        tools)      python3 tools/tests/test_status_gate.py && python3 tools/tests/test_impact.py ;;
        module)     local m names=()
                    for m in "$@"; do names+=("test_${m#test_}"); done
                    modules module "${names[@]}" ;;
        *) echo "no check named $what" >&2; return 2 ;;
    esac
}

run_changed() {
    local checks line ok=0
    checks="$(tools/impact.py "$@")" || return 1
    [ -n "$checks" ] || { echo "changed: nothing to run"; return 0; }
    while read -r line; do
        # shellcheck disable=SC2086 # a check and its arguments
        check $line || ok=1
    done <<< "$checks"
    return $ok
}

# --- The levels -----------------------------------------------------------------------------------

run_unit() {
    local p failed=0 checked=""
    for p in $(plugins_with_targets); do
        if check plugin "$p"; then echo "unit: $p passed"; else echo "unit: $p FAILED"; failed=1; fi
        checked+="$p "
    done
    local step
    for step in buildinfo-aarch64:check_build_info_aarch64 blockfir:dsp/juce_plugins/tests/blockfir/run.sh \
                plugin-versions:dsp/juce_plugins/tests/plugin-versions/run.sh \
                controller:"check controller" tools:"check tools"; do
        echo "=== /test unit: ${step%%:*}"
        if ${step#*:}; then echo "unit: ${step%%:*} passed"; else echo "unit: ${step%%:*} FAILED"; failed=1; fi
        checked+="${step%%:*} "
    done
    if [ $failed = 0 ]; then record unit pass "$checked"; else record unit fail "$checked"; fi
    return $failed
}

run_vpair() {
    echo "=== /test vpair"
    local result=pass
    modules vpair || result=fail
    case "$NOTE" in skipped:*) record vpair skipped "${NOTE#skipped: }"; return 1 ;; esac
    record vpair "$result" "$NOTE"
    [ "$result" = pass ]
}

what="${1:-}"
shift || true
[ "${*: -1}" = "--no-record" ] 2> /dev/null && { record=0; set -- "${@:1:$#-1}"; }
case "$what" in
    unit)  run_unit || rc=1 ;;
    vpair) run_vpair || rc=1 ;;
    all)   run_unit || rc=1; run_vpair || rc=1 ;;
    changed) run_changed "$@" || rc=1 ;;
    plugin|plugins|speakerd|ampd|pair|controller|installers|chain|tools|module)
        check "$what" "$@" || rc=1; exit $rc ;;
    *) sed -n '2,19p' "$0" >&2; exit 2 ;;
esac
case "$what" in unit|vpair|all) [ $record = 0 ] || { echo "=== status"; tools/status.py show; } ;; esac
exit $rc
