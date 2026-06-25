/*++
Copyright (c) 2025 CXLAgent Project

Module Name:
    mem.c

Abstract:
    CXL Memory Driver for Windows.
    Handles memory window discovery, RAM/PMEM reporting,
    and NUMA node information for CXL memory devices.

Environment:
    Kernel mode

--*/

#include <ntddk.h>
#include <wdf.h>
#include <wdmsec.h>
#include "cxlioctl.h"

//=============================================================================
// Constants
//=============================================================================

#define MAX_MEMORY_WINDOWS  8

// Memory type flags
#define CXL_MEM_TYPE_RAM    0x01
#define CXL_MEM_TYPE_PMEM   0x02

//=============================================================================
// Function Prototypes
//=============================================================================

DRIVER_INITIALIZE DriverEntry;
EVT_WDF_DRIVER_DEVICE_ADD CXLMemoryEvtDriverDeviceAdd;
EVT_WDF_IO_QUEUE_IO_DEVICE_CONTROL CXLMemoryEvtIoDeviceControl;

NTSTATUS CXLMemoryGetMemoryInfo(
    IN WDFDEVICE Device,
    OUT PCXL_MEMORY_INFO MemoryInfo
);

NTSTATUS CXLMemoryGetMemoryWindows(
    IN WDFDEVICE Device,
    OUT PCXL_MEMORY_WINDOW MemoryWindows,
    IN OUT PULONG WindowCount
);

VOID CXLMemoryInitializeMemoryWindows(
    IN WDFDEVICE Device
);

//=============================================================================
// Device Context
//=============================================================================

typedef struct _CXL_MEMORY_WINDOW_ENTRY {
    UINT64 StartPhysicalAddress;
    UINT64 EndPhysicalAddress;
    UINT64 Size;
    BOOLEAN IsPersistent;
    BOOLEAN Valid;
} CXL_MEMORY_WINDOW_ENTRY, *PCXL_MEMORY_WINDOW_ENTRY;

typedef struct _CXL_MEMORY_CONTEXT {
    WDFDEVICE Device;
    CXL_MEMORY_WINDOW_ENTRY Windows[MAX_MEMORY_WINDOWS];
    CXL_MEMORY_INFO MemoryInfo;
    WDFSPINLOCK Lock;
} CXL_MEMORY_CONTEXT, *PCXL_MEMORY_CONTEXT;

WDF_DECLARE_CONTEXT_TYPE_WITH_NAME(CXL_MEMORY_CONTEXT, GetMemoryContext)

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
    DriverEntry initializes the CXL Memory driver.

Arguments:
    DriverObject - Pointer to the driver object
    RegistryPath - Pointer to the registry path for the driver

Return Value:
    STATUS_SUCCESS if successful, error code otherwise
--*/
{
    WDF_DRIVER_CONFIG config;
    NTSTATUS status;

    KdPrint(("CXLMemory: DriverEntry\n"));

    //
    // Initialize the driver configuration
    //
    WDF_DRIVER_CONFIG_INIT(&config, CXLMemoryEvtDriverDeviceAdd);

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
        KdPrint(("CXLMemory: WdfDriverCreate failed: 0x%x\n", status));
    }

    return status;
}

//=============================================================================
// EvtDriverDeviceAdd
//=============================================================================

NTSTATUS
CXLMemoryEvtDriverDeviceAdd(
    IN WDFDRIVER Driver,
    IN PWDFDEVICE_INIT DeviceInit
)
/*++
Routine Description:
    EvtDriverDeviceAdd is called when a device is added.

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
    PCXL_MEMORY_CONTEXT memoryContext;
    WDF_IO_QUEUE_CONFIG queueConfig;
    UNICODE_STRING deviceName;
    UNICODE_STRING symbolicLink;

    UNREFERENCED_PARAMETER(Driver);

    KdPrint(("CXLMemory: EvtDriverDeviceAdd\n"));

    //
    // Initialize device name and symbolic link
    //
    RtlInitUnicodeString(&deviceName, CXL_MEMORY_DEVICE_NAME);
    RtlInitUnicodeString(&symbolicLink, CXL_MEMORY_SYMBOLIC_LINK);

    //
    // Configure device attributes
    //
    WDF_OBJECT_ATTRIBUTES_INIT(&deviceAttributes);
    WDF_OBJECT_ATTRIBUTES_SET_CONTEXT_TYPE(&deviceAttributes, CXL_MEMORY_CONTEXT);
    deviceAttributes.SynchronizationScope = WdfSynchronizationScopeDevice;

    //
    // Create the device
    //
    status = WdfDeviceCreate(&DeviceInit, &deviceAttributes, &device);

    if (!NT_SUCCESS(status)) {
        KdPrint(("CXLMemory: WdfDeviceCreate failed: 0x%x\n", status));
        return status;
    }

    //
    // Initialize device context
    //
    memoryContext = GetMemoryContext(device);
    memoryContext->Device = device;

    WDF_OBJECT_ATTRIBUTES lockAttributes;
    WDF_OBJECT_ATTRIBUTES_INIT(&lockAttributes);
    WDF_OBJECT_ATTRIBUTES_SET_EXECUTION_LEVEL(&lockAttributes, WdfExecutionLevelPassive);
    status = WdfSpinLockCreate(&lockAttributes, &memoryContext->Lock);

    if (!NT_SUCCESS(status)) {
        KdPrint(("CXLMemory: WdfSpinLockCreate failed: 0x%x\n", status));
        return status;
    }

    //
    // Initialize memory windows
    //
    CXLMemoryInitializeMemoryWindows(device);

    //
    // Initialize memory info (simulated)
    //
    RtlZeroMemory(&memoryContext->MemoryInfo, sizeof(CXL_MEMORY_INFO));
    RtlStringCchCopyNW(
        memoryContext->MemoryInfo.Name,
        32,
        L"mem0",
        4
    );
    RtlStringCchCopyNW(
        memoryContext->MemoryInfo.PciBdf,
        16,
        L"0000:04:00.0",
        14
    );
    memoryContext->MemoryInfo.TotalSize = 16 * 1024 * 1024 * 1024ULL;  // 16GB
    memoryContext->MemoryInfo.RamSize = 8 * 1024 * 1024 * 1024ULL;     // 8GB
    memoryContext->MemoryInfo.PmemSize = 8 * 1024 * 1024 * 1024ULL;    // 8GB
    memoryContext->MemoryInfo.NumaNode = 1;
    RtlStringCchCopyNW(
        memoryContext->MemoryInfo.FirmwareVersion,
        32,
        L"1.0.0",
        5
    );

    //
    // Create symbolic link
    //
    status = WdfDeviceCreateSymbolicLink(device, &symbolicLink);

    if (!NT_SUCCESS(status)) {
        KdPrint(("CXLMemory: WdfDeviceCreateSymbolicLink failed: 0x%x\n", status));
        return status;
    }

    //
    // Configure default I/O queue
    //
    WDF_IO_QUEUE_CONFIG_INIT_DEFAULT_QUEUE(
        &queueConfig,
        WdfIoQueueDispatchParallel
    );

    queueConfig.EvtIoDeviceControl = CXLMemoryEvtIoDeviceControl;

    status = WdfIoQueueCreate(
        device,
        &queueConfig,
        WDF_NO_OBJECT_ATTRIBUTES,
        WDF_NO_HANDLE
    );

    if (!NT_SUCCESS(status)) {
        KdPrint(("CXLMemory: WdfIoQueueCreate failed: 0x%x\n", status));
        return status;
    }

    KdPrint(("CXLMemory: Device initialized successfully\n"));

    return STATUS_SUCCESS;
}

//=============================================================================
// CXLMemoryInitializeMemoryWindows
//=============================================================================

VOID
CXLMemoryInitializeMemoryWindows(
    IN WDFDEVICE Device
)
/*++
Routine Description:
    Initializes CXL memory windows.

    In a real implementation, this would scan the CXL memory
    controller registers to discover memory windows.

Arguments:
    Device - Handle to the device object

Return Value:
    None
--*/
{
    PCXL_MEMORY_CONTEXT memoryContext = GetMemoryContext(Device);
    UINT i;

    //
    // Initialize all windows as invalid
    //
    for (i = 0; i < MAX_MEMORY_WINDOWS; i++) {
        memoryContext->Windows[i].Valid = FALSE;
    }

    //
    // In a real implementation, we would:
    // 1. Map BAR registers
    // 2. Read memory window configuration registers
    // 3. Parse window descriptors
    //
    // For this implementation, we create simulated windows
    //

    // Window 0: RAM region (8GB)
    memoryContext->Windows[0].StartPhysicalAddress = 0x100000000ULL;     // 4GB
    memoryContext->Windows[0].EndPhysicalAddress = 0x300000000ULL;       // 12GB
    memoryContext->Windows[0].Size = 0x200000000ULL;                      // 8GB
    memoryContext->Windows[0].IsPersistent = FALSE;
    memoryContext->Windows[0].Valid = TRUE;

    // Window 1: Persistent Memory region (8GB)
    memoryContext->Windows[1].StartPhysicalAddress = 0x300000000ULL;      // 12GB
    memoryContext->Windows[1].EndPhysicalAddress = 0x500000000ULL;        // 20GB
    memoryContext->Windows[1].Size = 0x200000000ULL;                       // 8GB
    memoryContext->Windows[1].IsPersistent = TRUE;
    memoryContext->Windows[1].Valid = TRUE;

    KdPrint(("CXLMemory: Initialized 2 memory windows\n"));
}

//=============================================================================
// EvtIoDeviceControl
//=============================================================================

VOID
CXLMemoryEvtIoDeviceControl(
    IN WDFQUEUE Queue,
    IN WDFREQUEST Request,
    IN size_t OutputBufferLength,
    IN size_t InputBufferLength,
    IN ULONG IoControlCode
)
/*++
Routine Description:
    Handles IOCTL requests for the memory driver.

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
    PCXL_MEMORY_CONTEXT memoryContext;

    UNREFERENCED_PARAMETER(Queue);

    device = WdfIoQueueGetDevice(Queue);
    memoryContext = GetMemoryContext(device);

    switch (IoControlCode) {
    case IOCTL_CXL_GET_MEMORY_INFO: {
        PCXL_MEMORY_INFO memoryInfo;

        KdPrint(("CXLMemory: IOCTL_CXL_GET_MEMORY_INFO\n"));

        if (OutputBufferLength < sizeof(CXL_MEMORY_INFO)) {
            status = STATUS_BUFFER_TOO_SMALL;
            break;
        }

        status = WdfRequestRetrieveOutputBuffer(
            Request,
            sizeof(CXL_MEMORY_INFO),
            &memoryInfo,
            NULL
        );

        if (NT_SUCCESS(status)) {
            status = CXLMemoryGetMemoryInfo(device, memoryInfo);

            if (NT_SUCCESS(status)) {
                bytesReturned = sizeof(CXL_MEMORY_INFO);
            }
        }

        break;
    }

    case IOCTL_CXL_GET_MEMORY_WINDOWS: {
        PCXL_MEMORY_WINDOW memoryWindows;
        PULONG windowCount;
        ULONG requestedCount;
        ULONG actualCount;

        KdPrint(("CXLMemory: IOCTL_CXL_GET_MEMORY_WINDOWS\n"));

        // Get requested window count from input
        if (InputBufferLength < sizeof(ULONG)) {
            status = STATUS_INVALID_BUFFER_SIZE;
            break;
        }

        status = WdfRequestRetrieveInputBuffer(
            Request,
            sizeof(ULONG),
            &windowCount,
            NULL
        );

        if (!NT_SUCCESS(status)) {
            break;
        }

        requestedCount = *windowCount;

        // Count valid windows
        actualCount = 0;
        for (UINT i = 0; i < MAX_MEMORY_WINDOWS; i++) {
            if (memoryContext->Windows[i].Valid) {
                actualCount++;
            }
        }

        // Update output count
        *windowCount = actualCount;

        // Check if output buffer is large enough
        if (OutputBufferLength < (actualCount * sizeof(CXL_MEMORY_WINDOW))) {
            bytesReturned = sizeof(ULONG);
            status = STATUS_BUFFER_OVERFLOW;
            break;
        }

        status = WdfRequestRetrieveOutputBuffer(
            Request,
            actualCount * sizeof(CXL_MEMORY_WINDOW),
            &memoryWindows,
            NULL
        );

        if (NT_SUCCESS(status)) {
            status = CXLMemoryGetMemoryWindows(device, memoryWindows, &actualCount);

            if (NT_SUCCESS(status)) {
                bytesReturned = actualCount * sizeof(CXL_MEMORY_WINDOW);
            }
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
// CXLMemoryGetMemoryInfo
//=============================================================================

NTSTATUS
CXLMemoryGetMemoryInfo(
    IN WDFDEVICE Device,
    OUT PCXL_MEMORY_INFO MemoryInfo
)
/*++
Routine Description:
    Gets CXL memory device information.

Arguments:
    Device - Handle to the device object
    MemoryInfo - Pointer to receive memory info

Return Value:
    STATUS_SUCCESS
--*/
{
    PCXL_MEMORY_CONTEXT memoryContext = GetMemoryContext(Device);

    //
    // Copy memory info from device context
    //
    RtlCopyMemory(MemoryInfo, &memoryContext->MemoryInfo, sizeof(CXL_MEMORY_INFO));

    return STATUS_SUCCESS;
}

//=============================================================================
// CXLMemoryGetMemoryWindows
//=============================================================================

NTSTATUS
CXLMemoryGetMemoryWindows(
    IN WDFDEVICE Device,
    OUT PCXL_MEMORY_WINDOW MemoryWindows,
    IN OUT PULONG WindowCount
)
/*++
Routine Description:
    Gets CXL memory windows.

Arguments:
    Device - Handle to the device object
    MemoryWindows - Pointer to receive window array
    WindowCount - Input: max windows, Output: actual windows

Return Value:
    STATUS_SUCCESS
--*/
{
    PCXL_MEMORY_CONTEXT memoryContext = GetMemoryContext(Device);
    ULONG index = 0;

    //
    // Copy valid windows to output array
    //
    for (UINT i = 0; i < MAX_MEMORY_WINDOWS && index < *WindowCount; i++) {
        if (memoryContext->Windows[i].Valid) {
            MemoryWindows[index].Index = i;
            MemoryWindows[index].StartPhysicalAddress =
                memoryContext->Windows[i].StartPhysicalAddress;
            MemoryWindows[index].EndPhysicalAddress =
                memoryContext->Windows[i].EndPhysicalAddress;
            MemoryWindows[index].Size = memoryContext->Windows[i].Size;
            MemoryWindows[index].IsPersistent = memoryContext->Windows[i].IsPersistent;
            index++;
        }
    }

    *WindowCount = index;

    KdPrint(("CXLMemory: Returning %d memory windows\n", index));

    return STATUS_SUCCESS;
}
