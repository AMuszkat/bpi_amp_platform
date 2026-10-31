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
# 2 (a usage, git or parse error: no such ref, not a repository, a git command that failed or
# a tree of the base it could not read, a CMakeLists.txt whose version or SHARED cannot be
# read, at HEAD or at the base). Needs git 2.15 or later (--no-optional-locks), bash,
# coreutils and cmake; runs from any directory, natively or in the development container. The
# version and the identity come from cmake/SourceId.cmake, the one implementation the build
# uses too. Tested by tests/plugin-versions/run.sh.
#
# errexit is not relied on: bash ignores it throughout a function called from a || or && list
# (as cmd_check is, below), and a die in a $(...) exits only that subshell. So every command
# that can fail handles its failure itself: `|| die` in this shell, `|| exit 2` after a
# substitution whose subshell died (and said why).
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/common.sh"
SOURCE_ID="$PLUGINS_DIR/cmake/SourceId.cmake"
usage="usage: $(basename "$0") list | check [REF]"

# Says why and exits 2: the script, or only the subshell of a $(...) it runs in, whose caller
# then adds `|| exit 2`.
die() { echo "plugin-versions.sh: $*" >&2; exit 2; }

command -v cmake > /dev/null || die "cmake is needed"
command -v git > /dev/null || die "git is needed"

git_() { git --no-optional-locks -C "$PLUGINS_DIR" "$@"; }

# The plugin folders' names, sorted, one per line (a folder name has no whitespace: it is the
# CMake target's name).
plugins() { list_plugins | LC_ALL=C sort; }

# "<version>[ <shared>...]" from a CMakeLists.txt. Run in $(...): `|| exit 2` after it.
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
    local names p line
    names="$(plugins)" || die "cannot list the plugins in $PLUGINS_DIR"
    for p in $names; do
        line="$(cmake -DMODE=identity -DPLUGIN_DIR="$PLUGINS_DIR/$p" -P "$SOURCE_ID")" \
            || die "cannot read the version and identity of $p"
        echo "$p $line"
    done
}

# Returns 0 or 1 (the version rule); exits 2 on any other failure.
cmd_check() {
    local ref="$1" base short names p decl new old shared paths name rc untracked at_base failed=0

    git_ rev-parse --git-dir > /dev/null 2>&1 || die "$PLUGINS_DIR is not in a git repository"
    git_ rev-parse --verify --quiet "$ref^{commit}" > /dev/null || die "no such commit: $ref"
    base="$(git_ merge-base HEAD "$ref")" || die "HEAD and $ref have no merge base"
    short="$(git_ rev-parse --short=12 "$base")" || die "cannot read the merge base $base"
    echo "plugin-versions.sh check: base $short (merge-base of HEAD and $ref)" >&2

    TMP="$(mktemp)" || die "cannot make a temporary file"
    trap 'rm -f "$TMP"' EXIT
    names="$(plugins)" || die "cannot list the plugins in $PLUGINS_DIR"

    for p in $names; do
        decl="$(declaration "$PLUGINS_DIR/$p/CMakeLists.txt" "$p/CMakeLists.txt")" || exit 2
        read -r new shared <<< "$decl"
        paths=("./$p")
        for name in $shared; do paths+=("./shared/$name"); done

        # Every tree under the paths at the base, read first: git diff --quiet reads only what
        # it needs, and with a tree missing it can print nothing and exit 0 or 1 (git 2.39 and
        # 2.53), a verdict after an error. ls-tree prints nothing for a path the base does not
        # have. A blob it cannot read fails git diff itself (exit 128).
        git_ ls-tree -r "$base" -- "${paths[@]}" > /dev/null || die "cannot read the base's trees for $p"
        rc=0
        git_ diff --quiet --no-ext-diff "$base" -- "${paths[@]}" || rc=$?
        [ $rc -le 1 ] || die "git diff failed for $p"
        untracked="$(git_ ls-files --others --exclude-standard -- "${paths[@]}")" \
            || die "git ls-files failed for $p"
        if [ $rc = 0 ] && [ -z "$untracked" ]; then
            echo "$p unchanged $new"
            continue
        fi

        # ls-tree prints nothing for a path the base does not have (a new plugin), and fails
        # only when git does.
        at_base="$(git_ ls-tree --name-only "$base" -- "./$p/CMakeLists.txt")" \
            || die "cannot read the base's tree for $p"
        if [ -z "$at_base" ]; then
            echo "$p new $new"
            continue
        fi
        git_ show "$base:./$p/CMakeLists.txt" > "$TMP" || die "cannot read $p/CMakeLists.txt at the base"
        decl="$(declaration "$TMP" "$p/CMakeLists.txt at the base")" || exit 2
        old="${decl%% *}"

        if version_gt "$new" "$old"; then
            echo "$p changed, raised $old -> $new"
        else
            echo "$p changed, NOT raised $old -> $new"
            failed=1
        fi
    done

    return $failed
}

case "${1:-}" in
    list)
        [ $# = 1 ] || die "$usage"
        cmd_list
        ;;
    check)
        [ $# -le 2 ] || die "$usage"
        # cmd_check exits 2 itself on an error, so its status here is the rule's, 0 or 1.
        cmd_check "${2:-origin/develop}" || exit 1
        ;;
    *)
        die "$usage"
        ;;
esac
