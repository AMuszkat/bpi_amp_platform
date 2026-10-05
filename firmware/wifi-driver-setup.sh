#!/usr/bin/env bash
#
# wifi-driver-setup.sh
#
# Fix slow WiFi on the BananaPi CM4's on-module Realtek RTL8822CS (SDIO combo).
#
# The board can bind the radio with either of two drivers:
#   - rtw88        : the mainline in-tree driver (rtw88_8822cs + rtw88_sdio +
#                    rtw88_8822c + rtw88_core). Clean/upstream, but tops out
#                    around ~270 Mbit/s on this SDIO part and has power-save
#                    quirks that throttle throughput.
#   - 88x2cs       : Realtek's out-of-tree vendor driver (single big module).
#                    Reaches ~400+ Mbit/s here and is what actually fixes the
#                    slow link -- measured 50 -> ~150 Mbit/s just by switching.
#
# When BOTH are installed they race to claim the SDIO function, which is itself
# a source of instability. This script picks ONE driver by blacklisting the
# other, rebuilds the initramfs so the choice sticks, and disables WiFi
# power-save (a separate throughput killer). A reboot applies the driver swap.
#
# Default: use 88x2cs (the fast path). Pass --driver rtw88 to standardize on
# mainline instead.
#
# Usage:
#   ./wifi-driver-setup.sh                                  # configure THIS machine (the CM4)
#   ./wifi-driver-setup.sh --mode remote \
#        --target-host user@bpi-amp.local               # ssh to the CM4 and configure it
#   ./wifi-driver-setup.sh --mode remote --reboot           # ...and reboot it when done
#
# Run with --help for all options.

set -euo pipefail

# ----------------------------------------------------------------------------
# Defaults
# ----------------------------------------------------------------------------
MODE="local"                             # local | remote
TARGET_HOST="root@${BOARD_HOST:-bpi-amp.local}"  # ssh destination of the CM4; set BOARD_HOST (for --mode remote)
DRIVER="88x2cs"                          # 88x2cs (vendor, fast) | rtw88 (mainline)
POWERSAVE="off"                          # off = disable WiFi power-save | keep = leave it alone
APPLY_NOW=1                              # set power_save off immediately via iw (no reboot needed for that)
DO_REBOOT=0                              # reboot after configuring (the driver swap needs it)
DRY_RUN=0

# The mainline rtw88 SDIO stack for the RTL8822CS, most-dependent first.
RTW88_MODS="rtw88_8822cs rtw88_8822c rtw88_sdio rtw88_core"
VENDOR_MOD="88x2cs"

BLACKLIST_RTW="/etc/modprobe.d/blacklist-rtw88-8822cs.conf"
BLACKLIST_VENDOR="/etc/modprobe.d/blacklist-88x2cs.conf"
NM_CONF="/etc/NetworkManager/conf.d/wifi-powersave-off.conf"
PS_SERVICE="/etc/systemd/system/wifi-powersave-off.service"

SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"

# ----------------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------------
c_blue=$'\033[1;34m'; c_yellow=$'\033[1;33m'; c_red=$'\033[1;31m'; c_green=$'\033[1;32m'; c_off=$'\033[0m'
info()  { printf '%s==>%s %s\n' "$c_blue"   "$c_off" "$*"; }
ok()    { printf '%s ok%s %s\n' "$c_green"  "$c_off" "$*"; }
warn()  { printf '%swarn%s %s\n' "$c_yellow" "$c_off" "$*" >&2; }
die()   { printf '%serr%s  %s\n' "$c_red"    "$c_off" "$*" >&2; exit 1; }

usage() {
    awk 'NR>1 { if ($0 ~ /^#/) { sub(/^# ?/, ""); print } else exit }' "$SELF"
    cat <<EOF

Options:
  --mode <local|remote>       Where to run (default: $MODE). 'remote' ssh's into --target-host.
  --target-host <[user@]host> SSH destination of the CM4 (default: $TARGET_HOST). Used by --mode remote.
  --driver <88x2cs|rtw88>     Which driver to keep (default: $DRIVER). The other is blacklisted.
  --keep-powersave            Do NOT disable WiFi power-save (default is to disable it).
  --no-apply-now              Do not toggle power_save live; only write persistent config.
  --reboot                    Reboot after configuring (the driver swap only takes effect on reboot).
  --dry-run                   Print actions without changing anything.
  -h, -help, --help           Show this help.

Examples:
  # On the CM4 itself, switch to the fast vendor driver and reboot:
  sudo ./wifi-driver-setup.sh --reboot

  # From the host, over ssh, and reboot the board when done:
  ./wifi-driver-setup.sh --mode remote --target-host user@bpi-amp.local --reboot

  # Go back to mainline rtw88:
  sudo ./wifi-driver-setup.sh --driver rtw88 --reboot
EOF
}

# ----------------------------------------------------------------------------
# Argument parsing
# ----------------------------------------------------------------------------
while [ $# -gt 0 ]; do
    case "$1" in
        --mode)           MODE="${2:?}"; shift 2 ;;
        --target-host)    TARGET_HOST="${2:?}"; shift 2 ;;
        --driver)         DRIVER="${2:?}"; shift 2 ;;
        --keep-powersave) POWERSAVE="keep"; shift ;;
        --no-apply-now)   APPLY_NOW=0; shift ;;
        --reboot)         DO_REBOOT=1; shift ;;
        --dry-run)        DRY_RUN=1; shift ;;
        -h|-help|--help)  usage; exit 0 ;;
        *)                die "Unknown option: $1 (try --help)" ;;
    esac
done
case "$MODE" in local|remote) ;; *) die "--mode must be local or remote" ;; esac
case "$DRIVER" in 88x2cs|rtw88) ;; *) die "--driver must be 88x2cs or rtw88" ;; esac
case "$POWERSAVE" in off|keep) ;; *) die "--keep-powersave takes no value" ;; esac

# ----------------------------------------------------------------------------
# Privilege helper
# ----------------------------------------------------------------------------
SUDO=""
if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then SUDO="sudo"; fi
run_root() { if [ -n "$SUDO" ]; then $SUDO "$@"; else "$@"; fi; }

write_root_file() {  # $1=path  $2=content
    local path="$1" content="$2"
    if [ "$DRY_RUN" -eq 1 ]; then
        info "(dry-run) would write $path:"; printf '%s\n' "$content" | sed 's/^/    /'; return 0
    fi
    run_root mkdir -p "$(dirname "$path")"
    printf '%s\n' "$content" | run_root tee "$path" >/dev/null
    ok "Wrote $path"
}

remove_root_file() {  # $1=path -- remove a stale config file if present
    local path="$1"
    [ -e "$path" ] || return 0   # /etc/* is world-readable; no sudo needed to check
    if [ "$DRY_RUN" -eq 1 ]; then info "(dry-run) would remove $path"; return 0; fi
    run_root rm -f "$path" && ok "Removed $path"
}

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
detect_iface() {  # print the first wireless interface, or empty
    local d
    for d in /sys/class/net/*/wireless; do
        [ -e "$d" ] || continue
        basename "$(dirname "$d")"; return 0
    done
    return 0
}

# ----------------------------------------------------------------------------
# Preflight
# ----------------------------------------------------------------------------
preflight() {
    info "Preflight checks (target driver: $DRIVER)"
    [ "$DRY_RUN" -eq 1 ] && return 0

    # The RTL8822CS SDIO device announces vendor:device 024c:c822.
    if grep -qi 'c822' /sys/bus/sdio/devices/*/device 2>/dev/null; then
        ok "RTL8822CS SDIO device present"
    else
        warn "Could not confirm an RTL8822CS SDIO device -- proceeding anyway."
    fi

    # The driver we want to KEEP must actually be installed.
    local keep_mod
    if [ "$DRIVER" = "88x2cs" ]; then keep_mod="$VENDOR_MOD"; else keep_mod="rtw88_8822cs"; fi
    if run_root modinfo "$keep_mod" >/dev/null 2>&1 || lsmod | grep -q "^${keep_mod//-/_} "; then
        ok "Driver '$keep_mod' is available"
    elif [ "$DRIVER" = "88x2cs" ]; then
        warn "Vendor module '$VENDOR_MOD' not found. Install it first, e.g.:"
        warn "    sudo apt-get install -y 8822cs-dkms   # or the Armbian 88x2cs dkms package"
        warn "Continuing to write config, but the radio will have no driver until it is installed."
    else
        warn "Mainline module 'rtw88_8822cs' not found in this kernel; check CONFIG_RTW88_8822CS."
    fi
}

# ----------------------------------------------------------------------------
# Setup steps
# ----------------------------------------------------------------------------
configure_driver() {
    if [ "$DRIVER" = "88x2cs" ]; then
        info "Selecting vendor 88x2cs; blacklisting mainline rtw88 stack"
        write_root_file "$BLACKLIST_RTW" \
            "# Managed by wifi-driver-setup.sh
# Keep the RTL8822CS on the vendor 88x2cs driver by blocking the mainline rtw88
# stack. Both drivers claim the SAME SDIO alias, so a soft 'blacklist' alone can
# race (we have seen both load at once). 'install <mod> /bin/true' is the hard
# block: it prevents loading via alias, as a dependency, AND via explicit modprobe.
$(for m in $RTW88_MODS; do echo "blacklist $m"; done)
$(for m in $RTW88_MODS; do echo "install $m /bin/true"; done)"
        remove_root_file "$BLACKLIST_VENDOR"   # in case we previously chose rtw88
    else
        info "Selecting mainline rtw88; blacklisting vendor 88x2cs"
        write_root_file "$BLACKLIST_VENDOR" \
            "# Managed by wifi-driver-setup.sh
# Keep the RTL8822CS on the mainline rtw88 driver by blocking the vendor module.
# Both drivers claim the SAME SDIO alias, so a soft 'blacklist' alone can race.
# 'install <mod> /bin/true' hard-blocks it (alias, dependency, and explicit load).
blacklist $VENDOR_MOD
install $VENDOR_MOD /bin/true"
        remove_root_file "$BLACKLIST_RTW"      # in case we previously chose 88x2cs
    fi

    # The blacklisted driver may live in the initramfs; rebuild so it isn't
    # loaded early and grab the radio before the blacklist applies.
    if [ "$DRY_RUN" -eq 1 ]; then
        info "(dry-run) update-initramfs -u"
    elif command -v update-initramfs >/dev/null 2>&1; then
        run_root update-initramfs -u 2>/dev/null && ok "Rebuilt initramfs" \
            || warn "update-initramfs failed; blacklist still applies to root fs modules"
    else
        warn "No update-initramfs; if the blacklisted driver is in your initramfs, rebuild it manually."
    fi
}

configure_powersave() {
    [ "$POWERSAVE" = "off" ] || { info "Leaving WiFi power-save untouched (--keep-powersave)"; return 0; }

    # Persistent: prefer NetworkManager (Armbian default). value 2 = disabled.
    if [ "$DRY_RUN" -eq 0 ] && command -v nmcli >/dev/null 2>&1 && run_root systemctl is-enabled NetworkManager >/dev/null 2>&1; then
        write_root_file "$NM_CONF" \
            "# Managed by wifi-driver-setup.sh -- disable WiFi power-save (2 = disabled)
[connection]
wifi.powersave = 2"
        run_root systemctl reload-or-restart NetworkManager 2>/dev/null \
            && ok "NetworkManager reloaded (power-save disabled for new activations)" \
            || warn "Could not reload NetworkManager; power-save config applies on next (re)activation."
        remove_root_file "$PS_SERVICE"   # drop the fallback unit if it existed
    else
        # Fallback: a boot-time oneshot that clears power_save on every wireless iface.
        [ "$DRY_RUN" -eq 1 ] && command -v nmcli >/dev/null 2>&1 && info "(dry-run) NetworkManager path or systemd fallback"
        write_root_file "$PS_SERVICE" \
            "# Managed by wifi-driver-setup.sh -- disable WiFi power-save at boot
[Unit]
Description=Disable WiFi power save
After=network.target

[Service]
Type=oneshot
ExecStart=/bin/sh -c 'for d in /sys/class/net/*/wireless; do i=\$(basename \"\$(dirname \"\$d\")\"); iw dev \"\$i\" set power_save off || true; done'

[Install]
WantedBy=multi-user.target"
        if [ "$DRY_RUN" -eq 0 ]; then
            run_root systemctl daemon-reload 2>/dev/null || true
            run_root systemctl enable wifi-powersave-off.service 2>/dev/null \
                && ok "Enabled wifi-powersave-off.service" \
                || warn "Could not enable wifi-powersave-off.service"
        fi
    fi
}

apply_powersave_now() {
    [ "$POWERSAVE" = "off" ] || return 0
    [ "$APPLY_NOW" -eq 1 ] || { info "Skipping live power_save toggle (--no-apply-now)"; return 0; }
    [ "$DRY_RUN" -eq 1 ] && { info "(dry-run) iw dev <wifi> set power_save off"; return 0; }
    command -v iw >/dev/null 2>&1 || { warn "iw not installed; skipping live power_save toggle"; return 0; }
    local iface; iface="$(detect_iface)"
    if [ -n "$iface" ]; then
        run_root iw dev "$iface" set power_save off 2>/dev/null \
            && ok "power_save off on $iface (now: $(iw dev "$iface" get power_save 2>/dev/null | awk '{print $3}'))" \
            || warn "Could not toggle power_save on $iface live (persistent config still applies)."
    else
        warn "No wireless interface found right now; live toggle skipped (persistent config still applies)."
    fi
}

maybe_reboot() {
    if [ "$DO_REBOOT" -eq 1 ]; then
        if [ "$DRY_RUN" -eq 1 ]; then info "(dry-run) reboot"; return 0; fi
        warn "Rebooting now to apply the driver swap..."
        run_root reboot
    fi
}

# ----------------------------------------------------------------------------
# Local vs remote
# ----------------------------------------------------------------------------
setup_local() {
    info "Configuring WiFi driver on this machine (driver=$DRIVER, powersave=$POWERSAVE)"
    preflight
    configure_driver
    configure_powersave
    apply_powersave_now
    cat <<EOF

${c_green}Done.${c_off}
  - Selected driver: ${DRIVER}$( [ "$DRIVER" = "88x2cs" ] && echo "  (vendor, fast path)" || echo "  (mainline)" )
  - The driver swap takes effect after a ${c_yellow}reboot${c_off}.
  - After reboot, verify the radio bound to the right driver:
        basename "\$(readlink /sys/class/net/wlan0/device/driver)"   # -> $( [ "$DRIVER" = "88x2cs" ] && echo 88x2cs || echo rtw88_sdio )
        lsmod | grep -E '88x2cs|rtw88'
  - Measure real WiFi throughput over the LAN (not an internet test):
        iperf3 -s              # on the host
        iperf3 -c <host-ip> -t 15        (and: -R for RX)
$( [ "$DO_REBOOT" -eq 0 ] && echo "  - Re-run with --reboot, or reboot manually, to switch drivers now." )
EOF
    maybe_reboot
}

setup_remote() {
    info "Configuring the CM4 ($TARGET_HOST) over ssh"
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "  (dry-run) scp $SELF $TARGET_HOST:/tmp/wifi-driver-setup.sh"
        echo "  (dry-run) ssh $TARGET_HOST sudo bash /tmp/wifi-driver-setup.sh --mode local ..."
        [ "$DO_REBOOT" -eq 1 ] && echo "  (dry-run) ...with --reboot"
        return 0
    fi
    command -v ssh >/dev/null 2>&1 || die "ssh not found; cannot configure the remote CM4"
    scp -q "$SELF" "$TARGET_HOST:/tmp/wifi-driver-setup.sh" || die "Failed to copy script to $TARGET_HOST"
    local rargs="--mode local --driver $DRIVER"
    [ "$POWERSAVE" = "keep" ] && rargs="$rargs --keep-powersave"
    [ "$APPLY_NOW" -eq 0 ]    && rargs="$rargs --no-apply-now"
    [ "$DO_REBOOT" -eq 1 ]    && rargs="$rargs --reboot"
    # Use sudo on the far side in case we logged in as a non-root user.
    # shellcheck disable=SC2029
    ssh -t "$TARGET_HOST" "sudo bash /tmp/wifi-driver-setup.sh $rargs" \
        || die "Remote setup failed on $TARGET_HOST"
    ok "CM4 configured$( [ "$DO_REBOOT" -eq 1 ] && echo " (rebooting)" )"
}

# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
case "$MODE" in
    local)  setup_local ;;
    remote) setup_remote ;;
esac
