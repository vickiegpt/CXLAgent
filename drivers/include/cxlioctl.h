/*++
Copyright (c) 2025 CXLAgent Project

Module Name:
    cxlioctl.h

Abstract:
    Shared IOCTL definitions and data structures for CXL drivers.
    Used by CXLBus, CXLCache, CXLMemory, CXLAccel, and PhysMem drivers.

--*/

#ifndef _CXLIOCTL_H_
#define _CXLIOCTL_H_

#include <ntdef.h>

//
// Device types
//
#define FILE_DEVICE_CXL         0x00008000  // Custom device type for CXL drivers
#define FILE_DEVICE_PHYSMEM     0x00008001  // Physical memory access driver

//
// IOCTL method definitions
//
#define METHOD_BUFFERED         0
#define FILE_READ_ACCESS        0x0001
#define FILE_WRITE_ACCESS       0x0002

//
// IOCTL control code macro
//
#define CTL_CODE(DeviceType, Function, Method, Access) \
    ((DeviceType) << 16 | ((Access) << 14) | ((Function) << 2) | (Method))

//=============================================================================
// CXL Bus IOCTLs (0x800 - 0x80F)
//=============================================================================

#define IOCTL_CXL_GET_TOPOLOGY \
    CTL_CODE(FILE_DEVICE_CXL, 0x800, METHOD_BUFFERED, FILE_READ_ACCESS)

#define IOCTL_CXL_GET_CHILD_DEVICES \
    CTL_CODE(FILE_DEVICE_CXL, 0x801, METHOD_BUFFERED, FILE_READ_ACCESS)

#define IOCTL_CXL_GET_PCI_INFO \
    CTL_CODE(FILE_DEVICE_CXL, 0x802, METHOD_BUFFERED, FILE_READ_ACCESS)

//=============================================================================
// CXL Cache IOCTLs (0x810 - 0x81F)
//=============================================================================

#define IOCTL_CXL_TRIGGER_WBINVD \
    CTL_CODE(FILE_DEVICE_CXL, 0x810, METHOD_BUFFERED, FILE_WRITE_ACCESS)

#define IOCTL_CXL_GET_CACHE_STATE \
    CTL_CODE(FILE_DEVICE_CXL, 0x811, METHOD_BUFFERED, FILE_READ_ACCESS)

#define IOCTL_CXL_SET_CACHE_DISABLE \
    CTL_CODE(FILE_DEVICE_CXL, 0x812, METHOD_BUFFERED, FILE_WRITE_ACCESS)

#define IOCTL_CXL_GET_CACHE_SIZE \
    CTL_CODE(FILE_DEVICE_CXL, 0x813, METHOD_BUFFERED, FILE_READ_ACCESS)

//=============================================================================
// CXL Memory IOCTLs (0x820 - 0x82F)
//=============================================================================

#define IOCTL_CXL_GET_MEMORY_INFO \
    CTL_CODE(FILE_DEVICE_CXL, 0x820, METHOD_BUFFERED, FILE_READ_ACCESS)

#define IOCTL_CXL_GET_MEMORY_WINDOWS \
    CTL_CODE(FILE_DEVICE_CXL, 0x821, METHOD_BUFFERED, FILE_READ_ACCESS)

#define IOCTL_CXL_GET_NUMA_INFO \
    CTL_CODE(FILE_DEVICE_CXL, 0x822, METHOD_BUFFERED, FILE_READ_ACCESS)

//=============================================================================
// CXL Accelerator IOCTLs (0x830 - 0x83F)
//=============================================================================

#define IOCTL_CXL_GET_ACCEL_INFO \
    CTL_CODE(FILE_DEVICE_CXL, 0x830, METHOD_BUFFERED, FILE_READ_ACCESS)

#define IOCTL_CXL_SUBMIT_WORK \
    CTL_CODE(FILE_DEVICE_CXL, 0x831, METHOD_BUFFERED, FILE_WRITE_ACCESS)

#define IOCTL_CXL_GET_WORK_STATUS \
    CTL_CODE(FILE_DEVICE_CXL, 0x832, METHOD_BUFFERED, FILE_READ_ACCESS)

#define IOCTL_CXL_RECONFIGURE_FPGA \
    CTL_CODE(FILE_DEVICE_CXL, 0x833, METHOD_BUFFERED, FILE_WRITE_ACCESS)

//=============================================================================
// Physical Memory IOCTLs (0x900 - 0x90F)
//=============================================================================

#define IOCTL_PHYSMEM_MAP_MEMORY \
    CTL_CODE(FILE_DEVICE_PHYSMEM, 0x900, METHOD_BUFFERED, FILE_READ_ACCESS)

#define IOCTL_PHYSMEM_UNMAP_MEMORY \
    CTL_CODE(FILE_DEVICE_PHYSMEM, 0x901, METHOD_BUFFERED, FILE_READ_ACCESS)

#define IOCTL_PHYSMEM_VALIDATE_ADDRESS \
    CTL_CODE(FILE_DEVICE_PHYSMEM, 0x902, METHOD_BUFFERED, FILE_READ_ACCESS)

//=============================================================================
// Data Structures
//=============================================================================

//
// CXL Bus Topology
//
typedef struct _CXL_TOPOLOGY {
    UINT32 CacheDeviceCount;
    UINT32 MemoryDeviceCount;
    UINT32 AcceleratorDeviceCount;
    UINT32 TotalDeviceCount;
} CXL_TOPOLOGY, *PCXL_TOPOLOGY;

//
// CXL Device Type
//
typedef enum _CXL_DEVICE_TYPE {
    CxlDeviceTypeCache = 1,
    CxlDeviceTypeMemory = 2,
    CxlDeviceTypeAccelerator = 3
} CXL_DEVICE_TYPE;

//
// CXL Cache Device Information
//
typedef struct _CXL_CACHE_INFO {
    WCHAR Name[32];              // e.g., L"cache0"
    WCHAR PciBdf[16];            // e.g., L"0000:3B:00.0"
    UINT64 Size;                 // Cache size in bytes
    WCHAR Unit[16];              // e.g., L"128 MiB"
    UINT32 NumaNode;
    BOOLEAN Disabled;
    BOOLEAN Invalid;
    BOOLEAN WbinvdSupported;
    UINT64 Bar2PhysicalAddress;  // BAR2 MMIO physical address
    UINT64 Bar2Size;              // BAR2 size
} CXL_CACHE_INFO, *PCXL_CACHE_INFO;

//
// CXL Memory Device Information
//
typedef struct _CXL_MEMORY_INFO {
    WCHAR Name[32];              // e.g., L"mem0"
    WCHAR PciBdf[16];
    UINT64 TotalSize;            // Total memory in bytes
    UINT64 RamSize;              // Volatile memory in bytes
    UINT64 PmemSize;             // Persistent memory in bytes
    UINT32 NumaNode;
    WCHAR FirmwareVersion[32];
} CXL_MEMORY_INFO, *PCXL_MEMORY_INFO;

//
// CXL Accelerator Device Information
//
typedef struct _CXL_ACCEL_INFO {
    WCHAR Name[32];              // e.g., L"accelerator0"
    WCHAR PciBdf[16];
    UINT64 Bar0PhysicalAddress;  // FPGA management BAR
    UINT64 Bar0Size;
    UINT64 Bar2PhysicalAddress;  // DMA BAR
    UINT64 Bar2Size;
    BOOLEAN FpgaConfigured;
    WCHAR BitstreamVersion[32];
    UINT32 WorkQueueDepth;
} CXL_ACCEL_INFO, *PCXL_ACCEL_INFO;

//
// CXL Memory Window
//
typedef struct _CXL_MEMORY_WINDOW {
    UINT32 Index;
    UINT64 StartPhysicalAddress;
    UINT64 EndPhysicalAddress;
    UINT64 Size;
    BOOLEAN IsPersistent;        // TRUE for PMEM, FALSE for RAM
} CXL_MEMORY_WINDOW, *PCXL_MEMORY_WINDOW;

//
// Physical Memory Mapping Request
//
typedef struct _PHYSMEM_MAP_REQUEST {
    UINT64 PhysicalAddress;
    UINT64 Size;
    UINT64 UserModeVirtualAddress;  // Output: mapped address in user mode
    HANDLE SectionHandle;           // Output: section handle for shared memory
} PHYSMEM_MAP_REQUEST, *PPHYSMEM_MAP_REQUEST;

//
// Physical Memory Unmap Request
//
typedef struct _PHYSMEM_UNMAP_REQUEST {
    HANDLE SectionHandle;
    UINT64 UserModeVirtualAddress;
} PHYSMEM_UNMAP_REQUEST, *PPHYSMEM_UNMAP_REQUEST;

//
// Physical Memory Validation Result
//
typedef struct _PHYSMEM_VALIDATE_RESULT {
    UINT64 PhysicalAddress;
    UINT64 Size;
    BOOLEAN IsValid;
    BOOLEAN IsCXLWindow;
    UINT32 WindowIndex;
} PHYSMEM_VALIDATE_RESULT, *PPHYSMEM_VALIDATE_RESULT;

//
// Cache State
//
typedef struct _CXL_CACHE_STATE {
    BOOLEAN Disabled;
    BOOLEAN Invalid;
    UINT64 Size;
    UINT64 Used;
} CXL_CACHE_STATE, *PCXL_CACHE_STATE;

//
// FPGA Reconfiguration Request
//
typedef struct _CXL_FPGA_RECONFIG_REQUEST {
    WCHAR BitstreamPath[MAX_PATH];
    UINT32 BitstreamSize;
    BOOLEAN ForceReconfigure;
} CXL_FPGA_RECONFIG_REQUEST, *PCXL_FPGA_RECONFIG_REQUEST;

//
// Work Submission
//
typedef struct _CXL_WORK_SUBMISSION {
    UINT64 InputAddress;
    UINT64 OutputAddress;
    UINT32 InputSize;
    UINT32 OutputSize;
    UINT64 WorkDescriptor;        // Work-queue descriptor
} CXL_WORK_SUBMISSION, *PCXL_WORK_SUBMISSION;

//
// Work Status
//
typedef struct _CXL_WORK_STATUS {
    UINT64 WorkId;
    BOOLEAN Completed;
    INT32 Status;
    UINT64 BytesProcessed;
} CXL_WORK_STATUS, *PCXL_WORK_STATUS;

//=============================================================================
// Device Names and Symbolic Links
//=============================================================================

#define CXLBUS_DEVICE_NAME      L"\\Device\\CXLBus0"
#define CXLBUS_SYMBOLIC_LINK    L"\\??\\CXLBus0"

#define CXLCACHE_DEVICE_NAME    L"\\Device\\CXLCache0"
#define CXLCACHE_SYMBOLIC_LINK  L"\\??\\CXLCache0"

#define CXL_MEMORY_DEVICE_NAME  L"\\Device\\CXLMemory0"
#define CXL_MEMORY_SYMBOLIC_LINK L"\\??\\CXLMemory0"

#define CXL_ACCEL_DEVICE_NAME   L"\\Device\\CXLAccel0"
#define CXL_ACCEL_SYMBOLIC_LINK L"\\??\\CXLAccel0"

#define PHYSMEM_DEVICE_NAME     L"\\Device\\PhysMem"
#define PHYSMEM_SYMBOLIC_LINK   L"\\??\\PhysMem"

//=============================================================================
// Registry Keys
//=============================================================================

#define CXL_REG_PATH_BASE       L"\\Registry\\Machine\\System\\CurrentControlSet\\Services"
#define CXLBUS_REG_PATH         CXL_REG_PATH_BASE L"\\CXLBus"
#define CXLCACHE_REG_PATH       CXL_REG_PATH_BASE L"\\CXLCache"
#define CXL_MEMORY_REG_PATH     CXL_REG_PATH_BASE L"\\CXLMemory"
#define CXL_ACCEL_REG_PATH      CXL_REG_PATH_BASE L"\\CXLAccel"
#define PHYSMEM_REG_PATH        CXL_REG_PATH_BASE L"\\PhysMem"

//=============================================================================
// CXL Extended Capability IDs (PCIe)
//=============================================================================

#define PCIE_CXL_CAP_ID         0x0001

#endif // _CXLIOCTL_H_
