# shellcheck shell=bash
# install-lib.sh: what firmware/setup.sh and firmware/speakerd-setup.sh share. Sourced, not run.
# See firmware/CLAUDE.md, "speakerd on a board" and "The whole architecture".
#
#   logging            info, ok, warn, die, loud (a warning nobody can miss)
#   the board          board_target: [user@]host from BOARD_USER / BOARD_HOST, in the
#                      environment or in firmware/local.mk (read, never executed)
#   the bundle         make_bundle: what the installers copy to a board, from the build tree;
#                      installed_setup: an installer's installed copy, for the hints
#   the remote mode    remote_run: copy one bundle to the board, run an installer there
#   pipewire.conf      is_repository_conf: whether a pipewire.conf is the repository's, which
#                      defines the speakerd sink itself; repository_conf_at_rate: the file
#                      --base-conf installs; pipewire_conf_state: whether the installed one is
#                      this version's (status and smoke)
#
# The bundle is a folder:
#
#     VERSION                 id, git commit, dirty or not, when, architecture, contents
#     bin/speakerd            the program (with artifacts only)
#     lv2/<target>.lv2/       the plugin bundles speakerd is given (with artifacts only)
#     firmware/...            the installers and the files they install: ampd, the unit, the
#                             drop-in and firmware/speakerd/factory.json, the chain document a
#                             first install writes as factory.json
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

# A pipewire.conf that is the repository's, firmware/pipewire/pipewire.conf (any --rate, any
# version that carries its header line). It defines the speakerd sink and resample.quality
# itself, so the drop-ins that define them (REPO_CONF_HAS, in pipewire.conf.d) do not belong
# beside it: two speakerd nodes otherwise.
is_repository_conf() { [ -f "$1" ] && grep -qF "The BPI AMP Platform's PipeWire configuration (firmware/pipewire/pipewire.conf)" "$1"; }
REPO_CONF_HAS=(20-resample.conf 60-speakerd-sink.conf)

# The repository's pipewire.conf ($1) as setup.sh --base-conf installs it at the rate $2 (empty:
# the file's own) into $3: the rate in default.clock.rate, in default.clock.allowed-rates when it
# is set, and in the amplifier's (each api.alsa.pcm.sink object's) audio.rate; nothing else.
repository_conf_at_rate() {   # $1 = source, $2 = rate or "", $3 = output
    if [ -z "$2" ]; then cp "$1" "$3"; return; fi
    awk -v rate="$2" '
        /^[[:space:]]*#/ { print; next }
        /\{[[:space:]]*factory[[:space:]]*=/ { alsa = 0 }
        /^[[:space:]]*factory\.name[[:space:]]*=[[:space:]]*"?api\.alsa\.pcm\.sink/ { alsa = 1 }
        /^[[:space:]]*default\.clock\.rate[[:space:]]*=/ { sub(/=[[:space:]]*[0-9]+/, "= " rate) }
        /^[[:space:]]*default\.clock\.allowed-rates[[:space:]]*=/ { sub(/=[[:space:]]*\[[^]]*\]/, "= [ " rate " ]") }
        alsa && /^[[:space:]]*audio\.rate[[:space:]]*=/ { sub(/=[[:space:]]*[0-9]+/, "= " rate) }
        { print }' "$1" > "$3"
}

# A key of a pipewire.conf's context.properties, uncommented, the first time it is set
# (default.clock.rate, default.clock.min-quantum ...), or nothing.
conf_value() {   # $1 = file, $2 = key
    awk -v key="$2" '
        /^[[:space:]]*#/ { next }
        { line = $0; sub(/^[[:space:]]+/, "", line) }
        index(line, key) == 1 && substr(line, length(key) + 1) ~ /^[[:space:]]*=/ {
            sub(/^[^=]*=[[:space:]]*/, "", line); sub(/[[:space:]#].*$/, "", line); print line; exit
        }' "$1" 2> /dev/null
}

# "quantum <default>, <min> to <max>" of a pipewire.conf, for the messages; "?" for a key the
# file does not set (PipeWire's default then).
conf_quantum() {   # $1 = file
    local q mn mx
    q="$(conf_value "$1" default.clock.quantum)"; mn="$(conf_value "$1" default.clock.min-quantum)"
    mx="$(conf_value "$1" default.clock.max-quantum)"
    printf 'quantum %s, %s to %s' "${q:-?}" "${mn:-?}" "${mx:-?}"
}

# Whether the user's pipewire.conf ($1) is this version's repository file ($2, the
# firmware/pipewire/pipewire.conf beside the running installer), at the rate it was installed
# at. Sets CONF_STATE: none (no file: PipeWire's own), own (the board's own), current (the
# repository's, as setup.sh --base-conf would install it now) or outdated (the repository's
# header, other content: an older copy, or one edited by hand); and CONF_WHAT, what to say of it.
pipewire_conf_state() {   # $1 = the installed pipewire.conf, $2 = the repository's
    local rate expected lines
    CONF_STATE="" CONF_WHAT=""
    if [ ! -f "$1" ]; then CONF_STATE=none; CONF_WHAT="none ($1): PipeWire's own configuration"; return 0; fi
    if ! is_repository_conf "$1"; then CONF_STATE=own; CONF_WHAT="$1, the board's own ($(conf_quantum "$1"))"; return 0; fi
    rate="$(conf_value "$1" default.clock.rate)"
    [ "$rate" != "$(conf_value "$2" default.clock.rate)" ] || rate=""
    expected="$(mktemp)"
    repository_conf_at_rate "$2" "$rate" "$expected"
    if cmp -s "$1" "$expected"; then
        CONF_STATE=current
        CONF_WHAT="$1, the repository's${rate:+ at --rate $rate} ($(conf_quantum "$1"))"
    else
        CONF_STATE=outdated
        # A changed line is one "<" and one ">": count the larger side.
        lines="$({ diff "$expected" "$1" 2> /dev/null || true; } | awk '/^</ { a++ } /^>/ { b++ } END { print (a > b ? a : b) + 0 }')"
        CONF_WHAT="$1 is an older (or edited) copy of the repository's: $(conf_quantum "$1"), where this version's has $(conf_quantum "$2") (${lines:-some} line(s) differ). Update it: setup.sh --only pipewire --base-conf${rate:+ --rate $rate} (from the development machine: firmware/setup.sh --mode target-remote --only pipewire --base-conf${rate:+ --rate $rate})"
    fi
    rm -f "$expected"
}

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

# The plugin bundles a speaker gets by default: <plugin>:<build>. A speaker runs the headless
# builds (the DSP alone, no editor) of every plugin, never the complete builds: loudness,
# crossover (from 0.0.4) and firConv (from 0.0.5) each have one, with the URI
# <base>/<plugin>_headless. firmware/speakerd/factory.json, the chain document a first install
# writes as factory.json, names exactly these.
DEFAULT_PLUGINS=(loudness:headless crossover:headless firConv:headless)

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

# The id of a bundle's contents: a hash of every file under the folders it covers, names
# included. A bundle covers bin/, lv2/ and firmware/ (the installers, and what a version
# installs from them: bin/ampd, bin/speakerd-run, the units, setup-helper.py, the PipeWire
# config, factory.json), and says so in its VERSION (contents=); one without that line was made
# before firmware/ counted (its id covers bin/ and lv2/ only) and is checked as it was made.
BUNDLE_CONTENTS="bin lv2 firmware"
contents_hash() {   # $1 = bundle folder, $2 = the folders (default BUNDLE_CONTENTS)
    local folders
    read -r -a folders <<< "${2:-$BUNDLE_CONTENTS}"
    # A folder the bundle lacks hashes nothing (find's error is dropped), which is harmless: the
    # hash then differs from the bundle's id, and the install refuses the bundle.
    (cd "$1" && find "${folders[@]}" -type f -print0 2> /dev/null | LC_ALL=C sort -z | xargs -0 -r sha256sum | sha256sum | cut -c1-8)
}

# Whether pid $1 is alive and is an ampd: a status.json that an ampd killed with SIGKILL left
# behind names a pid the kernel may since have given to anything else, so the pid alone does not
# tell. ampd runs as bin/ampd (its unit) or ampd.py (the virtual pair), directly or through
# python3: one of the first 4 words of /proc/<pid>/cmdline is a file of either name.
ampd_pid() {   # $1 = pid
    local arg n=0
    [[ "$1" =~ ^[0-9]+$ ]] && kill -0 "$1" 2> /dev/null && [ -r "/proc/$1/cmdline" ] || return 1
    while IFS= read -r -d '' arg && [ "$n" -lt 4 ]; do
        case "${arg##*/}" in ampd|ampd.py) return 0 ;; esac
        n=$((n + 1))
    done < "/proc/$1/cmdline"
    return 1
}

# The folders a bundle's id covers, from its VERSION: BUNDLE_CONTENTS, or "bin lv2" for a
# bundle made before the line (anything else is not a bundle this installer made).
bundle_contents() {   # $1 = the VERSION file
    local said
    said="$(sed -n 's/^contents=//p' "$1" | head -n 1)"
    if [ -z "$said" ]; then echo "bin lv2"
    elif [ "$said" = "$BUNDLE_CONTENTS" ]; then echo "$said"
    else return 1; fi
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
            echo "contents=$BUNDLE_CONTENTS"
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
    cp -p "$FIRMWARE_DIR"/speakerd/{speakerd-run,speakerd.service,setup-helper.py,factory.json} "$to/speakerd/"
    cp -p "$FIRMWARE_DIR"/ampd/{ampd.py,ampd.service} "$to/ampd/"
    cp -p "$FIRMWARE_DIR/pipewire/pipewire.conf" "$to/pipewire/"
    cp -p "$FIRMWARE_DIR"/pipewire/pipewire.conf.d/*.conf "$to/pipewire/pipewire.conf.d/"
    chmod 755 "$to"/{setup.sh,speakerd-setup.sh,snapcast-setup.sh,ampd-setup.sh} "$to"/speakerd/{speakerd-run,setup-helper.py} "$to/ampd/ampd.py"
}

# A key of a bundle's VERSION file.
version_value() {   # $1 = VERSION file, $2 = key
    sed -n "s/^$2=//p" "$1" 2> /dev/null | head -n 1
}

# An installer's installed copy, setup/ of the current version ($LIB/current/setup/$1, with the
# caller's LIB and H), as a person types it on the board: with ~ when H is this user's home.
# Never the running script's own path for a hint: it may be a bundle's, and the remote mode's
# copy of the bundle (in ~/.cache) is removed when the run ends.
installed_setup() {   # $1 = the script
    local path="$LIB/current/setup/$1"
    # shellcheck disable=SC2088  # a tilde for a person to read, not to expand here
    if [ -n "${HOME:-}" ] && [ "$H" = "${HOME%/}" ]; then path="~/${path#"$H"/}"; fi
    echo "$path"
}

# ---------------------------------------------------------------------------------------------
# The lab lock: what the pre-bash hook enforces on an agent's commands, enforced again where no
# hook runs (a person's shell, a script calling a script).

# Nothing reaches a board while a lab lock exists (this checkout's or the main checkout's). There
# is no test-status gate (2026-11-02, the person's decision): a deploy does not ask whether its
# commit passed the tests. $1 (lock | status) is kept for the callers and no longer changes
# anything; $2 is what is refused, for the message.
deploy_gate() {
    local repo common main="" lock
    repo="$(cd "$FIRMWARE_DIR/.." && pwd)"
    common="$(git -C "$repo" rev-parse --path-format=absolute --git-common-dir 2> /dev/null)" || common=""
    [ -z "$common" ] || main="$(dirname "$common")"
    for lock in "$repo/.claude/lab.lock" ${main:+"$main/.claude/lab.lock"}; do
        [ ! -e "$lock" ] || die "the lab is locked ($lock exists): nothing may reach a board ($2). A person removes the lock when the boards are free."
    done
    return 0
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
