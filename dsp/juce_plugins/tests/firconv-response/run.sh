#!/bin/bash
# firConv's response through speakerd, two builds compared (dsp/juce_plugins/README.md,
# "The response test"): builds speakerd and the plugin at the current tree and the plugin at a
# base revision, for x86_64 and aarch64 (builds.py), renders the same sweep and noise through
# each with speakerd --render, the aarch64 builds under qemu-aarch64-static, and runs the
# assertions of test_firconv_response.py, which also write one SVG figure and one CSV per
# comparison.
#
#     dsp/juce_plugins/tests/firconv-response/run.sh [--base REF]
#
# --base REF compares against REF instead of the plugin's previous version (the parent of the
# commit that raised its VERSION). Standalone: not part of rung 1. Runs in the development
# container when Podman is installed (tools/container.sh; BPI_CONTAINER=0 runs it natively,
# with the packages of dsp/juce_plugins/tools/Containerfile, python3-scipy included). Builds,
# renders and figures go into build/tests/firconv-response/ at the repository root, the current
# tree's builds where tools/build.sh puts them.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../../.." && pwd)"
. "$ROOT/tools/container.sh"
container_or_native "$0" "$@"

usage="usage: $0 [--base REF]"
base=()
while [ $# -gt 0 ]; do
    case "$1" in
        --base) [ $# -ge 2 ] || { echo "$usage" >&2; exit 2; }; base=(--base "$2"); shift 2 ;;
        --base=*) base=(--base "${1#--base=}"); shift ;;
        -h | --help) echo "$usage"; exit 0 ;;
        *) echo "unexpected argument '$1'" >&2; echo "$usage" >&2; exit 2 ;;
    esac
done

missing=()
for tool in aarch64-linux-gnu-g++ qemu-aarch64-static python3; do
    command -v $tool > /dev/null || missing+=("$tool")
done
python3 -c 'import numpy' 2> /dev/null || missing+=("numpy (python3-numpy)")
if [ ${#missing[@]} -gt 0 ]; then
    echo "firconv-response: FAILED: no ${missing[*]}: the packages of dsp/juce_plugins/tools/Containerfile" >&2
    exit 1
fi

ensure_juce
OUT="$ROOT/build/tests/firconv-response"
mkdir -p "$OUT"
start=$SECONDS
cd "$HERE"
read -r -a plugins <<< "$(python3 -c 'import test_firconv_response as t; print(" ".join(t.PLUGINS))')"
python3 builds.py --out "$OUT" "${base[@]}" "${plugins[@]}"
built=$SECONDS

rc=0
FRESP_OUT="$OUT" python3 -m unittest -v test_firconv_response || rc=1
echo "firconv-response: builds $((built - start)) s, renders and tests $((SECONDS - built)) s"
echo "firconv-response: figures and curves:"
for plugin in "${plugins[@]}"; do
    for f in "$OUT/$plugin"/*.svg "$OUT/$plugin"/*.csv; do
        [ -e "$f" ] && echo "  $f"
    done
done
exit $rc
