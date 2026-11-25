# ConnectX-5 power class unlock

Use a QSFP cable that your Mellanox / NVIDIA ConnectX-5 refuses with
**"Power budget exceeded"**, by changing one byte in the card's firmware.

---

## Is this your problem?

You plugged a cable into a ConnectX-5 and the link never comes up. The kernel log says:

```
mlx5_core 0000:21:00.0: Port module event[error]: module 0, Cable error, Power budget exceeded
```

and `ethtool` says:

```
Link detected: no (Power budget exceeded)
```

This usually happens with a **200G QSFP56 passive copper cable (DAC)** that
you want to run at 100G. Example: FS `QSFP-200G-PC01`.

### Why it happens

Every cable has a small chip that tells the card how much power it needs.
These 200G cables say **5.0 W** (power class 7). A normal PCIe ConnectX-5
only allows **4.5 W** (power class 6) per port, so it refuses the cable.

A passive copper cable actually uses almost no power. The label on the
chip is just higher than the card's limit.

### What this fixes

The patch raises the card's limit from class 6 to class 7. After that the
card accepts the cable.

Result on the tested card: **100G link, about 94 Gbit/s in both directions**
(measured with iperf3), with no errors.

---

## Before you start

> ⚠️ **You are flashing modified firmware onto your card.**
> Make the backup in step 2 and keep it. It's how you undo this.
> No warranty, use at your own risk.

- **Only raise the limit as far as your cable needs.** For a 5.0 W cable
  that's level 7.
- **Only use low-power cables in the patched port**, like passive DACs.
  A real high-power optical module could load the card beyond its design.
- **Tested on real hardware:** MCX515A-CCAT with firmware 16.33.1300.
  The firmware layout was also checked offline on 9 other ConnectX-5
  models (see [Details](#details)). The tool checks your image and
  refuses if anything doesn't match.

### You need

- Linux, with the card using the `mlx5_core` driver
- [mstflint](https://github.com/Mellanox/mstflint)
  (Debian/Ubuntu: `apt install mstflint`, Arch: AUR `mstflint`)
- Python 3

---

## Step by step

In the commands below, replace `21:00.0` with your card's PCI address.
Find it with:

```sh
lspci -d 15b3:
```

### 1. Check your card can be patched

```sh
sudo mstflint -d 21:00.0 q
```

Look for this line:

```
Security Attributes:   N/A
```

If it says `N/A`, continue. If it says `secure-fw`, **stop**: this method
won't work on your card.

### 2. Back up the firmware

```sh
sudo mstflint -d 21:00.0 ri original.bin
mstflint -i original.bin verify
```

The second command must end with `Image is bootable.`
Keep `original.bin` somewhere safe.

### 3. Make the patched image

```sh
./patch_power_level.py original.bin patched.bin --level 7
```

For the **second** port of a dual-port card, add `--port 1`.

The tool prints each check it does. If anything looks wrong, it stops
and writes nothing.

### 4. Check the patched image

```sh
mstflint -i patched.bin verify
```

It must end with `Image is bootable.`

### 5. Flash it

```sh
sudo mstflint -d 21:00.0 -i patched.bin --no_fw_ctrl burn
```

It should end with:

```
Burning FW image without signatures - OK
Restoring signature                 - OK
```

### 6. Load the new firmware

```sh
sudo mstfwreset -y -d 21:00.0 -l 3 r
```

Rebooting the PC also works.

### 7. Check it worked

```sh
sudo dmesg | grep 'Port module event'
```

You should now see `Cable plugged` instead of `Power budget exceeded`.

If the link still doesn't come up, see [Getting the link up](#getting-the-link-up).

---

## Undo

Flash your backup and load it:

```sh
sudo mstflint -d 21:00.0 -i original.bin --no_fw_ctrl burn
sudo mstfwreset -y -d 21:00.0 -l 3 r
```

Installing an official NVIDIA firmware update also removes the patch.

---

## Getting the link up

The patch removes the power block. The card and the switch still have to
agree on speed.

On a 200G/400G switch port, auto-negotiation down to 100G often fails.
Setting a fixed speed on **both ends** fixes it.

**On the ConnectX-5 (Linux):**

```sh
sudo ethtool -s <interface> autoneg off speed 100000 duplex full
sudo ethtool --set-fec <interface> encoding rs
```

**On a MikroTik switch (tested on a CRS812):**

```
/interface ethernet set [find name="qsfp56-1-1"] auto-negotiation=no speed=100G-baseCR4 fec-mode=fec91
```

The `ethtool` settings are lost on reboot. To keep them with
NetworkManager:

- On the connection profile, set `802-3-ethernet.auto-negotiate no`,
  `802-3-ethernet.speed 100000` and `802-3-ethernet.duplex full`.
- NetworkManager can't set FEC, so add a script in
  `/etc/NetworkManager/dispatcher.d/pre-up.d/` that runs the
  `ethtool --set-fec` line above.

---

## Check your cable's power class

```sh
sudo ethtool -m <interface> hex on | grep '^0x0080'
```

The second byte on that line is the power class byte. For example, in
`0x0080:  11 cf 23 ...` it's `cf`.

Look at the **last** hex digit first:

| Last digit | Power class |
|---|---|
| 3, 7, b, f | 7 (5.0 W) |
| 2, 6, a, e | 6 (4.5 W) |
| 1, 5, 9, d | 5 (4.0 W) |
| 0, 4, 8, c | 1–4, see the first digit below |

If the last digit is 0, 4, 8 or c, the **first** digit decides:

| First digit | Power class |
|---|---|
| 0–3 | 1 (1.5 W) |
| 4–7 | 2 (2.0 W) |
| 8–b | 3 (2.5 W) |
| c–f | 4 (3.5 W) |

`cf` → last digit `f` → class 7. That's the FS 200G DAC.

---

## What does NOT work

So you don't have to try these:

| Attempt | Result |
|---|---|
| `mstconfig` power settings (`ADVANCED_POWER_SETTINGS`, `DISABLE_SLOT_POWER_LIMITER`) | No effect. They control the whole PCIe slot, not each port. |
| `mstconfig MODULE_MAX_POWER_OVERRIDE` | Not supported on this firmware. |
| The "ignore power class" override (PMMP register) | The firmware rejects it, even from a kernel module. See [experiments/pmmp-kmod](experiments/pmmp-kmod). |
| Rewriting the cable's chip | The cable is write-protected. |
| A normal `mstflint burn` | Fails with `The Digest in the signature is wrong`. Use `--no_fw_ctrl` as in step 5. |

---

## Details

### Where the setting is

The firmware has a board settings section called `HW_MAIN_CFG`. Each port
has a block in it, and one byte in that block is the port's power limit
(`module_power_level_supported`):

| Port | Location |
|---|---|
| Port 0 | `HW_MAIN_CFG + 0x241` |
| Port 1 | `HW_MAIN_CFG + 0x281` |

The value is the standard QSFP power class:

| Class | Max power |
|---|---|
| 1 | 1.5 W |
| 2 | 2.0 W |
| 3 | 2.5 W |
| 4 | 3.5 W |
| 5 | 4.0 W |
| 6 | 4.5 W |
| 7 | 5.0 W |
| 8 | more than 5 W |

### What NVIDIA ships

Values from NVIDIA's official 16.33.1048 firmware, read from each image's
own settings file. They match the byte above on every board:

| Card | Type | Ports | Level |
|---|---|---|---|
| MCX542B-ACA | OCP 2.0 | 2× SFP28 | 1 |
| MCX562A-ACA | OCP 3.0 | 2× SFP28 | 3 |
| MCX566A-CDA, MCX565M-CDA | OCP 3.0 | QSFP28 | 4 |
| MCX512A-ACA | PCIe | 2× SFP28 | 4 |
| MCX515A-CCA, MCX515A-GCA, MCX516A-BDA, MCX516A-CDA | PCIe | QSFP28 | 6 |

### What the tool changes

Exactly 5 bytes:

- the power level byte
- the section's checksum (2 bytes)
- the checksum of the section's table entry (2 bytes)

The checksums use the same CRC-16 as mstflint: polynomial `0x100B`, start
`0xFFFF`, 32-bit big-endian words, then a 16-bit flush and XOR `0xFFFF`.

Before writing anything, the tool:

1. checks the file is a ConnectX FS4 image (`ITOC` marker at `0x5000`)
2. recomputes the existing checksums and stops if they don't match
3. checks the port block has the expected shape
4. compares the byte with the image's own settings file (via `mstflint`)
5. checks the new level is higher than the current one, and at most 8

### Why `--no_fw_ctrl`

NVIDIA signs its firmware. The patch can't re-sign it, so the normal update
path refuses it. On cards without secure boot (`Security Attributes: N/A`),
`--no_fw_ctrl` writes the flash directly, and the card boots the patched
image normally.

---

## Credits

- [Mellanox/mstflint](https://github.com/Mellanox/mstflint): the firmware
  format and checksum code come from its source.
- [Bulls729/Mellanox-ConnectX-5-PCIe-Gen-4-Enablement](https://github.com/Bulls729/Mellanox-ConnectX-5-PCIe-Gen-4-Enablement):
  showed that ConnectX-5 cards without secure boot run firmware with
  modified settings.

## License

GPL-2.0-only, see [LICENSE](LICENSE). The kernel module experiment has to be
GPL because it uses GPL-only kernel functions.
