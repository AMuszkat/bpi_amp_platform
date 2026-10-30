#!/bin/bash
# Builds the block FIR test (README.md) for x86_64 and for aarch64, the way tools/build.sh
# <plugin> aarch64 cross-compiles the plugins, and runs both, the aarch64 build under
# qemu-aarch64-static. Part of rung 1 (tools/test.sh unit).
#
#     dsp/juce_plugins/tests/blockfir/run.sh [--time]
#
# The aarch64 build is what ships, so without its toolchain or qemu this fails, naming the
# packages, rather than pass on x86_64 alone. Runs in the development container when Podman is
# installed (tools/container.sh; BPI_CONTAINER=0 runs it natively). Builds into
# build/tests/blockfir/ at the repository root.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../../.." && pwd)"
. "$ROOT/tools/container.sh"
container_or_native "$0" "$@"

missing=()
for tool in aarch64-linux-gnu-g++ qemu-aarch64-static; do
    command -v $tool > /dev/null || missing+=("$tool")
done
if [ ${#missing[@]} -gt 0 ]; then
    echo "blockfir test: FAILED: no ${missing[*]}. Its aarch64 half, which is what ships, needs the" >&2
    echo "  packages g++-aarch64-linux-gnu and qemu-user-static (both in the development container," >&2
    echo "  dsp/juce_plugins/tools/Containerfile)" >&2
    exit 1
fi

ensure_juce
OUT="$ROOT/build/tests/blockfir"
mkdir -p "$OUT"
configure=(cmake -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH="$JUCE_INSTALL_DIR" -S "$HERE")

echo "=== blockfir test: building for x86_64 and aarch64 (logs in $OUT)"
"${configure[@]}" -B "$OUT/x86_64" > "$OUT/x86_64-configure.log"
cmake --build "$OUT/x86_64" -j"$(nproc)" > "$OUT/x86_64-build.log" || { tail -30 "$OUT/x86_64-build.log"; exit 1; }

(
    export PKG_CONFIG_LIBDIR=/usr/lib/aarch64-linux-gnu/pkgconfig:/usr/share/pkgconfig
    unset PKG_CONFIG_PATH
    "${configure[@]}" -B "$OUT/aarch64" \
        -DCMAKE_TOOLCHAIN_FILE="$ROOT/dsp/juce_plugins/tools/toolchain-aarch64-linux.cmake" \
        -DCMAKE_FIND_ROOT_PATH="/;$JUCE_INSTALL_DIR" \
        -DCMAKE_CROSSCOMPILING_EMULATOR="$(command -v qemu-aarch64-static)" > "$OUT/aarch64-configure.log"
    cmake --build "$OUT/aarch64" -j"$(nproc)" > "$OUT/aarch64-build.log" || { tail -30 "$OUT/aarch64-build.log"; exit 1; }
)

for arch in x86_64 aarch64; do
    if grep -n 'warning:' "$OUT/$arch-build.log" | grep -v '/JUCE/\|juce-install' | grep -q .; then
        echo "blockfir test: FAILED: the $arch build warns in our sources:" >&2
        grep -n 'warning:' "$OUT/$arch-build.log" | grep -v '/JUCE/\|juce-install' >&2
        exit 1
    fi
done

binary=blockfir_test_artefacts/Release/blockfir_test
rc=0
"$OUT/x86_64/$binary" "$@" || rc=1
readelf -h "$OUT/aarch64/$binary" | grep -q 'Machine:.*AArch64' || { echo "blockfir test: the aarch64 build is not aarch64 code" >&2; exit 1; }
# The cross toolchain's own libraries, the ones the test was linked against.
[ -d /usr/aarch64-linux-gnu/lib ] && export QEMU_LD_PREFIX=/usr/aarch64-linux-gnu
qemu-aarch64-static "$OUT/aarch64/$binary" "$@" || rc=1
exit $rc
