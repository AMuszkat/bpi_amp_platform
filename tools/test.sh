#!/bin/bash
# Runs the test rungs and records their results for the deploy gate. See .claude/skills/test.
#
#     tools/test.sh unit | vpair | all
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
what="${1:-all}"
rc=0

run_unit() {
    local plugins=() p
    for f in dsp/juce_plugins/*/CMakeLists.txt; do
        grep -q '^[[:space:]]*TARGETS' "$f" && plugins+=("$(basename "$(dirname "$f")")")
    done
    if [ ${#plugins[@]} -eq 0 ]; then
        echo "unit: no plugin declares TARGETS yet"; tools/status.py set unit skipped "no plugin with TARGETS"; return 0
    fi
    if [ ! -d "${JUCE_INSTALL_DIR:-/opt/juce}" ]; then
        echo "unit: no JUCE install at ${JUCE_INSTALL_DIR:-/opt/juce} (set JUCE_INSTALL_DIR, see CLAUDE.md)"
        tools/status.py set unit skipped "no JUCE install"; return 1
    fi
    local failed=0
    for p in "${plugins[@]}"; do
        echo "=== /test unit: $p"
        if dsp/juce_apps/plugin-check/run.sh "$p"; then echo "unit: $p passed"; else echo "unit: $p FAILED"; failed=1; fi
    done
    if [ $failed = 0 ]; then tools/status.py set unit pass "${plugins[*]}"; else tools/status.py set unit fail "${plugins[*]}"; fi
    return $failed
}

run_vpair() {
    echo "=== /test vpair"
    if ! command -v pipewire > /dev/null || ! command -v wireplumber > /dev/null; then
        echo "vpair: pipewire and wireplumber are needed (apt install pipewire pipewire-bin wireplumber dbus python3-numpy)"
        tools/status.py set vpair skipped "no pipewire"; return 1
    fi
    if python3 -m unittest discover -s tests/vpair -v; then tools/status.py set vpair pass; else tools/status.py set vpair fail; return 1; fi
}

case "$what" in
    unit)  run_unit || rc=1 ;;
    vpair) run_vpair || rc=1 ;;
    all)   run_unit || rc=1; run_vpair || rc=1 ;;
    *) echo "usage: $0 unit | vpair | all" >&2; exit 2 ;;
esac
echo "=== status"; tools/status.py show
exit $rc
