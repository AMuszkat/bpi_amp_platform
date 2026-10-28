#!/bin/bash
# Builds a plugin or application, for this machine (x86_64) or for the board (aarch64). See
# .claude/skills/build.
#
#     tools/build.sh <plugin|app> [complete|control|headless|all]            # build/<what>/
#     tools/build.sh <plugin|app> aarch64 [complete|control|headless|all]    # build/aarch64/<what>/
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
usage="usage: $0 <plugin|app> [aarch64] [complete|control|headless|all]"
what="${1:?$usage}"
kind="${2:-all}"
arch=x86_64
max_args=2
if [ "$kind" = aarch64 ]; then
    arch=aarch64
    kind="${3:-all}"
    max_args=3
fi
# Nothing else: "loudness headless aarch64" must not quietly build for x86_64.
if [ $# -gt $max_args ]; then
    echo "unexpected argument(s) after '${*:1:$max_args}': ${*:$((max_args + 1))}" >&2
    echo "$usage" >&2
    exit 2
fi
case "$kind" in complete | control | headless | all) ;; *) echo "unknown target kind '$kind'" >&2; echo "$usage" >&2; exit 2 ;; esac

. tools/container.sh
container_or_native "$0" "$@"
ensure_juce

out="build/$what"
configure=(cmake -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH="$JUCE_INSTALL_DIR")
run=()

if [ $arch = aarch64 ]; then
    # The cross build: the native JUCE install (its juceaide runs here) with the aarch64
    # toolchain, the :arm64 libraries through pkg-config, and qemu for the LV2 manifest helper
    # JUCE builds for the target and runs after each plugin (the flags of
    # dsp/juce_plugins/tools/build-podman.sh -setup). In the development container, or natively
    # where the packages of its Containerfile are installed.
    out="build/aarch64/$what"
    toolchain="$ROOT/dsp/juce_plugins/tools/toolchain-aarch64-linux.cmake"
    for tool in aarch64-linux-gnu-g++ qemu-aarch64-static; do
        command -v $tool > /dev/null || { echo "no $tool: the aarch64 build needs the packages of dsp/juce_plugins/tools/Containerfile" >&2; exit 2; }
    done
    export PKG_CONFIG_LIBDIR=/usr/lib/aarch64-linux-gnu/pkgconfig:/usr/share/pkgconfig
    unset PKG_CONFIG_PATH

    # JUCE copies every plugin it builds into ~/.lv2 and ~/.vst3 (SpeakerPlugin.cmake sets
    # COPY_PLUGIN_AFTER_BUILD), where an x86_64 host, speakerd included, would then find
    # aarch64 binaries under the URIs of its own plugins. So the copy directories of a cross
    # build are pinned inside it, in the build tree's own cache (CMAKE_PROJECT_INCLUDE), so
    # that a later reconfigure keeps them too; and the build runs with a throwaway HOME.
    mkdir -p "$out" build/aarch64/home
    cat > "$out/copy-dirs.cmake" <<'CMAKE'
# Written by tools/build.sh: a cross build copies its plugins here, never into a ~/.lv2.
set_directory_properties(PROPERTIES
    JUCE_LV2_COPY_DIR  "${CMAKE_BINARY_DIR}/copied/lv2"
    JUCE_VST3_COPY_DIR "${CMAKE_BINARY_DIR}/copied/vst3")
CMAKE
    configure+=(-DCMAKE_TOOLCHAIN_FILE="$toolchain"
                -DCMAKE_FIND_ROOT_PATH="/;$JUCE_INSTALL_DIR"
                -DCMAKE_CROSSCOMPILING_EMULATOR=/usr/bin/qemu-aarch64-static
                -DCMAKE_PROJECT_INCLUDE="$ROOT/$out/copy-dirs.cmake")
    run=(env HOME="$ROOT/build/aarch64/home")
fi

# Says what was built; for the aarch64 build, after checking that it is aarch64 code.
report() {
    local artefact="$1" binary="$1"
    [ -d "$artefact" ] && binary="$(find "$artefact" -maxdepth 1 -name '*.so' | head -1)"
    if [ $arch = aarch64 ]; then
        readelf -h "$binary" | grep -q 'Machine:.*AArch64' || { echo "$binary is not aarch64 code" >&2; exit 1; }
    fi
    echo "built $artefact"
}

# Fails if an aarch64 binary is in the ~/.lv2 of this HOME, where the hosts here would load it.
check_home_lv2() {
    [ $arch = aarch64 ] || return 0
    local so
    for so in "$HOME"/.lv2/*.lv2/*.so; do
        [ -e "$so" ] || continue
        if readelf -h "$so" 2> /dev/null | grep -q 'Machine:.*AArch64'; then
            echo "an aarch64 binary is in $HOME/.lv2: $so. Remove it: hosts here would load it" >&2
            exit 1
        fi
    done
}

# An application: a folder of dsp/juce_apps (plugin-check, speakerd, the controller).
if [ -f "dsp/juce_apps/$what/CMakeLists.txt" ]; then
    "${run[@]}" "${configure[@]}" -S "dsp/juce_apps/$what" -B "$out"
    "${run[@]}" cmake --build "$out" -j"$(nproc)"
    if [ $arch = aarch64 ]; then
        report "$out/${what}_artefacts/Release/$what"
    else
        echo "built $out/${what}_artefacts/Release/"
    fi
    exit 0
fi

src="dsp/juce_plugins/$what"
[ -f "$src/CMakeLists.txt" ] || { echo "no plugin '$what' in dsp/juce_plugins and no application in dsp/juce_apps" >&2; exit 2; }

# Plugins without TARGETS build only their complete target.
has_targets=0
grep -q '^[[:space:]]*TARGETS' "$src/CMakeLists.txt" && has_targets=1
targets=()
case "$kind" in
    complete) targets=("${what}_LV2") ;;
    control)  targets=("${what}_control_LV2") ;;
    headless) targets=("${what}_headless_LV2") ;;
    all)
        targets=("${what}_LV2")
        [ $has_targets = 1 ] && targets+=("${what}_control_LV2" "${what}_headless_LV2")
        ;;
    *) echo "unknown target kind '$kind'" >&2; exit 2 ;;
esac
if [ $has_targets = 0 ] && [ "$kind" != complete ] && [ "$kind" != all ]; then
    echo "$what lists no TARGETS: it builds only its complete target" >&2
    exit 2
fi

"${run[@]}" "${configure[@]}" -S "$src" -B "$out"
"${run[@]}" cmake --build "$out" -j"$(nproc)" --target "${targets[@]}"
for t in "${targets[@]}"; do report "$out/${t%_LV2}_artefacts/Release/LV2/${t%_LV2}.lv2"; done
check_home_lv2
