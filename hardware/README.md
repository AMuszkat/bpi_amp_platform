# hardware: the two boards

The KiCad projects of the BPI AMP Platform's boards. The platform has two boards connected by
a 46-pin (2×23) board-to-board connector: a **base board** that carries the compute module,
and a **speaker amp board** with the amplifiers and the power supply.

![Hardware block diagram](block-diagram.svg)

| Project | Contents |
|---|---|
| [`base_board`](base_board) | Carrier board for the BPI-CM4 module |
| [`speaker_amp`](speaker_amp) | Amplifier and power board |

## Base board

- Carries the **BananaPi BPI-CM4** (Amlogic A311D) on two 100-pin DF40 board-to-board
  connectors.
- **USB-C** port on the module's USB OTG interface, ESD protected. It can serve as a login
  console over USB (see `firmware/usb-serial-console-setup.sh`). Jumper J3 links the USB VBUS
  to the 5 V rail.
- 3-pin **debug UART** header (J_LIN_DEB1).
- No SD-card slot: the module is installed to its eMMC beforehand (see
  [First boot](../firmware/README.md#1-first-boot-one-time)).

## Speaker amp board

- **Amplifiers:** two MAX98397 smart class-D amplifiers (U1, U2), one per speaker output
  (JOUT1, JOUT2). They share one TDM bus from the module's TDM_A interface: BCLK, FS and D0
  carry the audio to both amps, and D1 returns their voltage and current sense data. Each amp
  is configured over I²C, with reset, IRQ and LV_EN on module GPIOs, and the two are linked
  through their ICC pins.
- **PVDD** (the amplifiers' power stage supply) comes either from an external supply on
  JPVDD1 or from the on-board boost converter, selected with the jumpers on JBST_PVDD1.
- **Boost converter:** an LTC3786 synchronous boost controller (BSZ024N04LS6 MOSFETs,
  4.7 µH inductor) raises the battery or DC input on JBATT1 to PVDD. The module enables it
  and reads its power-good signal. The board has unpopulated footprints for trimming the
  output voltage from the module by PWM.
- **5 V buck:** an MP9943 converts PVDD to 5 V for the amplifiers' VBAT supply and, through
  the board-to-board connector, for the base board and the module.
- The amplifiers' **1.8 V** logic supply comes from the module.

## 3D models

Most vendor 3D models used by the boards are not in the repo. The PCBs reference them as
`${BPI_AMP_3DMODELS}/<file>`. To see them in the 3D viewer, download the models and point
`BPI_AMP_3DMODELS` at that folder in KiCad (*Preferences → Configure Paths*). Models that ship
with the repo (in `speaker_amp/speaker_amp_library.pretty`) load without it.
