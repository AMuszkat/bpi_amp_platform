# BPI AMP Platform

![Project overview](all.jpeg)

Audio amplifier platform built around a **BananaPi BPI-CM4** (Amlogic A311D) and dual
**MAX98397** I2S/TDM smart amplifiers.

## Repository layout

| Directory | Contents |
|---|---|
| [`hardware/base_board`](hardware/base_board) | KiCad project for the CM4 carrier board |
| [`hardware/speaker_amp`](hardware/speaker_amp) | KiCad project for the speaker amplifier board |
| [`firmware`](firmware) | Kernel device tree, `defconfig`, patches, MAX98396/7 and AXG sound-card drivers, PipeWire config, and board setup scripts (Snapcast, USB serial console, WiFi driver) |

See [`firmware/CLAUDE.md`](firmware/CLAUDE.md) for the detailed firmware notes.

## Hardware 3D models

Most vendor 3D models used by the boards are not in the repo. The PCBs reference them as
`${BPI_AMP_3DMODELS}/<file>`; to see them in the 3D viewer, download the models and point
`BPI_AMP_3DMODELS` at that folder in KiCad (*Preferences → Configure Paths*). Models that
ship with the repo (in `hardware/speaker_amp/speaker_amp_library.pretty`) load without it.

## Firmware quick start

The repo does not include a kernel tree. Fetch the matching stable kernel next to the
firmware files:

```sh
cd firmware
git clone --depth 1 --branch v6.12.22 \
    https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git linuxMainLine
make patch          # apply patch/*.patch
```

Board address and login are per-developer settings and stay out of git. Put them in
`firmware/local.mk` (gitignored):

```sh
cp local.mk.example local.mk   # then set BOARD_HOST / BOARD_USER (and LINUX if the tree lives elsewhere)
make config image dtbs modules
make install
```

The `*-setup.sh` scripts take `--target-host [user@]host`, or read `BOARD_HOST` /
`BOARD_USER` from the environment.
