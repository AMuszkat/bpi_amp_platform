#!/usr/bin/env bash
#
# ampd-setup.sh
#
# Installs ampd, the speaker's network agent (and the hub on the left speaker) of
# docs/remote-plugin-host.md, as a systemd user service of the audio user, next to speakerd.
# See firmware/CLAUDE.md, "ampd".
#
#   ~/.local/lib/speakerd/<version>/bin/ampd    the program: it comes with speakerd's version
#                                               (speakerd-setup.sh install puts it there)
#   ~/.local/state/speakerd/ampd.json           name, hub or agent, listen, hub_address, secret (mode 600)
#   ~/.config/systemd/user/ampd.service         firmware/ampd/ampd.service, byte for byte
#
# ampd never touches audio: the speaker plays without it. It writes graph.json and
# graph.lkg.json in the state directory; never device.json, factory.json or ampd.json.
#
# Usage:
#   ./ampd-setup.sh install [--name NAME] [--hub | --agent --hub-address ADDR] [--listen ADDR]
#   ./ampd-setup.sh status | smoke | check | uninstall
#
# Run `./ampd-setup.sh --help` for the full option list.

set -euo pipefail

SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
# shellcheck source=install-lib.sh
. "$(dirname "$SELF")/install-lib.sh"
UNIT_SOURCE="$FIRMWARE_DIR/ampd/ampd.service"
PYTHON_MIN=3.9

ACTION=""
NAME=""
ROLE=""
HUB=""            # yes | no | "" (keep ampd.json's, else the left speaker is the hub)
HUB_ADDRESS=""
SECRET_FILE=""
LISTEN=""
SPEAKERD_TOO=0
PURGE=0
DRY_RUN=0
NO_SYSTEMD=0
NO_RESTART=0
HOME_OVERRIDE=""
WAIT=10
BUNDLE_DIR=""

usage() {
    awk 'NR>1 { if ($0 ~ /^#/) { sub(/^# ?/, ""); print } else exit }' "$SELF"
    cat <<EOF

Actions:
  install     write ampd.json and the unit, then (re)start ampd and check that it listens
  status      ampd.json, the unit, the service, and what ampd says it is connected to
  smoke       pass only if ampd runs, is connected to speakerd and, on an agent, to the hub
  check       what install would refuse, without changing anything
  uninstall   stop and remove the unit (ampd.json stays unless --purge)

Options:
  --name <name>           this speaker's name (default: ampd.json's, else device.json's role)
  --role <left|right|mono>
                          the role device.json will have (setup.sh passes speakerd's on a first
                          install, before device.json exists), for the name and hub defaults
  --hub                   this speaker is the hub (default for role left)
  --agent                 this speaker is an agent of the hub (default for every other role)
  --hub-address <addr>    an agent's hub, until mDNS finds it: required for a new agent
                          (a placeholder such as bpi-amp.local in examples; the board's own address
                          comes from the person installing, never from a tracked file)
  --listen <addr>         the address ampd listens on (default 0.0.0.0)
  --secret-file <file>    the pair's secret: a file holding the same 16 to 256 printable characters
                          (no space) on both speakers; required on a first install, kept after.
                          Controllers prove it (firmware/CLAUDE.md, "ampd on a board"). Make one with
                          python3 -c 'import secrets; print(secrets.token_hex(24))' > ampd.secret
                          It goes into ampd.json (mode 600) and is never printed.
  --speakerd-too          check and a dry run: speakerd is installed in the same run (setup.sh),
                          so bin/ampd is not there yet
  --wait <seconds>        smoke: how long to wait for the connections (default $WAIT)
  --purge                 uninstall: also remove ampd.json
  --dry-run               say what would change, change nothing
  --no-restart            write the files, but do not (re)start ampd
  --no-systemd, --home <dir>
                          for the virtual pair tests: no systemctl or journalctl; install into
                          <dir> as if it were the user's home
  -h, --help              show this help
EOF
}

[ $# -gt 0 ] || { usage; exit 2; }
case "$1" in
    -h | -help | --help) usage; exit 0 ;;
    install | status | smoke | check | uninstall) ACTION="$1"; shift ;;
    *) die "unknown action '$1' (try --help)" ;;
esac
while [ $# -gt 0 ]; do
    case "$1" in
        --name)          NAME="${2:?}"; shift 2 ;;
        --role)          ROLE="${2:?}"; shift 2 ;;
        --hub)           HUB=yes; shift ;;
        --agent)         HUB=no; shift ;;
        --hub-address)   HUB_ADDRESS="${2:?}"; shift 2 ;;
        --secret-file)   SECRET_FILE="${2:?}"; shift 2 ;;
        --listen)        LISTEN="${2:?}"; shift 2 ;;
        --speakerd-too)  SPEAKERD_TOO=1; shift ;;
        --wait)          WAIT="${2:?}"; shift 2 ;;
        --purge)         PURGE=1; shift ;;
        --dry-run)       DRY_RUN=1; shift ;;
        --no-restart)    NO_RESTART=1; shift ;;
        --no-systemd)    NO_SYSTEMD=1; shift ;;
        --home)          HOME_OVERRIDE="${2:?}"; shift 2 ;;
        --bundle)        BUNDLE_DIR="${2:?}"; shift 2 ;;   # setup.sh passes it; a relative --secret-file is in it
        -h | -help | --help) usage; exit 0 ;;
        *)               die "unknown option: $1 (try --help)" ;;
    esac
done
case "$WAIT" in [0-9] | [0-9][0-9]) ;; *) die "--wait takes whole seconds, 0 to 99" ;; esac
[[ -z "$NAME" || "$NAME" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$ ]] || die "--name: letters, digits, '.', '_' or '-', at most 64"
[ -z "$HUB_ADDRESS" ] || [ "$HUB" != yes ] || die "--hub-address is for an agent: the hub has none"

if [ -n "$HOME_OVERRIDE" ]; then
    mkdir -p "$HOME_OVERRIDE"
    H="$(cd "$HOME_OVERRIDE" && pwd)"
else
    [ "$(id -u)" -ne 0 ] || die "run ampd-setup.sh as the board's audio user (the one speakerd runs as), not as root"
    H="$(getent passwd "$(id -un)" | cut -d: -f6)"
    [ -n "$H" ] || H="$HOME"
fi
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
LIB="$H/.local/lib/speakerd"
STATE="$H/.local/state/speakerd"
CONFIG="$STATE/ampd.json"
UNIT="$H/.config/systemd/user/ampd.service"
PROGRAM="$LIB/current/bin/ampd"
STATUS_FILE="$XDG_RUNTIME_DIR/ampd/status.json"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

dry() { [ "$DRY_RUN" -eq 1 ]; }
sc() {
    if [ "$NO_SYSTEMD" -eq 1 ]; then info "(no systemd) systemctl --user $*"; return 0; fi
    systemctl --user "$@"
}
# A value of a JSON file: $1 = the file, $2 = a Python expression of d.
json_value() {
    python3 -c "import json,sys; d=json.load(open(sys.argv[1])); v=($2); print('' if v is None else (str(v).lower() if isinstance(v, bool) else v))" "$1" 2> /dev/null || true
}

check_python() {
    command -v python3 > /dev/null || die "ampd needs python3 ($PYTHON_MIN or later): sudo apt install python3"
    python3 -c "import sys; sys.exit(sys.version_info < tuple(map(int, '$PYTHON_MIN'.split('.'))))" \
        || die "ampd needs python3 $PYTHON_MIN or later; this one is $(python3 -V 2>&1)"
}

# ampd.json as it will be, in $WORK/ampd.json: what is given, else what is there, else the
# defaults (name and hub from device.json's role). Refuses an agent with no hub address.
prepare_config() {
    local role="$ROLE" name hub listen address
    [ -n "$role" ] || [ ! -f "$STATE/device.json" ] || role="$(json_value "$STATE/device.json" 'd.get("role")')"
    if [ -f "$CONFIG" ]; then
        python3 -c "import json,sys; d=json.load(open(sys.argv[1])); assert isinstance(d, dict)" "$CONFIG" 2> /dev/null \
            || die "$CONFIG is not a JSON object: fix or remove it"
    fi
    name="${NAME:-$(json_value "$CONFIG" 'd.get("name")')}"
    case "$role" in left | right) : "${name:=$role}" ;; esac
    [ -n "$name" ] || die "no name for this speaker: give --name (device.json's role is ${role:-missing})"
    hub="$HUB"
    if [ -z "$hub" ] && [ -f "$CONFIG" ]; then hub="$(json_value "$CONFIG" '"yes" if d.get("hub") is True else "no" if d.get("hub") is False else None')"; fi
    if [ -z "$hub" ]; then if [ "$role" = left ]; then hub=yes; else hub=no; fi; fi
    [ -z "$HUB_ADDRESS" ] || [ "$hub" = no ] || die "--hub-address is for an agent, and $name is the hub (--agent makes it one)"
    listen="${LISTEN:-$(json_value "$CONFIG" 'd.get("listen")')}"
    : "${listen:=0.0.0.0}"
    address=""
    if [ "$hub" = no ]; then
        address="${HUB_ADDRESS:-$(json_value "$CONFIG" 'd.get("hub_address")')}"
        [ -n "$address" ] || die "an agent needs the hub's address: --hub-address <the left speaker's address> (until mDNS finds the hub)"
    fi
    if [ -n "$SECRET_FILE" ] && [ ! -f "$SECRET_FILE" ] && [ -n "$BUNDLE_DIR" ] && [ -f "$BUNDLE_DIR/$SECRET_FILE" ]; then
        SECRET_FILE="$BUNDLE_DIR/$SECRET_FILE"
    fi
    [ -z "$SECRET_FILE" ] || [ -r "$SECRET_FILE" ] || die "cannot read the secret file $SECRET_FILE"
    # The secret goes from its file (or the ampd.json there is) into ampd.json, never through a
    # command line or the output.
    python3 - "$WORK/ampd.json" "$name" "$hub" "$listen" "$address" "$SECRET_FILE" "$CONFIG" <<'PY' \
        || die "ampd needs the pair's secret (see above): --secret-file FILE, the same file on both speakers"
import json, os, re, sys
path, name, hub, listen, address, secret_file, old = sys.argv[1:]
secret = None
if secret_file:
    with open(secret_file) as f:
        secret = f.read().strip()
    if not re.fullmatch(r"[\x21-\x7e]{16,256}", secret):
        sys.exit("err  the secret file must hold 16 to 256 printable characters, no space")
elif os.path.exists(old):
    with open(old) as f:
        secret = json.load(f).get("secret")
if not secret:
    sys.exit("err  no secret: give --secret-file FILE (make one with: "
             "python3 -c 'import secrets; print(secrets.token_hex(24))' > ampd.secret)")
config = {"version": 1, "name": name, "hub": hub == "yes", "listen": listen, "hub_address": address or None,
          "secret": secret}
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    f.write(json.dumps(config, indent=2) + "\n")
PY
    CONFIG_SUMMARY="$name, $([ "$hub" = yes ] && echo "the hub" || echo "an agent of the hub at $address"), listening on $listen, with the pair's secret"
}

check_program() {
    if [ -x "$PROGRAM" ]; then
        "$PROGRAM" --config "$WORK/ampd.json" --check-config > /dev/null 2> "$WORK/check.err" \
            || die "ampd refuses this ampd.json: $(cat "$WORK/check.err")"
        ok "ampd: $("$PROGRAM" --version 2>&1)"
    elif [ "$SPEAKERD_TOO" -eq 1 ] && { [ "$ACTION" = check ] || dry; }; then
        info "ampd comes with the speakerd version this run installs"
    else
        die "no ampd at $PROGRAM: it comes with speakerd's version. Install speakerd from a bundle with ampd first (setup.sh does, in boot order)"
    fi
}

# After a (re)start under systemd: ampd must say it listens, and still run.
check_started() {   # $1 = epoch seconds of the restart
    [ "$NO_SYSTEMD" -eq 0 ] || return 0
    local log="" wait="${AMPD_START_WAIT:-10}"
    for _ in $(seq 1 $((wait * 2))); do
        log="$(journalctl --user -u ampd.service --since "@$1" -o cat --no-pager 2> /dev/null || true)"
        grep -qE '^ampd: (listening on|cannot listen|config .*: FAILED)' <<< "$log" && break
        sleep 0.5
    done
    sleep 1
    if ! grep -q '^ampd: listening on' <<< "$log"; then
        loud "ampd did not say it listens within $wait s:" "$(grep '^ampd: ' <<< "$log" | tail -n 5)"
        exit 1
    fi
    systemctl --user is-active --quiet ampd.service || { loud "ampd is not running after its start (systemctl --user status ampd)"; exit 1; }
    ok "$(grep -m1 '^ampd: listening on' <<< "$log")"
}

do_check() {
    check_python
    prepare_config
    check_program
    ok "ampd.json would say: $CONFIG_SUMMARY"
}

do_install() {
    check_python
    prepare_config
    check_program
    info "ampd.json: $CONFIG_SUMMARY"
    local changed=0
    if [ -f "$CONFIG" ] && cmp -s "$WORK/ampd.json" "$CONFIG"; then ok "$CONFIG is already so"
    elif dry; then info "(dry-run) would write $CONFIG"
    else
        mkdir -p "$STATE"
        # It holds the pair's secret: only this user reads it.
        cp "$WORK/ampd.json" "$CONFIG.tmp.$$" && chmod 600 "$CONFIG.tmp.$$" && mv -f "$CONFIG.tmp.$$" "$CONFIG"
        ok "Wrote $CONFIG"; changed=1
    fi
    if [ -f "$UNIT" ] && cmp -s "$UNIT_SOURCE" "$UNIT"; then ok "$UNIT is already the repository's"
    elif dry; then info "(dry-run) would write $UNIT"
    else
        mkdir -p "$(dirname "$UNIT")"
        cp "$UNIT_SOURCE" "$UNIT.tmp.$$" && chmod 644 "$UNIT.tmp.$$" && mv -f "$UNIT.tmp.$$" "$UNIT"
        ok "Wrote $UNIT"; changed=1
        sc daemon-reload
    fi
    if dry; then ok "Dry run: nothing changed"; return 0; fi
    sc enable ampd.service > /dev/null 2>&1 || warn "could not enable ampd.service"
    if [ "$NO_RESTART" -eq 1 ]; then info "Not (re)starting ampd (--no-restart)"; return 0; fi
    if [ "$changed" -eq 1 ] || ! { [ "$NO_SYSTEMD" -eq 1 ] || systemctl --user is-active --quiet ampd.service; }; then
        local restarted; restarted="$(date +%s)"
        sc restart ampd.service || die "could not start ampd: journalctl --user -u ampd"
        check_started "$restarted"
        ok "ampd (re)started"
    else
        ok "Nothing changed: ampd keeps running"
    fi
    cat <<EOF

${c_green}ampd installed.${c_off} The speaker plays with or without it.
  - Check it: $(dirname "$SELF")/ampd-setup.sh smoke
  - Its log:  journalctl --user -u ampd -b
EOF
}

# What ampd says about itself ($XDG_RUNTIME_DIR/ampd/status.json), if it runs.
read_status() {
    STATUS=""
    [ -f "$STATUS_FILE" ] || return 1
    local pid; pid="$(json_value "$STATUS_FILE" 'd.get("pid")')"
    [ -n "$pid" ] && kill -0 "$pid" 2> /dev/null || return 1
    STATUS="$STATUS_FILE"
}

do_status() {
    echo "ampd on $(id -un)@$(hostname) (home $H)"
    if [ -f "$CONFIG" ]; then
        echo "  ampd.json (the secret not shown):"
        python3 -c "import json,sys; d=json.load(open(sys.argv[1])); d['secret']='(set)' if d.get('secret') else '*** none ***'; print(json.dumps(d, indent=2))" \
            "$CONFIG" 2> /dev/null | sed 's/^/    /' || echo "    (not readable)"
    else echo "  ampd.json: none ($CONFIG)"; fi
    if [ -x "$PROGRAM" ]; then echo "  program: $PROGRAM ($("$PROGRAM" --version 2>&1))"; else echo "  program: none ($PROGRAM)"; fi
    if [ -f "$UNIT" ]; then echo "  unit: $UNIT"; else echo "  unit: none"; fi
    if [ "$NO_SYSTEMD" -eq 0 ]; then
        echo "  service: $(systemctl --user is-enabled ampd.service 2> /dev/null || echo not-installed), $(systemctl --user is-active ampd.service 2> /dev/null || true), restarts $(systemctl --user show ampd.service -p NRestarts --value 2> /dev/null || echo ?)"
    fi
    if read_status; then
        python3 - "$STATUS" <<'PY'
import json, sys
s = json.load(open(sys.argv[1]))
sd = s.get("speakerd", {})
print(f"  runs: pid {s.get('pid')}, {s.get('name')} as the {s.get('role')}, revision {s.get('revision')}"
      + (f" ({s.get('time')}, {s.get('origin')})" if s.get("time") else ""))
print("  speakerd: " + (f"connected ({sd.get('version')}, layer {sd.get('layer')}, revision {sd.get('revision')})"
                        if sd.get("connected") else f"NOT connected ({sd.get('socket')})"))
if "hub" in s:
    hub = s["hub"]
    print(f"  hub {hub.get('address')}: " + (f"connected ({hub.get('name')})" if hub.get("connected") else "NOT connected"))
if "agents" in s:
    for a in s["agents"]:
        print(f"  agent {a.get('name')}: {'online' if a.get('online') else 'offline'}, layer {a.get('layer')}, revision {a.get('revision')}")
    print(f"  controllers: {', '.join(s.get('controllers', [])) or 'none'}")
PY
    else
        echo "  runs: no ($STATUS_FILE is missing or its process is gone)"
    fi
}

SMOKE_FAILS=0
pass()   { printf '  %spass%s %s\n' "$c_green" "$c_off" "$*"; }
failed() { printf '  %sFAIL%s %s\n' "$c_red" "$c_off" "$*"; SMOKE_FAILS=$((SMOKE_FAILS + 1)); }

do_smoke() {
    set +e
    echo "smoke: ampd on $(id -un)@$(hostname)"
    local role="" sd="" hub="" i
    if [ ! -f "$CONFIG" ]; then failed "no $CONFIG"
    elif [ -x "$PROGRAM" ] && ! "$PROGRAM" --config "$CONFIG" --check-config > /dev/null 2> "$WORK/check.err"; then
        failed "ampd refuses $CONFIG: $(cat "$WORK/check.err")"
    else
        pass "ampd.json: $("$PROGRAM" --config "$CONFIG" --check-config 2> /dev/null || echo "($PROGRAM is missing)")"
    fi
    if [ "$NO_SYSTEMD" -eq 0 ]; then
        if systemctl --user is-active --quiet ampd.service; then pass "ampd.service is active"
        else failed "ampd.service is $(systemctl --user is-active ampd.service 2> /dev/null)"; fi
    fi
    # The connections take a moment after a start: wait for them, up to --wait seconds.
    for i in $(seq 0 $((WAIT * 4))); do
        if read_status; then
            role="$(json_value "$STATUS" 'd.get("role")')"
            sd="$(json_value "$STATUS" 'd["speakerd"].get("connected")')"
            hub="$(json_value "$STATUS" '(d.get("hub") or {}).get("connected")')"
            [ "$sd" = true ] && { [ "$role" = hub ] || [ "$hub" = true ]; } && break
        fi
        [ "$i" -lt $((WAIT * 4)) ] && sleep 0.25
    done
    if [ -z "$STATUS" ]; then
        failed "ampd does not run ($STATUS_FILE is missing or its process is gone)"
    else
        pass "ampd runs: pid $(json_value "$STATUS" 'd.get("pid")'), the $role"
        if [ "$sd" = true ]; then
            pass "connected to speakerd: $(json_value "$STATUS" 'd["speakerd"].get("version")'), layer $(json_value "$STATUS" 'd["speakerd"].get("layer")'), revision $(json_value "$STATUS" 'd["speakerd"].get("revision")')"
        else
            failed "not connected to speakerd ($(json_value "$STATUS" 'd["speakerd"].get("socket")')): journalctl --user -u ampd -b"
        fi
        if [ "$role" = agent ]; then
            if [ "$hub" = true ]; then pass "connected to the hub at $(json_value "$STATUS" 'd["hub"].get("address")') ($(json_value "$STATUS" 'd["hub"].get("name")'))"
            else failed "not connected to the hub at $(json_value "$STATUS" 'd["hub"].get("address")'): is it up, and is that its address (ampd.json's hub_address)?"; fi
        else
            printf '  info agents: %s\n' "$(json_value "$STATUS" '", ".join(a["name"] + (" online" if a.get("online") else " offline") for a in d.get("agents", [])) or "none yet"')"
        fi
    fi
    echo "smoke: ampd: $SMOKE_FAILS failed"
    [ "$SMOKE_FAILS" -eq 0 ]
}

do_uninstall() {
    if dry; then info "(dry-run) would stop and disable ampd and remove $UNIT$([ "$PURGE" = 1 ] && echo " and $CONFIG")"; return 0; fi
    sc stop ampd.service 2> /dev/null || true
    sc disable ampd.service 2> /dev/null || true
    if [ -f "$UNIT" ]; then rm -f "$UNIT" && ok "Removed $UNIT"; sc daemon-reload || true; fi
    if [ "$PURGE" -eq 1 ]; then rm -f "$CONFIG" && ok "Removed $CONFIG"
    elif [ -f "$CONFIG" ]; then info "Kept $CONFIG: --purge removes it"; fi
    ok "ampd uninstalled"
}

case "$ACTION" in
    install)   do_install ;;
    status)    do_status ;;
    smoke)     do_smoke ;;
    check)     do_check ;;
    uninstall) do_uninstall ;;
esac
