# shellcheck shell=bash
# install-lib.sh: what firmware/setup.sh and firmware/speakerd-setup.sh share. Sourced, not run.
# See firmware/CLAUDE.md, "speakerd on a board" and "The whole architecture".
#
#   logging            info, ok, warn, die, loud (a warning nobody can miss)
#   the board          board_target: [user@]host from BOARD_USER / BOARD_HOST, in the
#                      environment or in firmware/local.mk (read, never executed)
#   the bundle         make_bundle: what the installers copy to a board, from the build tree
#   the remote mode    remote_run: copy one bundle to the board, run an installer there
#
# The bundle is a folder:
#
#     VERSION                 id, git commit, dirty or not, when, architecture, contents
#     bin/speakerd            the program (with artifacts only)
#     lv2/<target>.lv2/       the plugin bundles speakerd is given (with artifacts only)
#     firmware/...            the installers and the files they install, ampd among them
#     extra/...               files named on the command line (--factory)
#
# Nothing here builds anything: make_bundle says which tools/build.sh command makes what is
# missing.

FIRMWARE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

c_blue=$'\033[1;34m'; c_yellow=$'\033[1;33m'; c_red=$'\033[1;31m'; c_green=$'\033[1;32m'; c_off=$'\033[0m'
[ -t 1 ] && [ -t 2 ] || { c_blue=""; c_yellow=""; c_red=""; c_green=""; c_off=""; }
info()  { printf '%s==>%s %s\n' "$c_blue"   "$c_off" "$*"; }
ok()    { printf '%s ok%s %s\n' "$c_green"  "$c_off" "$*"; }
warn()  { printf '%swarn%s %s\n' "$c_yellow" "$c_off" "$*" >&2; }
die()   { printf '%serr%s  %s\n' "$c_red"    "$c_off" "$*" >&2; exit 1; }
loud() {
    local line
    printf '%s%s%s\n' "$c_red" "*****************************************************************************" "$c_off" >&2
    for line in "$@"; do printf '%s*** %s%s\n' "$c_red" "$line" "$c_off" >&2; done
    printf '%s%s%s\n' "$c_red" "*****************************************************************************" "$c_off" >&2
}

# A command line as it would be typed, for --dry-run and messages.
shown() { printf '%q ' "$@"; }

# ---------------------------------------------------------------------------------------------
# The board: [user@]host, never written into a tracked file

# The value of KEY in firmware/local.mk (`KEY := value`, `KEY = value` or `KEY ?= value`), if
# the file exists. The file is read, not executed.
local_mk_value() {
    local key="$1" file="$FIRMWARE_DIR/local.mk"
    [ -f "$file" ] || return 0
    sed -n -E "s/^[[:space:]]*${key}[[:space:]]*[:?]?=[[:space:]]*([^[:space:]#]+).*/\1/p" "$file" | tail -n 1
}

# user@host of the board: BOARD_USER and BOARD_HOST from the environment, else from
# firmware/local.mk, else the placeholders user and bpi-amp.local.
board_target() {
    local host="${BOARD_HOST:-$(local_mk_value BOARD_HOST)}" user="${BOARD_USER:-$(local_mk_value BOARD_USER)}"
    echo "${user:-user}@${host:-bpi-amp.local}"
}

# ---------------------------------------------------------------------------------------------
# ELF: the installer checks that speakerd was built for the machine it is installed on

elf_machine() {   # $1 = file: x86_64, aarch64, or unknown
    local magic machine
    magic="$(head -c 4 "$1" 2> /dev/null | od -An -c | tr -d ' ')"
    [ "$magic" = '177ELF' ] || { echo unknown; return; }
    machine="$(od -An -tu2 -j18 -N2 "$1" | tr -d ' ')"
    case "$machine" in 62) echo x86_64 ;; 183) echo aarch64 ;; *) echo "unknown" ;; esac
}

# ---------------------------------------------------------------------------------------------
# The bundle

# The plugin bundles a speaker gets by default: <plugin>:<build>. Only loudness has a
# headless build; crossover and firConv are complete builds, which speakerd hosts too.
DEFAULT_PLUGINS=(loudness:headless crossover:complete firConv:complete)

# Where tools/build.sh puts a build: $1 = native|aarch64, $2 = speakerd or <plugin>:<build>.
artifact_path() {
    local arch="$1" what="$2" root="${BUILD_ROOT:-$(dirname "$FIRMWARE_DIR")/build}" plugin kind target
    [ "$arch" = aarch64 ] && root="$root/aarch64"
    if [ "$what" = speakerd ]; then
        echo "$root/speakerd/speakerd_artefacts/Release/speakerd"
        return
    fi
    plugin="${what%%:*}"; kind="${what#*:}"; [ "$kind" = "$what" ] && kind=complete
    target="$plugin"; [ "$kind" = complete ] || target="${plugin}_$kind"
    echo "$root/$plugin/${target}_artefacts/Release/LV2/$target.lv2"
}

# The tools/build.sh command that makes an artifact.
build_command() {   # $1 = native|aarch64, $2 = speakerd or <plugin>:<build>
    local arch="$1" what="$2" plugin kind
    plugin="${what%%:*}"; kind="${what#*:}"; [ "$kind" = "$what" ] && kind=""
    if [ "$arch" = aarch64 ]; then echo "tools/build.sh $plugin aarch64${kind:+ $kind}"
    else echo "tools/build.sh $plugin${kind:+ $kind}"; fi
}

# The id of a bundle's contents: a hash of every file under bin/ and lv2/, names included.
contents_hash() {   # $1 = bundle folder
    (cd "$1" && find bin lv2 -type f -print0 2> /dev/null | LC_ALL=C sort -z | xargs -0 -r sha256sum | sha256sum | cut -c1-8)
}

# Assembles a bundle in $1 (a new, empty or missing folder).
#   $2 = native|aarch64|none (none: the installers only, no speakerd and no plugins)
#   $3.. = the plugin bundles, <plugin>:<build> (default DEFAULT_PLUGINS)
# Fails, listing the tools/build.sh commands, when an artifact is missing.
make_bundle() {
    local out="$1" arch="$2" repo what path missing=() commit dirty id
    shift 2
    local plugins=("$@")
    [ ${#plugins[@]} -gt 0 ] || plugins=("${DEFAULT_PLUGINS[@]}")
    repo="$(dirname "$FIRMWARE_DIR")"
    mkdir -p "$out"
    [ -z "$(ls -A "$out")" ] || die "the bundle folder $out is not empty"

    if [ "$arch" != none ]; then
        for what in speakerd "${plugins[@]}"; do
            path="$(artifact_path "$arch" "$what")"
            [ -e "$path" ] || missing+=("$what")
        done
        if [ ${#missing[@]} -gt 0 ]; then
            warn "these $arch builds are missing; make them on the development machine with:"
            for what in "${missing[@]}"; do warn "    $(build_command "$arch" "$what")    # $(artifact_path "$arch" "$what")"; done
            die "the bundle is incomplete (nothing is built here)"
        fi
        mkdir -p "$out/bin" "$out/lv2"
        cp -p "$(artifact_path "$arch" speakerd)" "$out/bin/speakerd"
        for what in "${plugins[@]}"; do cp -a "$(artifact_path "$arch" "$what")" "$out/lv2/"; done
        local machine; machine="$(elf_machine "$out/bin/speakerd")"
        [ "$arch" = native ] || [ "$machine" = "$arch" ] || die "$(artifact_path "$arch" speakerd) is $machine, not $arch"
    fi

    copy_installers "$out/firmware"

    commit="$(git -C "$repo" rev-parse HEAD 2> /dev/null || echo unknown)"
    dirty=no; [ -z "$(git -C "$repo" status --porcelain 2> /dev/null)" ] || dirty=yes
    if [ "$arch" != none ]; then
        id="${commit:0:12}$([ $dirty = yes ] && echo -dirty)-$(contents_hash "$out")"
        {
            echo "id=$id"
            echo "commit=$commit"
            echo "dirty=$dirty"
            echo "bundled=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
            echo "arch=$(elf_machine "$out/bin/speakerd")"
            echo "speakerd=$(sha256sum "$out/bin/speakerd" | cut -c1-16)"
            echo "lv2=$(find "$out/lv2" -mindepth 1 -maxdepth 1 -name '*.lv2' -printf '%f\n' | LC_ALL=C sort | tr '\n' ' ' | sed 's/ $//')"
        } > "$out/VERSION"
    else
        { echo "id=none"; echo "commit=$commit"; echo "dirty=$dirty"; } > "$out/VERSION"
    fi
}

# The installers and the files they install (firmware/ of a bundle, setup/ of an installed
# version), into $1: the scripts made executable, since they are called directly.
copy_installers() {
    local to="$1"
    mkdir -p "$to/speakerd" "$to/ampd" "$to/pipewire/pipewire.conf.d"
    cp -p "$FIRMWARE_DIR"/{setup.sh,install-lib.sh,speakerd-setup.sh,snapcast-setup.sh,ampd-setup.sh} "$to/"
    cp -p "$FIRMWARE_DIR"/speakerd/{speakerd-run,speakerd.service,setup-helper.py,tweeter_rule.py} "$to/speakerd/"
    cp -p "$FIRMWARE_DIR"/ampd/{ampd.py,ampd.service} "$to/ampd/"
    cp -p "$FIRMWARE_DIR/pipewire/pipewire.conf" "$to/pipewire/"
    cp -p "$FIRMWARE_DIR"/pipewire/pipewire.conf.d/*.conf "$to/pipewire/pipewire.conf.d/"
    chmod 755 "$to"/{setup.sh,speakerd-setup.sh,snapcast-setup.sh,ampd-setup.sh} "$to"/speakerd/{speakerd-run,setup-helper.py} "$to/ampd/ampd.py"
}

# A key of a bundle's VERSION file.
version_value() {   # $1 = VERSION file, $2 = key
    sed -n "s/^$2=//p" "$1" 2> /dev/null | head -n 1
}

# ---------------------------------------------------------------------------------------------
# The deploy gate: what the pre-bash hook enforces on an agent's commands, enforced again where
# no hook runs (a person's shell, a script calling a script).

# Nothing reaches a board while a lab lock exists (this checkout's or the main checkout's). With
# $1 = status also not unless tests/.status/<HEAD>.json says unit: pass and vpair: pass, both run
# on a clean tree, and the tree is clean now. BPI_DEPLOY_OVERRIDE=1, set by a person in their
# own shell, bypasses the test status (logged in tests/.status/overrides.log), never a lock.
# $1 = lock | status, $2 = what is refused, for the message.
deploy_gate() {
    local need="$1" what="$2" repo common main="" lock head dirty why=""
    repo="$(cd "$FIRMWARE_DIR/.." && pwd)"
    common="$(git -C "$repo" rev-parse --path-format=absolute --git-common-dir 2> /dev/null)" || common=""
    [ -z "$common" ] || main="$(dirname "$common")"
    for lock in "$repo/.claude/lab.lock" ${main:+"$main/.claude/lab.lock"}; do
        [ ! -e "$lock" ] || die "the lab is locked ($lock exists): nothing may reach a board. A person removes the lock when the boards are free."
    done
    [ "$need" = status ] || return 0
    if [ -z "$common" ]; then
        why="$repo is not a git checkout, so nothing says what was tested"
    else
        head="$(git -C "$repo" rev-parse HEAD)"
        dirty="$(git -C "$repo" status --porcelain)"
        if [ -n "$dirty" ]; then
            why="the working tree has uncommitted changes: commit first, so that what is deployed is what was tested"
        else
            why="$(python3 - "$repo/tests/.status/$head.json" <<'PY'
import json, sys
try:
    with open(sys.argv[1]) as f:
        s = json.load(f)
    if not isinstance(s, dict):
        raise ValueError("not a JSON object")
except (OSError, ValueError) as e:
    print(f"no readable test status {sys.argv[1]} ({e}): run tools/test.sh all first")
    sys.exit()
bad = [f"{k}: {s.get(k, 'not run')}" + ("" if s.get(k + "_tree") == "clean" else f" (tree {s.get(k + '_tree', '?')})")
       for k in ("unit", "vpair") if s.get(k) != "pass" or s.get(k + "_tree") != "clean"]
if bad:
    print("the test status of this commit is not green on a clean tree: " + ", ".join(bad))
PY
)"
        fi
    fi
    [ -n "$why" ] || return 0
    if [ "${BPI_DEPLOY_OVERRIDE:-}" = 1 ]; then
        mkdir -p "$repo/tests/.status"
        printf '%s %s %s%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${head:0:12}" "${dirty:+dirty }" "$what" >> "$repo/tests/.status/overrides.log"
        warn "BPI_DEPLOY_OVERRIDE=1: going on although $why (logged in tests/.status/overrides.log)"
        return 0
    fi
    die "refusing to $what: $why. A person may bypass this once with BPI_DEPLOY_OVERRIDE=1, in their own shell."
}

# ---------------------------------------------------------------------------------------------
# The remote mode: copy one bundle, run an installer's local mode on the board, clean up.

# $1 = [user@]host, $2 = the bundle folder, $3 = the script (in firmware/), the rest its
# arguments. The script runs from the bundle's folder, as the ssh user (the board's audio
# user, never root: the installers run as that user, who could not read root's folders), with
# --bundle pointing at the copy (after the arguments: an installer's action comes first); relative paths in the arguments are relative to the bundle
# (extra/...). The copy goes to ~/.cache on the board, not /tmp (often mounted noexec), and is
# removed however the script ends. A --dry-run among the arguments is the board-side dry run:
# it contacts the board and changes nothing there.
remote_run() {
    local host="$1" bundle="$2" script="$3" rdir rc inner
    shift 3
    case "$host" in root@*) die "ssh to the board as its audio user, not root ($host)" ;; esac
    # The gate, as for tools/board.sh. Only the tests' hosts (.invalid: they cannot exist) skip it.
    case "${host##*@}" in *.invalid) ;; *) deploy_gate status "run firmware/$script on $host" ;; esac
    command -v ssh > /dev/null || die "ssh is not installed"
    # shellcheck disable=SC2016  # expanded on the board: its own $HOME
    rdir="$(ssh -- "$host" 'mkdir -p "$HOME/.cache" && mktemp -d "$HOME/.cache/bpi-amp-setup.XXXXXX"')" \
        || die "cannot reach $host over ssh"
    [ -n "$rdir" ] || die "$host made no folder for the bundle"
    info "Copying the bundle ($(du -sh "$bundle" | cut -f1)) to $host:$rdir"
    # shellcheck disable=SC2029  # expanded here on purpose: the folder made on the board
    if ! tar -C "$bundle" -cf - . | ssh -- "$host" "tar -C $(printf %q "$rdir") -xf -"; then
        ssh -- "$host" "rm -rf $(printf %q "$rdir")" || true
        die "copying the bundle to $host failed"
    fi
    info "Running firmware/$script on $host"
    # Every argument quoted for the board's shell; the folder goes whatever happens.
    inner="d=$(printf %q "$rdir"); trap 'cd / && rm -rf \"\$d\"' EXIT; cd \"\$d\" && bash firmware/$(printf %q "$script") $(shown "$@") --bundle ."
    set +e
    # -t: a terminal, so that sudo on the board can ask for a password (snapcast-setup.sh).
    # shellcheck disable=SC2029  # expanded here on purpose: the quoted command
    ssh -t -- "$host" "bash -c $(printf %q "$inner")"
    rc=$?
    set -e
    return $rc
}
