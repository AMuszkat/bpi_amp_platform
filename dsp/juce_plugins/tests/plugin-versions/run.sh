#!/bin/bash
# Tests tools/plugin-versions.sh (juce_plugins/README.md, "Versions and source identity") on a
# scratch git repository with the layout it expects and the real plugin-versions.sh, common.sh
# and cmake/SourceId.cmake copied in: check's verdict, output and exit status for each kind of
# change, exit status 2 for a bad ref, a git failure or a declaration that cannot be read (at
# HEAD or at the base), and list's identities against the README's git command, -dirty exactly
# when a plugin's inputs changed. Part of rung 1 (tools/test.sh unit).
#
#     dsp/juce_plugins/tests/plugin-versions/run.sh
#
# Needs git 2.15 or later (--no-optional-locks, which plugin-versions.sh and SourceId.cmake
# use; the test itself needs nothing later: no `git init -b`, 2.28, no GIT_CONFIG_GLOBAL, 2.32),
# bash, coreutils and cmake, and takes seconds. Run on git 2.39 (the development container's)
# and 2.53. Runs in the development container when Podman is installed (tools/container.sh;
# BPI_CONTAINER=0 runs it natively). The scratch repository, in a temporary directory, is
# removed at the end.
#
# It touches no repository but its own, wherever the temporary directory is (a TMPDIR inside a
# repository included) and whatever git's version and configuration: every git command, the
# tool's too, runs with GIT_CEILING_DIRECTORIES at the temporary directory, so git never
# searches above it, and the test stops unless git finds the scratch repository exactly where
# it made it. HOME and XDG_CONFIG_HOME point into the temporary directory, so no user
# configuration, global ignore or attributes file is read (with any git), and the variables
# that point git at another repository or configuration are unset.
#
# The scratch layout (dsp/juce_plugins/ of a repository, as in this one):
#   alpha/   0.1.0, SHARED fir           beta/   0.9.0, SHARED fir other
#   gamma/   1.2.3, no SHARED            notes/  a CMakeLists.txt without speaker_add_plugin
#   shared/fir, shared/other, shared/unused (declared by none)
#   cmake/SourceId.cmake, tools/{plugin-versions.sh,common.sh}, JUCE (a gitlink, not populated)
#   .gitignore: build-podman/
# and origin/develop at the first commit.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGINS="$(cd "$HERE/../.." && pwd)"
ROOT="$(cd "$PLUGINS/../.." && pwd)"
. "$ROOT/tools/container.sh"
container_or_native "$0" "$@"

for tool in git cmake; do
    command -v $tool > /dev/null || { echo "plugin-versions test: FAILED: no $tool" >&2; exit 1; }
done

fatal() { echo "plugin-versions test: FAILED: $*" >&2; exit 1; }

# The temporary directory, by its real path (git compares the ceiling with real paths). Two
# steps, so that a failed mktemp can never leave T empty or the current directory.
T="$(mktemp -d)" && T="$(cd "$T" && pwd -P)" && [ -n "$T" ] || fatal "no temporary directory"
trap 'rm -rf "$T"' EXIT
R="$T/repo"
P="$R/dsp/juce_plugins"
PV="$P/tools/plugin-versions.sh"
PV_SCRATCH="$PV"
g() { git -C "$R" "$@"; }

# A git of its own (see above), for the test's commands and the tool's alike: the variables
# that name another repository, object store or configuration unset (git's list of them, and
# every GIT_CONFIG_*), no system configuration or attributes, a HOME without configuration, and
# no search for a repository above T.
unset $(compgen -e | grep '^GIT_CONFIG')
local_vars="$(git rev-parse --local-env-vars)" || fatal "git rev-parse --local-env-vars failed"
unset $local_vars
mkdir -p "$T/home/.config" || fatal "cannot make $T/home"
export HOME="$T/home" XDG_CONFIG_HOME="$T/home/.config"
export GIT_CONFIG_NOSYSTEM=1 GIT_ATTR_NOSYSTEM=1 GIT_CEILING_DIRECTORIES="$T"
export GIT_AUTHOR_NAME=test GIT_AUTHOR_EMAIL=user@example.com
export GIT_COMMITTER_NAME=test GIT_COMMITTER_EMAIL=user@example.com

# init_repo <dir>: a repository of its own at <dir> (an absolute real path), on a branch
# develop with no commit, with any git (HEAD set by hand: `git init -b` is git 2.28). Stops the
# test unless git then finds exactly that repository there, so that no later `git -C` can
# reach another.
init_repo() {
    git init -q "$1" && git -C "$1" symbolic-ref HEAD refs/heads/develop \
        && [ "$(git -C "$1" rev-parse --show-toplevel)" = "$1" ] \
        || fatal "cannot make a git repository of its own in $1"
}

# not_in_repo <dir>: true when git finds no repository at <dir> (the cases "outside a
# repository" below; with the ceiling at T, nothing above T counts).
not_in_repo() { ! git -C "$1" rev-parse --git-dir > /dev/null 2>&1; }

passed=0
failed=0
ok() { passed=$((passed + 1)); echo "  ok    $*"; }
not_ok() { failed=$((failed + 1)); echo "  FAIL  $*"; }

# ---------------------------------------------------------------------------------------------
# The scratch repository.

JUCE_PIN=1111111111111111111111111111111111111111
JUCE_PIN2=2222222222222222222222222222222222222222
declare -A VERSION_OF=([alpha]=0.1.0 [beta]=0.9.0 [gamma]=1.2.3 [delta]=0.0.1)
declare -A SHARED_OF=([alpha]="fir" [beta]="fir other" [gamma]="" [delta]="")

# write_plugin <name> <version> [<shared>...]: its CMakeLists.txt and one source.
write_plugin() {
    local name="$1" version="$2" code="${1^}"
    shift 2
    mkdir -p "$P/$name"
    {
        echo "cmake_minimum_required(VERSION 3.22)"
        echo "project(${name^^} VERSION $version)"
        echo 'find_package(JUCE CONFIG REQUIRED)'
        echo 'include(${CMAKE_CURRENT_LIST_DIR}/../cmake/SpeakerPlugin.cmake)'
        echo
        echo "speaker_add_plugin($name"
        echo "    CODE    ${code:0:4}"
        echo "    INPUTS  1"
        echo "    OUTPUTS 1"
        echo "    SOURCES ${name^}.cpp"
        [ $# = 0 ] || echo "    SHARED  $*"
        echo "    MODULES juce_dsp)"
    } > "$P/$name/CMakeLists.txt"
    echo "// $name" > "$P/$name/${name^}.cpp"
}

# set_version <plugin> <text>: replaces the VERSION of its project() call by <text>.
set_version() { sed -i "s/^project(\([A-Z]*\) VERSION [^)]*)/project(\1 VERSION $2)/" "$P/$1/CMakeLists.txt"; }

mkdir -p "$P/tools" "$P/cmake" "$P/shared"/{fir,other,unused} "$P/notes" "$P/JUCE"
cp "$PLUGINS/tools/plugin-versions.sh" "$PLUGINS/tools/common.sh" "$P/tools/"
cp "$PLUGINS/cmake/SourceId.cmake" "$P/cmake/"
write_plugin alpha 0.1.0 fir
write_plugin beta 0.9.0 fir other
write_plugin gamma 1.2.3
printf 'cmake_minimum_required(VERSION 3.22)\nproject(NOTES VERSION 9.9.9)\n' > "$P/notes/CMakeLists.txt"
for name in fir other unused; do echo "// $name" > "$P/shared/$name/${name^}.h"; done
echo "build-podman/" > "$R/.gitignore"
echo "scratch" > "$R/README.md"
init_repo "$R"
g add -A
g update-index --add --cacheinfo "160000,$JUCE_PIN,dsp/juce_plugins/JUCE"
g commit -q -m base
BASE="$(g rev-parse HEAD)"
g update-ref refs/remotes/origin/develop "$BASE"

# Back to the base commit, every change undone (the JUCE folder stays, empty, as an
# uninitialised submodule).
reset() {
    g checkout -q --force develop
    g reset -q --hard "$BASE"
    g clean -q -f -d -x
    g branch -q -D side 2> /dev/null || true
    PV="$PV_SCRATCH"
}

# ---------------------------------------------------------------------------------------------
# check

# expect_check <what> <status> <expected stdout, one line per plugin> [REF]
expect_check() {
    local what="$1" want_status="$2" want="$3" out status
    shift 3
    out="$("$PV" check "$@" 2> "$T/stderr")"
    status=$?
    if [ "$status" = "$want_status" ] && [ "$out" = "$want" ]; then
        ok "check: $what: exit $status"
    else
        not_ok "check: $what: exit $status, expected $want_status"
        diff <(echo "$want") <(echo "$out") | sed 's/^/          /'
        sed 's/^/          stderr: /' "$T/stderr"
    fi
}

# expect_check_error <what> <a phrase of the message> [REF]: exit 2, the phrase on stderr.
expect_check_error() {
    local what="$1" phrase="$2" status
    shift 2
    "$PV" check "$@" > "$T/stdout" 2> "$T/stderr"
    status=$?
    if [ "$status" = 2 ] && grep -qF -- "$phrase" "$T/stderr"; then
        ok "check: $what: exit 2, '$(grep -m 1 -F -- "$phrase" "$T/stderr")'"
    else
        not_ok "check: $what: exit $status, expected 2 saying '$phrase'"
        sed 's/^/          stdout: /' "$T/stdout"
        sed 's/^/          stderr: /' "$T/stderr"
    fi
}

UNCHANGED="alpha unchanged 0.1.0
beta unchanged 0.9.0
gamma unchanged 1.2.3"

echo "=== plugin-versions test: check"

reset
expect_check "nothing changed" 0 "$UNCHANGED"
if grep -qx "plugin-versions.sh check: base ${BASE:0:12} (merge-base of HEAD and origin/develop)" "$T/stderr"; then
    ok "check: names its base, the merge base with origin/develop by default"
else
    not_ok "check: the base line on stderr: $(cat "$T/stderr")"
fi

reset
echo "more" >> "$R/README.md"
g commit -q -a -m "outside every plugin"
expect_check "a commit outside every plugin's sources" 0 "$UNCHANGED"

for how in unstaged staged committed; do
    reset
    echo "// changed" >> "$P/alpha/Alpha.cpp"
    [ $how = unstaged ] || g add -A
    [ $how != committed ] || g commit -q -m "alpha changed"
    expect_check "alpha's source changed ($how), no new version" 1 "alpha changed, NOT raised 0.1.0 -> 0.1.0
beta unchanged 0.9.0
gamma unchanged 1.2.3"
done

reset
echo "// new" > "$P/alpha/New.h"
expect_check "an untracked file in alpha" 1 "alpha changed, NOT raised 0.1.0 -> 0.1.0
beta unchanged 0.9.0
gamma unchanged 1.2.3"

reset
mkdir -p "$P/alpha/build-podman"
echo "object" > "$P/alpha/build-podman/Alpha.o"
expect_check "an ignored file in alpha" 0 "$UNCHANGED"

reset
rm "$P/alpha/Alpha.cpp"
expect_check "a file of alpha deleted" 1 "alpha changed, NOT raised 0.1.0 -> 0.1.0
beta unchanged 0.9.0
gamma unchanged 1.2.3"

reset
echo "// changed" >> "$P/shared/fir/Fir.h"
expect_check "shared/fir changed: alpha and beta declare it" 1 "alpha changed, NOT raised 0.1.0 -> 0.1.0
beta changed, NOT raised 0.9.0 -> 0.9.0
gamma unchanged 1.2.3"

reset
echo "// new" > "$P/shared/fir/More.h"
expect_check "an untracked file in shared/fir" 1 "alpha changed, NOT raised 0.1.0 -> 0.1.0
beta changed, NOT raised 0.9.0 -> 0.9.0
gamma unchanged 1.2.3"

reset
echo "// changed" >> "$P/shared/other/Other.h"
expect_check "shared/other changed: only beta declares it" 1 "alpha unchanged 0.1.0
beta changed, NOT raised 0.9.0 -> 0.9.0
gamma unchanged 1.2.3"

reset
echo "// changed" >> "$P/shared/unused/Unused.h"
expect_check "shared/unused changed: no plugin declares it" 0 "$UNCHANGED"

reset
echo "# changed" >> "$P/cmake/SourceId.cmake"
g update-index --cacheinfo "160000,$JUCE_PIN2,dsp/juce_plugins/JUCE"
expect_check "cmake/ and the JUCE pin changed: no version to raise" 0 "$UNCHANGED"

reset
echo "// changed" >> "$P/alpha/Alpha.cpp"
set_version alpha 0.1.1
expect_check "alpha changed and raised its patch" 0 "alpha changed, raised 0.1.0 -> 0.1.1
beta unchanged 0.9.0
gamma unchanged 1.2.3"

reset
echo "// changed" >> "$P/shared/fir/Fir.h"
set_version alpha 0.2.0
set_version beta 0.10.0
expect_check "shared/fir changed, both raised (0.9.0 -> 0.10.0 is numeric)" 0 "alpha changed, raised 0.1.0 -> 0.2.0
beta changed, raised 0.9.0 -> 0.10.0
gamma unchanged 1.2.3"

reset
echo "// changed" >> "$P/shared/fir/Fir.h"
set_version alpha 0.1.1
expect_check "shared/fir changed, only alpha raised" 1 "alpha changed, raised 0.1.0 -> 0.1.1
beta changed, NOT raised 0.9.0 -> 0.9.0
gamma unchanged 1.2.3"

reset
echo "// changed" >> "$P/gamma/Gamma.cpp"
set_version gamma 1.2.2
expect_check "gamma changed and lowered its version" 1 "alpha unchanged 0.1.0
beta unchanged 0.9.0
gamma changed, NOT raised 1.2.3 -> 1.2.2"

reset
set_version gamma 2.0.0
expect_check "only gamma's version raised (its CMakeLists.txt changed)" 0 "alpha unchanged 0.1.0
beta unchanged 0.9.0
gamma changed, raised 1.2.3 -> 2.0.0"

for how in untracked committed; do
    reset
    write_plugin delta 0.0.1 fir
    [ $how = untracked ] || { g add -A && g commit -q -m "delta"; }
    expect_check "a new plugin, delta ($how)" 0 "alpha unchanged 0.1.0
beta unchanged 0.9.0
delta new 0.0.1
gamma unchanged 1.2.3"
done

reset
echo "// changed" >> "$P/alpha/Alpha.cpp"
g commit -q -a -m "alpha changed"
expect_check "REF is HEAD: the committed change is at the base" 0 "$UNCHANGED" HEAD
expect_check "REF is the first commit" 1 "alpha changed, NOT raised 0.1.0 -> 0.1.0
beta unchanged 0.9.0
gamma unchanged 1.2.3" "$BASE"

reset
expect_check_error "a ref that does not exist" "no such commit: no/such/ref" no/such/ref
expect_check_error "too many arguments" "usage:" origin/develop extra

# The errors below once passed unnoticed: called as `cmd_check ... || exit 1`, the script ran
# its check without errexit, so a declaration it could not read became an empty one.
#
# An unreadable declaration at the base: a side branch whose first commit gives alpha a
# version of two numbers, and whose second fixes it and changes alpha (once: "alpha changed,
# NOT raised  -> 0.1.0", exit 1).
reset
g checkout -q -b side
set_version alpha 0.1
g commit -q -a -m "alpha's version unreadable"
BAD="$(g rev-parse HEAD)"
set_version alpha 0.1.0
echo "// changed" >> "$P/alpha/Alpha.cpp"
g commit -q -a -m "fixed"
expect_check_error "alpha's declaration unreadable at the base" \
    "cannot read the declaration in alpha/CMakeLists.txt at the base" "$BAD"

# Unreadable in the working tree.
reset
set_version alpha 0.1
expect_check_error "alpha's declaration unreadable in the working tree" \
    "cannot read the declaration in alpha/CMakeLists.txt" HEAD

# Unreadable at HEAD and at the base, so that beta's own folder is unchanged and only a shared
# folder it declares changed (once: beta's SHARED dropped, "beta unchanged", exit 0).
reset
g checkout -q -b side
set_version beta 0.9
g commit -q -a -m "beta's version unreadable"
echo "// changed" >> "$P/shared/other/Other.h"
expect_check_error "beta's declaration unreadable, only shared/other (beta's) changed" \
    "cannot read the declaration in beta/CMakeLists.txt" HEAD

reset
sed -i 's/^project(BETA VERSION 0.9.0)/project(BETA)/' "$P/beta/CMakeLists.txt"
expect_check_error "beta's project() without a VERSION" \
    "cannot read the declaration in beta/CMakeLists.txt"

# A git failure inside the loop: alpha's tree at the base missing, in a copy of the repository
# (once: "alpha new", exit 0). The script must exit 2, naming alpha, whichever git command
# notices first (now the ls-tree -r of the base's trees, before git diff; before it, the ls-tree
# of alpha/CMakeLists.txt, git diff having gone on and exited 1).
reset
cp -a "$R" "$T/broken"
tree="$(g rev-parse "$BASE:dsp/juce_plugins/alpha")"
rm "$T/broken/.git/objects/${tree:0:2}/${tree:2}"
echo "// changed" >> "$T/broken/dsp/juce_plugins/alpha/Alpha.cpp"
PV="$T/broken/dsp/juce_plugins/tools/plugin-versions.sh"
expect_check_error "a git command failing for alpha" " for alpha"
rm -rf "$T/broken"

# The tree of shared/other, which only beta declares, missing at the base, and nothing changed:
# git diff --quiet then says nothing and exits 0 (git 2.39 and 2.53), so the base's trees are
# read on their own (once: "beta unchanged", exit 0, without a word of error).
reset
cp -a "$R" "$T/broken"
tree="$(g rev-parse "$BASE:dsp/juce_plugins/shared/other")"
rm "$T/broken/.git/objects/${tree:0:2}/${tree:2}"
PV="$T/broken/dsp/juce_plugins/tools/plugin-versions.sh"
expect_check_error "shared/other's tree missing at the base, nothing changed" \
    "cannot read the base's trees for beta"
rm -rf "$T/broken"

# Outside a repository: git finds none in the copy (and, with the ceiling, none above T).
reset
cp -a "$P" "$T/nogit"
PV="$T/nogit/tools/plugin-versions.sh"
if not_in_repo "$T/nogit"; then
    expect_check_error "outside a git repository" "is not in a git repository"
else
    not_ok "check: outside a git repository: git finds one at $T/nogit"
fi

# ---------------------------------------------------------------------------------------------
# list

# The README's command, from the repository root, over HEAD's trees; a folder that is not in
# HEAD counts as the empty tree.
documented_id() {
    local p="$1" revs=() name i
    revs=("HEAD:dsp/juce_plugins/$p")
    for name in ${SHARED_OF[$p]}; do revs+=("HEAD:dsp/juce_plugins/shared/$name"); done
    revs+=(HEAD:dsp/juce_plugins/cmake HEAD:dsp/juce_plugins/JUCE)
    for i in "${!revs[@]}"; do
        g rev-parse --verify --quiet "${revs[i]}" > /dev/null || revs[i]="$(g hash-object -t tree /dev/null)"
    done
    (cd "$R" && git rev-parse "${revs[@]}" | git hash-object --stdin | cut -c1-12)
}

# expect_list <what> <plugins> <dirty plugins>: list prints exactly one line per plugin, sorted,
# "<plugin> <version> <documented id>", "-dirty" appended for the dirty ones and only those.
expect_list() {
    local what="$1" plugins="$2" dirty=" $3 " p want="" out status
    for p in $plugins; do
        want+="$p ${VERSION_OF[$p]} $(documented_id "$p")"
        case "$dirty" in *" $p "*) want+="-dirty" ;; esac
        want+=$'\n'
    done
    want="${want%$'\n'}"
    out="$("$PV" list 2> "$T/stderr")"
    status=$?
    if [ $status = 0 ] && [ "$out" = "$want" ]; then
        ok "list: $what: $(echo "$out" | awk '{ printf "%s%s %s", (NR > 1 ? ", " : ""), $1, $3 }')"
    else
        not_ok "list: $what: exit $status"
        diff <(echo "$want") <(echo "$out") | sed 's/^/          /'
        sed 's/^/          stderr: /' "$T/stderr"
    fi
}

echo "=== plugin-versions test: list"

reset
expect_list "a clean tree" "alpha beta gamma" ""
before="$("$PV" list)"

reset
echo "more" >> "$R/README.md"
g commit -q -a -m "outside every plugin"
expect_list "after a commit outside every plugin" "alpha beta gamma" ""
if [ "$("$PV" list)" = "$before" ]; then
    ok "list: the identities follow the tree, not the commit"
else
    not_ok "list: a commit outside every plugin changed an identity"
fi

reset
echo "// changed" >> "$P/alpha/Alpha.cpp"
expect_list "alpha's source changed" "alpha beta gamma" "alpha"
g add -A
expect_list "alpha's source changed, staged" "alpha beta gamma" "alpha"

reset
echo "// new" > "$P/alpha/New.h"
expect_list "an untracked file in alpha" "alpha beta gamma" "alpha"

reset
mkdir -p "$P/alpha/build-podman"
echo "object" > "$P/alpha/build-podman/Alpha.o"
expect_list "an ignored file in alpha" "alpha beta gamma" ""

reset
echo "// changed" >> "$P/shared/fir/Fir.h"
expect_list "shared/fir changed" "alpha beta gamma" "alpha beta"

reset
echo "// changed" >> "$P/shared/other/Other.h"
expect_list "shared/other changed" "alpha beta gamma" "beta"

reset
echo "// changed" >> "$P/shared/unused/Unused.h"
expect_list "shared/unused changed" "alpha beta gamma" ""

reset
echo "# new" > "$P/cmake/More.cmake"
expect_list "an untracked file in cmake/" "alpha beta gamma" "alpha beta gamma"

reset
echo "#!/bin/bash" > "$P/tools/more.sh"
echo "# changed" >> "$P/notes/CMakeLists.txt"
expect_list "tools/ and a folder that is no plugin changed" "alpha beta gamma" ""

reset
g update-index --cacheinfo "160000,$JUCE_PIN2,dsp/juce_plugins/JUCE"
expect_list "the JUCE pin moved, staged" "alpha beta gamma" "alpha beta gamma"
g commit -q -m "JUCE moved"
expect_list "the JUCE pin moved, committed" "alpha beta gamma" ""
after="$("$PV" list | awk '{ print $3 }')"
if [ -z "$(comm -12 <(echo "$after" | sort) <(echo "$before" | awk '{ print $3 }' | sort))" ] \
   && [ "$(echo "$after" | sort -u | wc -l)" = 3 ]; then
    ok "list: a new JUCE pin gave every plugin a new identity, still one each"
else
    not_ok "list: a new JUCE pin: $(echo "$after" | tr '\n' ' ')"
fi

reset
write_plugin delta 0.0.1
expect_list "a new plugin, delta, untracked (its folder the empty tree)" "alpha beta delta gamma" "delta"
g add -A
g commit -q -m "delta"
expect_list "delta committed" "alpha beta delta gamma" ""

# Outside a repository, and in one without a commit: "unknown".
reset
rm -rf "$T/nogit"
cp -a "$P" "$T/nogit"
out="$("$T/nogit/tools/plugin-versions.sh" list 2>&1)"
if ! not_in_repo "$T/nogit"; then
    not_ok "list: outside a repository: git finds one at $T/nogit"
elif [ "$out" = "alpha 0.1.0 unknown
beta 0.9.0 unknown
gamma 1.2.3 unknown" ]; then
    ok "list: outside a repository: every identity unknown"
else
    not_ok "list: outside a repository: $(echo "$out" | tr '\n' ' ')"
fi
init_repo "$T/nogit"
out="$("$T/nogit/tools/plugin-versions.sh" list 2>&1)"
if [ "$(echo "$out" | awk '{ print $3 }' | sort -u)" = unknown ] && [ "$(echo "$out" | wc -l)" = 3 ]; then
    ok "list: in a repository without a commit: every identity unknown"
else
    not_ok "list: in a repository without a commit: $(echo "$out" | tr '\n' ' ')"
fi

reset
set_version alpha 0.1
"$PV" list > /dev/null 2> "$T/stderr"
status=$?
if [ $status = 2 ] && grep -qF "cannot read the version and identity of alpha" "$T/stderr"; then
    ok "list: alpha's declaration unreadable: exit 2"
else
    not_ok "list: alpha's declaration unreadable: exit $status"
fi

echo "plugin-versions test: $passed passed, $failed failed"
[ $failed = 0 ]
