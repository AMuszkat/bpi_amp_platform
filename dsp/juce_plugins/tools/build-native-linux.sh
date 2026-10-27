#!/bin/bash
# Build plugins natively ON the ARM64 Linux target itself (slow, but needs no cross toolchain)
# and install them into ~/.lv2 there.
#
#     tools/build-native-linux.sh <plugin|all>
#
# Prerequisites (Debian/Ubuntu-based ARM Linux):
#   sudo apt install build-essential cmake ninja-build git pkg-config
#   sudo apt install libasound2-dev libfreetype6-dev libfontconfig1-dev libx11-dev libxext-dev \
#                    libxrandr-dev libxcursor-dev libxinerama-dev libxrender-dev libgl1-mesa-dev
# plus JUCE installed on the device (`cmake --install` of juce_plugins/JUCE); point
# JUCE_INSTALL_DIR at it (default: /opt/juce).

set -euo pipefail
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

[ $# -eq 1 ] || { echo "Usage: $0 <plugin|all>   (plugins: $(list_plugins | tr '\n' ' '))"; exit 1; }
mapfile -t PLUGINS < <(resolve_plugins "$1")
JUCE_INSTALL_DIR="${JUCE_INSTALL_DIR:-/opt/juce}"

# Cortex-A53 tuning runs on both core types of a big.LITTLE A53/A73 SoC.
export CFLAGS="-mcpu=cortex-a53 -mtune=cortex-a53 -O2"
export CXXFLAGS="-mcpu=cortex-a53 -mtune=cortex-a53 -O2"

mkdir -p "$HOME/.lv2"
for p in "${PLUGINS[@]}"; do
    src="$PLUGINS_DIR/$p"
    out="$src/build-native"
    echo "=== Building $p natively ==="
    cmake -S "$src" -B "$out" -G Ninja -DCMAKE_BUILD_TYPE=Release \
          -DCMAKE_PREFIX_PATH="$JUCE_INSTALL_DIR"
    # Few parallel jobs: these boards run out of RAM long before they run out of cores.
    cmake --build "$out" --target "${p}_LV2" -j2
    rm -rf "$HOME/.lv2/$p.lv2"
    cp -a "$out/${p}_artefacts/Release/LV2/$p.lv2" "$HOME/.lv2/"
    echo "Installed $HOME/.lv2/$p.lv2"
    echo ""
done
