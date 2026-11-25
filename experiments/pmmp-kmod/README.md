# PMMP `Ignore_Power_Class` kernel module — does NOT work on ConnectX-5 FW 16.33

Kept here as a documented dead end, so nobody has to repeat it.

## Idea

The ConnectX PRM register **PMMP** (`0x5044`, Port Module Memory Map Properties)
has an `eeprom_override` bitmask with bit 2 = `Ignore_Power_Class`. In theory
that tells the firmware to ignore the power class a cable declares, at runtime,
without flashing anything.

`mstreg` (open-source mlxreg) and the kernel `fwctl` interface both failed to
set it, so this module calls the driver's own exported
`mlx5_core_access_reg()` from inside the kernel (full driver privilege, uid 0):

1. write PMMP for module 0: `sticky=1`, `eeprom_override=0x4`
2. read PMMP back
3. toggle the module off/on with PMAOS so the cable is re-evaluated

## Result on MCX515A-CCAT, FW 16.33.1300

```
mlx5_core 0000:21:00.0: ACCESS_REG(0x805) op_mod(0x0) failed, status bad parameter(0x3), syndrome (0x89415f), err(-22)
mlx5_ignore_power: PMMP write result: -22
```

The **firmware itself** rejects the write (same syndrome `0x89415f` as via
`mstreg`/ICMD). PMAOS admin-disable is rejected too (`0xcdd119`). So it's not a
privilege problem: this firmware doesn't support the override on HCAs.

Other paths that were also tried and failed on this card:
- `fwctl` (`/dev/fwctl/fwctl0`, `ACCESS_REG`): returns status OK but the
  write is silently ignored (commands run in an unprivileged UCTX, uid 2).
- `mstconfig ADVANCED_POWER_SETTINGS=True DISABLE_SLOT_POWER_LIMITER=True`:
  that's the total PCIe slot budget, not the per-cage limit. No effect.
- `mstconfig MODULE_MAX_POWER_OVERRIDE_M0`: "Device doesn't support".
- Rewriting the cable EEPROM via `mstlink --cable --write`: cable is
  write-protected, write silently doesn't stick.

It *might* work on newer firmware or other ConnectX generations. If you test it
somewhere and it works, please open an issue.

## Build / run (at your own risk)

```
make
sudo insmod mlx5_ignore_power.ko bus=0x21   # PCI bus of your card, function 0
sudo dmesg | grep mlx5_ignore_power
sudo rmmod mlx5_ignore_power
```

It writes no flash. A card reset or reboot clears anything it set.
