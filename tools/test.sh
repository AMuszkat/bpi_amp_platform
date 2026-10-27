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
        if dsp/juce_apps/plugin-check/run.sh "$p"; then echo "unit: $p passed"; else echo "unit: $p FAILED"; failed=1; fi
    done
    if [ $failed = 0 ]; then record unit pass "${plugins[*]}"; else record unit fail "${plugins[*]}"; fi
    return $failed
}

run_vpair() {
    echo "=== /test vpair"
    if ! command -v pipewire > /dev/null || ! command -v wireplumber > /dev/null; then
        echo "vpair: pipewire and wireplumber are needed: install Podman (tools/container.sh), or see CLAUDE.md"
        record vpair skipped "no pipewire"; return 1
    fi
    if python3 -m unittest discover -s tests/vpair -v; then record vpair pass; else record vpair fail; return 1; fi
}

case "$what" in
    unit)  run_unit || rc=1 ;;
    vpair) run_vpair || rc=1 ;;
    all)   run_unit || rc=1; run_vpair || rc=1 ;;
    *) echo "usage: $0 unit | vpair | all [--no-record]" >&2; exit 2 ;;
esac
[ $record = 0 ] || { echo "=== status"; tools/status.py show; }
exit $rc
