#!/usr/bin/env python3
"""
max98397-diag.py — read/clear the MAX98397 fault & interrupt-status registers
directly over I2C on the BananaPi CM4 board.

The two MAX98397 smart amps (driven by the in-tree `max98396` codec driver) sit
on I2C bus 2 at 0x38 (amp2) and 0x39 (amp1). This tool reads the amp's LATCHED
interrupt registers to see whether any faults have accumulated — useful for
chasing clicks/pops during continuous playback. It talks to the bus directly via
the Linux i2c-dev I2C_RDWR ioctl (16-bit register addressing), so it needs no
i2c-tools and works even while the kernel driver has the device bound.

Why this helps with clicks/pops:
  The amp has clock- and data-monitor logic. If the I2S/TDM link glitches (e.g.
  a PipeWire buffer underrun / xrun, or an unstable BCLK/LRCLK), CLK_ERR /
  DMON_ERR / INT_CLK_ERR latch and the amp briefly mutes -> you hear a click.
  Supply sag latches PVDD_UVLO / the brownout engine (BPE_*) -> a pop. Reading
  these latched flags tells you WHICH is happening.

Registers used (same addresses on MAX98396/97):
  INT_RAW   0x2001..0x2004   live/instantaneous state
  INT_STATE 0x2006..0x2009   LATCHED, unmaskable (primary "accumulated errors")
  INT_FLAG  0x200B..0x200E   LATCHED, only for driver-enabled IRQ sources
  INT_CLR   0x2015..0x2018   write 1s here to clear the latched STATE/FLAG bits
  REV_ID    0x22FF (MAX98397) connectivity sanity check
  GLOBAL_EN 0x210F           amp enabled?      BPE_STATE 0x20E5  brownout level

Usage (run ON the board, needs root for /dev/i2c-2):
  sudo ./max98397-diag.py                 # report latched faults on both amps
  sudo ./max98397-diag.py --raw           # also show live (RAW) state
  sudo ./max98397-diag.py --clear         # clear all latched flags on both amps
  sudo ./max98397-diag.py --addr 0x39     # pick the I2C device (amp) to read
  sudo ./max98397-diag.py --reg 0x2006    # read ONE arbitrary register (decoded)
  sudo ./max98397-diag.py --addr 0x39 --reg 0x210F        # GLOBAL_EN of amp1
  sudo ./max98397-diag.py --reg 0x2015 --set 0xFF         # poke: clear FLAG bank 1
  sudo ./max98397-diag.py --watch 0.5     # clear, then poll every 0.5s and print
                                          # any faults that fire (play your sweep!)
Typical workflow for the click hunt:
  sudo ./max98397-diag.py --clear         # zero the counters
  # ... play the continuous sine / sweep ...
  sudo ./max98397-diag.py                 # see what latched
  # or, live:  sudo ./max98397-diag.py --watch 0.25   (Ctrl-C for a summary)
"""

import argparse
import ctypes
import fcntl
import glob
import os
import re
import sys
import time

# --- i2c-dev ioctl plumbing -------------------------------------------------
I2C_RDWR = 0x0707
I2C_M_RD = 0x0001


class i2c_msg(ctypes.Structure):
    _fields_ = [
        ("addr", ctypes.c_uint16),
        ("flags", ctypes.c_uint16),
        ("len", ctypes.c_uint16),
        ("buf", ctypes.POINTER(ctypes.c_uint8)),
    ]


class i2c_rdwr_ioctl_data(ctypes.Structure):
    _fields_ = [
        ("msgs", ctypes.POINTER(i2c_msg)),
        ("nmsgs", ctypes.c_uint32),
    ]


def read_reg16(fd, addr, reg):
    """Combined write(reg_hi, reg_lo) + read(1 byte) — 16-bit register read."""
    wbuf = (ctypes.c_uint8 * 2)((reg >> 8) & 0xFF, reg & 0xFF)
    rbuf = (ctypes.c_uint8 * 1)()
    msgs = (i2c_msg * 2)(
        i2c_msg(addr, 0, 2, ctypes.cast(wbuf, ctypes.POINTER(ctypes.c_uint8))),
        i2c_msg(addr, I2C_M_RD, 1, ctypes.cast(rbuf, ctypes.POINTER(ctypes.c_uint8))),
    )
    payload = i2c_rdwr_ioctl_data(ctypes.cast(msgs, ctypes.POINTER(i2c_msg)), 2)
    fcntl.ioctl(fd, I2C_RDWR, payload)
    return rbuf[0]


def write_reg16(fd, addr, reg, val):
    """Write one byte to a 16-bit register address."""
    wbuf = (ctypes.c_uint8 * 3)((reg >> 8) & 0xFF, reg & 0xFF, val & 0xFF)
    msgs = (i2c_msg * 1)(
        i2c_msg(addr, 0, 3, ctypes.cast(wbuf, ctypes.POINTER(ctypes.c_uint8))),
    )
    payload = i2c_rdwr_ioctl_data(ctypes.cast(msgs, ctypes.POINTER(i2c_msg)), 1)
    fcntl.ioctl(fd, I2C_RDWR, payload)


# --- Register addresses -----------------------------------------------------
INT_RAW = [0x2001, 0x2002, 0x2003, 0x2004]
INT_STATE = [0x2006, 0x2007, 0x2008, 0x2009]
INT_FLAG = [0x200B, 0x200C, 0x200D, 0x200E]
INT_CLR = [0x2015, 0x2016, 0x2017, 0x2018]
REG_REV_ID = 0x22FF     # MAX98397 (the '96 uses 0x21FF)
REG_GLOBAL_EN = 0x210F
REG_BPE_STATE = 0x20E5

ERR, WARN, INFO = "ERR", "WARN", "INFO"

# Bit tables, one per interrupt register index (RAW/STATE/FLAG/CLR share layout).
# (bit, name, description, severity)
BITS = [
    [  # reg1  0x2001 / 0x2006 / 0x200B / 0x2015
        (7, "THERMSHDN_BGN", "thermal shutdown entered (die over shutdown limit)", ERR),
        (6, "THERMSHDN_END", "thermal shutdown exited", INFO),
        (5, "THERMWARN1_BGN", "thermal warning 1 entered", WARN),
        (4, "THERMWARN1_END", "thermal warning 1 exited", INFO),
        (3, "THERMFB_BGN", "thermal foldback active (gain being reduced)", WARN),
        (2, "THERMFB_END", "thermal foldback ended", INFO),
        (1, "OTP_FAIL", "OTP load failed", ERR),
        (0, "SPK_OVC", "speaker overcurrent limit hit", ERR),
    ],
    [  # reg2  0x2002 / 0x2007 / 0x200C / 0x2016
        (7, "THERMWARN2_BGN", "thermal warning 2 entered", WARN),
        (6, "THERMWARN2_END", "thermal warning 2 exited", INFO),
        (5, "INT_SPKMON_ERR", "internal speaker monitor: data error", ERR),
        (4, "INT_CLK_ERR", "internal clock monitor: clock error", ERR),
        (2, "CLK_RECOVER", "clock error recovery (link re-locked after a glitch)", WARN),
        (1, "CLK_ERR", "external clock monitor: BCLK/LRCLK error", ERR),
        (0, "DMON_ERR", "external data monitor: I2S/TDM data error", ERR),
    ],
    [  # reg3  0x2003 / 0x2008 / 0x200D / 0x2017
        (7, "AVDD_UVLO", "AVDD undervoltage lockout", ERR),
        (6, "DVDD_UVLO", "DVDD undervoltage lockout", ERR),
        (5, "PWRUP_DONE", "power-up into active state completed", INFO),
        (4, "PWRDN_DONE", "power-down into shutdown completed", INFO),
        (3, "PVDD_UVLO_SHDN", "PVDD undervoltage (supply brownout)", ERR),
        (2, "VBAT_UVLO_SHDN", "VBAT undervoltage", ERR),
        (1, "DHT_ACTIVE_BGN", "dynamic headroom tracking active", INFO),
        (0, "DHT_ACTIVE_END", "dynamic headroom tracking ended", INFO),
    ],
    [  # reg4  0x2004 / 0x2009 / 0x200E / 0x2018
        (3, "BPE_L0", "brownout engine reached level 0", WARN),
        (2, "BPE_LEVEL", "brownout engine changing levels (gain moving)", WARN),
        (1, "BPE_ACTIVE_BGN", "brownout-prevention engine active", WARN),
        (0, "BPE_ACTIVE_END", "brownout-prevention engine no longer active", INFO),
    ],
]

# Bits that, when latched, are the prime suspects for clicks/pops.
CLICK_SUSPECTS = {
    "INT_SPKMON_ERR", "INT_CLK_ERR", "CLK_ERR", "DMON_ERR", "CLK_RECOVER",
    "SPK_OVC", "PVDD_UVLO_SHDN", "VBAT_UVLO_SHDN", "AVDD_UVLO", "DVDD_UVLO",
    "BPE_ACTIVE_BGN", "BPE_LEVEL", "BPE_L0", "THERMFB_BGN",
}

# --- Pretty output ----------------------------------------------------------
_USE_COLOR = sys.stdout.isatty()
_C = {
    ERR: "\033[1;31m", WARN: "\033[1;33m", INFO: "\033[2m",
    "ok": "\033[1;32m", "hd": "\033[1;34m", "off": "\033[0m",
}


def col(s, key):
    return f"{_C.get(key,'')}{s}{_C['off']}" if _USE_COLOR else s


def decode(regvals):
    """regvals: list of 4 byte values. Returns list of (sev,name,desc,reg,bit)."""
    out = []
    for i, val in enumerate(regvals):
        for bit, name, desc, sev in BITS[i]:
            if val & (1 << bit):
                out.append((sev, name, desc, INT_STATE[i], bit))
    return out


def bit_table_for(reg):
    """If `reg` is one of the RAW/STATE/FLAG/CLR interrupt regs, return its BITS row."""
    for i in range(4):
        if reg in (INT_RAW[i], INT_STATE[i], INT_FLAG[i], INT_CLR[i]):
            return BITS[i]
    return None


def print_hits(hits, title):
    if not hits:
        print(f"  {title}: {col('clean', 'ok')} (nothing latched)")
        return
    print(f"  {title}:")
    order = {ERR: 0, WARN: 1, INFO: 2}
    for sev, name, desc, reg, bit in sorted(hits, key=lambda h: order[h[0]]):
        star = " *" if name in CLICK_SUSPECTS else "  "
        line = f"    [{sev:<4}] 0x{reg:04X} bit{bit} {name:<15} {desc}{star}"
        print(col(line, sev))


# --- Device discovery -------------------------------------------------------
def detect_devices():
    """Return {bus: [addr,...]} for max98396-bound amps, from sysfs."""
    found = {}
    for p in glob.glob("/sys/bus/i2c/drivers/max98396/*"):
        m = re.match(r".*/(\d+)-([0-9a-fA-F]{4})$", p)
        if m:
            found.setdefault(int(m.group(1)), []).append(int(m.group(2), 16))
    for b in found:
        found[b].sort()
    return found


def open_bus(bus):
    try:
        return os.open(f"/dev/i2c-{bus}", os.O_RDWR)
    except PermissionError:
        sys.exit(f"error: need root to open /dev/i2c-{bus} — re-run with sudo")
    except FileNotFoundError:
        sys.exit(f"error: /dev/i2c-{bus} not found (is i2c-dev loaded / bus right?)")


# --- Per-amp actions --------------------------------------------------------
def amp_header(fd, addr):
    try:
        rev = read_reg16(fd, addr, REG_REV_ID)
        gen = read_reg16(fd, addr, REG_GLOBAL_EN)
        bpe = read_reg16(fd, addr, REG_BPE_STATE)
    except OSError as e:
        return f"amp 0x{addr:02X}: {col('NO RESPONSE', ERR)} ({e}) — check bus/addr/power", None
    if rev in (0x00, 0xFF):
        return f"amp 0x{addr:02X}: {col('NO RESPONSE', ERR)} (rev=0x{rev:02X}) — powered down?", None
    en = "on" if (gen & 0x01) else col("OFF", WARN)
    return (f"── amp @ i2c-{{bus}} 0x{addr:02X} — rev 0x{rev:02X} — "
            f"GLOBAL_EN={en} — BPE_STATE=0x{bpe:02X} ──"), rev


def report(fd, bus, addr, show_raw):
    hdr, rev = amp_header(fd, addr)
    print(col(hdr.replace("{bus}", str(bus)), "hd"))
    if rev is None:
        return []
    state = [read_reg16(fd, addr, r) for r in INT_STATE]
    flag = [read_reg16(fd, addr, r) for r in INT_FLAG]
    st_hits = decode(state)
    print_hits(st_hits, "Latched state (INT_STATE, accumulated since last --clear)")
    print_hits(decode(flag), "IRQ flags   (INT_FLAG, driver-enabled sources only)")
    if show_raw:
        print_hits(decode([read_reg16(fd, addr, r) for r in INT_RAW]),
                   "Live state  (INT_RAW, instantaneous right now)")
    return st_hits


def clear(fd, addr):
    for r in INT_CLR:
        write_reg16(fd, addr, r, 0xFF)


def interpret(all_hits):
    """Print a short 'what does this mean' summary across all amps."""
    names = {h[1] for h in all_hits}
    if not names:
        print(col("\nNo faults latched on any amp. If you still hear clicks, they are"
                  " upstream of the amp (DSP/PipeWire) — clear, reproduce, re-check with --watch.",
                  "ok"))
        return
    print("\n" + col("Interpretation:", "hd"))
    if names & {"CLK_ERR", "INT_CLK_ERR", "DMON_ERR", "INT_SPKMON_ERR", "CLK_RECOVER"}:
        print("  • Clock/data-monitor errors latched → the I2S/TDM link is glitching")
        print("    (PipeWire xruns/underruns or unstable BCLK/LRCLK). The amp mutes on")
        print("    each glitch = the click you hear. Cross-check `pw-top` ERR column and")
        print("    try a larger PipeWire quantum. This is the usual cause of playback clicks.")
    if names & {"PVDD_UVLO_SHDN", "VBAT_UVLO_SHDN", "AVDD_UVLO", "DVDD_UVLO",
                "BPE_ACTIVE_BGN", "BPE_LEVEL", "BPE_L0"}:
        print("  • Supply undervoltage / brownout-engine activity → PVDD sagging under")
        print("    load = a pop as the class-D output collapses. Check the PVDD rail/PSU.")
    if names & {"SPK_OVC"}:
        print("  • Speaker overcurrent → check speaker wiring/impedance and output level.")
    if names & {"THERMSHDN_BGN", "THERMWARN1_BGN", "THERMWARN2_BGN", "THERMFB_BGN"}:
        print("  • Thermal warning/foldback/shutdown → the die is hot; gain foldback")
        print("    changes level audibly. Improve cooling or reduce drive.")


def reg_mode(fd, bus, addrs, reg, setval):
    """Read (optionally write-then-read) one arbitrary register on each device."""
    tbl = bit_table_for(reg)
    if setval is not None:
        print(col("WARNING: writing an arbitrary register can conflict with the "
                  "kernel driver's regmap; proceed only if you know the register.", WARN))
    for a in addrs:
        try:
            if setval is not None:
                write_reg16(fd, a, reg, setval)
            val = read_reg16(fd, a, reg)
        except OSError as e:
            print(f"amp 0x{a:02X}: I/O error on reg 0x{reg:04X}: {e} — check bus/addr/power")
            continue
        wrote = f"  (wrote 0x{setval:02X})" if setval is not None else ""
        print(col(f"i2c-{bus} 0x{a:02X}  reg 0x{reg:04X} = 0x{val:02X}  0b{val:08b}{wrote}", "hd"))
        if tbl is not None:
            hit = False
            for bit, name, desc, sev in tbl:
                if val & (1 << bit):
                    hit = True
                    print(col(f"    bit{bit} {name:<15} {desc}", sev))
            if not hit:
                print(col("    (no bits set)", "ok"))


# --- Watch mode -------------------------------------------------------------
def watch(fd, bus, addrs, interval):
    print(col(f"Watching i2c-{bus} amps {', '.join(f'0x{a:02X}' for a in addrs)} "
              f"every {interval}s — clearing then reporting per-interval events. Ctrl-C for summary.",
              "hd"))
    for a in addrs:
        clear(fd, a)
    totals = {a: {} for a in addrs}
    try:
        while True:
            time.sleep(interval)
            stamp = time.strftime("%H:%M:%S")
            for a in addrs:
                state = [read_reg16(fd, a, r) for r in INT_STATE]
                hits = decode(state)
                if hits:
                    for sev, name, desc, reg, bit in hits:
                        totals[a][name] = totals[a].get(name, 0) + 1
                    tags = " ".join(col(h[1], h[0]) for h in hits)
                    print(f"  {stamp} 0x{a:02X}  {tags}")
                    clear(fd, a)
    except KeyboardInterrupt:
        print("\n" + col("Summary (events counted per amp):", "hd"))
        any_ev = False
        for a in addrs:
            if totals[a]:
                any_ev = True
                print(f"  0x{a:02X}:")
                for name, n in sorted(totals[a].items(), key=lambda kv: -kv[1]):
                    print(f"    {n:>6}  {name}")
        if not any_ev:
            print(col("  none — no faults fired during the watch window.", "ok"))


# --- main -------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Read/clear MAX98397 latched fault & interrupt-status registers over I2C.")
    ap.add_argument("--bus", type=int, help="I2C bus number (default: auto-detect, usually 2)")
    ap.add_argument("--addr", type=lambda x: int(x, 0), action="append", metavar="ADDR",
                    help="I2C device address of the amp, e.g. 0x38 (amp2) or 0x39 (amp1); "
                         "repeatable, default: both detected")
    ap.add_argument("--reg", type=lambda x: int(x, 0), metavar="REG",
                    help="read ONE arbitrary 16-bit register instead of the fault report, "
                         "e.g. --reg 0x2006 (decoded if it is a known interrupt register)")
    ap.add_argument("--set", type=lambda x: int(x, 0), metavar="VAL", dest="setval",
                    help="with --reg: WRITE this byte to the register first, then read back "
                         "(arbitrary poke — can fight the driver; use with care)")
    ap.add_argument("--clear", action="store_true", help="clear all latched flags, then show state")
    ap.add_argument("--raw", action="store_true", help="also show live INT_RAW state")
    ap.add_argument("--watch", nargs="?", type=float, const=0.5, metavar="SEC",
                    help="poll continuously (default 0.5s), printing faults as they fire")
    args = ap.parse_args()
    if args.setval is not None and args.reg is None:
        ap.error("--set requires --reg")

    detected = detect_devices()
    bus = args.bus if args.bus is not None else (
        next(iter(detected)) if detected else 2)
    addrs = args.addr if args.addr else (
        detected.get(bus) or [0x38, 0x39])

    fd = open_bus(bus)
    try:
        if args.reg is not None:
            reg_mode(fd, bus, addrs, args.reg, args.setval)
            return
        if args.watch is not None:
            watch(fd, bus, addrs, args.watch)
            return
        all_hits = []
        for a in addrs:
            if args.clear:
                before = [read_reg16(fd, a, r) for r in INT_STATE]
                clear(fd, a)
                n = sum(bin(v).count("1") for v in before)
                print(col(f"amp 0x{a:02X}: cleared ({n} latched bit(s) were set)", "ok"))
            all_hits += report(fd, bus, a, args.raw)
            print()
        if not args.clear:
            interpret(all_hits)
        print(col("\n('*' marks the bits most likely tied to clicks/pops.)", INFO))
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
