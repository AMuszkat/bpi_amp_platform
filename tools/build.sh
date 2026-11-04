#!/bin/bash
# Builds a plugin or application, for this machine (x86_64) or for the board (aarch64). See
# .claude/skills/build.
#
#     tools/build.sh <plugin|app> [complete|control|headless|all]...            # build/<what>/
#     tools/build.sh <plugin|app> aarch64 [complete|control|headless|all]...    # build/aarch64/<what>/
#
# Several kinds build together, in one `cmake --build` of all their targets
# (tools/build.sh loudness complete headless); no kind is all, a kind named twice is built
# once. An application has no kinds: they are checked and ignored. The architecture comes
# right after the name: "loudness headless aarch64" is refused, never an x86_64 build.
#
# With ccache (in the development container; ccache_env in tools/container.sh says when), every
# configure sets it as the compiler launcher, and otherwise sets no launcher, so that a build
# tree configured with it still builds where it is missing.
#
# One build at a time per build tree: each call holds <tree>/.build.lock (flock) from before its
# configure to its end, so that a serial test run's build, tests/vpair/pool.py's prebuild and a
# build by hand in the same tree wait for each other (saying once, on stderr, which tree they wait
# for) instead of running two ninjas in it. tools/build.sh never calls itself, so it cannot wait
# for its own lock. Without flock (util-linux: in the container and on CI) it builds unlocked.
#
# No build of this script copies a plugin into a ~/.lv2 or ~/.vst3: JUCE's copy step copies
# each bundle into the build tree's own <tree>/copied/lv2 and copied/vst3 (copy-dirs.cmake,
# below), so that two builds at once, of two worktrees or of a test pool, never write one
# folder, and no host here finds a bundle it was not given. The bundle to use is the one in
# <tree>/<target>_artefacts/Release/LV2/.
#
# `cmake --build` runs under a make jobserver (tools/jobserver.sh), so that the LTO links that
# run at once share one pool of $(nproc) LTRANS jobs.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
usage="usage: $0 <plugin|app> [aarch64] [complete|control|headless|all]..."
what="${1:?$usage}"
arch=x86_64
[ "${2:-}" = aarch64 ] && arch=aarch64
first_kind=2
[ $arch = aarch64 ] && first_kind=3
# An empty argument names no kind, as it did when there was one at most.
kinds=()
for kind in "${@:$first_kind}"; do [ -z "$kind" ] || kinds+=("$kind"); done
[ ${#kinds[@]} -gt 0 ] || kinds=(all)
for kind in "${kinds[@]}"; do
    case "$kind" in
        complete | control | headless | all) ;;
        # Nothing else: "loudness headless aarch64" must not quietly build for x86_64.
        aarch64) echo "'aarch64' is not a target kind: the architecture comes right after the name ($0 $what aarch64 [kind]...)" >&2
                 echo "$usage" >&2; exit 2 ;;
        *) echo "unknown target kind '$kind'" >&2; echo "$usage" >&2; exit 2 ;;
    esac
done
[ -f "dsp/juce_apps/$what/CMakeLists.txt" ] || [ -f "dsp/juce_plugins/$what/CMakeLists.txt" ] \
    || { echo "no plugin '$what' in dsp/juce_plugins and no application in dsp/juce_apps" >&2; exit 2; }

. tools/container.sh
container_or_native "$0" "$@"
ensure_juce
ccache_env

out="build/$what"
[ $arch = x86_64 ] || out="build/aarch64/$what"
# The tree's lock (the header), before anything writes to the tree, held to the end of the script;
# what it runs inherits the descriptor, and none of that outlives the build.
if command -v flock > /dev/null; then
    mkdir -p "$out"
    exec {tree_lock}> "$out/.build.lock"
    if ! flock -n "$tree_lock"; then
        echo "tools/build.sh: waiting for $out: another build runs in it ($out/.build.lock)" >&2
        flock "$tree_lock"
    fi
fi
# --log-level=NOTICE: no STATUS lines, among them JUCE's pkg-config search for libcurl, webkit2gtk
# and gtk, which none of these builds uses.
configure=(cmake -G Ninja --log-level=NOTICE -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH="$JUCE_INSTALL_DIR")
# The compiler launcher, set on every configure: ccache, or none (empty), which also takes it
# off a tree an earlier configure gave it to. Switching rebuilds the tree (the compile commands
# change).
launcher=
[ "$BPI_CCACHE" = 1 ] && command -v ccache > /dev/null && launcher=ccache
configure+=(-DCMAKE_C_COMPILER_LAUNCHER="$launcher" -DCMAKE_CXX_COMPILER_LAUNCHER="$launcher")
run=()

if [ $arch = aarch64 ]; then
    # The cross build: the native JUCE install (its juceaide runs here) with the aarch64
    # toolchain, the :arm64 libraries through pkg-config, and qemu for the LV2 manifest helper
    # JUCE builds for the target and runs after each plugin (the flags of
    # dsp/juce_plugins/tools/build-podman.sh -setup). In the development container, or natively
    # where the packages of its Containerfile are installed.
    toolchain="$ROOT/dsp/juce_plugins/tools/toolchain-aarch64-linux.cmake"
    for tool in aarch64-linux-gnu-g++ qemu-aarch64-static; do
        command -v $tool > /dev/null || { echo "no $tool: the aarch64 build needs the packages of dsp/juce_plugins/tools/Containerfile" >&2; exit 2; }
    done
    export PKG_CONFIG_LIBDIR=/usr/lib/aarch64-linux-gnu/pkgconfig:/usr/share/pkgconfig
    unset PKG_CONFIG_PATH

    # The copy directories below keep aarch64 binaries out of every ~/.lv2, where an x86_64
    # host, speakerd included, would find them under the URIs of its own plugins; the cross
    # build also runs with a throwaway HOME, and check_home_lv2 fails if one is found there.
    mkdir -p build/aarch64/home
    configure+=(-DCMAKE_TOOLCHAIN_FILE="$toolchain"
                -DCMAKE_FIND_ROOT_PATH="/;$JUCE_INSTALL_DIR"
                -DCMAKE_CROSSCOMPILING_EMULATOR=/usr/bin/qemu-aarch64-static)
    run=(env HOME="$ROOT/build/aarch64/home")
fi

# JUCE copies every plugin it builds (SpeakerPlugin.cmake adds the copy step,
# juce_enable_copy_plugin_step) into ~/.lv2 and ~/.vst3 unless told otherwise. Here the copy
# directories are pinned inside the build tree, in its own cache (CMAKE_PROJECT_INCLUDE), so that
# a later reconfigure keeps them too: <tree>/copied/lv2 and copied/vst3, for either architecture.
# Not a HOME of its own for each x86_64 build: the copy directory is in the link step's command, so
# a HOME that changed would relink every plugin. The file is written only when its text changes,
# so its date never makes CMake run again.
copy_dirs="$(cat <<'CMAKE'
# Written by tools/build.sh: its builds copy their plugins here, never into a ~/.lv2 or ~/.vst3.
set_directory_properties(PROPERTIES
    JUCE_LV2_COPY_DIR  "${CMAKE_BINARY_DIR}/copied/lv2"
    JUCE_VST3_COPY_DIR "${CMAKE_BINARY_DIR}/copied/vst3")
CMAKE
)"
mkdir -p "$out"
[ "$(cat "$out/copy-dirs.cmake" 2> /dev/null)" = "$copy_dirs" ] || printf '%s\n' "$copy_dirs" > "$out/copy-dirs.cmake"
configure+=(-DCMAKE_PROJECT_INCLUDE="$ROOT/$out/copy-dirs.cmake")

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
    "${run[@]}" tools/jobserver.sh cmake --build "$out" -j"$(nproc)"
    if [ $arch = aarch64 ]; then
        report "$out/${what}_artefacts/Release/$what"
    else
        echo "built $out/${what}_artefacts/Release/"
    fi
    exit 0
fi

src="dsp/juce_plugins/$what"

# Plugins without TARGETS build only their complete target.
has_targets=0
grep -q '^[[:space:]]*TARGETS' "$src/CMakeLists.txt" && has_targets=1
targets=()
for kind in "${kinds[@]}"; do
    case "$kind" in
        complete) add=("${what}_LV2") ;;
        control)  add=("${what}_control_LV2") ;;
        headless) add=("${what}_headless_LV2") ;;
        all)
            add=("${what}_LV2")
            [ $has_targets = 1 ] && add+=("${what}_control_LV2" "${what}_headless_LV2")
            ;;
    esac
    if [ $has_targets = 0 ] && [ "$kind" != complete ] && [ "$kind" != all ]; then
        echo "$what lists no TARGETS: it builds only its complete target" >&2
        exit 2
    fi
    # Each target once, in the order the kinds name them.
    for t in "${add[@]}"; do
        case " ${targets[*]} " in *" $t "*) ;; *) targets+=("$t") ;; esac
    done
done

"${run[@]}" "${configure[@]}" -S "$src" -B "$out"
"${run[@]}" tools/jobserver.sh cmake --build "$out" -j"$(nproc)" --target "${targets[@]}"
for t in "${targets[@]}"; do report "$out/${t%_LV2}_artefacts/Release/LV2/${t%_LV2}.lv2"; done
check_home_lv2
