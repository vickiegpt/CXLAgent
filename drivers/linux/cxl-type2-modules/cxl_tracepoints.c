// SPDX-License-Identifier: GPL-2.0-only
/*
 * CXL Tracepoint Definitions
 *
 * Provides kernel tracepoints for CXL transaction monitoring:
 * - cxl_general_media: General CXL memory transactions
 * - cxl_dram: DRAM-specific events
 * - cxl_poison: Memory poison detection
 *
 * Tracepoints appear under: /sys/kernel/tracing/events/cxl/
 *
 * Copyright(c) 2025 CXLAgent Project
 */

#undef TRACE_SYSTEM
#define TRACE_SYSTEM cxl

#if !defined(_CXL_TRACEPOINTS_H) || defined(TRACE_HEADER_MULTI_READ)
#define _CXL_TRACEPOINTS_H

#include <linux/tracepoint.h>
#include <linux/cxl/cxl.h>

/*
 * CXL General Media Transaction
 * Logged when CXL memory transactions occur
 */
TRACE_EVENT(cxl_general_media,

    TP_PROTO(const char *memdev, u64 serial, const char *transaction_type,
             u64 dpa, u64 hpa, const char *region),

    TP_ARGS(memdev, serial, transaction_type, dpa, hpa, region),

    TP_STRUCT__entry(
        __string(memdev, memdev)
        __field(u64, serial)
        __string(transaction_type, transaction_type)
        __field(u64, dpa)
        __field(u64, hpa)
        __string(region, region)
    ),

    TP_fast_assign(
        __assign_str(memdev, memdev);
        __entry->serial = serial;
        __assign_str(transaction_type, transaction_type);
        __entry->dpa = dpa;
        __entry->hpa = hpa;
        __assign_str(region, region);
    ),

    TP_printk("memdev=%s serial=0x%llx transaction_type=%s dpa=0x%llx hpa=0x%llx region=%s",
              __get_str(memdev), __entry->serial,
              __get_str(transaction_type), __entry->dpa,
              __entry->hpa, __get_str(region))
);

/*
 * CXL DRAM Event
 * Logged for DRAM-specific events (refresh, read, write)
 */
TRACE_EVENT(cxl_dram,

    TP_PROTO(const char *memdev, u64 serial, u64 physical_address,
             unsigned int event_type, unsigned int bank_number),

    TP_ARGS(memdev, serial, physical_address, event_type, bank_number),

    TP_STRUCT__entry(
        __string(memdev, memdev)
        __field(u64, serial)
        __field(u64, physical_address)
        __field(unsigned int, event_type)
        __field(unsigned int, bank_number)
    ),

    TP_fast_assign(
        __assign_str(memdev, memdev);
        __entry->serial = serial;
        __entry->physical_address = physical_address;
        __entry->event_type = event_type;
        __entry->bank_number = bank_number;
    ),

    TP_printk("memdev=%s serial=0x%llx addr=0x%llx event_type=%u bank=%u",
              __get_str(memdev), __entry->serial,
              __entry->physical_address, __entry->event_type,
              __entry->bank_number)
);

/*
 * CXL Poison Event
 * Logged when memory poison is detected
 */
TRACE_EVENT(cxl_poison,

    TP_PROTO(const char *memdev, u64 serial, u64 poison_address,
             unsigned int poison_type, u64 pattern),

    TP_ARGS(memdev, serial, poison_address, poison_type, pattern),

    TP_STRUCT__entry(
        __string(memdev, memdev)
        __field(u64, serial)
        __field(u64, poison_address)
        __field(unsigned int, poison_type)
        __field(u64, pattern)
    ),

    TP_fast_assign(
        __assign_str(memdev, memdev);
        __entry->serial = serial;
        __entry->poison_address = poison_address;
        __entry->poison_type = poison_type;
        __entry->pattern = pattern;
    ),

    TP_printk("memdev=%s serial=0x%llx poison_addr=0x%llx type=%u pattern=0x%llx",
              __get_str(memdev), __entry->serial,
              __entry->poison_address, __entry->poison_type,
              __entry->pattern)
);

#endif /* _CXL_TRACEPOINTS_H */

/* This must be outside the guard */
#include <trace/define_trace.h>
