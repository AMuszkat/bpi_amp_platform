# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this directory is

Out-of-tree kernel customization for a **BananaPi BPI-CM4** module on a **CM4IO baseboard** (Amlogic A311D, `g12b` SoC). The end goal is dual **MAX98397** I2S/TDM smart amplifiers (driven by the in-tree `max98396` codec driver) wired to the SoC's TDM_A interface, exposed to userspace through PipeWire.

This is the `firmware/` half of the BPI AMP Platform repo (the KiCad boards live in `../hardware/`). It holds *only* the deltas — device tree, kernel `defconfig`, patches, locally-iterated drivers, and PipeWire config. It does **not** contain a kernel tree. The mainline source (stable `v6.12.22`) is expected at `linuxMainLine/` (gitignored via `/linux*/`) or wherever `LINUX` points in `local.mk`, referenced as `$(LINUX)` in the Makefile.

## Build & deploy workflow

The top-level `Makefile` cross-builds on the host and pushes artifacts to the live board over SSH. Key variables at the top of `Makefile` must match the board's running kernel: `TARGET_KERNEL1/2`, `DTB1`. The board address, ssh user and kernel tree path are **not** in the repo (it is public): they come from `local.mk` (gitignored; template `local.mk.example`) or the environment — `BOARD_HOST`, `BOARD_USER`, `LINUX`. `TARGET_MACHINE` defaults to `root@$(BOARD_HOST)`. Never commit real hostnames, IPs or usernames; use placeholders like `bpi-amp.local` / `user` in docs and examples.

```sh
make config          # copy defconfig into $(LINUX), run `make defconfig`, scp .config to board
make image           # build Image, gzip -> vmlinuz-$(TARGET_KERNEL)
make dtbs            # copy meson-*.dts/.dtsi into the kernel tree and build DTBs
make modules         # build kernel modules
make patch           # git-apply every patch/*.patch into $(LINUX)
make all             # image + install_image + dtbs + install_dtbs + modules + install_modules + initrd + update_symlink
make install         # install_image + install_dtbs + install_modules (no rebuild)
```

`install_*` targets `scp`/`ssh` directly into the target's `/boot` and `/lib/modules`. `make all` reboots-worthy: it also regenerates initrd and flips the `/boot` symlinks. There is no test suite — verification is done by booting the board and checking ALSA/PipeWire.

Note `make dtbs` **overwrites** `$(LINUX)/arch/arm64/boot/dts/amlogic/Makefile` to build only `DTB1`.

## Local on-target module iteration (`localBuild/`)

`localBuild/` exists to rebuild individual codec/sound-card modules **natively on the board** (against `/lib/modules/$(uname -r)/build`) rather than cross-compiling the whole tree — much faster for driver iteration. `localBuild/Makefile` ssh's source to the board, builds, and copies the `.ko` back here *and* into the board's module tree.

```sh
cd localBuild
make all          # build & deploy dummy-codec-am.ko
make axg_card     # build & deploy snd-soc-meson-axg-sound-card.ko
make max98396     # build & deploy snd-soc-max98396.ko  (source: max98396.c/.h)
```

`targetMakefile_*` are the per-module kbuild Makefiles shipped to the board (renamed to `Makefile` on the far side). Update `TARGET_KERNEL` in `localBuild/Makefile` when the board's kernel changes.

## Audio architecture (the core of this project)

Signal path out: `FRDDR_A` (DMA fifo) → `TDMOUT_A` → `TDM_A` interface → two MAX98397 amps on I2C2, sharing one 8-slot TDM bus. Signal path back: the amps' `DOUT` telemetry → `TDM_A` → `TDMIN_A` → `TODDR_A`, added for the amplifier scope (see **Amplifier telemetry** below).

- **`meson-g12b-bananapi-cm4-cm4io.dts`** — baseboard DT. Defines the `amlogic,axg-sound-card` `sound` node and its `dai-link`s. The two amps are declared under `&i2c2` as `max98397_1` (`reg=0x39`, `amp1`) and `max98397_2` (`reg=0x38`, `amp2`), both `compatible = "adi,max98397"`. `dai-link-2` uses `dsp_a` format, 8 slots, 32-bit slot width (it was `dai-link-1` until the `toddr_a` capture front end took that index).
- **`meson-g12b-bananapi-cm4.dtsi`** — module-level DT (regulators, eMMC/SDIO, CPU OPP, `&tdmif_a`/`&tdmout_a`/`&frddr_a` enablement, pinctrl). Edit here for anything module-wide; edit the `.dts` for baseboard/audio-card topology.
- **`localBuild/max98396.c` / `.h`** — local copy of the in-tree MAX98396 codec driver (the MAX98397 is handled by this driver). This is where amp-driver fixes are prototyped before upstreaming.
- **`localBuild/snd-soc-meson-axg-sound-card.c`** — local copy of the Amlogic AXG sound-card machine driver, for iterating on DAI-link / multi-codec handling.
- **`localBuild/dummy-codec-am.c`** — a minimal stub ASoC codec (`custom,dummy-codec-am`, DAI `dummy-codec-am-hifi`) used to bring up / debug the TDM link without real amp hardware. Its DT node is currently commented out in the `.dts`.
- **`pipewire/pipewire.conf`** — PipeWire daemon config (v0.3.65). Pinned to `default.clock.rate = 44100` and `link.max-buffers = 16`. Deploy to `/etc/pipewire` or `~/.config/pipewire` on the board.

When changing the TDM topology, the DT (`dai-link` slots/masks), the codec DT nodes, and the amp driver's slot configuration must stay consistent, or the card won't probe.

## Amplifier telemetry (measurement ADC + IV sense)

The MAX98397 can report what it is doing to itself, by two routes, and both needed driver work
before anything came out. This is what the `ampScope` JUCE plugin (a separate
project) reads.

Both routes are things the **driver** publishes -- the ALSA control interface and an ALSA capture
stream. Userspace never touches the part directly (`max98397-diag.py` is the deliberate exception,
and it exists to read latched fault bits the driver does not expose). Anything a client needs that
is not on one of those two interfaces is a gap in the driver, which is how the slot-report controls
below came to exist.

**Route 1 -- the measurement ADC, through the control interface.** A 9-bit ADC with PVDD, VBAT,
VDDH_SNS and thermal channels, read back through the driver's `ampN ADC PVDD` / `ADC VBAT` /
`ADC VDDH` / `ADC TEMP` mixer controls, which the driver services over I2C. Those controls existed already but **always read zero**: nothing ever enabled the
channels. They are manual enables in *Meas ADC Config*, which on the '97 is **0x20C8**, not the
'96's 0x20C7 -- 0x20C7 on a '97 is *Meas ADC Optimal Mode*. `max98396_telemetry_setup()` now
switches PVDD, VBAT and (on a '97) VDDH on at probe. This touches no pin, so it is unconditional.
Conversions, from the Rev 3 datasheet: PVDD and VDDH are 54.703125 mV per code, VBAT 11.3965 mV,
thermal 1 degC per code minus 252 degC.

**Route 2 -- voltage/current sense on DOUT, through a capture stream.** IV sense is a continuous
16-bit-per-sample stream at 48-192 kHz, so a PCM device is the right interface for it and the
control interface could not carry it however much the driver were extended. This is the one that
behaves like a scope, and it needed four separate fixes plus a device-tree capture path.

The '97's register map is the '96's with **two extra TX Control registers inserted**, so everything
from the Hi-Z block onwards shifts. The local driver had already been fixed for RX Source and Bypass
Source (0x2055/0x2056/0x2058 -> 0x2057/0x2058/0x2059); the transmit side had not:

| what | '96 | '97 | what the driver had |
|---|---|---|---|
| PCM TX Control 9 / 10 (THERM, ICC slots) | -- | 0x204C / 0x204D | 9 only |
| PCM Tx HiZ Control 1..8 | 0x204C..0x2053 | **0x204E..0x2055** | 0x204D..0x2054 (off by one) |
| PCM TX Source Enables | 0x205D | **0x205C** | wrote 0x205D on both |
| PCM TX Source Enables 2 (THERM) | -- | 0x205D | -- |
| PCM Tx Supply Select | -- | 0x2060 | present |
| Meas ADC Optimal Mode | -- | **0x20C7** | called 0x20C5 |
| Meas ADC Config (channel enables) | 0x20C7 | **0x20C8** | never written |

Consequences of the old addresses, all of which had to be true at once for the feature to be dead:
writing VMON|IMON to 0x205D on a '97 set the *thermal* enable and left voltage and current sense
off; and the Hi-Z block being one register low meant slots 0-7 kept their reset value of 0xFF, so
those slots were Hi-Z no matter what. Nothing was ever driven on DOUT.

Also fixed: transmit slot numbers are **6 bits on the '97** (64 slots), not 4 -- the driver masked
every `adi,*-slot-no` with 0xF, which cannot reach past slot 15, and an 8-channel 32-bit frame is 32
amp slots wide. And the codec DAI's `capture.channels_max` was 2, which is not enough once a supply
rail and the temperature have slots of their own; it is now 8.

**Slots are 8 bits.** A transmit frame is up to 64 sequential 8-bit slots, so one 32-bit TDM slot
covers four of them and a 16-bit datum at amp slot *s* lands in TDM channel *s*/4, shifted left by
`16 - 8 * (s % 4)`. `s % 4 == 3` straddles two channels. This is the single most confusing thing
about the part and is why the board's slot plan counts in fours. The slot registers confirm it:
their decode reads "0x0: Slot 00/01, 0x1: Slot 01/02, ...", i.e. the value names the FIRST of the
two 8-bit slots a 16-bit datum occupies.

**What can go on the bus.** Per Table 6, the '97 can transmit voltage sense, current sense, the DSP
monitor, the applied DHT attenuation, Supply1 (PVDD), Supply2 (VBAT), the BPE level, device status
flags, ICC data and the die temperature -- each with its own enable and slot. So the supply rail is
genuinely available at the PCM sample rate, not only through the I2C readback; the board uses that
for PVDD and leaves Supply2 unassigned (VBAT is a 5 V housekeeping rail and the capture is kept to
four channels).

**New device-tree properties** (all on the codec node, all optional):

- `adi,telemetry-enable` -- the opt-in that actually lets the amp drive its slots. Without it the
  driver leaves DOUT fully Hi-Z exactly as before. It is gated on purpose: the amps share one DOUT
  line and arbitrate only by Hi-Z, so two amps driving the same slot is two push-pull outputs
  shorted together.
- `adi,supply1-slot-no`, `adi,supply2-slot-no`, `adi,therm-slot-no` -- '97 only; unset means that
  data type is not transmitted at all. **Supply1 and Supply2 are named, not generic**: the transmit
  enables define them, `PCM_SUPPLY1_EN` = "enables transmit of the measured PVDD supply voltage",
  `PCM_SUPPLY2_EN` = "the measured VBAT supply voltage". So `supply1-slot-no` puts PVDD on the bus
  and `supply2-slot-no` puts VBAT on it, and a capture client can decode them without being told.
- `adi,supply-select` -- the raw `SUPPLY_SELECT[1:0]` value, default 0. **Not an enum, because the
  datasheet does not document one**: the DECODE column for that field (0x2060) is a copy-paste of
  `PCM_TX_EN`'s ("0: PCM data output disabled / 1: enabled"), which cannot be what a 2-bit supply
  selector means. The reset state is the documented one -- Supply1 = PVDD, Supply2 = VBAT -- so
  leave it at 0 unless you have better information than the datasheet gives. An earlier version of
  this driver exposed it as an enum reading "PVDD / VBAT / VDDH"; that mapping was invented.

**New ALSA controls.** Enables and rates for the measurement ADC (`Meas ADC PVDD Switch`,
`… VBAT Switch`, `… VDDH Switch`, `… PVDD Rate`, `… VBAT Rate`, `… VDDH Rate`,
`… PVDD Filter Switch`, `… PVDD Filter BW`); the DOUT source enables (`PCM VMON Switch`,
`PCM IMON Switch`, `PCM Supply1 Switch`, `PCM Supply2 Switch`, `PCM Therm Switch`) and the raw
`PCM Supply Select`; the '97's `ADC VDDH`, `ADC Lowest PVDD` and `ADC Lowest VDDH` readbacks; and
five **read/write** slot controls, `PCM VMON Slot` / `PCM IMON Slot` / `PCM Supply1 Slot` /
`PCM Supply2 Slot` / `PCM Therm Slot`, where 64 means "not transmitted". Writing one gives the old
slot back to the bus before claiming the new one (so a move never has this amp driving two slots at
once) and switches that data type's transmit enable to match -- a data type with nowhere to go and
one that is switched off are the same thing, so probe and the controls derive the enables the same
way. This is what lets a client lay the frame out at runtime instead of only through the DT.

**The one control nothing works without** is `ampN VI Sense Switch`. It is a DAPM switch in front of
`PCM_TX_EN`, so the whole transmit path stays powered down until userspace closes it -- the amp is
silent however its slots are set. `ampScope` writes it (with the two `Meas ADC * Switch` rails behind
the supply data types) at start-up and on every device change. Those four exist so a capture client can find this amp's data in the frame without
being told -- with several amps on one DOUT line the answer differs per amp, and nothing else in
userspace knows it.

**Device tree: the capture path.** `meson-g12b-bananapi-cm4.dtsi` adds `&tdm_a_din0_pins` to
`&tdmif_a` and enables `&tdmin_a` and `&toddr_a`; `meson-g12b-bananapi-cm4-cm4io.dts` adds
`&tdmin_a` to `audio-aux-devs`, the routes `"TDMIN_A IN 0", "TDM_A Capture"` and
`"TODDR_A IN 0", "TDMIN_A OUT"`, a `toddr_a` front end (which renumbered the TDM link to
`dai-link-2`), and `dai-tdm-slot-rx-mask-0 = <1 1 1 1 0 0 0 0>`. The slot plan:

```
amp slot  0 /  2  -> TDM slot 0: amp1 VMON | amp1 IMON
amp slot  4 /  6  -> TDM slot 1: amp2 VMON | amp2 IMON
amp slot  8 / 10  -> TDM slot 2: amp1 PVDD | amp1 TEMP
amp slot 12 / 14  -> TDM slot 3: amp2 PVDD | amp2 TEMP
```

**Untested against hardware.** The driver builds clean (W=1) and the DTB compiles, but whether the
CM4IO baseboard actually routes the amps' DOUT to the SoC's TDM_A DIN pin is a wiring question this
repo cannot answer. If it does not, route 2 will stay silent and only the I2C measurement ADC works;
that is exactly what ampScope's "Control (I2C)" source is for.

## MAX98397 fault diagnostics (`max98397-diag.py`)

`max98397-diag.py` runs **on the board** and reads (or clears) the MAX98397's latched interrupt/fault registers directly over I2C, to chase clicks/pops during playback. It's dependency-free Python — it talks to the bus via the Linux `i2c-dev` `I2C_RDWR` ioctl (16-bit register addressing), so it needs **no i2c-tools** and works even while the `max98396` driver has the device bound (the `I2C_RDWR` path skips the `I2C_SLAVE` busy check, like i2ctransfer's `-f`). Needs **root** (`/dev/i2c-2` is `root:i2c`; the default board user isn't in the `i2c` group). Auto-detects bus+addrs from `/sys/bus/i2c/drivers/max98396/<bus>-00<addr>` (→ bus 2, 0x38 amp2 / 0x39 amp1).

- Reads **INT_STATE** (0x2006–0x2009, latched + *unmaskable* — the primary "accumulated errors", catches events even if the driver didn't enable that IRQ) and **INT_FLAG** (0x200B–0x200E, driver-enabled IRQ sources); `--raw` adds **INT_RAW** (0x2001–0x2004, live). `--clear` writes 0xFF to **INT_FLAG_CLR** (0x2015–0x2018). Connectivity sanity via **REV_ID 0x22FF** (the '97's; the '96 uses 0x21FF), plus GLOBAL_EN (0x210F) and BPE_STATE (0x20E5).
- Decodes each bit with severity and flags the click/pop suspects (`*`): clock/data monitor (`CLK_ERR`/`DMON_ERR`/`INT_CLK_ERR`/`INT_SPKMON_ERR`/`CLK_RECOVER` → I2S/TDM glitch/underrun → amp mutes → click), supply (`PVDD_UVLO_SHDN`/`*_UVLO`/`BPE_*` → brownout → pop), `SPK_OVC`, thermal (`THERM*`/foldback). Prints an interpretation summary tying clock errors back to PipeWire xruns/quantum.
- `--watch [sec]` clears then polls (default 0.5s), printing faults as they fire and a per-bit count summary on Ctrl-C — run it while playing the sine sweep to correlate audible clicks with which register bit latches.
- Device select: `--addr 0x38` (amp2) / `--addr 0x39` (amp1), repeatable, default both. Arbitrary register access: `--reg 0xADDR` reads one 16-bit register (auto-decoded if it's a known interrupt reg), and `--reg 0xADDR --set 0xVAL` pokes it (warns — can fight the driver's regmap).

Workflow: `sudo ./max98397-diag.py --clear` → play the sine/sweep → `sudo ./max98397-diag.py` (or `sudo ./max98397-diag.py --watch 0.25` live). Register/bit map source: `localBuild/max98396.h` + the MAX98397 datasheet (Analog Devices).

## Patches (`patch/`)

`patch/*.patch` are mainline kernel patches applied via `make patch` (git-apply) before building — board enablement (`board-bananapi-cm4-cm4io.patch`), Amlogic SoC/MMC/VDEC fixes, overlay-compilation support, etc. `patch/overlay/*.dtso` are device-tree overlay sources (i2c/uart/w1 bus activation); see `patch/overlay/README.meson-overlays`. Relevant to audio: `general-sound-soc-remove-mono-channel-as-it-curren.patch` and `general-meson-aiu-Fix-HDMI-codec-control-selection.patch`.

## Synchronized network audio streaming (`snapcast-setup.sh`)

`snapcast-setup.sh` sets up **Snapcast over PipeWire** to stream audio from a Linux host (server) to one or more Linux boards (clients), with tight (~1 ms) inter-client sync — suitable for a synchronized stereo pair (two boards as L and R). Snapcast was chosen over Roc Toolkit because Roc's per-receiver latency loops drift (no inter-receiver sync), whereas Snapcast schedules playback against a shared server clock. The trade-off is a buffered end-to-end latency (`--buffer`, default 1000 ms; lower for less delay, higher for Wi-Fi robustness).

Architecture:
- **Server (host):** the host is always assumed to run PipeWire, so `snapserver` uses Snapcast's **native `pipewire://` source** (confirmed built into the Ubuntu 0.34.0 package) — `source = pipewire://?name=multi-room&capture_sink=true` (do **not** add `send_silence=true` — with the pipewire capture source it feeds clients silence instead of the captured audio, so the receiver plays nothing). With `capture_sink=true` the source creates **its own single capture sink** (shown as "Snapcast/multi-room" in pavucontrol/qpwgraph) — route audio there to stream it. Do **not** add a separate `null-audio-sink`: snapserver always makes its own, so a hand-rolled one is just an unlinked decoy (an earlier version of this script did that and produced two confusing "Snapcast" nodes, the pretty-named one being dead). Note snapserver's sink has no `node.description`, so `wpctl`/pavucontrol may label it `(null)` even though qpwgraph shows `Snapcast/multi-room`; WirePlumber `stream.rules` can't rename it (it's an `Audio/Sink`, not a `Stream/*`). Because the native source needs the user's PipeWire session, snapserver runs as a **systemd user service** (`~/.config/systemd/user/snapserver.service`); the packaged *system* service is disabled to avoid a port clash, and `loginctl enable-linger` keeps it headless. Managed `/etc/snapserver.conf` backs up any original to `.orig`. Re-running `--mode host` also deletes a stale `60-snapcast-sink.conf` from the old decoy approach. The host setup also writes a **PipeWire quantum/rate drop-in** at `~/.config/pipewire/pipewire.conf.d/10-quantum.conf` (min/max-quantum 64/1024, `default.clock.rate = 48000`, with commented knobs for `force-quantum`/`allowed-rates`) — bigger quantum buys headroom against xruns/clicks in the captured stream — then restarts the user PipeWire (`pipewire`/`pipewire-pulse`/`wireplumber`) so it applies before snapserver starts. Skip with `--no-quantum`. The drop-in rate (**48000**) is aligned with the default `--sampleformat 48000:32:2` so PipeWire does not resample; if you change the stream rate, change both.
- **Client (board):** `snapclient` uses its **native PipeWire player** (`--player pipewire`), so it appears as a node ("Snapcast") in the board's PipeWire graph; routing that node to the AXG/MAX98397 sink is left to the user / WirePlumber (no auto channel-split — see history note below). It runs as a **systemd user service** (`~<user>/.config/systemd/user/snapclient.service`; user from `--client-user`, else the `user@` of `--target-host`, else the invoking/sudo user, else `$BOARD_USER`) sharing that user's PipeWire; the packaged system service is disabled and `loginctl enable-linger` keeps it headless. **Version gotcha:** the board's stock snapclient may be too old for `--player pipewire` (e.g. Debian bullseye ships 0.26.0, which has no pipewire player and exits with "No audio player support for: pipewire"). `ensure_snapclient_with_pipewire` checks for the player (`strings … | grep pipewire_player`) and, if missing, downloads the matching official release `.deb` — specifically the **`_with-pipewire`** variant for the board's `dpkg --print-architecture` + `/etc/os-release` `VERSION_CODENAME` (e.g. `snapclient_0.34.0-1_arm64_bullseye_with-pipewire.deb`) — and `apt-get install`s it (deps auto-resolved). Default version `--snapclient-version 0.34.0` to match the server.
- **Client auto-routing (`client-rt.conf`):** the target setup also deploys `~/.config/pipewire/client-rt.conf` (from `target_client_rt_conf()`; backs up any existing to `.orig`) whose `stream.rules` auto-connect nodes as they appear — snapclient → **`Ap.cap`**, AudioPluginHost output → the amp sink (`bpi-alsa-playback-pcm`), AudioPluginHost input ← AirPlay (`Ap.play`), shairport → AirPlay capture (`Ap.cap`). **Persistent-target rule (the important one):** snapclient must target a node that exists *from boot*. `Ap.cap`/`Ap.play` are created by a `module-loopback` in `pipewire.conf`, so they're always present (like the amp) — targeting them links at boot with no race. The AudioPluginHost's own input (`ALSA Capture`) is **transient** (exists only while that GUI app is open, which happens *after* snapclient starts), so pointing snapclient there makes WirePlumber fall back to the default sink (the amp) at boot — the reported bug. Targeting `Ap.cap` (the same loopback shairport feeds) reaches the plugin host via `Ap.cap → Ap.play → AudioPluginHost input`, exactly like AirPlay. (WirePlumber 0.4.13 — bookworm — has no `node.linger`/`node.dont-fallback`, and `node.dont-reconnect` *destroys* the persistent snapclient node instead of waiting, so the persistent-target approach is the only clean config-only fix; `node.linger` would work on WP 0.5+.) **Config gotcha:** snapclient (native pipewire player) loads `client.conf`, *not* `client-rt.conf`, so the unit sets `Environment=PIPEWIRE_CONFIG_NAME=client-rt.conf` to point it at that file (same reason a `client-rt.conf.d` drop-in for `resample.quality` had no effect but `client.conf.d` did). Skip with `--no-client-rt` (snapclient then uses the default `client.conf` and the node must be wired manually). Adjust the `target.object` values in `target_client_rt_conf()` to match your own graph's node names.

Modes mirror the build workflow: `--mode host` (server here), `--mode target` (client here, needs `--server-ip`), `--mode target-remote` (`scp`+`ssh -t … sudo` to `--target-host`, default `root@$BOARD_HOST` (placeholder `bpi-amp.local`), server IP auto-detected, `--client-user` and the ssh user from `user@host` forwarded), `--mode both` (server here + remote client). Server ports: 1704 stream / 1705 control / 1780 http. `--dry-run` prints all config without applying.

History note: an earlier version did automatic L/R channel-split on the client via a `null-audio-sink` + `module-loopback` (`stream.dont-remix`), driven by `--channel left|right`. That was removed in favor of the user wiring the node manually; if a synchronized stereo pair is wanted again, that split approach is the reference.

## USB-C serial console (`usb-serial-console-setup.sh`)

`usb-serial-console-setup.sh` turns the CM4's USB-C OTG port into a **post-boot serial console over USB** using the in-kernel CDC-ACM gadget (`g_serial`). The board appears to a connected PC as `/dev/ttyACM0` and locally as `/dev/ttyGS0` with a login prompt. It writes `/etc/modprobe.d` + `/etc/modules-load.d` entries (so `g_serial use_acm=1` loads at boot), an optional ModemManager-ignore udev rule, enables `serial-getty@ttyGS0`, and (with `--kernel-console`) appends `console=ttyGS0,115200` to `/boot/armbianEnv.txt` extraargs and adds `g_serial` to the initramfs.

- `--mode local` (default) configures the machine it runs on (the CM4); `--mode remote --target-host [user@]host` `scp`+`ssh`'s it to the board and runs it with `sudo`.
- Relies on what the repo's kernel/DT already provide: USB-C `dr_mode = "peripheral"` (`...cm4io.dts`), `CONFIG_USB_DWC2=y` (the g12b OTG peripheral controller), `CONFIG_USB_G_SERIAL=m`, `CONFIG_U_SERIAL_CONSOLE=y`. Preflight checks `modinfo g_serial` and `/sys/class/udc`.
- **Limitation:** this is a post-boot console only — it cannot show bootrom/U-Boot/early-kernel output (the port is USB, not a UART). For low-level boot debugging use the AO UART (`&uart_AO`, already the console). `--dry-run` previews all changes.

## WiFi driver selection (`wifi-driver-setup.sh`)

The on-module WiFi is a **Realtek RTL8822CS** (SDIO 802.11ac + BT combo, on `&sd_emmc_a` at 200 MHz SDR104 1v8 — DT already tuned in `meson-g12b-bananapi-cm4.dtsi`, `amlogic,dram-access-quirk` deleted). It can be driven by **either** the mainline in-tree `rtw88` stack (`rtw88_8822cs`+`rtw88_sdio`+`rtw88_8822c`+`rtw88_core`) **or** Realtek's out-of-tree vendor **`88x2cs`** module. Both are present on the board and, if both load, they race for the SDIO function — a source of instability *and* slowness. **The vendor `88x2cs` is the fast path** (measured 50 → ~150 Mbit/s just by switching away from rtw88; rtw88 tops out ~270 Mbit/s here, 88x2cs reaches ~400+). Mainline `rtw88` also ignores runtime `iw … power_save off` (its firmware LPS throttles throughput regardless).

`wifi-driver-setup.sh` pins **one** driver by blacklisting the other and rebuilding the initramfs, and disables WiFi power-save. Default `--driver 88x2cs`; pass `--driver rtw88` to standardize on mainline.
- Blacklist file: `/etc/modprobe.d/blacklist-rtw88-8822cs.conf` (for 88x2cs) or `/etc/modprobe.d/blacklist-88x2cs.conf` (for rtw88); re-running with the other `--driver` flips them cleanly. Each file carries **both** `blacklist <mod>` **and** `install <mod> /bin/true` for the bad driver — the `install … /bin/true` is the hard block that prevents loading via alias, as a dependency, *or* via explicit `modprobe`. This is necessary because both drivers claim the **same SDIO alias**, so a soft `blacklist` alone can race (both have been observed loaded at once). Then `update-initramfs -u` so the blacklisted module isn't pulled in early and grab the radio first.
- Power-save off: prefers a NetworkManager drop-in `/etc/NetworkManager/conf.d/wifi-powersave-off.conf` (`wifi.powersave = 2`); falls back to a `wifi-powersave-off.service` systemd oneshot (`iw … set power_save off` over every wireless iface at boot) when NM isn't managing WiFi. `--keep-powersave` skips this. `--no-apply-now` skips the live `iw` toggle.
- The driver swap **only takes effect on reboot** (`--reboot` to do it, else the script prints the manual steps). Verify with `basename "$(readlink /sys/class/net/wlan0/device/driver)"` (→ `88x2cs` or `rtw88_sdio`) and measure real throughput over the LAN with `iperf3` (not an internet speed test).
- `--mode local` (default) configures the machine it runs on; `--mode remote --target-host [user@]host` `scp`+`ssh`'s it to the board and runs with `sudo`. `--dry-run` previews all changes. Preflight checks the RTL8822CS SDIO id (`024c:c822`) and that the chosen driver's module is installed (if `88x2cs` is missing it points at the `8822cs` dkms package).

## Kernel config

`defconfig` is the full board kernel config (base: `config-6.12.13-current-meson64`, an Armbian current-meson64 config). `make config` installs it into the kernel tree. When adding driver support (e.g. `CONFIG_SND_SOC_MAX98396`), edit `defconfig` here so the change is tracked.
