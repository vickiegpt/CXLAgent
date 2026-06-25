// SPDX-License-Identifier: GPL-2.0-only
/*
 * CXL /proc/iomem Integration
 *
 * Adds CXL memory windows to /proc/iomem for discovery by user-space tools.
 * Format: "{start_hex}-{end_hex} : CXL Window {index}"
 *
 * Copyright(c) 2025 CXLAgent Project
 */

#include <linux/module.h>
#includelinux/kernel.h>
#include <linux/ioport.h>
#include <linux/slab.h>
#include <linux/cxl/cxl.h>

/* Maximum number of CXL windows */
#define CXL_MAX_WINDOWS  16

/* CXL window tracking */
struct cxl_window {
    resource_size_t start;
    resource_size_t end;
    int index;
    struct resource *resource;
    bool registered;
};

static struct cxl_window cxl_windows[CXL_MAX_WINDOWS];
static DEFINE_SPINLOCK(cxl_window_lock);
static int next_window_index;

/* Register a CXL memory window */
int cxl_add_memory_window(resource_size_t start, resource_size_t end, int index)
{
    struct cxl_window *win;
    struct resource *res;
    int i;
    char name[32];

    if (index < 0) {
        spin_lock(&cxl_window_lock);
        index = next_window_index++;
        spin_unlock(&cxl_window_lock);
    }

    if (index >= CXL_MAX_WINDOWS)
        return -ENOSPC;

    /* Check if already registered */
    for (i = 0; i < CXL_MAX_WINDOWS; i++) {
        if (cxl_windows[i].registered && cxl_windows[i].index == index) {
            pr_debug("CXL Window %d already registered\n", index);
            return -EEXIST;
        }
    }

    /* Find free slot */
    win = NULL;
    for (i = 0; i < CXL_MAX_WINDOWS; i++) {
        if (!cxl_windows[i].registered) {
            win = &cxl_windows[i];
            break;
        }
    }

    if (!win)
        return -ENOSPC;

    snprintf(name, sizeof(name), "CXL Window %d", index);

    /* Request resource region */
    res = request_mem_region(start, end - start + 1, name);
    if (!res) {
        pr_err("Failed to register CXL Window %d (0x%llx-0x%llx)\n",
               index, (unsigned long long)start,
               (unsigned long long)end);
        return -EBUSY;
    }

    win->start = start;
    win->end = end;
    win->index = index;
    win->resource = res;
    win->registered = true;

    pr_info("Registered CXL Window %d: 0x%llx-0x%llx (%llu MB)\n",
            index, (unsigned long long)start,
            (unsigned long long)end,
            (end - start + 1) / (1024 * 1024));

    return 0;
}
EXPORT_SYMBOL_GPL(cxl_add_memory_window);

/* Remove a CXL memory window */
int cxl_remove_memory_window(int index)
{
    struct cxl_window *win = NULL;
    int i;

    spin_lock(&cxl_window_lock);
    for (i = 0; i < CXL_MAX_WINDOWS; i++) {
        if (cxl_windows[i].registered && cxl_windows[i].index == index) {
            win = &cxl_windows[i];
            break;
        }
    }
    spin_unlock(&cxl_window_lock);

    if (!win)
        return -ENOENT;

    release_resource(win->resource);
    win->registered = false;

    pr_info("Removed CXL Window %d\n", index);
    return 0;
}
EXPORT_SYMBOL_GPL(cxl_remove_memory_window);

/* Module init/exit */
static int __init cxl_iomem_init(void)
{
    int i;

    /* Initialize window tracking */
    for (i = 0; i < CXL_MAX_WINDOWS; i++) {
        cxl_windows[i].registered = false;
    }
    next_window_index = 0;

    /* Register default CXL windows (example for QEMU) */
    cxl_add_memory_window(0x100000000ULL, 0x13fffffffULL, 0);  /* 1GB at 4GB */
    cxl_add_memory_window(0x140000000ULL, 0x17fffffffULL, 1);  /* 1GB at 5GB */

    pr_info("CXL /proc/iomem integration loaded\n");
    return 0;
}

static void __exit cxl_iomem_exit(void)
{
    int i;

    /* Clean up registered windows */
    for (i = 0; i < CXL_MAX_WINDOWS; i++) {
        if (cxl_windows[i].registered) {
            release_resource(cxl_windows[i].resource);
        }
    }

    pr_info("CXL /proc/iomem integration unloaded\n");
}

MODULE_LICENSE("GPL");
MODULE_AUTHOR("CXLAgent Project");
MODULE_DESCRIPTION("CXL /proc/iomem Integration");

module_init(cxl_iomem_init);
module_exit(cxl_iomem_exit);
