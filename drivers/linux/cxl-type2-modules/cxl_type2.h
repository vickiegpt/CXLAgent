/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * CXL Type 2 Driver Public API
 *
 * Copyright(c) 2025 CXLAgent Project
 */

#ifndef _CXL_TYPE2_H
#define _CXL_TYPE2_H

#include <linux/device.h>
#include <linux/cxl/cxl.h>

/* Forward declarations */
struct cxl_dev_state;
struct cxl_cachedev;
struct cxl_memdev;

/*
 * Register a CXL cache device with sysfs interface.
 * Creates /sys/bus/cxl/devices/cache{N} with attributes:
 *   - cache_size, cache_unit, numa_node (RO)
 *   - cache_disable (RW)
 *   - cache_invalid (RO)
 *   - init_wbinvd (WO)
 */
int devm_cxl_add_cachedev(struct device *parent,
                           struct cxl_dev_state *cxlds);

/*
 * Register a CXL memory device with sysfs interface.
 * Creates /sys/bus/cxl/devices/mem{N} with attributes:
 *   - serial, numa_node, firmware_version (RO)
 *   - ram/size, pmem/size (RO)
 */
int devm_cxl_add_memdev(struct device *parent,
                        struct cxl_dev_state *cxlds);

/*
 * Register a CXL memory window in /proc/iomem.
 * Format: "{start_hex}-{end_hex} : CXL Window {index}"
 */
int cxl_add_memory_window(resource_size_t start, resource_size_t end, int index);
int cxl_remove_memory_window(int index);

#endif /* _CXL_TYPE2_H */
