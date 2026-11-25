// SPDX-License-Identifier: GPL-2.0
/*
 * Tell a ConnectX card to ignore the power class a cable declares.
 *
 * It sets the PMMP register's "Ignore_Power_Class" override for module 0,
 * then switches the module off and on (PMAOS) so the firmware re-reads the
 * cable. Nothing is written to flash; a reboot or card reset clears it.
 *
 * Load with:   insmod mlx5_ignore_power.ko bus=0x21
 */
#include <linux/module.h>
#include <linux/pci.h>
#include <linux/delay.h>
#include <linux/mlx5/driver.h>

#define REG_PMMP  0x5044
#define REG_PMAOS 0x5012

#define PMMP_DWORDS  16   /* 64 bytes */
#define PMAOS_DWORDS 4    /* 16 bytes */

#define PMMP_STICKY             0x00000001  /* dword 0, bit 0 */
#define PMMP_IGNORE_POWER_CLASS 0x00000004  /* dword 1, eeprom_override bit 2 */

#define PMAOS_ADMIN_ENABLE  0x00000100      /* dword 0, admin_status = 1 */
#define PMAOS_ADMIN_DISABLE 0x00000200      /* dword 0, admin_status = 2 */
#define PMAOS_ASE           0x80000000      /* dword 1, admin status update enable */

static int bus = 0x21;
module_param(bus, int, 0444);
MODULE_PARM_DESC(bus, "PCI bus number of the ConnectX card (function 0)");

static int set_module_admin(struct mlx5_core_dev *mdev, u32 admin)
{
	__be32 in[PMAOS_DWORDS] = {};
	__be32 out[PMAOS_DWORDS] = {};

	in[0] = cpu_to_be32(admin);          /* module 0 */
	in[1] = cpu_to_be32(PMAOS_ASE);
	return mlx5_core_access_reg(mdev, in, sizeof(in), out, sizeof(out),
				    REG_PMAOS, 0, 1);
}

static u32 read_module_status(struct mlx5_core_dev *mdev)
{
	__be32 in[PMAOS_DWORDS] = {};
	__be32 out[PMAOS_DWORDS] = {};
	int err;

	err = mlx5_core_access_reg(mdev, in, sizeof(in), out, sizeof(out),
				   REG_PMAOS, 0, 0);
	if (err) {
		pr_info("mlx5_ignore_power: PMAOS read failed: %d\n", err);
		return 0;
	}
	return be32_to_cpu(out[0]);
}

static int __init mlx5_ignore_power_init(void)
{
	struct pci_dev *pdev;
	struct mlx5_core_dev *mdev;
	__be32 in[PMMP_DWORDS] = {};
	__be32 out[PMMP_DWORDS] = {};
	int err;

	pdev = pci_get_domain_bus_and_slot(0, bus, PCI_DEVFN(0, 0));
	if (!pdev) {
		pr_info("mlx5_ignore_power: no PCI device on bus 0x%x\n", bus);
		return -ENODEV;
	}

	if (pdev->vendor != PCI_VENDOR_ID_MELLANOX || !pdev->driver ||
	    strcmp(pdev->driver->name, "mlx5_core") != 0) {
		pr_info("mlx5_ignore_power: device is not a mlx5_core card\n");
		pci_dev_put(pdev);
		return -ENODEV;
	}

	mdev = pci_get_drvdata(pdev);
	if (!mdev) {
		pci_dev_put(pdev);
		return -ENODEV;
	}

	pr_info("mlx5_ignore_power: module status before: 0x%08x\n",
		read_module_status(mdev));

	/* 1. Set the override. */
	in[0] = cpu_to_be32(PMMP_STICKY);    /* module 0 */
	in[1] = cpu_to_be32(PMMP_IGNORE_POWER_CLASS);
	err = mlx5_core_access_reg(mdev, in, sizeof(in), out, sizeof(out),
				   REG_PMMP, 0, 1);
	pr_info("mlx5_ignore_power: PMMP write result: %d\n", err);

	/* 2. Read it back. */
	memset(in, 0, sizeof(in));
	memset(out, 0, sizeof(out));
	err = mlx5_core_access_reg(mdev, in, sizeof(in), out, sizeof(out),
				   REG_PMMP, 0, 0);
	pr_info("mlx5_ignore_power: PMMP read result: %d, dword0 0x%08x, dword1 0x%08x\n",
		err, be32_to_cpu(out[0]), be32_to_cpu(out[1]));

	/* 3. Switch the module off and on so the cable is checked again. */
	err = set_module_admin(mdev, PMAOS_ADMIN_DISABLE);
	pr_info("mlx5_ignore_power: module off result: %d, status 0x%08x\n",
		err, read_module_status(mdev));
	msleep(1000);
	err = set_module_admin(mdev, PMAOS_ADMIN_ENABLE);
	msleep(3000);
	pr_info("mlx5_ignore_power: module on result: %d, status 0x%08x\n",
		err, read_module_status(mdev));

	pci_dev_put(pdev);
	return 0;
}

static void __exit mlx5_ignore_power_exit(void)
{
}

module_init(mlx5_ignore_power_init);
module_exit(mlx5_ignore_power_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Set ConnectX PMMP Ignore_Power_Class override");
