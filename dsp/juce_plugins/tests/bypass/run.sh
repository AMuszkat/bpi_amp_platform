#!/bin/bash
# The level-matched bypass of firConv and crossover (dsp/juce_plugins/README.md, "The bypass
# test"): fade_test.cpp checks the shared dry delay and fade (shared/bypass) on their own, then
# test_bypass.py renders through speakerd, with the plugins' complete builds bypassed as a node
# bypass does it on a speaker, and checks each bypassed output against the input times the gain.
#
#     dsp/juce_plugins/tests/bypass/run.sh
#
# Standalone, not part of rung 1. Runs in the development container when Podman is installed
# (tools/container.sh; BPI_CONTAINER=0 runs it natively). It builds speakerd, firConv and
# crossover with tools/build.sh; its output goes to build/tests/bypass/ at the repository root.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../../.." && pwd)"
. "$ROOT/tools/container.sh"
container_or_native "$0" "$@"
[ $# -eq 0 ] || { echo "usage: $0" >&2; exit 2; }

OUT="$ROOT/build/tests/bypass"
rm -rf "$OUT"
mkdir -p "$OUT/logs"

echo "bypass: fade_test"
g++ -std=c++17 -O2 -Wall -Wextra -Wconversion -Wshadow -Wfloat-equal -Werror \
    -I "$ROOT/dsp/juce_plugins/shared/bypass" "$HERE/fade_test.cpp" -o "$OUT/fade_test"
"$OUT/fade_test"

for args in speakerd "firConv complete" "crossover complete"; do
    log="$OUT/logs/${args%% *}.log"
    echo "bypass: tools/build.sh $args, log $log"
    # shellcheck disable=SC2086
    "$ROOT/tools/build.sh" $args > "$log" 2>&1 || { tail -n 30 "$log"; echo "bypass: the build FAILED" >&2; exit 1; }
done

rc=0
(cd "$HERE" && BYPASS_OUT="$OUT" python3 -m unittest -v test_bypass) || rc=1
[ -e "$OUT/levels.txt" ] && cat "$OUT/levels.txt"
exit $rc
