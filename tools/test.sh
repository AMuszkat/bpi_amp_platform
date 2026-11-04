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
#     tools/test.sh changed [REF]      the checks that cover the files changed (tools/impact.py),
#                                      and, before and after them, what its map says to run by hand
#   Several in one command (tools/test.sh plugin loudness speakerd installers; module last, as it
#   takes every name after it) run all their modules of tests/vpair in one run of the pool;
#   changed does the same with the checks it finds.
#
#   Levels, end to end, run when asked and before a push (recorded by tools/status.py):
#     tools/test.sh unit | vpair | all [--no-record]
#
#   With more than one job, every check and level runs as a schedule (tools/schedule.py), in
#   phases: 1, the builds (every LTO build: plugin-check's, the block FIR's, the aarch64 build
#   identity's; `plugin <name>`, the pool's prebuild: at most two at once, at nice 19) and the
#   light steps beside them (plugin-versions, the controller's build); 2, once all of those have
#   ended, the tests (the pool's main units, the controller's own tests, the `tools` check); 3, in
#   the pool, its quiet units, once every other test has ended (the gate), then those alone. Each
#   step's output is printed whole when it ends and kept in build/test-steps/<step>.log; a table at
#   the end. One schedule at a time per checkout (build/test-run.lock). With one job (CI), the
#   steps run one after another as before.
#
#   --jobs N, anywhere (or BPI_TEST_JOBS=N): the pool's main phase runs N units at a time, each on
#   its own virtual pair (tests/vpair/pool.py), its quiet phase min(N, 12); 1 runs the modules one
#   after another, in one process (the serial runner, which holds the pool's lock of the checkout,
#   build/vpair-pool.lock, so that it and a pool never run at once). N is 1 to 64, the pool's
#   limit. Default: 1 where CI is set, otherwise two thirds of the processors, at most 16: measured
#   on 24 threads, the pool gains nothing past 16 workers, and 20 or 24 made timing tests fail. The
#   container's tasks are not the bound: 16 workers peaked at 1008 to 1021 of its 2048 (24 at
#   1393); the schedule prints a run's own peak at its end. Where CI is set, the checks are also
#   CI's: `plugin <name>` with LTO (BPI_TEST_LTO=1 too), chain_cost's full window (the checks
#   otherwise set VPAIR_CHAIN_COST_QUICK=1 unless it is set; the levels never do), and no ccache
#   (ccache_env in tools/container.sh; the cache is otherwise shared by every worktree).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
. tools/container.sh
container_or_native "$0" "$@"
ccache_env
record=1
rc=0
MAX_JOBS=64          # tests/vpair/pool.py's limit (its --jobs: 1 to 64)
MAX_DEFAULT_JOBS=16  # the default's limit: where the pool stops gaining (measured; 20 and 24 failed timing tests)

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
        JOBS=$(( $(nproc) * 2 / 3 ))
        [ "$JOBS" -ge 1 ] || JOBS=1
        [ "$JOBS" -le $MAX_DEFAULT_JOBS ] || JOBS=$MAX_DEFAULT_JOBS
    fi
fi
[[ "$JOBS" =~ ^[1-9][0-9]?$ ]] && [ "$JOBS" -le $MAX_JOBS ] \
    || { echo "$jobs_from takes a whole number from 1 to $MAX_JOBS (tests/vpair/pool.py's limit), not '$JOBS'" >&2; exit 2; }

# The note's temporary file (modules) goes however the script ends, by a signal too, which then
# still ends it (bash runs the EXIT trap, then dies of the signal).
NOTE_FILE=""
PLAN_FILE=""
RUN_DIR=""
TOOLS_OUT=""
trap '[ -z "$NOTE_FILE" ] || rm -f "$NOTE_FILE"; [ -z "$PLAN_FILE" ] || rm -f "$PLAN_FILE"
      [ -z "$RUN_DIR" ] || rm -rf "$RUN_DIR"; [ -z "$TOOLS_OUT" ] || rm -rf "$TOOLS_OUT"' EXIT

# BPI_TEST_STUB=DIR, for tools/tests/test_test_sh.py: a run as it is, its steps (run_step, the
# serial runner) replaced by DIR/step STEP ARG..., tools/status.py by DIR/status, pipewire not asked
# for (STUB_PIPEWIRE=no: as if missing), the schedule's folder and lock DIR's; and what would end
# with 0 ends with 3 (as a dry run,
# for the same reason: tools/container.sh passes every BPI_* variable on), saying so on stderr.
STUB="${BPI_TEST_STUB:-}"
STATUS_PY=tools/status.py
[ -z "$STUB" ] || STATUS_PY="$STUB/status"
leave() {
    if [ -n "$STUB" ] && [ "$1" = 0 ]; then echo "BPI_TEST_STUB: stand-in steps, so no pass (3)" >&2; exit 3; fi
    exit "$1"
}

record() { [ $record = 0 ] || "$STATUS_PY" set "$@"; }

# python3 -c "$VPAIR_RUNNER" NOTE [MODULE ...]: the modules of tests/vpair named, or all of them.
# Run under pool.py --hold-checkout (the checkout's lock, which a pool holds too), it keeps the
# lock's descriptor to itself: nothing it starts inherits it, so nothing left running holds it.
VPAIR_RUNNER='
import os, sys, unittest
lock_fd = os.environ.pop("VPAIR_CHECKOUT_LOCK_FD", None)
if lock_fd:
    os.set_inheritable(int(lock_fd), False)
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

# (With BPI_TEST_STUB, there unless STUB_PIPEWIRE=no.)
have_pipewire() {
    if [ -n "$STUB" ]; then
        [ "${STUB_PIPEWIRE:-yes}" = no ] || return 0
    elif command -v pipewire > /dev/null && command -v wireplumber > /dev/null; then
        return 0
    fi
    echo "$1: pipewire and wireplumber are needed: install Podman (tools/container.sh), or see CLAUDE.md"
    return 1
}

# The serial runner (JOBS 1, what CI runs): the named modules of tests/vpair (none: all of them)
# one after another, in one process (VPAIR_RUNNER), holding the checkout's lock as a pool does
# (tests/vpair/pool.py --hold-checkout: it waits for a pool of this checkout, or another serial
# runner, naming it). Prints the note; the note's text in $NOTE. With more than one job the pool
# runs them, as a step of the schedule (run_step pool).
modules() {
    local what="$1" rc
    shift
    have_pipewire "$what" || { NOTE="skipped: no pipewire"; return 1; }
    NOTE_FILE="$(mktemp)"
    if [ -n "$STUB" ]; then
        "$STUB/step" serial "$NOTE_FILE" "$@"
    else
        python3 tests/vpair/pool.py --hold-checkout --what "${*:-every module}" -- \
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
# (dsp/juce_plugins/tests/buildinfo), read in the build tree itself, not in the copies JUCE makes
# of them (in $dir/$name-builds/copied/{lv2,vst3}; no build writes ~/.lv2). $2: lto, as the
# plugins ship (the levels, `plugins`, CI), in build-check; or nolto, in build-check-nolto, for the
# focused check, where a one-line edit then relinks in seconds instead of most of a minute (six LTO
# links). An explicit BUILD_DIR is used for either.
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
    for p in $(plugins_with_targets); do run_step plugin-lto "$p" || ok=1; done
    run_step blockfir || ok=1
    run_step plugin-versions || ok=1
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

# The unit level's controller: its build and its own tests. test_controller_end_to_end, which the
# `controller` check runs after them, the vpair level runs (once, not here too).
check_controller_own() {
    run_step controller-build && run_step controller-tests
}

# The test tools' own tests (tools/tests/test_*.py), and the self-tests of tests/vpair's tools
# (tests/vpair/*_selftest.py, pool.py's: not test_*, so that the vpair level never runs them).
# With more than one job all at once, each one's output printed whole, in this order, when all
# have ended (none depends on how fast the others run: they wait on files, not on time); with
# one (CI), one after another.
check_tools() {
    local t ok=0 files=() pids=() i
    for t in tools/tests/test_*.py tests/vpair/*_selftest.py; do [ -e "$t" ] && files+=("$t"); done
    if [ "$JOBS" = 1 ]; then
        for t in "${files[@]}"; do
            echo "tools: $t"
            python3 "$t" || { echo "tools: $t FAILED"; ok=1; }
        done
        return $ok
    fi
    TOOLS_OUT="$(mktemp -d)"
    for i in "${!files[@]}"; do
        python3 "${files[$i]}" > "$TOOLS_OUT/$i.out" 2>&1 < /dev/null &
        pids+=($!)
    done
    for i in "${!files[@]}"; do
        if wait "${pids[$i]}"; then t=""; else t=FAILED; fi
        echo "tools: ${files[$i]}"
        cat "$TOOLS_OUT/$i.out"
        [ -z "$t" ] || { echo "tools: ${files[$i]} FAILED"; ok=1; }
    done
    rm -rf "$TOOLS_OUT"
    TOOLS_OUT=""
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
            plugin)     header plugin "$1"; run_step plugin "$1" || ok=1 ;;
            plugins)    header plugins; check_plugins || ok=1 ;;
            tools)      header tools; run_step tools || ok=1 ;;
            controller) header controller
                        if check_controller_own; then
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

# The checks tools/impact.py finds. What it says on stderr (the files no rule names, a rule's
# by_hand advice) comes first; the advice again after the checks, whose output would bury it.
run_changed() {
    local checks line said status by_hand
    NOTE_FILE="$(mktemp)"   # impact.py's stderr, for a moment (the EXIT trap removes it)
    checks="$(tools/impact.py "$@" 2> "$NOTE_FILE")"
    status=$?
    said="$(cat "$NOTE_FILE")"
    rm -f "$NOTE_FILE"
    NOTE_FILE=""
    [ -z "$said" ] || echo "$said" >&2
    [ $status = 0 ] || return 1
    [ -n "$checks" ] || { echo "changed: nothing to run"; return 0; }
    CHECK_LIST=()
    while read -r line; do
        # shellcheck disable=SC2086 # a check and its arguments
        add_checks $line || return 1
    done <<< "$checks"
    run_checks
    status=$?
    by_hand="$(grep '^by hand, for ' <<< "$said" | sed 's/^/changed: /')"
    [ -z "$by_hand" ] || echo "$by_hand" >&2
    return $status
}

# --- The levels -----------------------------------------------------------------------------------

run_unit() {
    local p failed=0 checked=""
    for p in $(plugins_with_targets); do
        header plugin "$p"
        if run_step plugin-lto "$p"; then echo "unit: $p passed"; else echo "unit: $p FAILED"; failed=1; fi
        checked+="$p "
    done
    local step
    for step in buildinfo-aarch64:"run_step buildinfo-aarch64" blockfir:"run_step blockfir" \
                plugin-versions:"run_step plugin-versions" controller:check_controller_own tools:"run_named tools"; do
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

# --- The schedule (JOBS > 1) ----------------------------------------------------------------------
#
# The steps of a check or a level, for tools/schedule.py (its docstring has the plan's format):
# each runs `tools/test.sh --jobs N __step STEP ARG...` (run_step, below), in phases, so that no
# test runs beside a build (docs/autonomous-development.md, section 4, "How the checks run").
# What a run's steps tell each other and this script is the run's own, in RUN_DIR (a mktemp -d,
# TEST_RUN_DIR in the steps' environment, removed at the end): the results, the gate, the pool's
# note and the prebuild's report; another run's schedule never empties it, nor leaves its results
# for this one to read.
STEPS_DIR=build/test-steps      # the schedule's own: the steps' logs (the last run's)
RUN_LOCK=build/test-run.lock    # one schedule at a time per checkout
[ -z "$STUB" ] || { STEPS_DIR="$STUB/steps"; RUN_LOCK="$STUB/test-run.lock"; }
PLAN=()
PLAN_FIRST=()                   # the pool's prebuild: first of the heavy steps
declare -A PLANNED=()
declare -A RESULT=()

# plan NAME KIND [OPTION...] -- STEP [ARG...]: a step (once, by its name).
plan() {
    local name="$1" line="" w
    [ -z "${PLANNED[$name]:-}" ] || { while [ "$1" != -- ]; do shift; done; return 0; }
    PLANNED[$name]=1
    while [ "$1" != -- ]; do line+="$(printf '%q ' "$1")"; shift; done
    shift
    for w in -- "$ROOT/tools/test.sh" --jobs "$JOBS" __step "$@"; do line+="$(printf '%q ' "$w")"; done
    PLAN+=("${line% }")
}

# The pool's prebuild (phase 1, both heavy slots, first) and its run (phase 2), of modules $2...
# shown as $1; --after-gate for those AFTER_GATE names (the controller check's end to end). The
# pool does not need the prebuild to pass: it runs the units whose builds the prebuild made, from
# its report (pool.py --prebuilt), and fails those whose required build failed under their names,
# so that a failed build stops only what needs it (a failed controller build, the end to end).
AFTER_GATE=()
plan_pool() {
    local shown="$1" said="$2" a held=()
    shift 2
    for a in ${AFTER_GATE[@]+"${AFTER_GATE[@]}"}; do held+=(--after-gate "$a"); done
    plan prebuild heavy slots=2 "title=the pool's prebuild" -- prebuild "$@"
    PLAN_FIRST+=("${PLAN[-1]}")
    unset 'PLAN[-1]'
    plan pool test gated "title=$shown" -- pool "$said" ${held[@]+"${held[@]}"} -- "$@"
}

# The plugins' LTO builds, heavy: the block FIR first (the longest of them when nothing changed,
# a few seconds; as long as the LTO plugins' when everything did), then plugin-check of each
# plugin. Two LTO trees at most at once, and so the aarch64 build identity, an LTO cross build,
# is heavy too (plan_unit, after these: the shortest).
plan_plugin_builds() {   # $1: the title's prefix ("unit:", "plugins:")
    local p
    plan blockfir heavy "title=$1 blockfir" -- blockfir
    for p in $(plugins_with_targets); do
        plan "plugin-lto-$p" heavy "title=$1 $p" -- plugin-lto "$p"
    done
}

# The checks of CHECK_LIST as steps. MODULE_SHOWN, MODULE_SAID: the modules' title and name.
# shellcheck disable=SC2086 # a check and its arguments; the module lists, one word each
plan_checks() {
    local entry what m names=() shown=() said=()
    CONTROLLER_CHECKED=0
    for entry in "${CHECK_LIST[@]}"; do
        set -- $entry
        what="$1"
        shift
        case "$what" in
            plugin)     plan "plugin-$1" heavy "title=plugin $1" -- plugin "$1" ;;
            plugins)    plan_plugin_builds plugins:
                        plan plugin-versions light title=plugin-versions -- plugin-versions ;;
            tools)      plan tools test title=tools -- tools ;;
            controller) plan controller-build light "title=controller: its build" -- controller-build
                        plan controller-tests test "title=controller: its own tests" needs=controller-build \
                             guards=test_controller_end_to_end -- controller-tests
                        CONTROLLER_CHECKED=1
                        AFTER_GATE+=(test_controller_end_to_end)
                        shown+=(controller); said+=(controller); names+=(test_controller_end_to_end) ;;
            speakerd)   shown+=(speakerd); said+=(speakerd); names+=($SPEAKERD_MODULES) ;;
            ampd)       shown+=(ampd); said+=(ampd); names+=($AMPD_MODULES) ;;
            pair)       shown+=(pair); said+=(pair); names+=($PAIR_MODULES) ;;
            installers) shown+=(installers); said+=(installers); names+=($INSTALLER_MODULES) ;;
            chain)      shown+=(chain); said+=(chain); names+=($CHAIN_MODULES) ;;
            module)     shown+=("module $*"); said+=(module); for m in "$@"; do names+=("test_${m#test_}"); done ;;
        esac
    done
    MODULE_SAID="${said[*]}"
    MODULES_PLANNED=0
    [ ${#names[@]} -gt 0 ] || return 0
    local -A seen=()
    UNIQUE_MODULES=()
    for m in "${names[@]}"; do [ -n "${seen[$m]:-}" ] || { seen[$m]=1; UNIQUE_MODULES+=("$m"); }; done
    [ "${BPI_TEST_DRY_RUN:-}" = 1 ] || have_pipewire "$MODULE_SAID" || return 1
    plan_pool "${shown[*]}" "$MODULE_SAID" "${UNIQUE_MODULES[@]}"
    MODULES_PLANNED=1
}

plan_unit() {
    plan_plugin_builds unit:
    plan buildinfo-aarch64 heavy "title=unit: buildinfo-aarch64" -- buildinfo-aarch64
    plan plugin-versions light "title=unit: plugin-versions" -- plugin-versions
    plan controller-build light "title=unit: controller (its build)" -- controller-build
    plan controller-tests test "title=unit: controller (its own tests)" needs=controller-build -- controller-tests
    plan tools test "title=unit: tools" -- tools
}

plan_vpair() {
    MODULES_PLANNED=0
    [ "${BPI_TEST_DRY_RUN:-}" = 1 ] || have_pipewire vpair || return 1
    plan_pool vpair vpair
    MODULES_PLANNED=1
}

# Runs PLAN (the prebuild first); RESULT[step] = passed, failed, skipped or interrupted, from this
# run's own results (none, when the schedule ended without writing them: no step passed).
run_schedule() {
    local status line name result
    PLAN_FILE="$(mktemp)"
    RUN_DIR="$(mktemp -d)"
    printf '%s\n' ${PLAN_FIRST[@]+"${PLAN_FIRST[@]}"} ${PLAN[@]+"${PLAN[@]}"} > "$PLAN_FILE"
    TEST_RUN_DIR="$RUN_DIR" python3 tools/schedule.py --plan "$PLAN_FILE" --logs "$STEPS_DIR" \
        --results "$RUN_DIR/results" --gate "$RUN_DIR/gate" --lock "$RUN_LOCK" --what "tools/test.sh $ARGS_TEXT"
    status=$?
    rm -f "$PLAN_FILE"
    PLAN_FILE=""
    if [ -f "$RUN_DIR/results" ]; then
        while read -r name result; do RESULT[$name]="$result"; done < "$RUN_DIR/results"
    fi
    return $status
}

passed() { local s; for s in "$@"; do [ "${RESULT[$s]:-}" = passed ] || return 1; done; }

# The modules' note, after the schedule: the pool's (or its prebuild's, when the pool never ran).
pool_note() {
    NOTE=""
    [ -z "$RUN_DIR" ] || [ ! -f "$RUN_DIR/pool.note" ] || NOTE="$(cat "$RUN_DIR/pool.note")"
    [ -n "$NOTE" ] || NOTE="the test run wrote no note"
}

scheduled_checks() {
    local ok=0
    plan_checks || ok=1
    [ ${#PLAN[@]} -gt 0 ] || [ ${#PLAN_FIRST[@]} -gt 0 ] || return $ok
    [ "$MODULES_PLANNED" = 0 ] || quick_chain_cost "${UNIQUE_MODULES[@]}"
    run_schedule || ok=1
    if [ "$CONTROLLER_CHECKED" = 1 ] && ! passed controller-build controller-tests; then
        echo "controller: its build or its own tests failed: test_controller_end_to_end not run"
    fi
    if [ "$MODULES_PLANNED" = 1 ]; then pool_note; echo "$MODULE_SAID: $NOTE"; fi
    return $ok
}

scheduled_levels() {   # $1: unit, vpair or all
    local ok=0 unit_ok=1 vpair_skipped=0 p checked="" step
    case "$1" in unit|all) plan_unit ;; esac
    case "$1" in vpair|all) plan_vpair || vpair_skipped=1 ;; esac
    if [ ${#PLAN[@]} -gt 0 ] || [ ${#PLAN_FIRST[@]} -gt 0 ]; then run_schedule || ok=1; fi
    if [ "$1" != vpair ]; then
        echo "=== /test unit"
        for p in $(plugins_with_targets); do
            if passed "plugin-lto-$p"; then echo "unit: $p passed"; else echo "unit: $p FAILED"; unit_ok=0; fi
            checked+="$p "
        done
        for step in buildinfo-aarch64:buildinfo-aarch64 blockfir:blockfir plugin-versions:plugin-versions \
                    controller:"controller-build controller-tests" tools:tools; do
            # shellcheck disable=SC2086 # the step's steps, one word each
            if passed ${step#*:}; then echo "unit: ${step%%:*} passed"; else echo "unit: ${step%%:*} FAILED"; unit_ok=0; fi
            checked+="${step%%:*} "
        done
        if [ $unit_ok = 1 ]; then record unit pass "$checked"; else record unit fail "$checked"; ok=1; fi
    fi
    if [ "$1" != unit ]; then
        echo "=== /test vpair"
        if [ $vpair_skipped = 1 ]; then
            record vpair skipped "no pipewire"; ok=1
        else
            pool_note
            echo "vpair: $NOTE"
            if passed pool; then record vpair pass "$NOTE"; else record vpair fail "$NOTE"; ok=1; fi
        fi
    fi
    return $ok
}

# tools/test.sh --jobs N __step STEP [ARG...]: one step of the schedule, as plan() names it.
run_step() {
    local step="$1"
    shift
    [ -z "$STUB" ] || { "$STUB/step" "$step" "$@"; return; }
    case "$step" in
        plugin-lto)        check_plugin "$1" lto ;;
        plugin)            check_plugin_focused "$1" ;;
        buildinfo-aarch64) check_build_info_aarch64 ;;
        blockfir)          dsp/juce_plugins/tests/blockfir/run.sh ;;
        plugin-versions)   dsp/juce_plugins/tests/plugin-versions/run.sh ;;
        tools)             check_tools ;;
        controller-build)  tools/build.sh controller ;;
        controller-tests)  dsp/juce_apps/controller/tests/run.sh ;;
        prebuild)          [ -n "${TEST_RUN_DIR:-}" ] || { echo "__step prebuild: no TEST_RUN_DIR (a schedule's)" >&2; return 2; }
                           python3 tests/vpair/pool.py --jobs "$JOBS" --prebuild-only --built "$TEST_RUN_DIR/prebuilt.json" \
                               --note "$TEST_RUN_DIR/pool.note" "$@" ;;
        pool)              # pool SAID [--after-gate NAME]... -- MODULE...
                           local said="$1" held=() count owner=()
                           shift
                           while [ "$1" != -- ]; do held+=("$1"); shift; done
                           shift
                           [ -n "${TEST_RUN_DIR:-}" ] || { echo "__step pool: no TEST_RUN_DIR (a schedule's)" >&2; return 2; }
                           [ -z "${TEST_SCHEDULE_PID:-}" ] || owner=(--gate-owner "$TEST_SCHEDULE_PID")
                           count="every module"
                           [ $# = 0 ] || count="$# module(s)"
                           echo "$said: $count, $JOBS at a time, the quiet ones min($JOBS, 12) after the other tests" \
                                "(tests/vpair/pool.py; --jobs 1: one after another)"
                           python3 tests/vpair/pool.py --jobs "$JOBS" --prebuilt "$TEST_RUN_DIR/prebuilt.json" \
                               --gate "$TEST_RUN_DIR/gate" ${owner[@]+"${owner[@]}"} --note "$TEST_RUN_DIR/pool.note" \
                               ${held[@]+"${held[@]}"} "$@" ;;
        *)                 echo "__step: no step named $step" >&2; return 2 ;;
    esac
}

ARGS_TEXT="$*"
what="${1:-}"
shift || true
if [ "$what" = __step ]; then
    [ $# -ge 1 ] || { echo "__step: which step?" >&2; exit 2; }
    run_step "$@"
    exit $?             # a step's own status: the schedule reads it (never a run's end)
fi
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
# For tools/tests/test_test_sh.py: what the arguments decided, and nothing run. It exits with a
# status of its own, 3 (not 0: tools/container.sh passes every BPI_* variable into the container,
# so a stray export must fail every check and level, CI's too, rather than pass them unrun; not 2,
# the arguments refused), and says so on stderr.
# With more than one job, the plan too, a line "plan NAME KIND [OPTION...] -- STEP [ARG...]" a step
# in the schedule's order (not for changed, whose checks tools/impact.py finds when it runs).
if [ "${BPI_TEST_DRY_RUN:-}" = 1 ]; then
    echo "jobs $JOBS"; echo "record $record"; echo "run $what${*:+ $*}"
    for entry in ${CHECK_LIST[@]+"${CHECK_LIST[@]}"}; do echo "check $entry"; done
    if [ "$JOBS" -gt 1 ] && [ "$what" != changed ]; then
        case "$what" in
            unit | vpair | all) case "$what" in unit|all) plan_unit ;; esac
                                case "$what" in vpair|all) plan_vpair ;; esac ;;
            *)                  plan_checks ;;
        esac
        for line in ${PLAN_FIRST[@]+"${PLAN_FIRST[@]}"} ${PLAN[@]+"${PLAN[@]}"}; do
            line="${line%% -- "$ROOT"/tools/test.sh --jobs "$JOBS" __step *} -- ${line##* __step }"
            echo "plan $line"
        done
    fi
    echo "BPI_TEST_DRY_RUN=1: nothing ran" >&2
    exit 3
fi
if [ "$JOBS" -gt 1 ]; then
    run_checks() { scheduled_checks; }
    case "$what" in
        unit | vpair | all) scheduled_levels "$what" || rc=1
                            [ $record = 0 ] || { echo "=== status"; "$STATUS_PY" show; }
                            leave $rc ;;
    esac
fi
case "$what" in
    unit)  run_unit || rc=1 ;;
    vpair) run_vpair || rc=1 ;;
    all)   run_unit || rc=1; run_vpair || rc=1 ;;
    changed) run_changed "$@" || rc=1 ;;
    *)     run_checks || rc=1
           leave $rc ;;
esac
case "$what" in unit|vpair|all) [ $record = 0 ] || { echo "=== status"; "$STATUS_PY" show; } ;; esac
leave $rc
