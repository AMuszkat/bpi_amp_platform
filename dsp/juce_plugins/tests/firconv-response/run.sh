#!/bin/bash
# firConv's magnitude response (dsp/juce_plugins/README.md, "The response test"): builds speakerd
# and firConv for x86_64 and aarch64 with tools/build.sh, then test_firconv_response.py renders
# a sweep through both builds with speakerd --render, compares them with the design computed
# from filters.hpp with scipy, and writes response.svg, response.csv and landmarks.txt.
#
#     dsp/juce_plugins/tests/firconv-response/run.sh
#
# Standalone, not part of rung 1. Runs in the development container when Podman is installed
# (tools/container.sh; BPI_CONTAINER=0 runs it natively, with the packages of
# dsp/juce_plugins/tools/Containerfile, python3-scipy included). Its output goes to
# build/tests/firconv-response/ at the repository root, the builds where tools/build.sh puts them.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../../.." && pwd)"
. "$ROOT/tools/container.sh"
container_or_native "$0" "$@"
[ $# -eq 0 ] || { echo "usage: $0" >&2; exit 2; }

OUT="$ROOT/build/tests/firconv-response"
rm -rf "$OUT"
mkdir -p "$OUT/logs"
for what in speakerd firConv; do
    for arch in x86_64 aarch64; do
        args=("$what")
        [ $arch = aarch64 ] && args+=(aarch64)
        [ "$what" = firConv ] && args+=(complete)
        log="$OUT/logs/$what-$arch.log"
        echo "firconv-response: tools/build.sh ${args[*]}, log $log"
        "$ROOT/tools/build.sh" "${args[@]}" > "$log" 2>&1 || { tail -n 30 "$log"; echo "firconv-response: the build FAILED" >&2; exit 1; }
    done
done

rc=0
(cd "$HERE" && FRESP_OUT="$OUT" python3 -m unittest -v test_firconv_response) || rc=1
for f in response.svg response.csv; do
    [ -e "$OUT/firConv/$f" ] && echo "firconv-response: $OUT/firConv/$f"
done
[ -e "$OUT/firConv/landmarks.txt" ] && cat "$OUT/firConv/landmarks.txt"
exit $rc
