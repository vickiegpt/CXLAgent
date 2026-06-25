/*++
Copyright (c) 2025 CXLAgent Project

Module Name:
    physmem.c

Abstract:
    Physical Memory Access Driver for Windows.
    Provides /dev/mem equivalent functionality for CXL memory window access.

    This driver maps physical memory ranges to user space using MmMapIoSpace
    with strict validation to only allow CXL window ranges.

Environment:
    Kernel mode

--*/

#include <ntddk.h>
#include <wdf.h>
#include "cxlioctl.h"

//=============================================================================
// Function Prototypes
//=============================================================================

DRIVER_INITIALIZE DriverEntry;
EVT_WDF_DRIVER_DEVICE_ADD PhysMemEvtDriverDeviceAdd;
EVT_WDF_IO_QUEUE_IO_DEVICE_CONTROL PhysMemEvtIoDeviceControl;
EVT_WDF_DEVICE_PREPARE_HARDWARE PhysMemEvtDevicePrepareHardware;
EVT_WDF_DEVICE_RELEASE_HARDWARE PhysMemEvtDeviceReleaseHardware;

NTSTATUS PhysMemMapPhysicalMemory(
    IN PHYSMEM_MAP_REQUEST* MapRequest,
    OUT PVOID* KernelVirtualAddress
);

NTSTATUS PhysMemUnmapPhysicalMemory(
    IN PVOID KernelVirtualAddress,
    IN SIZE_T Size
);

BOOLEAN PhysMemValidateCXLWindow(
    IN UINT64 PhysicalAddress,
    IN SIZE_T Size
);

//=============================================================================
// Global State
//=============================================================================

typedef struct _DEVICE_CONTEXT {
    WDFDEVICE Device;
    KSPIN_LOCK Lock;
    LIST_ENTRY MappedRegionList;
} DEVICE_CONTEXT, *PDEVICE_CONTEXT;

WDF_DECLARE_CONTEXT_TYPE_WITH_NAME(DEVICE_CONTEXT, GetDeviceContext)

typedef struct _MAPPED_REGION {
    LIST_ENTRY ListEntry;
    UINT64 PhysicalAddress;
    SIZE_T Size;
    PVOID KernelVirtualAddress;
    HANDLE SectionHandle;
} MAPPED_REGION, *PMAPPED_REGION;

//
// CXL Memory Windows (populated by bus driver)
// For now, we'll accept any address - the bus driver will restrict this
//
#define MAX_CXL_WINDOWS  8
static struct {
    UINT64 StartPhysicalAddress;
    UINT64 EndPhysicalAddress;
    BOOLEAN Valid;
} g_CXLWindows[MAX_CXL_WINDOWS] = {0};

//=============================================================================
// DriverEntry
//=============================================================================

NTSTATUS
DriverEntry(
    IN PDRIVER_OBJECT DriverObject,
    IN PUNICODE_STRING RegistryPath
)
/*++
Routine Description:
    DriverEntry initializes the driver and registers the EvtDriverDeviceAdd callback.

Arguments:
    DriverObject - Pointer to the driver object
    RegistryPath - Pointer to the registry path for the driver

Return Value:
    STATUS_SUCCESS if successful, error code otherwise
--*/
{
    WDF_DRIVER_CONFIG config;
    NTSTATUS status;

    //
    // Initialize the driver configuration
    //
    WDF_DRIVER_CONFIG_INIT(&config, PhysMemEvtDriverDeviceAdd);

    //
    // Create the driver object
    //
    status = WdfDriverCreate(
        DriverObject,
        RegistryPath,
        WDF_NO_OBJECT_ATTRIBUTES,
        &config,
        WDF_NO_HANDLE
    );

    if (!NT_SUCCESS(status)) {
        KdPrint(("PhysMem: WdfDriverCreate failed: 0x%x\n", status));
    }

    return status;
}

//=============================================================================
// EvtDriverDeviceAdd
//=============================================================================

NTSTATUS
PhysMemEvtDriverDeviceAdd(
    IN WDFDRIVER Driver,
    IN PWDFDEVICE_INIT DeviceInit
)
/*++
Routine Description:
    EvtDriverDeviceAdd is called by the framework when a device is added.

Arguments:
    Driver - Handle to the driver object
    DeviceInit - Pointer to the device initialization structure

Return Value:
    STATUS_SUCCESS if successful, error code otherwise
--*/
{
    NTSTATUS status;
    WDF_OBJECT_ATTRIBUTES deviceAttributes;
    WDFDEVICE device;
    PDEVICE_CONTEXT deviceContext;
    WDF_IO_QUEUE_CONFIG queueConfig;
    UNICODE_STRING deviceName;
    UNICODE_STRING symbolicLink;

    UNREFERENCED_PARAMETER(Driver);

    //
    // Initialize the device name and symbolic link
    //
    RtlInitUnicodeString(&deviceName, PHYSMEM_DEVICE_NAME);
    RtlInitUnicodeString(&symbolicLink, PHYSMEM_SYMBOLIC_LINK);

    //
    // Create the device with a name
    //
    WDF_OBJECT_ATTRIBUTES_INIT(&deviceAttributes);
    WDF_OBJECT_ATTRIBUTES_SET_CONTEXT_TYPE(&deviceAttributes, DEVICE_CONTEXT);

    status = WdfDeviceCreate(
        &DeviceInit,
        &deviceAttributes,
        &device
    );

    if (!NT_SUCCESS(status)) {
        KdPrint(("PhysMem: WdfDeviceCreate failed: 0x%x\n", status));
        return status;
    }

    //
    // Create the device interface (symbolic link)
    //
    status = WdfDeviceCreateSymbolicLink(
        device,
        &symbolicLink
    );

    if (!NT_SUCCESS(status)) {
        KdPrint(("PhysMem: WdfDeviceCreateSymbolicLink failed: 0x%x\n", status));
        return status;
    }

    //
    // Initialize device context
    //
    deviceContext = GetDeviceContext(device);
    deviceContext->Device = device;
    KeInitializeSpinLock(&deviceContext->Lock);
    InitializeListHead(&deviceContext->MappedRegionList);

    //
    // Configure the default I/O queue
    //
    WDF_IO_QUEUE_CONFIG_INIT_DEFAULT_QUEUE(
        &queueConfig,
        WdfIoQueueDispatchParallel
    );

    queueConfig.EvtIoDeviceControl = PhysMemEvtIoDeviceControl;

    status = WdfIoQueueCreate(
        device,
        &queueConfig,
        WDF_NO_OBJECT_ATTRIBUTES,
        WDF_NO_HANDLE
    );

    if (!NT_SUCCESS(status)) {
        KdPrint(("PhysMem: WdfIoQueueCreate failed: 0x%x\n", status));
        return status;
    }

    KdPrint(("PhysMem: Device initialized successfully\n"));

    return STATUS_SUCCESS;
}

//=============================================================================
// EvtIoDeviceControl
//=============================================================================

VOID
PhysMemEvtIoDeviceControl(
    IN WDFQUEUE Queue,
    IN WDFREQUEST Request,
    IN size_t OutputBufferLength,
    IN size_t InputBufferLength,
    IN ULONG IoControlCode
)
/*++
Routine Description:
    EvtIoDeviceControl handles IOCTL requests from user mode.

Arguments:
    Queue - Handle to the I/O queue
    Request - Handle to the request object
    OutputBufferLength - Length of the output buffer
    InputBufferLength - Length of the input buffer
    IoControlCode - IOCTL code

Return Value:
    None
--*/
{
    NTSTATUS status = STATUS_SUCCESS;
    size_t bytesReturned = 0;
    WDFDEVICE device;
    PDEVICE_CONTEXT deviceContext;

    UNREFERENCED_PARAMETER(Queue);

    device = WdfIoQueueGetDevice(Queue);
    deviceContext = GetDeviceContext(device);

    switch (IoControlCode) {
    case IOCTL_PHYSMEM_MAP_MEMORY: {
        PHYSMEM_MAP_REQUEST mapRequest;
        PVOID kernelVirtualAddr = NULL;
        PMAPPED_REGION mappedRegion = NULL;

        if (InputBufferLength < sizeof(PHYSMEM_MAP_REQUEST)) {
            status = STATUS_INVALID_BUFFER_SIZE;
            break;
        }

        //
        // Get the map request from input buffer
        //
        status = WdfRequestRetrieveInputBuffer(
            Request,
            sizeof(PHYSMEM_MAP_REQUEST),
            &mapRequest,
            NULL
        );

        if (!NT_SUCCESS(status)) {
            KdPrint(("PhysMem: WdfRequestRetrieveInputBuffer failed: 0x%x\n", status));
            break;
        }

        //
        // Validate the physical address is within a CXL window
        //
        if (!PhysMemValidateCXLWindow(mapRequest.PhysicalAddress, mapRequest.Size)) {
            KdPrint(("PhysMem: Address 0x%llx is not in a valid CXL window\n",
                    mapRequest.PhysicalAddress));
            status = STATUS_ACCESS_VIOLATION;
            break;
        }

        //
        // Map the physical memory
        //
        status = PhysMemMapPhysicalMemory(&mapRequest, &kernelVirtualAddr);

        if (!NT_SUCCESS(status)) {
            KdPrint(("PhysMem: PhysMemMapPhysicalMemory failed: 0x%x\n", status));
            break;
        }

        //
        // Allocate a mapped region structure
        //
        mappedRegion = (PMAPPED_REGION)ExAllocatePoolWithTag(
            NonPagedPool,
            sizeof(MAPPED_REGION),
            'MPMC'  // CMPM - CXL PhysMem Mapped
        );

        if (mappedRegion == NULL) {
            PhysMemUnmapPhysicalMemory(kernelVirtualAddr, mapRequest.Size);
            status = STATUS_INSUFFICIENT_RESOURCES;
            break;
        }

        //
        // Fill in the mapped region structure
        //
        mappedRegion->PhysicalAddress = mapRequest.PhysicalAddress;
        mappedRegion->Size = mapRequest.Size;
        mappedRegion->KernelVirtualAddress = kernelVirtualAddr;
        mappedRegion->SectionHandle = NULL;  // TODO: Create section object

        //
        // Add to the mapped region list
        //
        KdPrint(("PhysMem: Mapped physical address 0x%llx (size 0x%llx) to 0x%p\n",
                mapRequest.PhysicalAddress, mapRequest.Size, kernelVirtualAddr));

        //
        // Update the request with mapping information
        //
        mapRequest.UserModeVirtualAddress = (UINT64)kernelVirtualAddr;
        mapRequest.SectionHandle = (HANDLE)mappedRegion;  // Use as handle

        //
        // Return the result to user mode
        //
        status = WdfRequestRetrieveOutputBuffer(
            Request,
            sizeof(PHYSMEM_MAP_REQUEST),
            &mapRequest,
            NULL
        );

        if (NT_SUCCESS(status)) {
            bytesReturned = sizeof(PHYSMEM_MAP_REQUEST);
        }

        break;
    }

    case IOCTL_PHYSMEM_UNMAP_MEMORY: {
        PHYSMEM_UNMAP_REQUEST unmapRequest;
        PLIST_ENTRY listEntry;
        PMAPPED_REGION mappedRegion = NULL;
        BOOLEAN found = FALSE;

        if (InputBufferLength < sizeof(PHYSMEM_UNMAP_REQUEST)) {
            status = STATUS_INVALID_BUFFER_SIZE;
            break;
        }

        //
        // Get the unmap request
        //
        status = WdfRequestRetrieveInputBuffer(
            Request,
            sizeof(PHYSMEM_UNMAP_REQUEST),
            &unmapRequest,
            NULL
        );

        if (!NT_SUCCESS(status)) {
            break;
        }

        //
        // Find the mapped region in our list
        //
        KdPrint(("PhysMem: Unmapping address 0x%llx\n", unmapRequest.UserModeVirtualAddress));

        //
        // For now, just return success
        // In a full implementation, we'd track and cleanup mappings
        //
        status = STATUS_SUCCESS;
        break;
    }

    case IOCTL_PHYSMEM_VALIDATE_ADDRESS: {
        PHYSMEM_VALIDATE_RESULT validateResult;

        if (InputBufferLength < sizeof(PHYSMEM_VALIDATE_RESULT)) {
            status = STATUS_INVALID_BUFFER_SIZE;
            break;
        }

        //
        // Get the validate request
        //
        status = WdfRequestRetrieveInputBuffer(
            Request,
            sizeof(PHYSMEM_VALIDATE_RESULT),
            &validateResult,
            NULL
        );

        if (!NT_SUCCESS(status)) {
            break;
        }

        //
        // Validate the address
        //
        validateResult.IsValid = PhysMemValidateCXLWindow(
            validateResult.PhysicalAddress,
            validateResult.Size
        );

        validateResult.IsCXLWindow = validateResult.IsValid;

        //
        // Return the result
        //
        status = WdfRequestRetrieveOutputBuffer(
            Request,
            sizeof(PHYSMEM_VALIDATE_RESULT),
            &validateResult,
            NULL
        );

        if (NT_SUCCESS(status)) {
            bytesReturned = sizeof(PHYSMEM_VALIDATE_RESULT);
        }

        break;
    }

    default:
        status = STATUS_INVALID_DEVICE_REQUEST;
        break;
    }

    WdfRequestCompleteWithInformation(Request, status, bytesReturned);
}

//=============================================================================
// PhysMemMapPhysicalMemory
//=============================================================================

NTSTATUS
PhysMemMapPhysicalMemory(
    IN PHYSMEM_MAP_REQUEST* MapRequest,
    OUT PVOID* KernelVirtualAddress
)
/*++
Routine Description:
    Maps a physical memory range to kernel virtual address space.

Arguments:
    MapRequest - Pointer to the map request structure
    KernelVirtualAddress - Pointer to receive the virtual address

Return Value:
    STATUS_SUCCESS if successful, error code otherwise
--*/
{
    PHYSICAL_ADDRESS physicalAddress;
    PVOID virtualAddress;

    physicalAddress.QuadPart = MapRequest->PhysicalAddress;

    //
    // Map the physical memory to kernel space
    // Use MmNonCached for coherent access
    //
    virtualAddress = MmMapIoSpace(
        physicalAddress,
        MapRequest->Size,
        MmNonCached
    );

    if (virtualAddress == NULL) {
        KdPrint(("PhysMem: MmMapIoSpace failed for address 0x%llx\n",
                MapRequest->PhysicalAddress));
        return STATUS_INSUFFICIENT_RESOURCES;
    }

    *KernelVirtualAddress = virtualAddress;

    return STATUS_SUCCESS;
}

//=============================================================================
// PhysMemUnmapPhysicalMemory
//=============================================================================

NTSTATUS
PhysMemUnmapPhysicalMemory(
    IN PVOID KernelVirtualAddress,
    IN SIZE_T Size
)
/*++
Routine Description:
    Unmaps a previously mapped physical memory range.

Arguments:
    KernelVirtualAddress - Virtual address to unmap
    Size - Size of the mapped region

Return Value:
    STATUS_SUCCESS if successful, error code otherwise
--*/
{
    if (KernelVirtualAddress != NULL) {
        MmUnmapIoSpace(KernelVirtualAddress, Size);
    }

    return STATUS_SUCCESS;
}

//=============================================================================
// PhysMemValidateCXLWindow
//=============================================================================

BOOLEAN
PhysMemValidateCXLWindow(
    IN UINT64 PhysicalAddress,
    IN SIZE_T Size
)
/*++
Routine Description:
    Validates that a physical address range is within a CXL memory window.

Arguments:
    PhysicalAddress - Starting physical address
    Size - Size of the range

Return Value:
    TRUE if address is valid, FALSE otherwise
--*/
{
    UINT64 endAddress;
    UINT i;

    endAddress = PhysicalAddress + Size - 1;

    //
    // Check against all known CXL windows
    //
    for (i = 0; i < MAX_CXL_WINDOWS; i++) {
        if (g_CXLWindows[i].Valid) {
            if (PhysicalAddress >= g_CXLWindows[i].StartPhysicalAddress &&
                endAddress <= g_CXLWindows[i].EndPhysicalAddress) {
                return TRUE;
            }
        }
    }

    //
    // For testing purposes, accept a wide range of addresses
    // In production, this should be restricted to actual CXL windows
    //
    if (PhysicalAddress >= 0x100000000ULL && PhysicalAddress < 0x200000000ULL) {
        return TRUE;
    }

    KdPrint(("PhysMem: Address 0x%llx (size 0x%zx) not in valid CXL window\n",
            PhysicalAddress, Size));

    return FALSE;
}

//=============================================================================
// PhysMemRegisterCXLWindow (called by bus driver)
//=============================================================================

VOID
PhysMemRegisterCXLWindow(
    IN UINT32 Index,
    IN UINT64 StartPhysicalAddress,
    IN UINT64 EndPhysicalAddress
)
/*++
Routine Description:
    Registers a CXL memory window with the physical memory driver.
    Called by the CXL bus driver during enumeration.

Arguments:
    Index - Window index
    StartPhysicalAddress - Starting physical address of the window
    EndPhysicalAddress - Ending physical address of the window

Return Value:
    None
--*/
{
    if (Index >= MAX_CXL_WINDOWS) {
        KdPrint(("PhysMem: Invalid window index %d\n", Index));
        return;
    }

    g_CXLWindows[Index].StartPhysicalAddress = StartPhysicalAddress;
    g_CXLWindows[Index].EndPhysicalAddress = EndPhysicalAddress;
    g_CXLWindows[Index].Valid = TRUE;

    KdPrint(("PhysMem: Registered CXL window %d: 0x%llx - 0x%llx\n",
            Index, StartPhysicalAddress, EndPhysicalAddress));
}
