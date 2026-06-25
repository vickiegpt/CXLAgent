/*++
Copyright (c) 2025 CXLAgent Project

Module Name:
    cache.c

Abstract:
    CXL Cache Driver for Windows.
    Handles cache control, WBINVD trigger, and BAR2 MMIO access
    for CXL cache devices.

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

#define CXL_CACHE_BAR2_INDEX    2    // BAR2 for cache controller registers
#define CXL_CACHE_BAR_SIZE      0x1000  // 4KB register space

// Cache controller register offsets (from BAR2 base)
#define CACHE_CTRL_REG          0x00   // Cache control register
#define CACHE_STATUS_REG        0x04   // Cache status register
#define CACHE_SIZE_REG          0x08   // Cache size register
#define CACHE_WBINVD_REG        0x0C   // WBINVD trigger register
#define CACHE_INVALID_REG       0x10   // Cache invalid register
#define CACHE_DISABLE_REG       0x14   // Cache disable register

// Cache control register bits
#define CACHE_CTRL_ENABLE       0x01   // Enable cache
#define CACHE_CTRL_FLUSH        0x02   // Flush cache
#define CACHE_CTRL_WBINVD       0x04   // Trigger WBINVD

// Cache status register bits
#define CACHE_STATUS_ENABLED    0x01   // Cache is enabled
#define CACHE_STATUS_DISABLED   0x02   // Cache is disabled
#define CACHE_STATUS_INVALID    0x04   // Cache is invalid

//=============================================================================
// Function Prototypes
//=============================================================================

DRIVER_INITIALIZE DriverEntry;
EVT_WDF_DRIVER_DEVICE_ADD CXLCacheEvtDriverDeviceAdd;
EVT_WDF_IO_QUEUE_IO_DEVICE_CONTROL CXLCacheEvtIoDeviceControl;
EVT_WDF_DEVICE_PREPARE_HARDWARE CXLCacheEvtDevicePrepareHardware;
EVT_WDF_DEVICE_RELEASE_HARDWARE CXLCacheEvtDeviceReleaseHardware;
EVT_WDF_DEVICE_D0_ENTRY CXLCacheEvtDeviceD0Entry;

NTSTATUS CXLCacheTriggerWbinvd(
    IN WDFDEVICE Device
);

NTSTATUS CXLCacheGetCacheState(
    IN WDFDEVICE Device,
    OUT PCXL_CACHE_STATE CacheState
);

NTSTATUS CXLCacheSetCacheDisable(
    IN WDFDEVICE Device,
    IN BOOLEAN Disable
);

NTSTATUS CXLCacheMapBar2(
    IN WDFDEVICE Device
);

VOID CXLCacheUnmapBar2(
    IN WDFDEVICE Device
);

//=============================================================================
// Device Context
//=============================================================================

typedef struct _CXL_CACHE_CONTEXT {
    WDFDEVICE Device;
    PVOID Bar2BaseAddress;      // Mapped BAR2 virtual address
    ULONG Bar2Length;           // BAR2 size
    PHYSICAL_ADDRESS Bar2PhysicalAddress;
    BOOLEAN CacheDisabled;
    BOOLEAN CacheInvalid;
    ULONG CacheSize;            // Cache size in bytes
    WDFSPINLOCK Lock;
} CXL_CACHE_CONTEXT, *PCXL_CACHE_CONTEXT;

WDF_DECLARE_CONTEXT_TYPE_WITH_NAME(CXL_CACHE_CONTEXT, GetCacheContext)

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
    DriverEntry initializes the CXL Cache driver.

Arguments:
    DriverObject - Pointer to the driver object
    RegistryPath - Pointer to the registry path for the driver

Return Value:
    STATUS_SUCCESS if successful, error code otherwise
--*/
{
    WDF_DRIVER_CONFIG config;
    NTSTATUS status;

    KdPrint(("CXLCache: DriverEntry\n"));

    //
    // Initialize the driver configuration
    //
    WDF_DRIVER_CONFIG_INIT(&config, CXLCacheEvtDriverDeviceAdd);

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
        KdPrint(("CXLCache: WdfDriverCreate failed: 0x%x\n", status));
    }

    return status;
}

//=============================================================================
// EvtDriverDeviceAdd
//=============================================================================

NTSTATUS
CXLCacheEvtDriverDeviceAdd(
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
    PCXL_CACHE_CONTEXT cacheContext;
    WDF_IO_QUEUE_CONFIG queueConfig;
    UNICODE_STRING deviceName;
    UNICODE_STRING symbolicLink;

    UNREFERENCED_PARAMETER(Driver);

    KdPrint(("CXLCache: EvtDriverDeviceAdd\n"));

    //
    // Set device flags for power management
    //
    WDF_PNPPOWER_EVENT_CALLBACKS pnpPowerCallbacks;
    WDF_PNPPOWER_EVENT_CALLBACKS_INIT(&pnpPowerCallbacks);
    pnpPowerCallbacks.EvtDevicePrepareHardware = CXLCacheEvtDevicePrepareHardware;
    pnpPowerCallbacks.EvtDeviceReleaseHardware = CXLCacheEvtDeviceReleaseHardware;
    pnpPowerCallbacks.EvtDeviceD0Entry = CXLCacheEvtDeviceD0Entry;
    WdfDeviceInitSetPnpPowerEventCallbacks(DeviceInit, &pnpPowerCallbacks);

    //
    // Initialize device name and symbolic link
    //
    RtlInitUnicodeString(&deviceName, CXLCACHE_DEVICE_NAME);
    RtlInitUnicodeString(&symbolicLink, CXLCACHE_SYMBOLIC_LINK);

    //
    // Configure device attributes
    //
    WDF_OBJECT_ATTRIBUTES_INIT(&deviceAttributes);
    WDF_OBJECT_ATTRIBUTES_SET_CONTEXT_TYPE(&deviceAttributes, CXL_CACHE_CONTEXT);
    deviceAttributes.SynchronizationScope = WdfSynchronizationScopeDevice;

    //
    // Create the device
    //
    status = WdfDeviceCreate(&DeviceInit, &deviceAttributes, &device);

    if (!NT_SUCCESS(status)) {
        KdPrint(("CXLCache: WdfDeviceCreate failed: 0x%x\n", status));
        return status;
    }

    //
    // Initialize device context
    //
    cacheContext = GetCacheContext(device);
    cacheContext->Device = device;
    cacheContext->Bar2BaseAddress = NULL;
    cacheContext->Bar2Length = 0;
    cacheContext->Bar2PhysicalAddress.QuadPart = 0;
    cacheContext->CacheDisabled = FALSE;
    cacheContext->CacheInvalid = FALSE;
    cacheContext->CacheSize = 0;

    WDF_OBJECT_ATTRIBUTES lockAttributes;
    WDF_OBJECT_ATTRIBUTES_INIT(&lockAttributes);
    WDF_OBJECT_ATTRIBUTES_SET_EXECUTION_LEVEL(&lockAttributes, WdfExecutionLevelPassive);
    status = WdfSpinLockCreate(&lockAttributes, &cacheContext->Lock);

    if (!NT_SUCCESS(status)) {
        KdPrint(("CXLCache: WdfSpinLockCreate failed: 0x%x\n", status));
        return status;
    }

    //
    // Create symbolic link
    //
    status = WdfDeviceCreateSymbolicLink(device, &symbolicLink);

    if (!NT_SUCCESS(status)) {
        KdPrint(("CXLCache: WdfDeviceCreateSymbolicLink failed: 0x%x\n", status));
        return status;
    }

    //
    // Configure default I/O queue
    //
    WDF_IO_QUEUE_CONFIG_INIT_DEFAULT_QUEUE(
        &queueConfig,
        WdfIoQueueDispatchParallel
    );

    queueConfig.EvtIoDeviceControl = CXLCacheEvtIoDeviceControl;

    status = WdfIoQueueCreate(
        device,
        &queueConfig,
        WDF_NO_OBJECT_ATTRIBUTES,
        WDF_NO_HANDLE
    );

    if (!NT_SUCCESS(status)) {
        KdPrint(("CXLCache: WdfIoQueueCreate failed: 0x%x\n", status));
        return status;
    }

    KdPrint(("CXLCache: Device initialized successfully\n"));

    return STATUS_SUCCESS;
}

//=============================================================================
// EvtDevicePrepareHardware
//=============================================================================

NTSTATUS
CXLCacheEvtDevicePrepareHardware(
    IN WDFDEVICE Device,
    IN WDFCMRESLIST ResourcesRaw,
    IN WDFCMRESLIST ResourcesTranslated
)
/*++
Routine Description:
    EvtDevicePrepareHardware is called when the device is starting.
    Maps BAR2 for cache controller register access.

Arguments:
    Device - Handle to the device object
    ResourcesRaw - Raw resource list
    ResourcesTranslated - Translated resource list

Return Value:
    STATUS_SUCCESS if successful, error code otherwise
--*/
{
    PCXL_CACHE_CONTEXT cacheContext = GetCacheContext(Device);
    ULONG resourceCount;
    PCM_PARTIAL_RESOURCE_DESCRIPTOR descriptor;
    NTSTATUS status;
    BOOLEAN foundBar = FALSE;

    UNREFERENCED_PARAMETER(ResourcesRaw);

    KdPrint(("CXLCache: EvtDevicePrepareHardware\n"));

    //
    // Scan resources for BAR2
    //
    resourceCount = WdfCmResourceListGetCount(ResourcesTranslated);

    for (ULONG i = 0; i < resourceCount; i++) {
        descriptor = WdfCmResourceListGetDescriptor(ResourcesTranslated, i);

        if (descriptor->Type == CmResourceTypeMemory) {
            //
            // Check if this is BAR2 (cache controller registers)
            //
            // In a real implementation, we'd check resource descriptors
            // to identify BAR2 specifically. For now, use the first memory resource.
            //
            if (!foundBar) {
                cacheContext->Bar2PhysicalAddress = descriptor->u.Memory.Start;
                cacheContext->Bar2Length = descriptor->u.Memory.Length;

                KdPrint(("CXLCache: Found BAR at 0x%llx, size 0x%x\n",
                        cacheContext->Bar2PhysicalAddress.QuadPart,
                        cacheContext->Bar2Length));

                foundBar = TRUE;
            }
        }
    }

    if (!foundBar) {
        KdPrint(("CXLCache: No memory resources found\n"));
        return STATUS_DEVICE_CONFIGURATION_ERROR;
    }

    //
    // Map BAR2
    //
    status = CXLCacheMapBar2(Device);

    if (!NT_SUCCESS(status)) {
        KdPrint(("CXLCache: CXLCacheMapBar2 failed: 0x%x\n", status));
        return status;
    }

    //
    // Read cache size from register
    //
    if (cacheContext->Bar2BaseAddress != NULL) {
        PUCHAR registerBase = (PUCHAR)cacheContext->Bar2BaseAddress;

        //
        // Simulated: read from CACHE_SIZE_REG
        // In a real implementation, this would read the actual hardware register
        //
        cacheContext->CacheSize = 128 * 1024 * 1024;  // 128MB default

        KdPrint(("CXLCache: Cache size: 0x%x bytes\n", cacheContext->CacheSize));
    }

    return STATUS_SUCCESS;
}

//=============================================================================
// EvtDeviceReleaseHardware
//=============================================================================

NTSTATUS
CXLCacheEvtDeviceReleaseHardware(
    IN WDFDEVICE Device,
    IN WDFCMRESLIST ResourcesTranslated
)
/*++
Routine Description:
    EvtDeviceReleaseHardware is called when the device is stopping.
    Unmaps BAR2.

Arguments:
    Device - Handle to the device object
    ResourcesTranslated - Translated resource list

Return Value:
    STATUS_SUCCESS
--*/
{
    UNREFERENCED_PARAMETER(ResourcesTranslated);

    KdPrint(("CXLCache: EvtDeviceReleaseHardware\n"));

    CXLCacheUnmapBar2(Device);

    return STATUS_SUCCESS;
}

//=============================================================================
// EvtDeviceD0Entry
//=============================================================================

NTSTATUS
CXLCacheEvtDeviceD0Entry(
    IN WDFDEVICE Device,
    IN WDF_POWER_DEVICE_STATE PreviousState
)
/*++
Routine Description:
    EvtDeviceD0Entry is called when the device enters D0 (working) state.

Arguments:
    Device - Handle to the device object
    PreviousState - Previous power state

Return Value:
    STATUS_SUCCESS
--*/
{
    UNREFERENCED_PARAMETER(Device);
    UNREFERENCED_PARAMETER(PreviousState);

    KdPrint(("CXLCache: Entering D0 state\n"));

    return STATUS_SUCCESS;
}

//=============================================================================
// EvtIoDeviceControl
//=============================================================================

VOID
CXLCacheEvtIoDeviceControl(
    IN WDFQUEUE Queue,
    IN WDFREQUEST Request,
    IN size_t OutputBufferLength,
    IN size_t InputBufferLength,
    IN ULONG IoControlCode
)
/*++
Routine Description:
    Handles IOCTL requests for the cache driver.

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
    PCXL_CACHE_CONTEXT cacheContext;

    UNREFERENCED_PARAMETER(Queue);

    device = WdfIoQueueGetDevice(Queue);
    cacheContext = GetCacheContext(device);

    switch (IoControlCode) {
    case IOCTL_CXL_TRIGGER_WBINVD: {
        KdPrint(("CXLCache: IOCTL_CXL_TRIGGER_WBINVD\n"));

        status = CXLCacheTriggerWbinvd(device);

        if (NT_SUCCESS(status)) {
            KdPrint(("CXLCache: WBINVD triggered successfully\n"));
        }

        break;
    }

    case IOCTL_CXL_GET_CACHE_STATE: {
        PCXL_CACHE_STATE cacheState;

        KdPrint(("CXLCache: IOCTL_CXL_GET_CACHE_STATE\n"));

        if (OutputBufferLength < sizeof(CXL_CACHE_STATE)) {
            status = STATUS_BUFFER_TOO_SMALL;
            break;
        }

        status = WdfRequestRetrieveOutputBuffer(
            Request,
            sizeof(CXL_CACHE_STATE),
            &cacheState,
            NULL
        );

        if (NT_SUCCESS(status)) {
            status = CXLCacheGetCacheState(device, cacheState);

            if (NT_SUCCESS(status)) {
                bytesReturned = sizeof(CXL_CACHE_STATE);
            }
        }

        break;
    }

    case IOCTL_CXL_SET_CACHE_DISABLE: {
        BOOLEAN disable;

        KdPrint(("CXLCache: IOCTL_CXL_SET_CACHE_DISABLE\n"));

        if (InputBufferLength < sizeof(BOOLEAN)) {
            status = STATUS_INVALID_BUFFER_SIZE;
            break;
        }

        status = WdfRequestRetrieveInputBuffer(
            Request,
            sizeof(BOOLEAN),
            &disable,
            NULL
        );

        if (NT_SUCCESS(status)) {
            status = CXLCacheSetCacheDisable(device, disable);
        }

        break;
    }

    case IOCTL_CXL_GET_CACHE_SIZE: {
        PULONG cacheSize;

        KdPrint(("CXLCache: IOCTL_CXL_GET_CACHE_SIZE\n"));

        if (OutputBufferLength < sizeof(ULONG)) {
            status = STATUS_BUFFER_TOO_SMALL;
            break;
        }

        status = WdfRequestRetrieveOutputBuffer(
            Request,
            sizeof(ULONG),
            &cacheSize,
            NULL
        );

        if (NT_SUCCESS(status)) {
            *cacheSize = cacheContext->CacheSize;
            bytesReturned = sizeof(ULONG);
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
// CXLCacheMapBar2
//=============================================================================

NTSTATUS
CXLCacheMapBar2(
    IN WDFDEVICE Device
)
/*++
Routine Description:
    Maps BAR2 for cache controller register access.

Arguments:
    Device - Handle to the device object

Return Value:
    STATUS_SUCCESS if successful, error code otherwise
--*/
{
    PCXL_CACHE_CONTEXT cacheContext = GetCacheContext(Device);
    PHYSICAL_ADDRESS physicalAddress;

    KdPrint(("CXLCache: Mapping BAR2 at 0x%llx\n",
            cacheContext->Bar2PhysicalAddress.QuadPart));

    //
    // Map the physical memory to kernel space
    //
    physicalAddress.QuadPart = cacheContext->Bar2PhysicalAddress.QuadPart;

    cacheContext->Bar2BaseAddress = MmMapIoSpace(
        physicalAddress,
        cacheContext->Bar2Length,
        MmNonCached
    );

    if (cacheContext->Bar2BaseAddress == NULL) {
        KdPrint(("CXLCache: MmMapIoSpace failed\n"));
        return STATUS_INSUFFICIENT_RESOURCES;
    }

    KdPrint(("CXLCache: BAR2 mapped to 0x%p\n", cacheContext->Bar2BaseAddress));

    return STATUS_SUCCESS;
}

//=============================================================================
// CXLCacheUnmapBar2
//=============================================================================

VOID
CXLCacheUnmapBar2(
    IN WDFDEVICE Device
)
/*++
Routine Description:
    Unmaps BAR2.

Arguments:
    Device - Handle to the device object

Return Value:
    None
--*/
{
    PCXL_CACHE_CONTEXT cacheContext = GetCacheContext(Device);

    if (cacheContext->Bar2BaseAddress != NULL) {
        KdPrint(("CXLCache: Unmapping BAR2\n"));

        MmUnmapIoSpace(
            cacheContext->Bar2BaseAddress,
            cacheContext->Bar2Length
        );

        cacheContext->Bar2BaseAddress = NULL;
    }
}

//=============================================================================
// CXLCacheTriggerWbinvd
//=============================================================================

NTSTATUS
CXLCacheTriggerWbinvd(
    IN WDFDEVICE Device
)
/*++
Routine Description:
    Triggers WBINVD (Write Back + Invalidate) to flush cache to CXL memory.

Arguments:
    Device - Handle to the device object

Return Value:
    STATUS_SUCCESS if successful, error code otherwise
--*/
{
    PCXL_CACHE_CONTEXT cacheContext = GetCacheContext(Device);
    PUCHAR registerBase;
    ULONG registerValue;

    KdPrint(("CXLCache: Triggering WBINVD\n"));

    if (cacheContext->Bar2BaseAddress == NULL) {
        KdPrint(("CXLCache: BAR2 not mapped\n"));
        return STATUS_INVALID_DEVICE_STATE;
    }

    //
    // Write to WBINVD trigger register
    //
    registerBase = (PUCHAR)cacheContext->Bar2BaseAddress;

    //
    // In a real implementation, this would:
    // 1. Write to the WBINVD trigger register at offset CACHE_WBINVD_REG
    // 2. Wait for completion status
    // 3. Check for errors
    //
    // For now, we simulate the operation
    //

    // Simulated: Write 1 to trigger WBINVD
    registerValue = 1;
    // WRITE_REGISTER_ULONG((PULONG)(registerBase + CACHE_WBINVD_REG), registerValue);

    //
    // Simulated: Wait for operation to complete
    //
    LARGE_INTEGER delay;
    delay.QuadPart = -10000;  // 1 millisecond
    KeDelayExecutionThread(KernelMode, FALSE, &delay);

    KdPrint(("CXLCache: WBINVD triggered\n"));

    return STATUS_SUCCESS;
}

//=============================================================================
// CXLCacheGetCacheState
//=============================================================================

NTSTATUS
CXLCacheGetCacheState(
    IN WDFDEVICE Device,
    OUT PCXL_CACHE_STATE CacheState
)
/*++
Routine Description:
    Gets the current cache state.

Arguments:
    Device - Handle to the device object
    CacheState - Pointer to receive cache state

Return Value:
    STATUS_SUCCESS if successful, error code otherwise
--*/
{
    PCXL_CACHE_CONTEXT cacheContext = GetCacheContext(Device);
    PUCHAR registerBase;
    ULONG statusReg;

    if (cacheContext->Bar2BaseAddress == NULL) {
        return STATUS_INVALID_DEVICE_STATE;
    }

    //
    // Read cache status register
    //
    registerBase = (PUCHAR)cacheContext->Bar2BaseAddress;

    //
    // In a real implementation, this would read CACHE_STATUS_REG
    //
    // statusReg = READ_REGISTER_ULONG((PULONG)(registerBase + CACHE_STATUS_REG));
    //

    //
    // Fill in cache state structure
    //
    CacheState->Disabled = cacheContext->CacheDisabled;
    CacheState->Invalid = cacheContext->CacheInvalid;
    CacheState->Size = cacheContext->CacheSize;
    CacheState->Used = cacheContext->CacheSize / 2;  // Simulated: 50% used

    return STATUS_SUCCESS;
}

//=============================================================================
// CXLCacheSetCacheDisable
//=============================================================================

NTSTATUS
CXLCacheSetCacheDisable(
    IN WDFDEVICE Device,
    IN BOOLEAN Disable
)
/*++
Routine Description:
    Enables or disables the cache.

Arguments:
    Device - Handle to the device object
    Disable - TRUE to disable cache, FALSE to enable

Return Value:
    STATUS_SUCCESS if successful, error code otherwise
--*/
{
    PCXL_CACHE_CONTEXT cacheContext = GetCacheContext(Device);
    PUCHAR registerBase;
    ULONG ctrlValue;

    if (cacheContext->Bar2BaseAddress == NULL) {
        return STATUS_INVALID_DEVICE_STATE;
    }

    KdPrint(("CXLCache: %s cache\n", Disable ? "Disabling" : "Enabling"));

    registerBase = (PUCHAR)cacheContext->Bar2BaseAddress;

    //
    // Write to cache control register
    //
    ctrlValue = Disable ? 0 : CACHE_CTRL_ENABLE;

    //
    // In a real implementation:
    // WRITE_REGISTER_ULONG((PULONG)(registerBase + CACHE_CTRL_REG), ctrlValue);
    //

    cacheContext->CacheDisabled = Disable;

    return STATUS_SUCCESS;
}
