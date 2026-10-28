#!/bin/bash
# The script behind the /board skill: the one way an agent reaches a real speaker, rung 3 of
# docs/autonomous-development.md. See firmware/CLAUDE.md, "tools/board.sh".
#
#     tools/board.sh status                      what is installed and what runs (reads only)
#     tools/board.sh journal [-n N] [--follow]   journalctl --user -u speakerd on the board (reads only)
#     tools/board.sh deploy [setup.sh options]   builds the aarch64 artifacts, installs them capped
#                                                (firmware/setup.sh --mode target-remote; default
#                                                --only speakerd)
#     tools/board.sh smoke [--window S]          checks, without a sound, that speakerd plays capped
#     tools/board.sh rollback                    makes the previous speakerd current again
#     tools/board.sh --dry-run <command> ...     says what it would run; builds and reaches nothing
#
# The board is $BOARD_USER@$BOARD_HOST, from the environment or the gitignored
# firmware/local.mk; never a name written here.
#
# It refuses every command while .claude/lab.lock exists (in this checkout or the main one),
# and deploy, smoke and rollback unless tests/.status/<HEAD>.json says unit: pass and
# vpair: pass, both run on a clean tree, and the tree is clean now. The pre-bash hook gates the
# same; this is defence in depth. A person may bypass the test status, never the lock, with
# BPI_DEPLOY_OVERRIDE=1; that is logged in tests/.status/overrides.log. Nothing here lifts the
# output cap: every deploy installs capped, and only a person at the speaker lifts it
# (firmware/speakerd-setup.sh limits --person-present).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../firmware/install-lib.sh
. "$ROOT/firmware/install-lib.sh"

DRY_RUN=0
if [ "${1:-}" = --dry-run ]; then DRY_RUN=1; shift; fi
command="${1:-}"
[ $# -eq 0 ] || shift
case "$command" in
    status | journal | deploy | smoke | rollback) ;;
    -h | --help) sed -n '2,/^set -euo/{/^set -euo/d;s/^# \{0,1\}//;p}' "$0"; exit 0 ;;
    *) echo "usage: $0 [--dry-run] status | journal | deploy | smoke | rollback (see --help)" >&2; exit 2 ;;
esac

# Runs a command, or with --dry-run prints it.
run() {
    if [ "$DRY_RUN" -eq 1 ]; then echo "(dry-run) $(shown "$@")"; return 0; fi
    "$@"
}

# --- The gate ----------------------------------------------------------------------------------
main_checkout() {
    local common
    common="$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir 2> /dev/null)" && dirname "$common" || echo "$ROOT"
}
for lock in "$ROOT/.claude/lab.lock" "$(main_checkout)/.claude/lab.lock"; do
    [ ! -e "$lock" ] || die "the lab is locked ($lock exists): nothing may reach a board. A person removes the lock when the boards are free."
done

if [ "$command" != status ] && [ "$command" != journal ]; then
    head="$(git -C "$ROOT" rev-parse HEAD)"
    dirty="$(git -C "$ROOT" status --porcelain)"
    status_file="$ROOT/tests/.status/$head.json"
    why=""
    [ -z "$dirty" ] || why="the working tree has uncommitted changes: commit first, so that what is deployed is what was tested"
    if [ -z "$why" ]; then
        why="$(python3 - "$status_file" <<'PY'
import json, sys
try:
    with open(sys.argv[1]) as f:
        s = json.load(f)
except (OSError, ValueError):
    print(f"no test status {sys.argv[1]}: run /test all (tools/test.sh all) first")
    sys.exit()
bad = [f"{k}: {s.get(k, 'not run')}" + ("" if s.get(k + "_tree") == "clean" else f" (tree {s.get(k + '_tree', '?')})")
       for k in ("unit", "vpair") if s.get(k) != "pass" or s.get(k + "_tree") != "clean"]
if bad:
    print("the test status of this commit is not green on a clean tree: " + ", ".join(bad))
PY
)"
    fi
    if [ -n "$why" ]; then
        if [ "${BPI_DEPLOY_OVERRIDE:-}" = 1 ]; then
            mkdir -p "$ROOT/tests/.status"
            printf '%s %s %stools/board.sh %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${head:0:12}" "${dirty:+dirty }" "$command" "$*" \
                >> "$ROOT/tests/.status/overrides.log"
            warn "BPI_DEPLOY_OVERRIDE=1: going on although $why (logged in tests/.status/overrides.log)"
        else
            die "refusing to $command: $why. A person may bypass this once with BPI_DEPLOY_OVERRIDE=1."
        fi
    fi
fi

TARGET="$(board_target)"
info "board: $TARGET"

# --- The commands ------------------------------------------------------------------------------
case "$command" in
    status)
        run "$ROOT/firmware/speakerd-setup.sh" status --mode target-remote --target-host "$TARGET"
        ;;
    journal)
        lines=200 follow=()
        while [ $# -gt 0 ]; do
            case "$1" in
                -n) lines="${2:?}"; shift 2 ;;
                --follow | -f) follow=(--follow); shift ;;
                *) die "journal takes -n <lines> and --follow" ;;
            esac
        done
        run ssh -t "$TARGET" journalctl --user -u speakerd.service -b --no-pager -n "$lines" "${follow[@]}"
        ;;
    smoke)
        run "$ROOT/firmware/speakerd-setup.sh" smoke --mode target-remote --target-host "$TARGET" "$@"
        ;;
    rollback)
        run "$ROOT/firmware/speakerd-setup.sh" rollback --mode target-remote --target-host "$TARGET"
        ;;
    deploy)
        plugins=() args=() components=0
        while [ $# -gt 0 ]; do
            case "$1" in
                --plugin) plugins+=("${2:?}"); shift 2 ;;
                --only | --skip) components=1; args+=("$1" "${2:?}"); shift 2 ;;
                --mode | --target-host) die "deploy chooses $1 itself" ;;
                *) args+=("$1"); shift ;;
            esac
        done
        [ ${#plugins[@]} -gt 0 ] || plugins=("${DEFAULT_PLUGINS[@]}")
        [ $components = 1 ] || args=(--only speakerd "${args[@]}")
        info "building the aarch64 artifacts"
        run "$ROOT/tools/build.sh" speakerd aarch64
        for p in "${plugins[@]}"; do
            kind="${p#*:}"; [ "$kind" = "$p" ] && kind=complete
            run "$ROOT/tools/build.sh" "${p%%:*}" aarch64 "$kind"
        done
        plugin_args=()
        for p in "${plugins[@]}"; do plugin_args+=(--plugin "$p"); done
        run "$ROOT/firmware/setup.sh" --mode target-remote --target-host "$TARGET" "${plugin_args[@]}" "${args[@]}"
        info "next: tools/board.sh smoke"
        ;;
esac
