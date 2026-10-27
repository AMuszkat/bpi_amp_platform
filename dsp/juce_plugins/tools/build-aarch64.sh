#!/bin/bash
# Cross-compile plugins to aarch64-linux directly on a Linux x86_64 host, WITHOUT a container.
# Prefer tools/build-podman.sh, which carries all of this inside a pinned image.
#
#     tools/build-aarch64.sh <plugin|all>
#
# Prerequisites (Ubuntu/Debian host):
#   sudo apt install gcc-aarch64-linux-gnu g++-aarch64-linux-gnu cmake ninja-build qemu-user-static
#   sudo dpkg --add-architecture arm64 && sudo apt update
#   sudo apt install libasound2-dev:arm64 libfreetype6-dev:arm64 libfontconfig1-dev:arm64 \
#                    libx11-dev:arm64 libxext-dev:arm64 libxrandr-dev:arm64 libxcursor-dev:arm64 \
#                    libxinerama-dev:arm64 libxrender-dev:arm64 libgl1-mesa-dev:arm64
# plus a JUCE install whose juceaide runs on this host (a native `cmake --install` of
# juce_plugins/JUCE); point JUCE_INSTALL_DIR at it (default: /opt/juce).

set -euo pipefail
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

[ $# -eq 1 ] || { echo "Usage: $0 <plugin|all>   (plugins: $(list_plugins | tr '\n' ' '))"; exit 1; }
mapfile -t PLUGINS < <(resolve_plugins "$1")
JUCE_INSTALL_DIR="${JUCE_INSTALL_DIR:-/opt/juce}"

for p in "${PLUGINS[@]}"; do
    src="$PLUGINS_DIR/$p"
    out="$src/build-aarch64"
    echo "=== Cross-compiling $p for aarch64-linux ==="
    PKG_CONFIG_LIBDIR=/usr/lib/aarch64-linux-gnu/pkgconfig:/usr/share/pkgconfig \
    cmake -S "$src" -B "$out" -G Ninja \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_TOOLCHAIN_FILE="$TOOLS_DIR/toolchain-aarch64-linux.cmake" \
        -DCMAKE_PREFIX_PATH="$JUCE_INSTALL_DIR" \
        -DCMAKE_FIND_ROOT_PATH="/;$JUCE_INSTALL_DIR" \
        -DCMAKE_CROSSCOMPILING_EMULATOR=/usr/bin/qemu-aarch64-static
    PKG_CONFIG_LIBDIR=/usr/lib/aarch64-linux-gnu/pkgconfig:/usr/share/pkgconfig \
    cmake --build "$out" --target "${p}_LV2" -j"$(nproc)"
    echo "LV2 bundle: $out/${p}_artefacts/Release/LV2/$p.lv2"
    echo ""
done
echo "Deploy with:  tools/build-podman.sh <plugin> -install  (uses DEPLOY_HOST from config/local.env)"
echo "or copy the .lv2 folder into ~/.lv2 on the target yourself."
