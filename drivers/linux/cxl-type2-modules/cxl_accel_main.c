// SPDX-License-Identifier: GPL-2.0-only
/*
 * CXL Type 2 Accelerator Driver
 *
 * Supports CXL Type 2 accelerators (FPGAs, GPUs) with:
 * - PCI device enumeration
 * - Cache device registration (cxl_type2_cache)
 * - Memory device registration (cxl_type2_mem)
 * - HDM decoder setup
 * - Multi-function device support (PF0 + PF1)
 *
 * Supported devices:
 * - Intel QEMU CXL Type 2 (0x8086:0x0d92)
 * - Intel IA-780I Agilex 7 CXL Type 2 (0x8086:0x0ddb)
 *
 * Copyright(c) 2025 CXLAgent Project
 */

#include <linux/module.h>
#include <linux/pci.h>
#include <linux/delay.h>
#include <linux/compat.h>
#include <linux/fs.h>
#include <linux/io.h>
#include <linux/miscdevice.h>
#include <linux/mutex.h>
#include <linux/slab.h>
#include <linux/sizes.h>
#include <linux/uaccess.h>
#include <linux/cxl/cxl.h>

/* Device IDs */
#define CXL_TYPE2_VENDOR_ID          0x8086
#define CXL_TYPE2_DEVICE_ID_QEMU      0x0d92
#define CXL_TYPE2_DEVICE_ID_IA780I    0x0ddb

/* CXL register offsets */
#define CXL_HDM_DECODER_CAP_OFFSET       0x0
#define CXL_HDM_DECODER_CTRL_OFFSET       0x4
#define CXL_HDM_DECODER_ENABLE            BIT(1)
#define CXL_HDM_DECODER0_BASE_LOW_OFFSET(i)  (0x20 * (i) + 0x10)
#define CXL_HDM_DECODER0_BASE_HIGH_OFFSET(i) (0x20 * (i) + 0x14)
#define CXL_HDM_DECODER0_SIZE_LOW_OFFSET(i)  (0x20 * (i) + 0x18)
#define CXL_HDM_DECODER0_SIZE_HIGH_OFFSET(i) (0x20 * (i) + 0x1c)
#define CXL_HDM_DECODER0_CTRL_OFFSET(i)      (0x20 * (i) + 0x20)
#define CXL_HDM_DECODER0_CTRL_COMMIT         BIT(9)
#define CXL_HDM_DECODER0_CTRL_COMMITTED      BIT(10)

/* CXL DVSEC offsets */
#define CXL_DVSEC_PCIE_DEVICE             0
#define CXL_DVSEC_CAP_OFFSET               0x4
#define CXL_DVSEC_CTRL_OFFSET              0x8
#define CXL_DVSEC_CAP_CACHE_CAPABLE        BIT(2)
#define CXL_DVSEC_CAP_IO_CAPABLE           BIT(3)
#define CXL_DVSEC_CAP_MEM_CAPABLE          BIT(4)
#define CXL_DVSEC_CTRL_CACHE_ENABLE        BIT(2)
#define CXL_DVSEC_CTRL_IO_ENABLE           BIT(3)
#define CXL_DVSEC_CTRL_MEM_ENABLE          BIT(4)

/* Per-device context */
struct cxl_type2_context {
    struct pci_dev *pdev;
    struct cxl_dev_state *cxlds;
    struct cxl_cachedev *cxlcd;
    struct cxl_memdev *cxlmd;
    void __iomem *bar2;      /* BAR2 for cache controller */
    struct mutex lock;
    int cache_id;
    int mem_id;
    static atomic_t next_cache_id;
    static atomic_t next_mem_id;
};

static atomic_t next_cache_id = ATOMIC_INIT(0);
static atomic_t next_mem_id = ATOMIC_INIT(0);

/* Enable PF1 for multi-function devices (IA-780I) */
static void cxl_type2_enable_pf1(struct pci_dev *pf0)
{
    struct pci_dev *pf1;
    u16 cmd, want;

    pf1 = pci_get_slot(pf0->bus, PCI_DEVFN(PCI_SLOT(pf0->devfn), 1));
    if (!pf1) {
        dev_warn(&pf0->dev,
                 "PF1 not enumerated; AFU CXL.mem path will be inactive\n");
        return;
    }

    want = PCI_COMMAND_MEMORY | PCI_COMMAND_MASTER |
           PCI_COMMAND_PARITY | PCI_COMMAND_SERR;

    pci_read_config_word(pf1, PCI_COMMAND, &cmd);
    if ((cmd & want) != want) {
        pci_write_config_word(pf1, PCI_COMMAND, cmd | want);
        pci_read_config_word(pf1, PCI_COMMAND, &cmd);
    }

    dev_info(&pf0->dev, "PF1 %s enabled (COMMAND=0x%04x)\n",
             pci_name(pf1), cmd);
    pci_dev_put(pf1);
}

/* Force-commit HDM Decoder 0 */
static void cxl_type2_commit_hdm_decoder(struct pci_dev *pdev,
                                         u64 base_pa, u64 size)
{
    struct cxl_register_map comp_map = {};
    void __iomem *comp_base = NULL, *cap_base = NULL, *hdm_base = NULL;
    u32 cap_hdr, global_ctrl, ctrl;
    int array_size, i, rc;

    /* Find component registers */
    rc = cxl_find_regblock(pdev, CXL_REGLOC_RBI_COMPONENT, &comp_map);
    if (rc || comp_map.resource == CXL_RESOURCE_NONE) {
        dev_warn(&pdev->dev, "No component registers found\n");
        return;
    }

    comp_base = devm_ioremap(&pdev->dev, comp_map.resource,
                              comp_map.max_size);
    if (IS_ERR_OR_NULL(comp_base)) {
        dev_warn(&pdev->dev, "Failed to map component registers\n");
        return;
    }

    /* Walk capability array to find HDM (id=5) */
    cap_base = comp_base + 0x1000;
    cap_hdr = readl(cap_base);
    array_size = (cap_hdr >> 20) & 0xfff;

    for (i = 0; i < array_size && i < 32; i++) {
        u32 entry = readl(cap_base + 4 + i * 4);
        u16 cap_id = entry & 0xffff;
        u32 cap_off = (entry >> 20) & 0xfff;

        if (cap_id == 5) {
            hdm_base = cap_base + cap_off;
            break;
        }
    }

    if (!hdm_base) {
        dev_warn(&pdev->dev, "HDM Decoder capability not found\n");
        return;
    }

    /* Enable HDM decoding globally */
    global_ctrl = readl(hdm_base + CXL_HDM_DECODER_CTRL_OFFSET);
    if (!(global_ctrl & CXL_HDM_DECODER_ENABLE)) {
        writel(global_ctrl | CXL_HDM_DECODER_ENABLE,
               hdm_base + CXL_HDM_DECODER_CTRL_OFFSET);
        global_ctrl = readl(hdm_base + CXL_HDM_DECODER_CTRL_OFFSET);
    }

    /* Program Decoder 0 base/size and commit */
    writel(lower_32_bits(base_pa),
           hdm_base + CXL_HDM_DECODER0_BASE_LOW_OFFSET(0));
    writel(upper_32_bits(base_pa),
           hdm_base + CXL_HDM_DECODER0_BASE_HIGH_OFFSET(0));
    writel(lower_32_bits(size),
           hdm_base + CXL_HDM_DECODER0_SIZE_LOW_OFFSET(0));
    writel(upper_32_bits(size),
           hdm_base + CXL_HDM_DECODER0_SIZE_HIGH_OFFSET(0));
    writel(CXL_HDM_DECODER0_CTRL_COMMIT,
           hdm_base + CXL_HDM_DECODER0_CTRL_OFFSET(0));

    msleep(100);
    ctrl = readl(hdm_base + CXL_HDM_DECODER0_CTRL_OFFSET(0));

    dev_info(&pdev->dev,
             "HDM Decoder 0 committed: ctrl=0x%x base=0x%llx size=0x%llx\n",
             ctrl, base_pa, size);
}

/* Probe function */
static int cxl_type2_probe(struct pci_dev *pdev,
                            const struct pci_device_id *id)
{
    struct cxl_type2_context *ctx;
    struct cxl_dev_state *cxlds;
    struct cxl_memdev_state *mds;
    int rc;
    u16 dvsec;

    dev_info(&pdev->dev, "CXL Type 2 Accelerator probing\n");

    /* Only bind to function 0 (multi-function devices) */
    if (PCI_FUNC(pdev->devfn) != 0)
        return -ENODEV;

    /* Enable PCI device */
    rc = pcim_enable_device(pdev);
    if (rc) {
        dev_err(&pdev->dev, "Failed to enable device: %d\n", rc);
        return rc;
    }

    pci_set_master(pdev);

    /* Allocate context */
    ctx = devm_kzalloc(&pdev->dev, sizeof(*ctx), GFP_KERNEL);
    if (!ctx)
        return -ENOMEM;

    ctx->pdev = pdev;
    mutex_init(&ctx->lock);
    pci_set_drvdata(pdev, ctx);

    /* Enable PF1 for multi-function devices */
    cxl_type2_enable_pf1(pdev);

    /* Allocate device state */
    cxlds = devm_kzalloc(&pdev->dev, sizeof(*cxlds), GFP_KERNEL);
    if (!cxlds)
        return -ENOMEM;

    cxlds->dev = &pdev->dev;
    cxlds->serial = pci_get_dsn(pdev);
    cxlds->cxl_dvsec = pci_find_dvsec_capability(pdev,
                                                    PCI_VENDOR_ID_CXL,
                                                    CXL_DVSEC_PCIE_DEVICE);
    cxlds->type = CXL_DEVTYPE_CLASSMEM;
    cxlds->media_ready = true;

    /* Set cache state defaults (128MB cache, 64-byte lines) */
    cxlds->cstate.size = 128 * 1024 * 1024;
    cxlds->cstate.unit = 64;
    cxlds->cstate.snoop_id = CXL_SNOOP_ID_NO_ID;
    cxlds->cstate.cache_id = CXL_CACHE_ID_NO_ID;

    /* Check CXL capabilities */
    dvsec = cxlds->cxl_dvsec;
    if (dvsec) {
        u16 cap, ctrl;
        pci_read_config_word(pdev, dvsec + CXL_DVSEC_CAP_OFFSET, &cap);
        pci_read_config_word(pdev, dvsec + CXL_DVSEC_CTRL_OFFSET, &ctrl);

        dev_info(&pdev->dev, "CXL DVSEC: cap=0x%04x ctrl=0x%04x\n",
                 cap, ctrl);

        /* Enable CXL capabilities */
        if (cap & CXL_DVSEC_CAP_CACHE_CAPABLE) {
            ctrl |= CXL_DVSEC_CTRL_CACHE_ENABLE;
            dev_info(&pdev->dev, "CXL.cache capable\n");
        }

        if (cap & CXL_DVSEC_CAP_IO_CAPABLE) {
            ctrl |= CXL_DVSEC_CTRL_IO_ENABLE;
        }

        if (cap & CXL_DVSEC_CAP_MEM_CAPABLE) {
            ctrl |= CXL_DVSEC_CTRL_MEM_ENABLE;
            dev_info(&pdev->dev, "CXL.mem capable\n");
        }

        pci_write_config_word(pdev, dvsec + CXL_DVSEC_CTRL_OFFSET, ctrl);
        pci_read_config_word(pdev, dvsec + CXL_DVSEC_CTRL_OFFSET, &ctrl);

        dev_info(&pdev->dev, "CXL DVSEC ctrl=0x%04x: Cache%c IO%c Mem%c\n",
                 ctrl,
                 (ctrl & CXL_DVSEC_CTRL_CACHE_ENABLE) ? '+' : '-',
                 (ctrl & CXL_DVSEC_CTRL_IO_ENABLE) ? '+' : '-',
                 (ctrl & CXL_DVSEC_CTRL_MEM_ENABLE) ? '+' : '-');
    }

    ctx->cxlds = cxlds;

    /* Register cache device */
    ctx->cache_id = atomic_inc_return(&next_cache_id) - 1;
    rc = devm_cxl_add_cachedev(&pdev->dev, cxlds);
    if (IS_ERR(rc)) {
        dev_warn(&pdev->dev, "Cache device registration failed: %d\n",
                 PTR_ERR(rc));
    } else {
        dev_info(&pdev->dev, "Cache device registered\n");
    }

    /* Register memory device */
    ctx->mem_id = atomic_inc_return(&next_mem_id) - 1;
    rc = devm_cxl_add_memdev(&pdev->dev, cxlds);
    if (IS_ERR(rc)) {
        dev_warn(&pdev->dev, "Memory device registration failed: %d\n",
                 PTR_ERR(rc));
    } else {
        dev_info(&pdev->dev, "Memory device registered\n");
    }

    /* Commit HDM decoder with 4GB window at 0x100000000 (example) */
    cxl_type2_commit_hdm_decoder(pdev, 0x100000000ULL, 4ULL * SZ_1G);

    dev_info(&pdev->dev, "CXL Type 2 Accelerator initialized\n");
    return 0;
}

/* Remove function */
static void cxl_type2_remove(struct pci_dev *pdev)
{
    struct cxl_type2_context *ctx = pci_get_drvdata(pdev);

    dev_info(&pdev->dev, "CXL Type 2 Accelerator removed\n");

    /* Clean up will be handled by devm_ helpers */
    mutex_destroy(&ctx->lock);
}

/* PCI device table */
static const struct pci_device_id cxl_type2_pci_ids[] = {
    /* QEMU CXL Type 2 device */
    { PCI_DEVICE(CXL_TYPE2_VENDOR_ID, CXL_TYPE2_DEVICE_ID_QEMU) },
    /* Intel IA-780i Agilex 7 CXL Type 2 */
    { PCI_DEVICE(CXL_TYPE2_VENDOR_ID, CXL_TYPE2_DEVICE_ID_IA780I) },
    { }
};
MODULE_DEVICE_TABLE(pci, cxl_type2_pci_ids);

/* PCI driver */
static struct pci_driver cxl_type2_driver = {
    .name = "cxl_type2_accel",
    .id_table = cxl_type2_pci_ids,
    .probe = cxl_type2_probe,
    .remove = cxl_type2_remove,
};

/* Module init/exit */
static int __init cxl_type2_accel_init(void)
{
    int ret;

    ret = pci_register_driver(&cxl_type2_driver);
    if (ret) {
        pr_err("Failed to register CXL Type 2 driver: %d\n", ret);
        return ret;
    }

    pr_info("CXL Type 2 Accelerator driver loaded\n");
    return 0;
}

static void __exit cxl_type2_accel_exit(void)
{
    pci_unregister_driver(&cxl_type2_driver);
    pr_info("CXL Type 2 Accelerator driver unloaded\n");
}

MODULE_LICENSE("GPL");
MODULE_AUTHOR("CXLAgent Project");
MODULE_DESCRIPTION("CXL Type 2 Accelerator Driver");

module_init(cxl_type2_accel_init);
module_exit(cxl_type2_accel_exit);
