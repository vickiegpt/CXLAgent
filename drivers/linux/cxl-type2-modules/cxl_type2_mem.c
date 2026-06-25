// SPDX-License-Identifier: GPL-2.0-only
/*
 * CXL Type 2 Memory Device Driver
 *
 * Provides sysfs interface for CXL memory devices:
 * - serial, numa_node, firmware_version (RO)
 * - ram/size and pmem/size subdirectories
 *
 * Copyright(c) 2025 CXLAgent Project
 */

#include <linux/module.h>
#include <linux/device.h>
#include <linux/slab.h>
#include <linux/sysfs.h>
#include <linux/kobject.h>
#include <linux/cxl/cxl.h>
#include <linux/pci.h>

/* Memory device state */
struct cxl_memdev_state {
    struct cxl_dev_state cxlds;
    u64 total_bytes;
    u64 volatile_only_bytes;
    u64 active_volatile_bytes;
    u64 persistent_only_bytes;
    u64 active_persistent_bytes;
    char firmware_version[32];
};

/* Memory device structure */
struct cxl_memdev {
    struct device dev;
    struct device *parent;
    struct cxl_memdev_state *mds;
    struct kobject *ram_kobj;
    struct kobject *pmem_kobj;
    int id;
    struct mutex sysfs_lock;
};

/* Subdirectory size attributes */
struct mem_size_attr {
    struct kobj_attribute attr;
    u64 *size_ptr;
};

/* Serial number */
static ssize_t serial_show(struct device *dev,
                           struct device_attribute *attr, char *buf)
{
    struct cxl_memdev *cxlmd = dev_get_drvdata(dev);
    struct cxl_dev_state *cxlds = &cxlmd->mds->cxlds;

    return sysfs_emit(buf, "0x%llx\n", cxlds->serial);
}

/* NUMA node */
static ssize_t numa_node_show(struct device *dev,
                              struct device_attribute *attr, char *buf)
{
    struct cxl_memdev *cxlmd = dev_get_drvdata(dev);
    return sysfs_emit(buf, "%d\n", dev_to_node(&cxlmd->dev));
}

/* Firmware version */
static ssize_t firmware_version_show(struct device *dev,
                                      struct device_attribute *attr,
                                      char *buf)
{
    struct cxl_memdev *cxlmd = dev_get_drvdata(dev);
    struct cxl_memdev_state *mds = cxlmd->mds;

    return sysfs_emit(buf, "%s\n", mds->firmware_version);
}

/* RAM size attribute */
static ssize_t ram_size_show(struct kobject *kobj, struct kobj_attribute *attr,
                              char *buf)
{
    struct cxl_memdev *cxlmd = container_of(kobj, struct cxl_memdev, ram_kobj->parent);
    struct cxl_memdev_state *mds = cxlmd->mds;

    /* Return hex format as expected by Python code */
    return sysfs_emit(buf, "0x%llx\n", mds->active_volatile_bytes);
}

/* PMEM size attribute */
static ssize_t pmem_size_show(struct kobject *kobj, struct kobj_attribute *attr,
                               char *buf)
{
    struct cxl_memdev *cxlmd = container_of(kobj, struct cxl_memdev, pmem_kobj->parent);
    struct cxl_memdev_state *mds = cxlmd->mds;

    /* Return hex format as expected by Python code */
    return sysfs_emit(buf, "0x%llx\n", mds->active_persistent_bytes);
}

/* Device attributes */
static DEVICE_ATTR_RO(serial);
static DEVICE_ATTR_RO(numa_node);
static DEVICE_ATTR_RO(firmware_version);

static struct attribute *cxl_memdev_attrs[] = {
    &dev_attr_serial.attr,
    &dev_attr_numa_node.attr,
    &dev_attr_firmware_version.attr,
    NULL,
};

static const struct attribute_group cxl_memdev_attr_group = {
    .attrs = cxl_memdev_attrs,
};

/* RAM/PMEM kobj attributes */
static struct kobj_attribute ram_size_attr = __ATTR_RO(ram_size);
static struct kobj_attribute pmem_size_attr = __ATTR_RO(pmem_size);

static struct attribute *ram_attrs[] = {
    &ram_size_attr.attr,
    NULL,
};

static struct attribute *pmem_attrs[] = {
    &pmem_size_attr.attr,
    NULL,
};

static const struct attribute_group ram_attr_group = {
    .attrs = ram_attrs,
    .name = "ram",
};

static const struct attribute_group pmem_attr_group = {
    .attrs = pmem_attrs,
    .name = "pmem",
};

/* Create ram/ and pmem/ subdirectories */
static int cxl_memdev_create_size_kobjs(struct cxl_memdev *cxlmd)
{
    struct kobject *dev_kobj = &cxlmd->dev.kobj;
    int ret;

    /* Create ram subdirectory */
    cxlmd->ram_kobj = kobject_create();
    if (!cxlmd->ram_kobj) {
        dev_err(&cxlmd->dev, "Failed to create ram kobject\n");
        return -ENOMEM;
    }

    ret = kobject_add(cxlmd->ram_kobj, dev_kobj, "ram");
    if (ret) {
        dev_err(&cxlmd->dev, "Failed to add ram kobject: %d\n", ret);
        goto err_ram;
    }

    ret = sysfs_create_group(cxlmd->ram_kobj, &ram_attr_group);
    if (ret) {
        dev_err(&cxlmd->dev, "Failed to create ram attributes: %d\n", ret);
        goto err_ram_add;
    }

    /* Create pmem subdirectory */
    cxlmd->pmem_kobj = kobject_create();
    if (!cxlmd->pmem_kobj) {
        dev_err(&cxlmd->dev, "Failed to create pmem kobject\n");
        ret = -ENOMEM;
        goto err_ram_attrs;
    }

    ret = kobject_add(cxlmd->pmem_kobj, dev_kobj, "pmem");
    if (ret) {
        dev_err(&cxlmd->dev, "Failed to add pmem kobject: %d\n", ret);
        goto err_pmem;
    }

    ret = sysfs_create_group(cxlmd->pmem_kobj, &pmem_attr_group);
    if (ret) {
        dev_err(&cxlmd->dev, "Failed to create pmem attributes: %d\n", ret);
        goto err_pmem_add;
    }

    return 0;

err_pmem_add:
    kobject_del(cxlmd->pmem_kobj);
err_pmem:
    kobject_put(cxlmd->pmem_kobj);
err_ram_attrs:
    sysfs_remove_group(cxlmd->ram_kobj, &ram_attr_group);
err_ram_add:
    kobject_del(cxlmd->ram_kobj);
err_ram:
    kobject_put(cxlmd->ram_kobj);
    return ret;
}

/* Memory device release */
static void cxl_memdev_release(struct device *dev)
{
    struct cxl_memdev *cxlmd = to_cxl_memdev(dev);

    /* Clean up subdirectories */
    if (cxlmd->ram_kobj) {
        sysfs_remove_group(cxlmd->ram_kobj, &ram_attr_group);
        kobject_del(cxlmd->ram_kobj);
        kobject_put(cxlmd->ram_kobj);
    }

    if (cxlmd->pmem_kobj) {
        sysfs_remove_group(cxlmd->pmem_kobj, &pmem_attr_group);
        kobject_del(cxlmd->pmem_kobj);
        kobject_put(cxlmd->pmem_kobj);
    }

    kfree(cxlmd->mds);
    kfree(cxlmd);
}

/* Device class */
static struct class cxl_memdev_class = {
    .name = "cxl_memdev",
    .owner = THIS_MODULE,
    .dev_release = cxl_memdev_release,
};

/* Memory device registration */
int devm_cxl_add_memdev(struct device *parent,
                        struct cxl_dev_state *cxlds)
{
    struct cxl_memdev *cxlmd;
    struct cxl_memdev_state *mds;
    int ret;

    if (!parent || !cxlds)
        return -EINVAL;

    /* Allocate memory device state */
    mds = kzalloc(sizeof(*mds), GFP_KERNEL);
    if (!mds)
        return -ENOMEM;

    mds->cxlds = *cxlds;
    mds->total_bytes = cxlds->dpa_res.end - cxlds->dpa_res.start + 1;

    /* Set up partition sizes (example values, should come from hardware) */
    mds->active_volatile_bytes = mds->total_bytes / 2;
    mds->active_persistent_bytes = mds->total_bytes / 2;
    snprintf(mds->firmware_version, sizeof(mds->firmware_version),
             "1.0.0");

    /* Allocate memory device */
    cxlmd = kzalloc(sizeof(*cxlmd), GFP_KERNEL);
    if (!cxlmd) {
        kfree(mds);
        return -ENOMEM;
    }

    cxlmd->parent = parent;
    cxlmd->mds = mds;
    cxlmd->dev.parent = parent;
    cxlmd->dev.class = &cxl_memdev_class;
    cxlmd->dev.groups = &cxl_memdev_attr_group;
    device_initialize(&cxlmd->dev);

    mutex_init(&cxlmd->sysfs_lock);

    ret = dev_set_name(&cxlmd->dev, "mem%d", cxlmd->id);
    if (ret)
        goto err;

    ret = device_add(&cxlmd->dev);
    if (ret)
        goto err;

    /* Create ram/ and pmem/ subdirectories */
    ret = cxl_memdev_create_size_kobjs(cxlmd);
    if (ret) {
        device_del(&cxlmd->dev);
        goto err;
    }

    dev_set_drvdata(&cxlmd->dev, cxlmd);
    dev_info(parent, "CXL memory device registered: mem%d\n", cxlmd->id);

    return 0;

err:
    put_device(&cxlmd->dev);
    return ret;
}
EXPORT_SYMBOL_GPL(devm_cxl_add_memdev);

/* Module init/exit */
static int __init cxl_type2_mem_init(void)
{
    int ret;

    ret = class_register(&cxl_memdev_class);
    if (ret) {
        pr_err("Failed to register cxl_memdev class: %d\n", ret);
        return ret;
    }

    pr_info("CXL Type 2 Memory driver loaded\n");
    return 0;
}

static void __exit cxl_type2_mem_exit(void)
{
    class_unregister(&cxl_memdev_class);
    pr_info("CXL Type 2 Memory driver unloaded\n");
}

MODULE_LICENSE("GPL");
MODULE_AUTHOR("CXLAgent Project");
MODULE_DESCRIPTION("CXL Type 2 Memory Device Driver");

module_init(cxl_type2_mem_init);
module_exit(cxl_type2_mem_exit);
