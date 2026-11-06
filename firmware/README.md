# firmware: the board side

What makes a BananaPi BPI-CM4 a speaker: the device tree, the kernel config and the amplifier
drivers; the PipeWire configuration; the installers of the audio side (`setup.sh` and one
installer per component); and `ampd`, the speaker's network agent. The developer notes, the
amplifier telemetry work and what the first deploy measured on a real board are in
[`CLAUDE.md`](CLAUDE.md).

> **Status: bring-up and debugging.** The firmware currently sits on top of a stock Armbian
> (Debian) install: a device tree, a kernel config, locally built amplifier drivers and setup
> scripts. Proper BSP support through Yocto or the Armbian build framework is planned.

The board address and login are per-developer settings and stay out of git, in `local.mk`
(gitignored, template [`local.mk.example`](local.mk.example)): `BOARD_HOST` and `BOARD_USER`.
The scripts read them from there or from the environment.

## The audio side: `setup.sh`

[`setup.sh`](setup.sh) installs the audio side of a speaker in the order the speaker boots it:
the PipeWire sink that `speakerd` plays from, `snapclient` routed into that sink, `speakerd`
itself, and `ampd`. It runs on the board, or from the development machine with
`--mode target-remote`, which copies one bundle (the aarch64 builds of `tools/build.sh` and
these installers) to the board and runs it there. It checks every component before it changes
any, and builds nothing. Each component also has its own installer:

| Script | What it installs |
|---|---|
| [`setup.sh`](setup.sh) | Everything below, component by component; `--only` and `--skip` choose |
| [`speakerd-setup.sh`](speakerd-setup.sh) | `speakerd`: its versions under `~/.local/lib/speakerd` (each with the plugins' **headless** builds, the only builds a speaker runs), its systemd unit, the sink drop-in, `device.json` and `factory.json` (on a first install [`speakerd/factory.json`](speakerd/factory.json), the board's chain over the headless builds; `--factory` replaces it). Also `status`, `smoke`, `limits`, `rollback` and `uninstall` |
| [`ampd-setup.sh`](ampd-setup.sh) | `ampd`: `ampd.json` and its unit, as the hub on the left speaker or the agent on the right. Its program comes with `speakerd`'s version, so an install of `speakerd` that changes it restarts `ampd`; `status` and `smoke` say when the `ampd` that runs is not the installed program |
| [`snapcast-setup.sh`](snapcast-setup.sh) | Synchronized network audio: Snapcast over PipeWire, server on the host and client on the board |

`snapclient` finds the snapserver by name: `snapcast-setup.sh --server` (or its old name
`--server-ip`) takes a name or an address, and from the development machine the default is that
machine's mDNS name, `$(hostname).local` (like `example.local`), so a speaker keeps finding the
server when the network changes. On the board a `.local` name needs `libnss-mdns`, which the
client setup installs when the name does not resolve without it; a name that still does not is
refused (pass `--server <ip>`). `setup.sh --server` passes it on.

The remote modes refuse to touch a board only while a lab lock (`.claude/lab.lock`) exists;
they do not ask whether the commit passed the tests. A first
deploy, step by step, and what the board measured are in [`CLAUDE.md`](CLAUDE.md).

## The kernel side

### 1. First boot (one time)

The base board has no SD-card slot, so the BPI-CM4 gets its operating system once on Banana
Pi's CM4IO baseboard:

1. Write the Armbian (Debian) image for the BananaPi CM4IO to an SD card.
2. Mount the BPI-CM4 on the **BPI-CM4IO** baseboard, insert the card and boot.
3. Transfer the system to the module's eMMC with `armbian-install`, then power off.
4. Move the module to the BPI AMP base board. From now on it boots from eMMC.

### 2. Host setup

The device tree is built in a kernel source tree matching the board's kernel series:

```sh
cd firmware
cp local.mk.example local.mk            # set BOARD_HOST and BOARD_USER
git clone --depth 1 --branch v6.12.22 \
    https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git linuxMainLine
cp defconfig linuxMainLine/arch/arm64/configs/defconfig
make -C linuxMainLine ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- defconfig
```

You need an arm64 cross toolchain and the usual kernel build tools (on Debian/Ubuntu:
`gcc-aarch64-linux-gnu flex bison bc`), plus ssh access to the board as root for installing.

### 3. Device tree: `Makefile`

`dtbs` and `install_dtbs` are currently the only working targets of the main Makefile:

```sh
make dtbs           # build meson-g12b-bananapi-cm4-cm4io.dtb from the .dts/.dtsi in firmware/
make install_dtbs   # copy it to the board's /boot over ssh
```

Set `TARGET_KERNEL1` in the Makefile to the board's kernel version (`uname -r` on the board),
because `install_dtbs` writes to `/boot/dtb-<version>/amlogic/`. Reboot the board to load the
new device tree. The other targets (`image`, `modules`, `install`, `all`, ...) do not work yet.

### 4. Amplifier drivers: `localBuild`

The drivers are built natively on the board against its running kernel. Install the kernel
headers on the board first (Armbian: `sudo apt install linux-headers-current-meson64`), set
`TARGET_KERNEL` in `localBuild/Makefile` to the board's `uname -r`, then run from the host:

```sh
cd firmware/localBuild
make max98396    # MAX98396/MAX98397 codec driver
make axg_card    # Amlogic AXG sound-card machine driver
make all         # dummy codec, for TDM bring-up without amplifiers
```

Each target copies the sources to the board, builds them there, copies the `.ko` back and
installs it into the board's module tree.

### 5. Board setup and tools

| File | Purpose |
|---|---|
| [`pipewire/pipewire.conf`](pipewire/pipewire.conf) | The measured board's own PipeWire configuration with the quantum 512 by default and 64 to 2048 (the buffer sizes the controller may choose), at 48000 Hz, with the `speakerd` sink in it; `setup.sh --mode target-remote --only pipewire --base-conf` installs it |
| [`pipewire/pipewire.conf.d/60-speakerd-sink.conf`](pipewire/pipewire.conf.d/60-speakerd-sink.conf) | The persistent `speakerd` sink, a drop-in the speakerd installer puts on a board that runs its own `pipewire.conf` (never beside the repository's) |
| [`usb-serial-console-setup.sh`](usb-serial-console-setup.sh) | Login console over the USB-C port |
| [`wifi-driver-setup.sh`](wifi-driver-setup.sh) | Pins the RTL8822CS WiFi to one driver and disables power saving |
| [`max98397-diag.py`](max98397-diag.py) | Reads and clears the amplifiers' fault registers over I²C (runs on the board) |

The scripts take `--target-host [user@]host`, or read `BOARD_HOST` and `BOARD_USER` from
`local.mk` or the environment.
