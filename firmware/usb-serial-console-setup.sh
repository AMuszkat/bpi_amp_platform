#!/usr/bin/env bash
#
# usb-serial-console-setup.sh
#
# Turn the BananaPi CM4's USB-C (OTG / peripheral) port into a serial console
# over USB, using the in-kernel USB CDC-ACM serial gadget (g_serial). After
# setup the board shows up on a connected PC as /dev/ttyACM0, and locally as
# /dev/ttyGS0 with a login prompt.
#
# IMPORTANT: this is a POST-BOOT console. The gadget only exists once Linux has
# booted and loaded g_serial -- it cannot show bootrom / U-Boot / early-kernel
# output or early panics. For full low-level boot debugging use the AO UART.
#
# Requirements (already satisfied by this repo's kernel):
#   - USB-C port in peripheral mode  (dr_mode = "peripheral" in the board DT)
#   - CONFIG_USB_DWC2=y (the OTG peripheral controller on g12b)
#   - CONFIG_USB_G_SERIAL=m, CONFIG_USB_GADGET=y, CONFIG_U_SERIAL_CONSOLE=y
#
# Usage:
#   ./usb-serial-console-setup.sh                         # configure THIS machine (the CM4)
#   ./usb-serial-console-setup.sh --mode remote \
#        --target-host user@bpi-amp.local             # ssh to the CM4 and configure it
#
# Run with --help for all options.

set -euo pipefail

# ----------------------------------------------------------------------------
# Defaults
# ----------------------------------------------------------------------------
MODE="local"                             # local | remote
TARGET_HOST="root@${BOARD_HOST:-bpi-amp.local}"  # ssh destination of the CM4; set BOARD_HOST (for --mode remote)
BAUD=115200                              # baud advertised to getty / kernel console
USE_ACM=1                                # 1 = CDC-ACM (host sees /dev/ttyACM0); 0 = generic gadget serial
LOGIN_CONSOLE=1                          # enable a login prompt (serial-getty@ttyGS0)
KERNEL_CONSOLE=0                         # also route the kernel console to ttyGS0 (experimental)
MM_IGNORE=1                              # tell ModemManager to ignore ttyGS0
START_NOW=1                              # load g_serial immediately (not just at next boot)
DRY_RUN=0

GADGET_MOD="g_serial"
TTY_DEV="ttyGS0"
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
  --baud <rate>               Baud for getty/kernel console (default: $BAUD; irrelevant to ACM data rate).
  --use-acm <0|1>             1 = CDC-ACM, host sees /dev/ttyACM0 (default: $USE_ACM).
  --no-login                  Do not enable the serial-getty login prompt on $TTY_DEV.
  --kernel-console            Also add console=$TTY_DEV to the kernel cmdline (experimental, edits boot env).
  --no-mm-ignore              Do not add the ModemManager-ignore udev rule for $TTY_DEV.
  --no-start                  Configure for next boot only; do not modprobe $GADGET_MOD now.
  --dry-run                   Print actions without changing anything.
  -h, -help, --help           Show this help.

Examples:
  # On the CM4 itself (login console over USB-C):
  sudo ./usb-serial-console-setup.sh

  # From the host, configure the CM4 over ssh:
  ./usb-serial-console-setup.sh --mode remote --target-host user@bpi-amp.local

  # Also make it the kernel console (captures kernel msgs once the gadget is up):
  sudo ./usb-serial-console-setup.sh --kernel-console
EOF
}

# ----------------------------------------------------------------------------
# Argument parsing
# ----------------------------------------------------------------------------
while [ $# -gt 0 ]; do
    case "$1" in
        --mode)          MODE="${2:?}"; shift 2 ;;
        --target-host)   TARGET_HOST="${2:?}"; shift 2 ;;
        --baud)          BAUD="${2:?}"; shift 2 ;;
        --use-acm)       USE_ACM="${2:?}"; shift 2 ;;
        --no-login)      LOGIN_CONSOLE=0; shift ;;
        --kernel-console) KERNEL_CONSOLE=1; shift ;;
        --no-mm-ignore)  MM_IGNORE=0; shift ;;
        --no-start)      START_NOW=0; shift ;;
        --dry-run)       DRY_RUN=1; shift ;;
        -h|-help|--help) usage; exit 0 ;;
        *)               die "Unknown option: $1 (try --help)" ;;
    esac
done
case "$MODE" in local|remote) ;; *) die "--mode must be local or remote" ;; esac
case "$USE_ACM" in 0|1) ;; *) die "--use-acm must be 0 or 1" ;; esac

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

backup_once() {  # back up a file to .orig the first time we touch it
    local f="$1"
    [ "$DRY_RUN" -eq 1 ] && return 0
    if run_root test -f "$f" && ! run_root test -f "$f.orig"; then
        run_root cp -a "$f" "$f.orig" && ok "Backed up $f -> $f.orig"
    fi
}

# ----------------------------------------------------------------------------
# Preflight: confirm the kernel/hardware can do this
# ----------------------------------------------------------------------------
preflight() {
    info "Preflight checks"
    # 1) g_serial module must exist in this kernel.
    if [ "$DRY_RUN" -eq 0 ] && ! run_root modinfo "$GADGET_MOD" >/dev/null 2>&1; then
        die "Kernel module '$GADGET_MOD' not found. The kernel must be built with CONFIG_USB_G_SERIAL."
    fi
    # 2) A USB Device Controller must be present (port actually in peripheral mode).
    if [ "$DRY_RUN" -eq 0 ]; then
        local udc; udc="$(ls /sys/class/udc 2>/dev/null | head -1)"
        if [ -n "$udc" ]; then
            ok "USB Device Controller present: $udc (peripheral mode active)"
        else
            warn "No UDC under /sys/class/udc -- the USB-C port may not be in peripheral mode."
            warn "Ensure the board DT has 'dr_mode = \"peripheral\"' on &usb (it does in this repo's DT),"
            warn "and that you boot that DTB. The gadget will only bind once a UDC exists."
        fi
    fi
    # 3) U_SERIAL_CONSOLE is needed for --kernel-console.
    if [ "$KERNEL_CONSOLE" -eq 1 ] && [ "$DRY_RUN" -eq 0 ]; then
        zcat /proc/config.gz 2>/dev/null | grep -q 'CONFIG_U_SERIAL_CONSOLE=y' \
            || warn "Could not confirm CONFIG_U_SERIAL_CONSOLE=y; kernel console on $TTY_DEV may not work."
    fi
}

# ----------------------------------------------------------------------------
# Setup steps
# ----------------------------------------------------------------------------
configure_module() {
    # Load g_serial at every boot, with the chosen ACM mode.
    write_root_file /etc/modprobe.d/usb-serial-gadget.conf \
        "# Managed by usb-serial-console-setup.sh
options $GADGET_MOD use_acm=$USE_ACM"
    write_root_file /etc/modules-load.d/usb-serial-gadget.conf \
        "# Managed by usb-serial-console-setup.sh -- load the USB serial gadget at boot
$GADGET_MOD"
}

mm_ignore_rule() {
    [ "$MM_IGNORE" -eq 1 ] || return 0
    write_root_file /etc/udev/rules.d/99-ttyGS0-mm-ignore.rules \
        "# Managed by usb-serial-console-setup.sh -- keep ModemManager off the console tty
SUBSYSTEM==\"tty\", KERNEL==\"$TTY_DEV\", ENV{ID_MM_DEVICE_IGNORE}=\"1\""
}

load_now() {
    [ "$START_NOW" -eq 1 ] || { info "Skipping immediate load (--no-start)"; return 0; }
    if [ "$DRY_RUN" -eq 1 ]; then info "(dry-run) modprobe $GADGET_MOD use_acm=$USE_ACM"; return 0; fi
    info "Loading $GADGET_MOD now"
    run_root modprobe -r "$GADGET_MOD" 2>/dev/null || true
    if run_root modprobe "$GADGET_MOD" "use_acm=$USE_ACM"; then
        sleep 1
        [ -e "/dev/$TTY_DEV" ] && ok "/dev/$TTY_DEV is present" \
            || warn "/dev/$TTY_DEV not present yet (appears once a UDC binds / cable connected)"
    else
        warn "modprobe $GADGET_MOD failed (will still be configured for next boot)"
    fi
}

enable_login() {
    [ "$LOGIN_CONSOLE" -eq 1 ] || { info "Skipping login console (--no-login)"; return 0; }
    if [ "$DRY_RUN" -eq 1 ]; then info "(dry-run) systemctl enable --now serial-getty@$TTY_DEV.service"; return 0; fi
    info "Enabling login console on $TTY_DEV"
    run_root systemctl enable "serial-getty@$TTY_DEV.service" 2>/dev/null || true
    run_root systemctl restart "serial-getty@$TTY_DEV.service" 2>/dev/null \
        && ok "Login prompt active on $TTY_DEV" \
        || warn "Could not start serial-getty@$TTY_DEV (it will start at boot when $TTY_DEV exists)"
}

enable_kernel_console() {
    [ "$KERNEL_CONSOLE" -eq 1 ] || return 0
    info "Configuring kernel console on $TTY_DEV (experimental)"
    # g_serial must be in the initramfs so the console comes up as early as possible.
    if run_root test -d /etc/initramfs-tools; then
        write_root_file /etc/initramfs-tools/modules \
            "$(run_root cat /etc/initramfs-tools/modules 2>/dev/null | grep -v "^$GADGET_MOD$"; echo "$GADGET_MOD")"
        [ "$DRY_RUN" -eq 0 ] && { run_root update-initramfs -u 2>/dev/null && ok "Rebuilt initramfs" || warn "update-initramfs failed"; }
    else
        warn "No /etc/initramfs-tools; skipping early-load. Kernel console will start late."
    fi
    # Append console=ttyGS0,<baud> to the boot cmdline.
    local env=/boot/armbianEnv.txt
    if run_root test -f "$env"; then
        backup_once "$env"
        if [ "$DRY_RUN" -eq 1 ]; then
            info "(dry-run) add 'console=$TTY_DEV,$BAUD' to extraargs in $env"
        elif run_root grep -q "console=$TTY_DEV" "$env"; then
            ok "$env already references $TTY_DEV"
        elif run_root grep -q '^extraargs=' "$env"; then
            run_root sed -i "s|^extraargs=\(.*\)|extraargs=\1 console=$TTY_DEV,$BAUD|" "$env" && ok "Appended to extraargs in $env"
        else
            printf 'extraargs=console=%s,%s\n' "$TTY_DEV" "$BAUD" | run_root tee -a "$env" >/dev/null && ok "Added extraargs to $env"
        fi
    else
        warn "No $env found. Add 'console=$TTY_DEV,$BAUD' to your kernel cmdline manually."
    fi
    warn "Kernel console still cannot capture bootrom/U-Boot/early-kernel output -- use the AO UART for that."
}

# ----------------------------------------------------------------------------
# Local vs remote
# ----------------------------------------------------------------------------
setup_local() {
    info "Configuring USB-C serial console on this machine (acm=$USE_ACM, login=$LOGIN_CONSOLE, kernel=$KERNEL_CONSOLE)"
    preflight
    configure_module
    mm_ignore_rule
    load_now
    enable_login
    enable_kernel_console
    cat <<EOF

${c_green}Done.${c_off}
  - On a PC connected to the CM4's USB-C port, open the console:
        screen /dev/ttyACM0 $BAUD       (or: picocom -b $BAUD /dev/ttyACM0)
  - Device side: /dev/$TTY_DEV$( [ "$LOGIN_CONSOLE" -eq 1 ] && echo "  (login prompt enabled)" )
  - Persists across reboots (g_serial via /etc/modules-load.d).
$( [ "$KERNEL_CONSOLE" -eq 1 ] && echo "  - Kernel console added to cmdline; reboot to take effect." )
  - Remember: no bootrom/U-Boot/early-boot output here -- that needs the AO UART.
EOF
}

setup_remote() {
    info "Configuring the CM4 ($TARGET_HOST) over ssh"
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "  (dry-run) scp $SELF $TARGET_HOST:/tmp/usb-serial-console-setup.sh"
        echo "  (dry-run) ssh $TARGET_HOST sudo bash /tmp/usb-serial-console-setup.sh --mode local ..."
        return 0
    fi
    command -v ssh >/dev/null 2>&1 || die "ssh not found; cannot configure the remote CM4"
    scp -q "$SELF" "$TARGET_HOST:/tmp/usb-serial-console-setup.sh" || die "Failed to copy script to $TARGET_HOST"
    local rargs="--mode local --baud $BAUD --use-acm $USE_ACM"
    [ "$LOGIN_CONSOLE" -eq 0 ]  && rargs="$rargs --no-login"
    [ "$KERNEL_CONSOLE" -eq 1 ] && rargs="$rargs --kernel-console"
    [ "$MM_IGNORE" -eq 0 ]      && rargs="$rargs --no-mm-ignore"
    [ "$START_NOW" -eq 0 ]      && rargs="$rargs --no-start"
    # Use sudo on the far side in case we logged in as a non-root user.
    # shellcheck disable=SC2029
    ssh -t "$TARGET_HOST" "sudo bash /tmp/usb-serial-console-setup.sh $rargs" \
        || die "Remote setup failed on $TARGET_HOST"
    ok "CM4 configured"
}

# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
case "$MODE" in
    local)  setup_local ;;
    remote) setup_remote ;;
esac
