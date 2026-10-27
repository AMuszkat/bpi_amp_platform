#!/bin/bash
# Build plugins inside an ARM64 Linux VM (e.g. UTM on an Apple-silicon Mac) that sees
# juce_plugins/ through a shared folder -- the original flow, kept for macOS hosts. On a
# Linux host prefer tools/build-podman.sh.
#
# SETUP (one-time): share juce_plugins/ with the VM and mount it (tools/setup-linux-vm.sh does
# the mount + fstab entry), then install the build dependencies in the VM:
#     sudo apt install build-essential cmake ninja-build pkg-config \
#          libasound2-dev libfreetype6-dev libfontconfig1-dev libx11-dev libxext-dev \
#          libxrandr-dev libxcursor-dev libxinerama-dev libxrender-dev libgl1-mesa-dev
#
# USAGE:
#   tools/build-linux-vm.sh <plugin|all>                # all steps (incl. -install)
#   tools/build-linux-vm.sh <plugin|all> -setup-juce    # build + install JUCE (one-time)
#   tools/build-linux-vm.sh <plugin|all> -setup -build  # configure + compile
#   tools/build-linux-vm.sh <plugin|all> -install       # deploy to DEPLOY_HOST (config/local.env)
#   tools/build-linux-vm.sh <plugin|all> -clean         # remove build dirs + the JUCE install
#
# The VM is ARM64 already, so this is a native build: no toolchain file.

set -euo pipefail
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
load_local_config

if [ $# -lt 1 ] || [ "${1#-}" != "$1" ]; then
    echo "Usage: $0 <plugin|all> [-setup-juce] [-setup] [-build] [-install] [-clean]"
    echo "Plugins: $(list_plugins | tr '\n' ' ')"
    exit 1
fi
mapfile -t PLUGINS < <(resolve_plugins "$1")
shift

# Build trees stay in the VM's /tmp: building on the 9p shared folder is slow, and it keeps
# the JUCE submodule free of build output.
JUCE_BUILD_DIR="/tmp/juce_build_linux_arm64"
JUCE_INSTALL_DIR="/tmp/juce_install_linux_arm64"

DO_SETUP_JUCE=false; DO_SETUP=false; DO_BUILD=false; DO_INSTALL=false; DO_CLEAN=false
if [ $# -eq 0 ]; then
    DO_SETUP_JUCE=true; DO_SETUP=true; DO_BUILD=true; DO_INSTALL=true
fi
while [ $# -gt 0 ]; do
    case "$1" in
        -setup-juce) DO_SETUP_JUCE=true ;;
        -setup)      DO_SETUP=true ;;
        -build)      DO_BUILD=true ;;
        -install)    DO_INSTALL=true ;;
        -clean)      DO_CLEAN=true ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
    shift
done

if $DO_CLEAN; then
    for p in "${PLUGINS[@]}"; do rm -rf "/tmp/$p-build"; done
    rm -rf "$JUCE_BUILD_DIR" "$JUCE_INSTALL_DIR"
    echo "Cleaned build directories and the JUCE install."
fi

if $DO_SETUP_JUCE; then
    if [ -d "$JUCE_INSTALL_DIR/lib/cmake" ]; then
        echo ">>> JUCE already installed at $JUCE_INSTALL_DIR (use -clean to reinstall)"
    else
        echo ">>> Building + installing JUCE to $JUCE_INSTALL_DIR..."
        cmake -S "$PLUGINS_DIR/JUCE" -B "$JUCE_BUILD_DIR" -G Ninja \
              -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$JUCE_INSTALL_DIR" \
              -DJUCE_BUILD_EXTRAS=OFF -DJUCE_BUILD_EXAMPLES=OFF
        cmake --build "$JUCE_BUILD_DIR" --target install -j"$(nproc)"
    fi
fi

if $DO_INSTALL; then
    require_deploy_host
    if ! ssh "$DEPLOY_HOST" "test -w '$DEPLOY_LV2_DIR'"; then
        echo "Error: '~/$DEPLOY_LV2_DIR' on $DEPLOY_HOST is missing or not writable by you. Once:"
        echo "      ssh -t $DEPLOY_HOST 'mkdir -p ~/$DEPLOY_LV2_DIR && sudo chown -R \$(id -un):\$(id -gn) ~/$DEPLOY_LV2_DIR'"
        exit 1
    fi
fi

for p in "${PLUGINS[@]}"; do
    build="/tmp/$p-build"
    if $DO_SETUP; then
        [ -d "$JUCE_INSTALL_DIR/lib/cmake" ] || { echo "Error: JUCE not installed. Run with -setup-juce first."; exit 1; }
        echo ">>> Configuring $p..."
        cmake -S "$PLUGINS_DIR/$p" -B "$build" -G Ninja -DCMAKE_BUILD_TYPE=Release \
              -DCMAKE_PREFIX_PATH="$JUCE_INSTALL_DIR"
    fi
    if $DO_BUILD; then
        echo ">>> Building $p..."
        cmake --build "$build" --target "${p}_LV2" -j"$(nproc)"
    fi
    if $DO_INSTALL; then
        lv2="$build/${p}_artefacts/Release/LV2/$p.lv2"
        [ -d "$lv2" ] || { echo "Error: $lv2 not found. Did you run -build first?"; exit 1; }
        # tar over ssh (not `scp -r`): newer OpenSSH scp fails to upload a dir into an
        # existing remote dir; tar works on every ssh version.
        tar -C "$(dirname "$lv2")" -cf - "$p.lv2" | ssh "$DEPLOY_HOST" "tar -C '$DEPLOY_LV2_DIR' -xf -"
        echo "Deployed to $DEPLOY_HOST:~/$DEPLOY_LV2_DIR/$p.lv2"
    fi
done
echo "=== Done ==="
