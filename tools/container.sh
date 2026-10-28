#!/bin/bash
# The development container: the builder image of dsp/juce_plugins/tools/Containerfile with
# this checkout mounted at its own path, so the builds and both test rungs run on a machine
# that has Podman and none of the packages of the root CLAUDE.md. See the root CLAUDE.md.
#
#     tools/container.sh                  # a shell in this checkout's container
#     tools/container.sh <command...>     # run a command in it, from the current directory
#     tools/container.sh --status         # the image and the container
#     tools/container.sh --stop           # remove this checkout's container
#
# tools/build.sh, tools/test.sh, tools/pw-inspect.sh and tests/vpair/vpair.sh source this
# file and move into the container themselves (container_or_native), so they are used the
# same way with or without it. BPI_CONTAINER chooses:
#     auto   (default) in the container when Podman is installed, natively otherwise
#     1      always in the container
#     0      always natively: the packages of the root CLAUDE.md must be installed
#
# Each checkout, and each git worktree, has its own long-running container named after its
# path. What runs in it, such as the virtual pair, survives between commands and cannot
# collide with another worktree's. The image is rebuilt when the Containerfile changes, and
# the container is recreated on the new image.

CONTAINER_CHECKOUT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTAINER_IMAGE="localhost/bpi-amp-builder:bookworm"
CONTAINER_FILE="$CONTAINER_CHECKOUT/dsp/juce_plugins/tools/Containerfile"
CONTAINER_NAME="bpi-dev-$(printf %s "$CONTAINER_CHECKOUT" | sha256sum | cut -c1-10)"

# The main checkout: the one holding the git directory and the JUCE install, shared by its
# worktrees. For the main checkout itself, that is this checkout.
main_checkout() {
    local common
    if common="$(git -C "$CONTAINER_CHECKOUT" rev-parse --path-format=absolute --git-common-dir 2> /dev/null)"; then
        dirname "$common"
    else
        echo "$CONTAINER_CHECKOUT"
    fi
}

in_dev_container() { [ -n "${BPI_IN_CONTAINER:-}" ]; }

container_wanted() {
    in_dev_container && return 1
    case "${BPI_CONTAINER:-auto}" in
        0) return 1 ;;
        1) command -v podman > /dev/null || { echo "BPI_CONTAINER=1 but Podman is not installed" >&2; exit 2; } ;;
        *) command -v podman > /dev/null ;;
    esac
}

# The hash that labels an image built from the current Containerfile. build-podman.sh
# computes the same, so either script rebuilds an image that predates an edit.
containerfile_hash() { sha256sum "$CONTAINER_FILE" | cut -c1-16; }

ensure_image() {
    local want have
    want="$(containerfile_hash)"
    have="$(podman image inspect --format '{{ index .Labels "bpi.containerfile" }}' "$CONTAINER_IMAGE" 2> /dev/null || true)"
    [ "$want" = "$have" ] && return 0
    echo ">>> building the image $CONTAINER_IMAGE from the Containerfile (several minutes the first time)" >&2
    podman build --label "bpi.containerfile=$want" -t "$CONTAINER_IMAGE" - < "$CONTAINER_FILE" >&2
}

ensure_container() {
    local main image state
    main="$(main_checkout)"
    image="$(podman image inspect --format '{{.Id}}' "$CONTAINER_IMAGE")"
    state="$(podman container inspect --format '{{.State.Running}} {{.Image}}' "$CONTAINER_NAME" 2> /dev/null || true)"
    [ "$state" = "true $image" ] && return 0
    [ -n "$state" ] && podman rm -f "$CONTAINER_NAME" > /dev/null

    # The paths are the same inside as outside, so build trees, reports and the test status
    # files mean the same on both sides. A worktree outside the main checkout is mounted too.
    local mounts=(-v "$main:$main:z")
    case "$CONTAINER_CHECKOUT" in "$main" | "$main"/*) ;; *) mounts+=(-v "$CONTAINER_CHECKOUT:$CONTAINER_CHECKOUT:z") ;; esac
    echo ">>> starting the container $CONTAINER_NAME for $CONTAINER_CHECKOUT" >&2
    # 9>&-: the container's monitor process outlives this call, and must not inherit the
    # lock that container_exec holds on fd 9, or it would hold it for as long as it runs.
    podman run -d --init --name "$CONTAINER_NAME" --hostname bpi-dev \
        --label "bpi.checkout=$CONTAINER_CHECKOUT" -e BPI_IN_CONTAINER=1 \
        "${mounts[@]}" "$CONTAINER_IMAGE" sleep infinity > /dev/null 9>&-
}

# Runs a command in this checkout's container, from the current directory when the
# container can see it. Does not return.
container_exec() {
    mkdir -p "$(main_checkout)/build"
    (flock 9 && ensure_image && ensure_container) 9> "$(main_checkout)/build/.container.lock" || exit 1

    local flags=(-i) var dir="$PWD" main
    [ -t 0 ] && [ -t 1 ] && flags+=(-t)
    for var in TERM JUCE_INSTALL_DIR BUILD_DIR $(compgen -e | grep -E '^(BPI|VPAIR)_' || true); do
        [ -n "${!var:-}" ] && flags+=(-e "$var=${!var}")
    done
    main="$(main_checkout)"
    case "$dir" in "$main" | "$main"/* | "$CONTAINER_CHECKOUT" | "$CONTAINER_CHECKOUT"/*) ;; *) dir="$CONTAINER_CHECKOUT" ;; esac
    exec podman exec "${flags[@]}" -w "$dir" "$CONTAINER_NAME" "$@"
}

# For a script that sources this file: re-runs the script in the container when
# BPI_CONTAINER says so, and returns to run it natively otherwise.
#     container_or_native "$0" "$@"
container_or_native() {
    if container_wanted; then
        local self
        self="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
        shift
        container_exec "$self" "$@"
    fi
    return 0
}

# Points JUCE_INSTALL_DIR at a JUCE install, making one when needed: an explicit
# JUCE_INSTALL_DIR is used as it is; otherwise build/juce-install of the main checkout, built
# from the pinned submodule the first time and again whenever the pin moves.
ensure_juce() {
    if [ -n "${JUCE_INSTALL_DIR:-}" ]; then
        [ -d "$JUCE_INSTALL_DIR" ] && return 0
        echo "no JUCE install at JUCE_INSTALL_DIR=$JUCE_INSTALL_DIR" >&2
        return 2
    fi
    local main src pin log
    main="$(main_checkout)"
    log="$main/build/juce-build.log"
    src="$main/dsp/juce_plugins/JUCE"
    export JUCE_INSTALL_DIR="$main/build/juce-install"

    [ -f "$src/CMakeLists.txt" ] || git -C "$main" submodule update --init dsp/juce_plugins/JUCE >&2
    pin="$(git -C "$src" rev-parse HEAD)"
    [ "$(cat "$JUCE_INSTALL_DIR/.juce-commit" 2> /dev/null)" = "$pin" ] && return 0

    mkdir -p "$main/build"
    (
        set -e
        flock 9
        [ "$(cat "$JUCE_INSTALL_DIR/.juce-commit" 2> /dev/null)" = "$pin" ] && exit 0
        echo ">>> building JUCE ${pin:0:12} into $JUCE_INSTALL_DIR (once per JUCE pin), log in $log"
        rm -rf "$main/build/juce" "$JUCE_INSTALL_DIR"
        {
            cmake -S "$src" -B "$main/build/juce" -G Ninja -DCMAKE_BUILD_TYPE=Release \
                  -DCMAKE_INSTALL_PREFIX="$JUCE_INSTALL_DIR" -DJUCE_BUILD_EXTRAS=OFF -DJUCE_BUILD_EXAMPLES=OFF \
            && cmake --build "$main/build/juce" \
            && cmake --install "$main/build/juce"
        } > "$log" 2>&1 || { tail -30 "$log"; exit 1; }
        echo "$pin" > "$JUCE_INSTALL_DIR/.juce-commit"
    ) 9> "$main/build/.juce.lock" >&2
}

container_status() {
    local have
    have="$(podman image inspect --format '{{ index .Labels "bpi.containerfile" }}' "$CONTAINER_IMAGE" 2> /dev/null || true)"
    if [ -z "$have" ]; then
        echo "image:     $CONTAINER_IMAGE not built yet (the first command builds it)"
    elif [ "$have" = "$(containerfile_hash)" ]; then
        echo "image:     $CONTAINER_IMAGE, current"
    else
        echo "image:     $CONTAINER_IMAGE, older than the Containerfile (the next command rebuilds it)"
    fi
    echo "container: $CONTAINER_NAME, $(podman container inspect --format '{{.State.Status}} since {{.State.StartedAt}}' "$CONTAINER_NAME" 2> /dev/null || echo 'not running')"
    echo "checkout:  $CONTAINER_CHECKOUT"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    set -euo pipefail
    command -v podman > /dev/null || { echo "Podman is not installed: apt install podman (or see https://podman.io)" >&2; exit 2; }
    case "${1:-}" in
        --status) container_status ;;
        --stop) podman rm -f "$CONTAINER_NAME" > /dev/null 2>&1 || true; echo "$CONTAINER_NAME: removed" ;;
        "") container_exec bash ;;
        *) container_exec "$@" ;;
    esac
fi
