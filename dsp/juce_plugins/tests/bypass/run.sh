#!/bin/bash
# The level-matched bypass of firConv and crossover (dsp/juce_plugins/README.md, "The bypass
# test"): fade_test.cpp checks the shared dry delay and fade (shared/bypass) on their own, then
# test_bypass.py renders through speakerd, with the plugins' complete builds bypassed as a node
# bypass does it on a speaker, and checks each bypassed output against the input times the gain.
#
#     dsp/juce_plugins/tests/bypass/run.sh
#
# Standalone, not part of level 1. Runs in the development container when Podman is installed
# (tools/container.sh; BPI_CONTAINER=0 runs it natively). It builds speakerd, firConv and
# crossover with tools/build.sh, and the versions before the bypass (firConv 0.0.3, crossover
# 0.0.2) from their sources at commit PREVIOUS, below, into build/tests/bypass-previous/ (kept
# between runs: those sources never change). Its output goes to build/tests/bypass/ at the
# repository root.
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

# The last commit with firConv 0.0.3 and crossover 0.0.2, the versions before the bypass: their
# processed output is the reference for the current one. Move it only when a later version
# changes the processed output on purpose, and say so in the plugin's README.
PREVIOUS=5fd1cc23a0216e600a7268947802521377fbd891

for args in speakerd "firConv complete" "crossover complete"; do
    log="$OUT/logs/${args%% *}.log"
    echo "bypass: tools/build.sh $args, log $log"
    # shellcheck disable=SC2086
    "$ROOT/tools/build.sh" $args > "$log" 2>&1 || { tail -n 30 "$log"; echo "bypass: the build FAILED" >&2; exit 1; }
done

# The previous versions: their plugin sources, shared folders and cmake/ at PREVIOUS (git
# archive, no JUCE: the build uses the JUCE install, and the pin did not move), built like
# tools/build.sh builds a complete target. Their copy step is pinned inside the build tree and
# HOME is a throwaway one, so these bundles never reach a ~/.lv2 where a host would find them
# under the current versions' URIs. GIT_CEILING_DIRECTORIES keeps the build's source identity
# from reading this checkout's git (it says "unknown").
PREV="$ROOT/build/tests/bypass-previous/${PREVIOUS:0:12}"
git -C "$ROOT" cat-file -e "$PREVIOUS^{commit}" 2> /dev/null \
    || { echo "bypass: commit $PREVIOUS (the versions before the bypass) is not in this repository: git fetch" >&2; exit 1; }
if [ ! -f "$PREV/src/.complete" ]; then
    rm -rf "$PREV/src"
    mkdir -p "$PREV/src"
    git -C "$ROOT" archive "$PREVIOUS" dsp/juce_plugins/cmake dsp/juce_plugins/shared \
        dsp/juce_plugins/firConv dsp/juce_plugins/crossover | tar -x -C "$PREV/src"
    touch "$PREV/src/.complete"
fi
ensure_juce
mkdir -p "$PREV/home"
cat > "$PREV/copy-dirs.cmake" <<'CMAKE'
# Written by dsp/juce_plugins/tests/bypass/run.sh: the previous versions copy here, never into a ~/.lv2.
set_directory_properties(PROPERTIES
    JUCE_LV2_COPY_DIR  "${CMAKE_BINARY_DIR}/copied/lv2"
    JUCE_VST3_COPY_DIR "${CMAKE_BINARY_DIR}/copied/vst3")
CMAKE
for plugin in firConv crossover; do
    log="$OUT/logs/$plugin-previous.log"
    echo "bypass: $plugin at ${PREVIOUS:0:12}, log $log"
    {
        env HOME="$PREV/home" GIT_CEILING_DIRECTORIES="$PREV" cmake -G Ninja -DCMAKE_BUILD_TYPE=Release \
            -DCMAKE_PREFIX_PATH="$JUCE_INSTALL_DIR" -DCMAKE_PROJECT_INCLUDE="$PREV/copy-dirs.cmake" \
            -S "$PREV/src/dsp/juce_plugins/$plugin" -B "$PREV/$plugin" \
        && env HOME="$PREV/home" GIT_CEILING_DIRECTORIES="$PREV" cmake --build "$PREV/$plugin" -j"$(nproc)" --target "${plugin}_LV2"
    } > "$log" 2>&1 || { tail -n 30 "$log"; echo "bypass: the build of $plugin at ${PREVIOUS:0:12} FAILED" >&2; exit 1; }
done

rc=0
(cd "$HERE" && BYPASS_OUT="$OUT" BYPASS_PREVIOUS="$PREV" python3 -m unittest -v test_bypass) || rc=1
[ -e "$OUT/levels.txt" ] && cat "$OUT/levels.txt"
exit $rc
