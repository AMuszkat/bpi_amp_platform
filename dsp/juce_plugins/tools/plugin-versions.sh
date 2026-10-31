#!/bin/bash
# The plugins' versions and source identities, and the check of the version rule
# (juce_plugins/README.md, "Versions and source identity").
#
#     plugin-versions.sh list           "<plugin> <version> <source-id>", one line per plugin,
#                                       sorted by name: what a build made now would embed
#     plugin-versions.sh check [REF]    every plugin whose sources differ between
#                                       `git merge-base HEAD REF` (REF defaults to
#                                       origin/develop) and the working tree must have a higher
#                                       version than at that base
#
# A plugin's sources, for check, are its own folder and the shared folders it declares
# (SHARED): tracked changes, staged or not, and untracked files that are not ignored. Not
# cmake/ and not the JUCE pin, which are inputs of the source identity but not reasons to
# raise a version. A plugin absent at the base passes.
#
# Exit status: 0 (check: every changed plugin raised its version), 1 (check: one did not),
# 2 (a usage or git error: no such ref, not a repository). Needs git, coreutils and cmake;
# runs from any directory, natively or in the development container. The version and the
# identity come from cmake/SourceId.cmake, the one implementation the build uses too.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/common.sh"
SOURCE_ID="$PLUGINS_DIR/cmake/SourceId.cmake"
usage="usage: $(basename "$0") list | check [REF]"

die() { echo "plugin-versions.sh: $*" >&2; exit 2; }

command -v cmake > /dev/null || die "cmake is needed"
command -v git > /dev/null || die "git is needed"

git_() { git --no-optional-locks -C "$PLUGINS_DIR" "$@"; }

plugins() { list_plugins | LC_ALL=C sort; }

# "<version>[ <shared>...]" from a CMakeLists.txt.
declaration() {
    cmake -DMODE=declaration -DFILE="$1" -P "$SOURCE_ID" || die "cannot read the declaration in $2"
}

# 0 when version $1 is greater than $2, both MAJOR.MINOR.PATCH, compared numerically.
version_gt() {
    local a b i
    IFS=. read -r -a a <<< "$1"
    IFS=. read -r -a b <<< "$2"
    for i in 0 1 2; do
        if ((10#${a[i]} > 10#${b[i]})); then return 0; fi
        if ((10#${a[i]} < 10#${b[i]})); then return 1; fi
    done
    return 1
}

cmd_list() {
    local p line
    while read -r p; do
        line="$(cmake -DMODE=identity -DPLUGIN_DIR="$PLUGINS_DIR/$p" -P "$SOURCE_ID")" \
            || die "cannot read the version and identity of $p"
        echo "$p $line"
    done < <(plugins)
}

cmd_check() {
    local ref="${1:-origin/develop}" base p decl new old shared paths name rc failed=0

    git_ rev-parse --git-dir > /dev/null 2>&1 || die "$PLUGINS_DIR is not in a git repository"
    git_ rev-parse --verify --quiet "$ref^{commit}" > /dev/null || die "no such commit: $ref"
    base="$(git_ merge-base HEAD "$ref")" || die "HEAD and $ref have no merge base"
    echo "plugin-versions.sh check: base $(git_ rev-parse --short=12 "$base") (merge-base of HEAD and $ref)" >&2

    TMP="$(mktemp)"
    trap 'rm -f "$TMP"' EXIT

    while read -r p; do
        decl="$(declaration "$PLUGINS_DIR/$p/CMakeLists.txt" "$p/CMakeLists.txt")"
        read -r new shared <<< "$decl"
        paths=("./$p")
        for name in $shared; do paths+=("./shared/$name"); done

        rc=0
        git_ diff --quiet --no-ext-diff "$base" -- "${paths[@]}" || rc=$?
        [ $rc -le 1 ] || die "git diff failed for $p"
        if [ $rc = 0 ] && [ -z "$(git_ ls-files --others --exclude-standard -- "${paths[@]}")" ]; then
            echo "$p unchanged $new"
            continue
        fi

        if ! git_ cat-file -e "$base:./$p/CMakeLists.txt" 2> /dev/null; then
            echo "$p new $new"
            continue
        fi
        git_ show "$base:./$p/CMakeLists.txt" > "$TMP" || die "cannot read $p/CMakeLists.txt at the base"
        decl="$(declaration "$TMP" "$p/CMakeLists.txt at the base")"
        old="${decl%% *}"

        if version_gt "$new" "$old"; then
            echo "$p changed, raised $old -> $new"
        else
            echo "$p changed, NOT raised $old -> $new"
            failed=1
        fi
    done < <(plugins)

    return $failed
}

case "${1:-}" in
    list)
        [ $# = 1 ] || die "$usage"
        cmd_list
        ;;
    check)
        [ $# -le 2 ] || die "$usage"
        cmd_check "${2:-origin/develop}" || exit 1
        ;;
    *)
        die "$usage"
        ;;
esac
