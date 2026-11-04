#!/bin/bash
# The tests. See .claude/skills/test.
#
#   Checks, minutes each, run while working:
#     tools/test.sh plugin <name>      plugin-check of one plugin, without LTO, and its build identity
#     tools/test.sh plugins            every plugin's plugin-check, the block FIR, plugin-versions
#     tools/test.sh speakerd           speakerd alone on the virtual pair: sound, layers, restarts
#     tools/test.sh ampd               ampd against a stand-in speakerd: the protocol, no audio
#     tools/test.sh pair               speakerd and ampd together: knobs, edits, meters, failover
#     tools/test.sh controller         the controller's own tests, and end to end on the pair
#     tools/test.sh installers         the installers and the board script, on a virtual speaker
#     tools/test.sh chain              the board's chain: its cost (quickly) and the builds' identity
#     tools/test.sh tools              the test tools: status.py, impact.py, the lab lock, this script's
#                                      arguments, and the self-tests of tests/vpair's tools (pool.py's)
#     tools/test.sh module <name>...   named modules of tests/vpair
#     tools/test.sh changed [REF]      the checks that cover the files changed (tools/impact.py)
#   Several in one command (tools/test.sh plugin loudness speakerd installers; module last, as it
#   takes every name after it) run what runs outside the virtual pair first, then all their
#   modules of tests/vpair in one run; changed does the same with the checks it finds.
#
#   Levels, end to end, run when asked and before a push (recorded by tools/status.py):
#     tools/test.sh unit | vpair | all [--no-record]
#
#   --jobs N, anywhere (or BPI_TEST_JOBS=N): the modules of tests/vpair run N at a time, each on
#   its own virtual pair (tests/vpair/pool.py); 1 runs them one after another, in one process.
#   N is 1 to 64, the pool's limit (it warns past 80 % of the container's pids.max). Default: 1
#   where CI is set, otherwise half the processors, at most 16 (a busy worker takes 65 to 75 of
#   the container's 2048 tasks). Where CI is set, the checks are also CI's: `plugin <name>` with
#   LTO (BPI_TEST_LTO=1 too), chain_cost's full window (the checks otherwise set
#   VPAIR_CHAIN_COST_QUICK=1 unless it is set; the levels never do), and no ccache (ccache_env in
#   tools/container.sh; the cache is otherwise shared by every worktree).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
. tools/container.sh
container_or_native "$0" "$@"
ccache_env
record=1
rc=0
MAX_JOBS=64          # tests/vpair/pool.py's limit (its --jobs: 1 to 64)
MAX_DEFAULT_JOBS=16  # the default's limit: 16 busy workers take about 1200 of the container's 2048 tasks (pids.max)

usage() { awk 'NR > 1 && /^set -uo pipefail/ { exit } NR > 1' "$0" >&2; exit 2; }

# The options, wherever they are; the other words keep their order.
JOBS="${BPI_TEST_JOBS:-}"
jobs_from="BPI_TEST_JOBS"
words=()
while [ $# -gt 0 ]; do
    case "$1" in
        --no-record) record=0 ;;
        --jobs)      [ $# -ge 2 ] || { echo "--jobs takes a number" >&2; exit 2; }
                     JOBS="$2"; jobs_from="--jobs"; shift ;;
        --jobs=*)    JOBS="${1#--jobs=}"; jobs_from="--jobs" ;;
        *)           words+=("$1") ;;
    esac
    shift
done
set -- ${words[@]+"${words[@]}"}
if [ -z "$JOBS" ]; then
    [ "$jobs_from" = BPI_TEST_JOBS ] || { echo "--jobs takes a number" >&2; exit 2; }
    if [ -n "${CI:-}" ]; then
        JOBS=1
    else
        JOBS=$(( $(nproc) / 2 ))
        [ "$JOBS" -ge 1 ] || JOBS=1
        [ "$JOBS" -le $MAX_DEFAULT_JOBS ] || JOBS=$MAX_DEFAULT_JOBS
    fi
fi
[[ "$JOBS" =~ ^[1-9][0-9]?$ ]] && [ "$JOBS" -le $MAX_JOBS ] \
    || { echo "$jobs_from takes a whole number from 1 to $MAX_JOBS (tests/vpair/pool.py's limit), not '$JOBS'" >&2; exit 2; }

# The note's temporary file (modules) goes however the script ends, by a signal too, which then
# still ends it (bash runs the EXIT trap, then dies of the signal).
NOTE_FILE=""
trap '[ -z "$NOTE_FILE" ] || rm -f "$NOTE_FILE"' EXIT

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

# Runs the named modules of tests/vpair (none: all of them): with JOBS 1 one after another, in
# one process (VPAIR_RUNNER, what CI runs); otherwise JOBS at a time, each on its own virtual
# pair, after one build of what they all need (tests/vpair/pool.py, which writes the same note).
# Prints the note; the note's text in $NOTE.
modules() {
    local what="$1" rc
    shift
    have_pipewire "$what" || { NOTE="skipped: no pipewire"; return 1; }
    NOTE_FILE="$(mktemp)"
    if [ "$JOBS" -gt 1 ]; then
        local count="every module"
        [ $# = 0 ] || count="$# module(s)"
        echo "$what: $count, $JOBS at a time (tests/vpair/pool.py; --jobs 1: one after another)"
        python3 tests/vpair/pool.py --jobs "$JOBS" --note "$NOTE_FILE" "$@"
    else
        python3 -c "$VPAIR_RUNNER" "$NOTE_FILE" "$@"
    fi
    rc=$?
    NOTE="$(cat "$NOTE_FILE")"
    [ -n "$NOTE" ] || NOTE="the test run wrote no note"
    rm -f "$NOTE_FILE"
    NOTE_FILE=""
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
# build in the same HOME may have written those since. $2: lto, as the plugins ship (the levels,
# `plugins`, CI), in build-check; or nolto, in build-check-nolto, for the focused check, where a
# one-line edit then relinks in seconds instead of most of a minute (six LTO links). An explicit
# BUILD_DIR is used for either.
check_plugin() {
    local name="$1" lto=1 dir="${BUILD_DIR:-dsp/juce_apps/plugin-check/build-check}"
    [ "$2" = lto ] || { lto=0; dir="${BUILD_DIR:-dsp/juce_apps/plugin-check/build-check-nolto}"; }
    ensure_juce || { echo "plugin $name: no JUCE install (see CLAUDE.md)"; return 1; }
    PLUGIN_CHECK_LTO=$lto BUILD_DIR="$dir" dsp/juce_apps/plugin-check/run.sh "$name" || return 1
    local bundles=("$dir/$name-builds/plugin/"*_artefacts/Release/LV2/*.lv2)
    dsp/juce_plugins/tests/buildinfo/check.sh --arch x86_64 "$name" "${bundles[@]}"
}

# The focused `plugin <name>`: without LTO, unless CI is set or BPI_TEST_LTO=1.
check_plugin_focused() {
    local why=""
    if [ -n "${CI:-}" ]; then why="CI is set"; elif [ "${BPI_TEST_LTO:-}" = 1 ]; then why="BPI_TEST_LTO=1"; fi
    if [ -n "$why" ]; then
        echo "plugin $1: with LTO ($why), in ${BUILD_DIR:-dsp/juce_apps/plugin-check/build-check}"
        check_plugin "$1" lto
    else
        echo "plugin $1: without LTO, in ${BUILD_DIR:-dsp/juce_apps/plugin-check/build-check-nolto}" \
             "(BPI_TEST_LTO=1: with it, as \`plugins\`, the levels and CI build)"
        check_plugin "$1" nolto
    fi
}

check_plugins() {
    local p ok=0
    for p in $(plugins_with_targets); do check_plugin "$p" lto || ok=1; done
    dsp/juce_plugins/tests/blockfir/run.sh || ok=1
    dsp/juce_plugins/tests/plugin-versions/run.sh || ok=1
    return $ok
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

# The test tools' own tests (tools/tests/test_*.py), and the self-tests of tests/vpair's tools
# (tests/vpair/*_selftest.py, pool.py's: not test_*, so that the vpair level never runs them).
check_tools() {
    local t ok=0
    for t in tools/tests/test_*.py tests/vpair/*_selftest.py; do
        [ -e "$t" ] || continue
        echo "tools: $t"
        python3 "$t" || { echo "tools: $t FAILED"; ok=1; }
    done
    return $ok
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

LEVEL=0          # 1 while a level runs
LAST_HEADER=""
header() { [ "$*" = "$LAST_HEADER" ] || echo "=== /test $*"; LAST_HEADER="$*"; }

# Appends the checks named to CHECK_LIST, one element per check with its arguments ("plugin
# loudness", "speakerd", "module test_a b"). `module` takes every word after it.
add_checks() {
    while [ $# -gt 0 ]; do
        case "$1" in
            plugin) [ $# -ge 2 ] || { echo "plugin: which plugin? (tools/test.sh plugin <name>)" >&2; return 2; }
                    CHECK_LIST+=("plugin $2"); shift 2 ;;
            plugins | speakerd | ampd | pair | controller | installers | chain | tools)
                    CHECK_LIST+=("$1"); shift ;;
            module) [ $# -ge 2 ] || { echo "module: which module? (tools/test.sh module <name>...)" >&2; return 2; }
                    shift; CHECK_LIST+=("module $*"); return 0 ;;
            *)      echo "no check named $1" >&2; return 2 ;;
        esac
    done
}

# chain_cost's quick window (VPAIR_CHAIN_COST_QUICK=1, tests/vpair/test_chain_cost.py) for a
# check that runs it, unless CI is set or the variable is; never in a level.
quick_chain_cost() {
    [ $LEVEL = 0 ] && [ -z "${CI:-}" ] && [ -z "${VPAIR_CHAIN_COST_QUICK+set}" ] || return 0
    case " $* " in *" test_chain_cost "*) ;; *) return 0 ;; esac
    export VPAIR_CHAIN_COST_QUICK=1
    echo "chain_cost: its quick window (VPAIR_CHAIN_COST_QUICK=1); the levels and CI measure the full one"
}

# Runs CHECK_LIST: what runs outside the virtual pair first (plugin, plugins, tools, the
# controller's build and its own tests), in order; then every module of tests/vpair the checks
# name, each once, in one run (one build, one schedule). The controller's module runs only once
# its build and its own tests have passed.
# shellcheck disable=SC2086 # a check and its arguments; the module lists, one word each
run_checks() {
    local entry what m ok=0 names=() shown=() said=()
    for entry in "${CHECK_LIST[@]}"; do
        set -- $entry
        what="$1"
        shift
        case "$what" in
            plugin)     header plugin "$1"; check_plugin_focused "$1" || ok=1 ;;
            plugins)    header plugins; check_plugins || ok=1 ;;
            tools)      header tools; check_tools || ok=1 ;;
            controller) header controller
                        if tools/build.sh controller && dsp/juce_apps/controller/tests/run.sh; then
                            shown+=(controller); said+=(controller); names+=(test_controller_end_to_end)
                        else
                            echo "controller: its build or its own tests failed: test_controller_end_to_end not run"; ok=1
                        fi ;;
            speakerd)   shown+=(speakerd); said+=(speakerd); names+=($SPEAKERD_MODULES) ;;
            ampd)       shown+=(ampd); said+=(ampd); names+=($AMPD_MODULES) ;;
            pair)       shown+=(pair); said+=(pair); names+=($PAIR_MODULES) ;;
            installers) shown+=(installers); said+=(installers); names+=($INSTALLER_MODULES) ;;
            chain)      shown+=(chain); said+=(chain); names+=($CHAIN_MODULES) ;;
            module)     shown+=("module $*"); said+=(module); for m in "$@"; do names+=("test_${m#test_}"); done ;;
        esac
    done
    [ ${#names[@]} -gt 0 ] || return $ok

    local -A seen=()
    local unique=()
    for m in "${names[@]}"; do [ -n "${seen[$m]:-}" ] || { seen[$m]=1; unique+=("$m"); }; done
    header "${shown[*]}"
    quick_chain_cost "${unique[@]}"
    modules "${said[*]}" "${unique[@]}" || ok=1
    return $ok
}

run_named() { CHECK_LIST=(); add_checks "$@" && run_checks; }

run_changed() {
    local checks line
    checks="$(tools/impact.py "$@")" || return 1
    [ -n "$checks" ] || { echo "changed: nothing to run"; return 0; }
    CHECK_LIST=()
    while read -r line; do
        # shellcheck disable=SC2086 # a check and its arguments
        add_checks $line || return 1
    done <<< "$checks"
    run_checks
}

# --- The levels -----------------------------------------------------------------------------------

run_unit() {
    local p failed=0 checked=""
    for p in $(plugins_with_targets); do
        header plugin "$p"
        if check_plugin "$p" lto; then echo "unit: $p passed"; else echo "unit: $p FAILED"; failed=1; fi
        checked+="$p "
    done
    local step
    for step in buildinfo-aarch64:check_build_info_aarch64 blockfir:dsp/juce_plugins/tests/blockfir/run.sh \
                plugin-versions:dsp/juce_plugins/tests/plugin-versions/run.sh \
                controller:"run_named controller" tools:"run_named tools"; do
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
CHECK_LIST=()
case "$what" in
    unit | vpair | all)
        [ $# = 0 ] || { echo "$what takes no arguments but --no-record and --jobs N" >&2; exit 2; }
        LEVEL=1
        unset VPAIR_CHAIN_COST_QUICK ;;
    changed) ;;
    plugin|plugins|speakerd|ampd|pair|controller|installers|chain|tools|module)
        add_checks "$what" "$@" || exit 2 ;;
    *) usage ;;
esac
# For tools/tests/test_test_sh.py: what the arguments decided, and nothing run.
if [ "${BPI_TEST_DRY_RUN:-}" = 1 ]; then
    echo "jobs $JOBS"; echo "record $record"; echo "run $what${*:+ $*}"
    for entry in ${CHECK_LIST[@]+"${CHECK_LIST[@]}"}; do echo "check $entry"; done
    exit 0
fi
case "$what" in
    unit)  run_unit || rc=1 ;;
    vpair) run_vpair || rc=1 ;;
    all)   run_unit || rc=1; run_vpair || rc=1 ;;
    changed) run_changed "$@" || rc=1 ;;
    *)     run_checks || rc=1
           exit $rc ;;
esac
case "$what" in unit|vpair|all) [ $record = 0 ] || { echo "=== status"; tools/status.py show; } ;; esac
exit $rc
