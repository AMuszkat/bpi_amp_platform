#!/bin/bash
# The development container: the builder image of dsp/juce_plugins/tools/Containerfile with
# this checkout mounted at its own path, so the builds and both test levels run on a machine
# that has Podman and none of the packages of the root CLAUDE.md. See the root CLAUDE.md.
#
#     tools/container.sh                  # a shell in this checkout's container
#     tools/container.sh <command...>     # run a command in it, from the current directory
#     tools/container.sh --status         # the image and the container, and every other builder
#                                         # image and container, with what --prune would remove
#     tools/container.sh --stop           # remove this checkout's container
#     tools/container.sh --prune [--dry-run]   # remove the stopped containers of checkouts that
#                                         # are gone, then the builder images no container uses
#
# tools/build.sh, tools/test.sh, tools/pw-inspect.sh and tests/vpair/vpair.sh source this
# file and move into the container themselves (container_or_native), so they are used the
# same way with or without it. BPI_CONTAINER chooses:
#     auto   (default) in the container when Podman is installed, natively otherwise
#     1      always in the container
#     0      always natively: the packages of the root CLAUDE.md must be installed
# The container is given TERM, CI, JUCE_INSTALL_DIR, BUILD_DIR, PLUGIN_CHECK_LTO and every
# BPI_* and VPAIR_* variable, nothing else. The build scripts then call ccache_env (below):
# BPI_CCACHE=0 builds without ccache, BPI_CCACHE=1 with it even where CI is set.
#
# Each checkout, and each git worktree, has its own long-running container named after its
# path. What runs in it, such as the virtual pair, survives between commands and cannot
# collide with another worktree's. The image is tagged by its Containerfile's content,
# localhost/bpi-amp-builder:bookworm-<the first 16 hex digits of its sha256> (the label
# bpi.containerfile says the same), so checkouts with different Containerfiles each have their
# own image, and never rebuild one tag in turn and recreate each other's containers. An image
# built from this Containerfile under another tag (localhost/bpi-amp-builder:bookworm, the one
# tag there was before, or none) is tagged rather than built again. A checkout's container is
# recreated only when it does not run its checkout's image (after an edit of the Containerfile).
#
# Old images stay until --prune: it removes the stopped bpi-dev containers whose checkout no
# longer exists, then every builder image (one with the label bpi.containerfile) that no
# container uses and that is not the image of a Containerfile in a worktree of this repository.
# It never touches a running container, nor an image a container uses: --stop a checkout's
# container first to let its image go.

CONTAINER_CHECKOUT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTAINER_FILE="$CONTAINER_CHECKOUT/dsp/juce_plugins/tools/Containerfile"
CONTAINER_REPOSITORY="localhost/bpi-amp-builder"
CONTAINER_OLD_TAG="$CONTAINER_REPOSITORY:bookworm"   # the one tag before the image was tagged by its Containerfile
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

# The hash that labels and tags an image built from a Containerfile (this checkout's by default).
# dsp/juce_plugins/tools/build-podman.sh computes the same, and uses the same tag.
containerfile_hash() { sha256sum "${1:-$CONTAINER_FILE}" | cut -c1-16; }
CONTAINER_IMAGE="$CONTAINER_REPOSITORY:bookworm-$(containerfile_hash 2> /dev/null || true)"

image_label() { podman image inspect --format '{{ index .Labels "bpi.containerfile" }}' "$1" 2> /dev/null || true; }

ensure_image() {
    local want id
    want="$(containerfile_hash)"
    [ "$(image_label "$CONTAINER_IMAGE")" = "$want" ] && return 0
    # Built already, under the old tag or another: the old tag first (the image its containers
    # run), then the newest.
    if [ "$(image_label "$CONTAINER_OLD_TAG")" = "$want" ]; then
        id="$(podman image inspect --format '{{.Id}}' "$CONTAINER_OLD_TAG")"
    else
        id="$(podman images --no-trunc --filter "label=bpi.containerfile=$want" --format '{{.ID}}' | head -1)"
    fi
    if [ -n "$id" ]; then
        echo ">>> tagging the image ${id#sha256:} as $CONTAINER_IMAGE: it was built from this Containerfile" >&2
        podman tag "$id" "$CONTAINER_IMAGE" >&2
        return
    fi
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
    # CI too, so that what CI decides (ccache_env, the test defaults) is decided alike inside.
    for var in TERM CI JUCE_INSTALL_DIR BUILD_DIR PLUGIN_CHECK_LTO $(compgen -e | grep -E '^(BPI|VPAIR)_' || true); do
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

# The ccache policy of the build scripts, called after container_or_native (in the container,
# or natively), before a configure. With ccache on PATH, BPI_CCACHE not 0 and CI unset
# (BPI_CCACHE=1 uses it with CI too): BPI_CCACHE=1, and the cache in build/ccache of the main
# checkout, which every checkout's and worktree's container mounts, so they all share it;
# paths under this checkout are hashed relative to it (CCACHE_BASEDIR), so that two worktrees
# hit each other's entries for their own sources (the JUCE install, outside every worktree,
# keeps its absolute paths there; in the main checkout, which contains it, they are relative,
# so the main checkout and the worktrees do not hit each other's entries).
# Otherwise BPI_CCACHE=0. A build script then sets ccache as the compiler launcher when
# BPI_CCACHE=1 and ccache is on PATH, and sets no launcher otherwise. CCACHE_DIR and
# CCACHE_BASEDIR already set are kept; the container is not given the outside ones (a
# CCACHE_DIR of the host is not mounted in it). Nothing else is needed: the builds have no -g
# (so the directory is not hashed), and the one compile that misses at another path is JUCE's
# three-line juce_core_CompilationTime.cpp (__TIME__). Its statistics:
# tools/container.sh env CCACHE_DIR=<main checkout>/build/ccache ccache -s
ccache_env() {
    if command -v ccache > /dev/null && [ "${BPI_CCACHE:-}" != 0 ] \
       && { [ -z "${CI:-}" ] || [ "${BPI_CCACHE:-}" = 1 ]; }; then
        export BPI_CCACHE=1
        export CCACHE_DIR="${CCACHE_DIR:-$(main_checkout)/build/ccache}"
        export CCACHE_BASEDIR="${CCACHE_BASEDIR:-$CONTAINER_CHECKOUT}"
    else
        export BPI_CCACHE=0
    fi
}

# What --prune keeps and removes, one line each, tab-separated:
#     container <name> <keep|remove> <state> <checkout>
#     image <id> <keep|remove> <label> <tags, comma-separated or none> <containers using it>
# A container is removed when it is a bpi-dev container (label bpi.checkout), not running, and its
# checkout is gone; an image (label bpi.containerfile) when no container that stays uses it and
# its label is the hash of no Containerfile in a worktree of this repository.
prune_plan() {
    local name state checkout image id label tags users wanted=" " wt
    local -A uses=()
    while IFS=$'\t' read -r name state checkout image; do
        if [ "$state" != running ] && [ ! -d "$checkout" ]; then
            printf 'container\t%s\tremove\t%s\t%s\n' "$name" "$state" "$checkout"
        else
            printf 'container\t%s\tkeep\t%s\t%s\n' "$name" "$state" "$checkout"
            uses[$image]+="$name,"
        fi
    done < <(podman ps -a --no-trunc --filter label=bpi.checkout \
                 --format $'{{.Names}}\t{{.State}}\t{{ index .Labels "bpi.checkout" }}\t{{.ImageID}}')
    while read -r wt; do
        [ -f "$wt/dsp/juce_plugins/tools/Containerfile" ] && wanted+="$(containerfile_hash "$wt/dsp/juce_plugins/tools/Containerfile") "
    done < <(git -C "$CONTAINER_CHECKOUT" worktree list --porcelain 2> /dev/null | sed -n 's/^worktree //p'; echo "$CONTAINER_CHECKOUT")
    while read -r id; do
        label="$(image_label "$id")"
        tags="$(podman image inspect --format '{{range .RepoTags}}{{.}},{{end}}' "$id" 2> /dev/null || true)"
        tags="${tags%,}"
        users="${uses[sha256:$id]:-${uses[$id]:-}}"
        users="${users%,}"
        if [ -z "$users" ] && [[ "$wanted" != *" $label "* ]]; then
            printf 'image\t%s\tremove\t%s\t%s\t%s\n' "${id:0:12}" "$label" "${tags:-none}" "none"
        else
            printf 'image\t%s\tkeep\t%s\t%s\t%s\n' "${id:0:12}" "$label" "${tags:-none}" "${users:-none}"
        fi
    done < <(podman images --no-trunc --filter label=bpi.containerfile --format '{{.ID}}' | sed 's/^sha256://' | sort -u)
}

container_status() {
    local have kind what verdict a b c
    have="$(image_label "$CONTAINER_IMAGE")"
    if [ -z "$have" ] && [ -n "$(podman images -q --filter "label=bpi.containerfile=$(containerfile_hash)")" ]; then
        echo "image:     $CONTAINER_IMAGE not tagged yet (the next command tags the image of this Containerfile there is)"
    elif [ -z "$have" ]; then
        echo "image:     $CONTAINER_IMAGE not built yet (the next command builds it)"
    elif [ "$have" = "$(containerfile_hash)" ]; then
        echo "image:     $CONTAINER_IMAGE, current"
    else
        echo "image:     $CONTAINER_IMAGE, labelled $have, not this Containerfile's (the next command tags or builds it)"
    fi
    echo "container: $CONTAINER_NAME, $(podman container inspect --format '{{.State.Status}} since {{.State.StartedAt}}' "$CONTAINER_NAME" 2> /dev/null || echo 'not running')"
    echo "checkout:  $CONTAINER_CHECKOUT"
    echo "every builder image and bpi-dev container (--prune removes those marked remove):"
    while IFS=$'\t' read -r kind what verdict a b c; do
        if [ "$kind" = container ]; then
            printf '  %-6s container %s, %s, for %s\n' "$verdict" "$what" "$a" "$b"
        else
            printf '  %-6s image %s (%s), tags %s, used by %s\n' "$verdict" "$what" "$a" "$b" "$c"
        fi
    done < <(prune_plan)
}

# Removes what prune_plan marks remove (--dry-run: only says it).
container_prune() {
    local dry="${1:-}" line kind what verdict a b c tag doing=removing
    local -a plan
    [ -z "$dry" ] || doing="would remove"
    mapfile -t plan < <(prune_plan)
    for line in "${plan[@]}"; do
        IFS=$'\t' read -r kind what verdict a b c <<< "$line"
        [ "$verdict" = remove ] || continue
        if [ "$kind" = container ]; then
            echo "$doing the container $what ($a, its checkout $b is gone)"
            [ -n "$dry" ] || podman rm "$what" > /dev/null
        else
            echo "$doing the image $what ($a, tags $b): no container uses it"
            [ -n "$dry" ] && continue
            # By each tag (removing the last one removes the image), or by its id when it has none.
            if [ "$b" = none ]; then podman rmi "$what" > /dev/null; else
                for tag in ${b//,/ }; do podman rmi "$tag" > /dev/null; done
            fi
        fi
    done
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    set -euo pipefail
    command -v podman > /dev/null || { echo "Podman is not installed: apt install podman (or see https://podman.io)" >&2; exit 2; }
    case "${1:-}" in
        --status) container_status ;;
        --stop) podman rm -f "$CONTAINER_NAME" > /dev/null 2>&1 || true; echo "$CONTAINER_NAME: removed" ;;
        --prune) case "${2:-}" in "") container_prune ;; --dry-run) container_prune dry ;;
                     *) echo "usage: $0 --prune [--dry-run]" >&2; exit 2 ;; esac ;;
        "") container_exec bash ;;
        *) container_exec "$@" ;;
    esac
fi
