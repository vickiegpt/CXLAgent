// SPDX-License-Identifier: GPL-2.0-only
/*
 * CXL Type 2 Cache Device Driver
 *
 * Provides sysfs interface for CXL cache devices with:
 * - cache_size, cache_unit, numa_node (RO)
 * - cache_disable (RW)
 * - cache_invalid (RO)
 * - init_wbinvd (WO trigger for cache flush)
 *
 * Copyright(c) 2025 CXLAgent Project
 */

#include <linux/module.h>
#include <linux/device.h>
#include <linux/slab.h>
#include <linux/sysfs.h>
#include <linux/kobject.h>
#include <linux/uaccess.h>
#include <linux/delay.h>
#include <linux/notifier.h>
#include <linux/cxl/cxl.h>
#include <linux/pci.h>

/* Cache state structure */
struct cxl_cache_state {
    u64 size;           /* Cache size in bytes */
    u32 unit;           /* Cache line size (bytes) */
    int snoop_id;
    int cache_id;
    bool disabled;      /* Cache disabled state */
    bool invalid;       /* Cache invalid state */
    spinlock_t lock;
};

/* Cache device structure */
struct cxl_cachedev {
    struct device dev;
    struct device *parent;
    struct cxl_cache_state cstate;
    struct kobject kobj;
    void __iomem *bar2;      /* BAR2 MMIO for cache controller */
    struct mutex sysfs_lock;
    int id;
};

/* Device attributes */
static ssize_t cache_size_show(struct device *dev,
                                struct device_attribute *attr, char *buf)
{
    struct cxl_cachedev *cxlcd = dev_get_drvdata(dev);
    struct cxl_cache_state *cstate = &cxlcd->cstate;
    unsigned long flags;
    u64 size;

    spin_lock_irqsave(&cstate->lock, flags);
    size = cstate->size;
    spin_unlock_irqrestore(&cstate->lock, flags);

    return sysfs_emit(buf, "%llu\n", size);
}

static ssize_t cache_unit_show(struct device *dev,
                                struct device_attribute *attr, char *buf)
{
    struct cxl_cachedev *cxlcd = dev_get_drvdata(dev);
    struct cxl_cache_state *cstate = &cxlcd->cstate;
    unsigned long flags;
    u32 unit;

    spin_lock_irqsave(&cstate->lock, flags);
    unit = cstate->unit;
    spin_unlock_irqrestore(&cstate->lock, flags);

    return sysfs_emit(buf, "%u\n", unit);
}

static ssize_t cache_unit_str_show(struct device *dev,
                                    struct device_attribute *attr, char *buf)
{
    struct cxl_cachedev *cxlcd = dev_get_drvdata(dev);
    struct cxl_cache_state *cstate = &cxlcd->cstate;
    unsigned long flags;
    u64 size;
    u32 unit;

    spin_lock_irqsave(&cstate->lock, flags);
    size = cstate->size;
    unit = cstate->unit;
    spin_unlock_irqrestore(&cstate->lock, flags);

    /* Human-readable size string */
    if (size >= (1024 * 1024 * 1024)) {
        return sysfs_emit(buf, "%llu GiB\n", size / (1024 * 1024 * 1024));
    } else if (size >= (1024 * 1024)) {
        return sysfs_emit(buf, "%llu MiB\n", size / (1024 * 1024));
    } else {
        return sysfs_emit(buf, "%llu KiB\n", size / 1024);
    }
}

static ssize_t numa_node_show(struct device *dev,
                               struct device_attribute *attr, char *buf)
{
    struct cxl_cachedev *cxlcd = dev_get_drvdata(dev);
    return sysfs_emit(buf, "%d\n", dev_to_node(&cxlcd->dev));
}

static ssize_t cache_disable_show(struct device *dev,
                                    struct device_attribute *attr, char *buf)
{
    struct cxl_cachedev *cxlcd = dev_get_drvdata(dev);
    struct cxl_cache_state *cstate = &cxlcd->cstate;
    unsigned long flags;
    bool disabled;

    spin_lock_irqsave(&cstate->lock, flags);
    disabled = cstate->disabled;
    spin_unlock_irqrestore(&cstate->lock, flags);

    return sysfs_emit(buf, "%u\n", disabled ? 1 : 0);
}

static ssize_t cache_disable_store(struct device *dev,
                                     struct device_attribute *attr,
                                     const char *buf, size_t count)
{
    struct cxl_cachedev *cxlcd = dev_get_drvdata(dev);
    struct cxl_cache_state *cstate = &cxlcd->cstate;
    unsigned long flags;
    bool disable;
    int ret;

    ret = kstrtobool(buf, &disable);
    if (ret)
        return ret;

    mutex_lock(&cxlcd->sysfs_lock);

    spin_lock_irqsave(&cstate->lock, flags);
    cstate->disabled = disable;
    spin_unlock_irqrestore(&cstate->lock, flags);

    dev_info(dev, "Cache %s\n", disable ? "disabled" : "enabled");

    mutex_unlock(&cxlcd->sysfs_lock);
    return count;
}

static ssize_t cache_invalid_show(struct device *dev,
                                   struct device_attribute *attr, char *buf)
{
    struct cxl_cachedev *cxlcd = dev_get_drvdata(dev);
    struct cxl_cache_state *cstate = &cxlcd->cstate;
    unsigned long flags;
    bool invalid;

    spin_lock_irqsave(&cstate->lock, flags);
    invalid = cstate->invalid;
    spin_unlock_irqrestore(&cstate->lock, flags);

    return sysfs_emit(buf, "%u\n", invalid ? 1 : 0);
}

/* WBINVD trigger - write-only attribute */
static ssize_t init_wbinvd_store(struct device *dev,
                                   struct device_attribute *attr,
                                   const char *buf, size_t count)
{
    struct cxl_cachedev *cxlcd = dev_get_drvdata(dev);
    struct cxl_cache_state *cstate = &cxlcd->cstate;
    unsigned long flags;
    bool trigger;
    int ret;

    ret = kstrtobool(buf, &trigger);
    if (ret)
        return ret;

    if (!trigger)
        return count;  /* Only trigger on "1" */

    mutex_lock(&cxlcd->sysfs_lock);

    dev_info(dev, "Triggering WBINVD (Write Back + Invalidate)\n");

    /* Execute WBINVD instruction */
    wbinvd();

    /* Update invalid state */
    spin_lock_irqsave(&cstate->lock, flags);
    cstate->invalid = false;
    spin_unlock_irqrestore(&cstate->lock, flags);

    dev_info(dev, "WBINVD complete - cache flushed\n");

    mutex_unlock(&cxlcd->sysfs_lock);
    return count;
}

/* BAR2 resource */
static ssize_t resource2_show(struct device *dev,
                               struct device_attribute *attr, char *buf)
{
    struct cxl_cachedev *cxlcd = dev_get_drvdata(dev);
    struct pci_dev *pdev = to_pci_dev(cxlcd->parent);
    resource_size_t start, end;

    if (!cxlcd->bar2) {
        return sysfs_emit(buf, "0\n");
    }

    start = pci_resource_start(pdev, 2);
    end = pci_resource_end(pdev, 2);

    return sysfs_emit(buf, "0x%llx-0x%llx\n",
                      (unsigned long long)start,
                      (unsigned long long)end);
}

/* Device attribute definitions */
static DEVICE_ATTR_RO(cache_size);
static DEVICE_ATTR_RO(cache_unit);
static DEVICE_ATTR_RO(cache_unit_str);  /* Human-readable */
static DEVICE_ATTR_RO(numa_node);
static DEVICE_ATTR_RW(cache_disable);
static DEVICE_ATTR_RO(cache_invalid);
static DEVICE_ATTR_WO(init_wbinvd);
static DEVICE_ATTR_RO(resource2);

static struct attribute *cxl_cache_attrs[] = {
    &dev_attr_cache_size.attr,
    &dev_attr_cache_unit.attr,
    &dev_attr_cache_unit_str.attr,
    &dev_attr_numa_node.attr,
    &dev_attr_cache_disable.attr,
    &dev_attr_cache_invalid.attr,
    &dev_attr_init_wbinvd.attr,
    &dev_attr_resource2.attr,
    NULL,
};

static const struct attribute_group cxl_cache_attr_group = {
    .attrs = cxl_cache_attrs,
};

/* Bus name for sysfs path */
static const char *cxl_cache_name(struct cxl_cachedev *cxlcd)
{
    return dev_name(&cxlcd->dev);
}

/* Cache device release */
static void cxl_cache_dev_release(struct device *dev)
{
    struct cxl_cachedev *cxlcd = to_cxl_cachedev(dev);

    kfree(cxlcd);
}

/* Device class */
static struct class cxl_cache_class = {
    .name = "cxl_cache",
    .owner = THIS_MODULE,
    .dev_release = cxl_cache_dev_release,
};

/* Cache device registration */
int devm_cxl_add_cachedev(struct device *parent,
                           struct cxl_dev_state *cxlds)
{
    struct cxl_cachedev *cxlcd;
    int ret;

    if (!parent || !cxlds)
        return -EINVAL;

    cxlcd = kzalloc(sizeof(*cxlcd), GFP_KERNEL);
    if (!cxlcd)
        return -ENOMEM;

    cxlcd->parent = parent;
    cxlcd->dev.parent = parent;
    cxlcd->dev.class = &cxl_cache_class;
    cxlcd->dev.type = &cxl_cache_type;
    device_initialize(&cxlcd->dev);

    /* Set up cache state */
    spin_lock_init(&cxlcd->cstate.lock);
    mutex_init(&cxlcd->sysfs_lock);
    cxlcd->cstate.size = cxlds->cstate.size;
    cxlcd->cstate.unit = cxlds->cstate.unit;
    cxlcd->cstate.snoop_id = cxlds->cstate.snoop_id;
    cxlcd->cstate.cache_id = cxlds->cstate.cache_id;
    cxlcd->cstate.disabled = false;
    cxlcd->cstate.invalid = false;

    /* Map BAR2 for cache controller access */
    if (dev_is_pci(parent)) {
        struct pci_dev *pdev = to_pci_dev(parent);
        resource_size_t bar2_start = pci_resource_start(pdev, 2);
        resource_size_t bar2_len = pci_resource_len(pdev, 2);

        if (bar2_start && bar2_len) {
            cxlcd->bar2 = ioremap(bar2_start, bar2_len);
            if (!cxlcd->bar2)
                dev_warn(parent, "Failed to map BAR2\n");
        }
    }

    dev_set_drvdata(&cxlcd->dev, cxlcd);
    ret = dev_set_name(&cxlcd->dev, "cache%d", cxlcd->id);
    if (ret)
        goto err;

    ret = device_add(&cxlcd->dev);
    if (ret)
        goto err;

    dev_info(parent, "CXL cache device registered: %s\n",
             cxl_cache_name(cxlcd));

    return 0;

err:
    put_device(&cxlcd->dev);
    return ret;
}
EXPORT_SYMBOL_GPL(devm_cxl_add_cachedev);

/* Module init/exit */
static int __init cxl_type2_cache_init(void)
{
    int ret;

    ret = class_register(&cxl_cache_class);
    if (ret) {
        pr_err("Failed to register cxl_cache class: %d\n", ret);
        return ret;
    }

    pr_info("CXL Type 2 Cache driver loaded\n");
    return 0;
}

static void __exit cxl_type2_cache_exit(void)
{
    class_unregister(&cxl_cache_class);
    pr_info("CXL Type 2 Cache driver unloaded\n");
}

MODULE_LICENSE("GPL");
MODULE_AUTHOR("CXLAgent Project");
MODULE_DESCRIPTION("CXL Type 2 Cache Device Driver");

module_init(cxl_type2_cache_init);
module_exit(cxl_type2_cache_exit);
