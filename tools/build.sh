#!/bin/bash
# Builds a plugin or application. See .claude/skills/build.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
what="${1:?usage: $0 <plugin|app> [complete|control|headless|all|aarch64]}"
kind="${2:-all}"

# The aarch64 build runs its own container, from the host.
if [ "$kind" = aarch64 ]; then
    exec dsp/juce_plugins/tools/build-podman.sh "$what"
fi

. tools/container.sh
container_or_native "$0" "$@"
ensure_juce

# An application: a folder of dsp/juce_apps (plugin-check, speakerd, the controller).
if [ -f "dsp/juce_apps/$what/CMakeLists.txt" ]; then
    cmake -S "dsp/juce_apps/$what" -B "build/$what" -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH="$JUCE_INSTALL_DIR"
    cmake --build "build/$what" -j"$(nproc)"
    echo "built build/$what/${what}_artefacts/Release/"
    exit 0
fi

src="dsp/juce_plugins/$what"
[ -f "$src/CMakeLists.txt" ] || { echo "no plugin '$what' in dsp/juce_plugins and no application in dsp/juce_apps" >&2; exit 2; }
cmake -S "$src" -B "build/$what" -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH="$JUCE_INSTALL_DIR"

targets=()
case "$kind" in
    complete) targets=("${what}_LV2") ;;
    control)  targets=("${what}_control_LV2") ;;
    headless) targets=("${what}_headless_LV2") ;;
    all)
        targets=("${what}_LV2")
        grep -q '^[[:space:]]*TARGETS' "$src/CMakeLists.txt" && targets+=("${what}_control_LV2" "${what}_headless_LV2")
        ;;
    *) echo "unknown target kind '$kind'" >&2; exit 2 ;;
esac
cmake --build "build/$what" -j"$(nproc)" --target "${targets[@]}"
for t in "${targets[@]}"; do echo "built build/$what/${t%_LV2}_artefacts/Release/LV2/${t%_LV2}.lv2"; done
