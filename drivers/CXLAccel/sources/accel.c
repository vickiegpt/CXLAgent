/*++
Copyright (c) 2025 CXLAgent Project

Module Name:
    accel.c

Abstract:
    CXL Accelerator Driver for Windows.
    Handles FPGA configuration, DMA engine control, and
    interrupt handling for CXL Type 2 accelerators.

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

#define CXL_ACCEL_BAR0_INDEX    0    // BAR0 for FPGA management
#define CXL_ACCEL_BAR2_INDEX    2    // BAR2 for DMA engine

#define CXL_ACCEL_BAR0_SIZE    0x1000  // 4KB management registers
#define CXL_ACCEL_BAR2_SIZE    0x10000 // 64KB DMA registers

// FPGA management register offsets (BAR0)
#define FPGA_MGMT_STATUS       0x00   // FPGA status register
#define FPGA_MGMT_CTRL         0x04   // FPGA control register
#define FPGA_MGMT_RECONFIG     0x08   // Reconfiguration trigger
#define FPGA_MGMT_VERSION      0x0C   // Bitstream version
#define FPGA_MGMT_ID           0x10   // FPGA ID register

// DMA engine register offsets (BAR2)
#define DMA_CTRL_REG           0x00   // DMA control
#define DMA_SRC_ADDR_REG       0x04   // Source address
#define DMA_DST_ADDR_REG       0x0C   // Destination address
#define DMA_SIZE_REG           0x14   // Transfer size
#define DMA_STATUS_REG         0x1C   // DMA status

// Status bits
#define FPGA_STATUS_CONFIGURED  0x01  // FPGA is configured
#define FPGA_STATUS_RECONFIGING 0x02  // Reconfiguration in progress
#define FPGA_STATUS_ERROR       0x04  // Error detected

// Control bits
#define FPGA_CTRL_RESET        0x01   // Reset FPGA
#define FPGA_CTRL_ENABLE       0x02   // Enable FPGA

//=============================================================================
// Function Prototypes
//=============================================================================

DRIVER_INITIALIZE DriverEntry;
EVT_WDF_DRIVER_DEVICE_ADD CXLAccelEvtDriverDeviceAdd;
EVT_WDF_IO_QUEUE_IO_DEVICE_CONTROL CXLAccelEvtIoDeviceControl;
EVT_WDF_DEVICE_PREPARE_HARDWARE CXLAccelEvtDevicePrepareHardware;
EVT_WDF_DEVICE_RELEASE_HARDWARE CXLAccelEvtDeviceReleaseHardware;
EVT_WDF_INTERRUPT_ISR CXLAccelEvtInterruptIsr;
EVT_WDF_INTERRUPT_DPC_ENTRY CXLAccelEvtInterruptDpc;
EVT_WDF_INTERRUPT_ENABLE CXLAccelEvtInterruptEnable;

NTSTATUS CXLAccelGetAccelInfo(
    IN WDFDEVICE Device,
    OUT PCXL_ACCEL_INFO AccelInfo
);

NTSTATUS CXLAccelSubmitWork(
    IN WDFDEVICE Device,
    IN PCXL_WORK_SUBMISSION WorkSubmission
);

NTSTATUS CXLAccelGetWorkStatus(
    IN WDFDEVICE Device,
    IN UINT64 WorkId,
    OUT PCXL_WORK_STATUS WorkStatus
);

NTSTATUS CXLAccelReconfigureFpga(
    IN WDFDEVICE Device,
    IN PCWSTR BitstreamPath
);

NTSTATUS CXLAccelMapBar0(
    IN WDFDEVICE Device
);

NTSTATUS CXLAccelMapBar2(
    IN WDFDEVICE Device
);

VOID CXLAccelUnmapBars(
    IN WDFDEVICE Device
);

//=============================================================================
// Device Context
//=============================================================================

typedef struct _CXL_WORK_ENTRY {
    LIST_ENTRY ListEntry;
    UINT64 WorkId;
    CXL_WORK_SUBMISSION Submission;
    BOOLEAN Completed;
    NTSTATUS Status;
    UINT64 BytesProcessed;
    WDFREQUEST CompletionRequest;  // Optional: request to complete on done
} CXL_WORK_ENTRY, *PCXL_WORK_ENTRY;

typedef struct _CXL_ACCEL_CONTEXT {
    WDFDEVICE Device;
    PVOID Bar0BaseAddress;         // Mapped BAR0 virtual address
    PVOID Bar2BaseAddress;         // Mapped BAR2 virtual address
    ULONG Bar0Length;
    ULONG Bar2Length;
    PHYSICAL_ADDRESS Bar0PhysicalAddress;
    PHYSICAL_ADDRESS Bar2PhysicalAddress;

    // FPGA state
    BOOLEAN FpgaConfigured;
    WCHAR BitstreamVersion[32];

    // Work queue
    LIST_ENTRY WorkQueue;
    WDFSPINLOCK WorkQueueLock;
    UINT64 NextWorkId;

    // Interrupt
    WDFINTERRUPT Interrupt;

    WDFSPINLOCK Lock;
} CXL_ACCEL_CONTEXT, *PCXL_ACCEL_CONTEXT;

WDF_DECLARE_CONTEXT_TYPE_WITH_NAME(CXL_ACCEL_CONTEXT, GetAccelContext)

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
    DriverEntry initializes the CXL Accelerator driver.

Arguments:
    DriverObject - Pointer to the driver object
    RegistryPath - Pointer to the registry path for the driver

Return Value:
    STATUS_SUCCESS if successful, error code otherwise
--*/
{
    WDF_DRIVER_CONFIG config;
    NTSTATUS status;

    KdPrint(("CXLAccel: DriverEntry\n"));

    //
    // Initialize the driver configuration
    //
    WDF_DRIVER_CONFIG_INIT(&config, CXLAccelEvtDriverDeviceAdd);

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
        KdPrint(("CXLAccel: WdfDriverCreate failed: 0x%x\n", status));
    }

    return status;
}

//=============================================================================
// EvtDriverDeviceAdd
//=============================================================================

NTSTATUS
CXLAccelEvtDriverDeviceAdd(
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
    PCXL_ACCEL_CONTEXT accelContext;
    WDF_IO_QUEUE_CONFIG queueConfig;
    WDF_INTERRUPT_CONFIG interruptConfig;
    UNICODE_STRING deviceName;
    UNICODE_STRING symbolicLink;

    UNREFERENCED_PARAMETER(Driver);

    KdPrint(("CXLAccel: EvtDriverDeviceAdd\n"));

    //
    // Set device flags for power management and interrupts
    //
    WDF_PNPPOWER_EVENT_CALLBACKS pnpPowerCallbacks;
    WDF_PNPPOWER_EVENT_CALLBACKS_INIT(&pnpPowerCallbacks);
    pnpPowerCallbacks.EvtDevicePrepareHardware = CXLAccelEvtDevicePrepareHardware;
    pnpPowerCallbacks.EvtDeviceReleaseHardware = CXLAccelEvtDeviceReleaseHardware;
    WdfDeviceInitSetPnpPowerEventCallbacks(DeviceInit, &pnpPowerCallbacks);

    //
    // Initialize device name and symbolic link
    //
    RtlInitUnicodeString(&deviceName, CXL_ACCEL_DEVICE_NAME);
    RtlInitUnicodeString(&symbolicLink, CXL_ACCEL_SYMBOLIC_LINK);

    //
    // Configure device attributes
    //
    WDF_OBJECT_ATTRIBUTES_INIT(&deviceAttributes);
    WDF_OBJECT_ATTRIBUTES_SET_CONTEXT_TYPE(&deviceAttributes, CXL_ACCEL_CONTEXT);
    deviceAttributes.SynchronizationScope = WdfSynchronizationScopeDevice;

    //
    // Create the device
    //
    status = WdfDeviceCreate(&DeviceInit, &deviceAttributes, &device);

    if (!NT_SUCCESS(status)) {
        KdPrint(("CXLAccel: WdfDeviceCreate failed: 0x%x\n", status));
        return status;
    }

    //
    // Initialize device context
    //
    accelContext = GetAccelContext(device);
    accelContext->Device = device;
    accelContext->Bar0BaseAddress = NULL;
    accelContext->Bar2BaseAddress = NULL;
    accelContext->Bar0Length = 0;
    accelContext->Bar2Length = 0;
    accelContext->FpgaConfigured = FALSE;
    accelContext->NextWorkId = 1;
    InitializeListHead(&accelContext->WorkQueue);

    WDF_OBJECT_ATTRIBUTES lockAttributes;
    WDF_OBJECT_ATTRIBUTES_INIT(&lockAttributes);
    WDF_OBJECT_ATTRIBUTES_SET_EXECUTION_LEVEL(&lockAttributes, WdfExecutionLevelPassive);
    status = WdfSpinLockCreate(&lockAttributes, &accelContext->Lock);

    if (!NT_SUCCESS(status)) {
        KdPrint(("CXLAccel: WdfSpinLockCreate failed for lock: 0x%x\n", status));
        return status;
    }

    status = WdfSpinLockCreate(&lockAttributes, &accelContext->WorkQueueLock);

    if (!NT_SUCCESS(status)) {
        KdPrint(("CXLAccel: WdfSpinLockCreate failed for work queue: 0x%x\n", status));
        return status;
    }

    //
    // Create interrupt object
    //
    WDF_INTERRUPT_CONFIG_INIT(&interruptConfig, CXLAccelEvtInterruptIsr, CXLAccelEvtInterruptDpc);
    interruptConfig.EvtInterruptEnable = CXLAccelEvtInterruptEnable;

    status = WdfInterruptCreate(
        device,
        &interruptConfig,
        WDF_NO_OBJECT_ATTRIBUTES,
        &accelContext->Interrupt
    );

    if (!NT_SUCCESS(status)) {
        KdPrint(("CXLAccel: WdfInterruptCreate failed: 0x%x\n", status));
        // Continue without interrupt support (polled mode)
    }

    //
    // Create symbolic link
    //
    status = WdfDeviceCreateSymbolicLink(device, &symbolicLink);

    if (!NT_SUCCESS(status)) {
        KdPrint(("CXLAccel: WdfDeviceCreateSymbolicLink failed: 0x%x\n", status));
        return status;
    }

    //
    // Configure default I/O queue
    //
    WDF_IO_QUEUE_CONFIG_INIT_DEFAULT_QUEUE(
        &queueConfig,
        WdfIoQueueDispatchParallel
    );

    queueConfig.EvtIoDeviceControl = CXLAccelEvtIoDeviceControl;

    status = WdfIoQueueCreate(
        device,
        &queueConfig,
        WDF_NO_OBJECT_ATTRIBUTES,
        WDF_NO_HANDLE
    );

    if (!NT_SUCCESS(status)) {
        KdPrint(("CXLAccel: WdfIoQueueCreate failed: 0x%x\n", status));
        return status;
    }

    KdPrint(("CXLAccel: Device initialized successfully\n"));

    return STATUS_SUCCESS;
}

//=============================================================================
// EvtDevicePrepareHardware
//=============================================================================

NTSTATUS
CXLAccelEvtDevicePrepareHardware(
    IN WDFDEVICE Device,
    IN WDFCMRESLIST ResourcesRaw,
    IN WDFCMRESLIST ResourcesTranslated
)
/*++
Routine Description:
    EvtDevicePrepareHardware is called when the device is starting.
    Maps BAR0 and BAR2.

Arguments:
    Device - Handle to the device object
    ResourcesRaw - Raw resource list
    ResourcesTranslated - Translated resource list

Return Value:
    STATUS_SUCCESS if successful, error code otherwise
--*/
{
    PCXL_ACCEL_CONTEXT accelContext = GetAccelContext(Device);
    ULONG resourceCount;
    PCM_PARTIAL_RESOURCE_DESCRIPTOR descriptor;
    NTSTATUS status;
    BOOLEAN foundBar0 = FALSE;
    BOOLEAN foundBar2 = FALSE;

    UNREFERENCED_PARAMETER(ResourcesRaw);

    KdPrint(("CXLAccel: EvtDevicePrepareHardware\n"));

    //
    // Scan resources for BAR0 and BAR2
    //
    resourceCount = WdfCmResourceListGetCount(ResourcesTranslated);

    for (ULONG i = 0; i < resourceCount; i++) {
        descriptor = WdfCmResourceListGetDescriptor(ResourcesTranslated, i);

        if (descriptor->Type == CmResourceTypeMemory) {
            //
            // First memory resource is BAR0 (FPGA management)
            //
            if (!foundBar0) {
                accelContext->Bar0PhysicalAddress = descriptor->u.Memory.Start;
                accelContext->Bar0Length = descriptor->u.Memory.Length;
                foundBar0 = TRUE;

                KdPrint(("CXLAccel: Found BAR0 at 0x%llx, size 0x%x\n",
                        accelContext->Bar0PhysicalAddress.QuadPart,
                        accelContext->Bar0Length));
            }
            //
            // Second memory resource is BAR2 (DMA engine)
            //
            else if (!foundBar2) {
                accelContext->Bar2PhysicalAddress = descriptor->u.Memory.Start;
                accelContext->Bar2Length = descriptor->u.Memory.Length;
                foundBar2 = TRUE;

                KdPrint(("CXLAccel: Found BAR2 at 0x%llx, size 0x%x\n",
                        accelContext->Bar2PhysicalAddress.QuadPart,
                        accelContext->Bar2Length));
            }
        }

        //
        // Check for interrupt resource
        //
        if (descriptor->Type == CmResourceTypeInterrupt) {
            KdPrint(("CXLAccel: Found interrupt resource, vector %d, level %d\n",
                    descriptor->u.Interrupt.Vector,
                    descriptor->u.Interrupt.Level));
        }
    }

    if (!foundBar0) {
        KdPrint(("CXLAccel: BAR0 not found\n"));
        return STATUS_DEVICE_CONFIGURATION_ERROR;
    }

    //
    // Map BAR0
    //
    status = CXLAccelMapBar0(Device);

    if (!NT_SUCCESS(status)) {
        KdPrint(("CXLAccel: CXLAccelMapBar0 failed: 0x%x\n", status));
        return status;
    }

    //
    // Map BAR2 if found
    //
    if (foundBar2) {
        status = CXLAccelMapBar2(Device);

        if (!NT_SUCCESS(status)) {
            KdPrint(("CXLAccel: CXLAccelMapBar2 failed: 0x%x\n", status));
            CXLAccelUnmapBars(Device);
            return status;
        }
    }

    //
    // Read FPGA status to check if configured
    //
    if (accelContext->Bar0BaseAddress != NULL) {
        PUCHAR registerBase = (PUCHAR)accelContext->Bar0BaseAddress;

        //
        // In a real implementation, read FPGA_MGMT_STATUS register
        //
        // ULONG statusReg = READ_REGISTER_ULONG((PULONG)(registerBase + FPGA_MGMT_STATUS));
        //

        //
        // Simulated: Assume FPGA is configured
        //
        accelContext->FpgaConfigured = TRUE;
        RtlStringCchCopyNW(accelContext->BitstreamVersion, 32, L"1.0.0", 5);

        KdPrint(("CXLAccel: FPGA configured, version %ws\n", accelContext->BitstreamVersion));
    }

    return STATUS_SUCCESS;
}

//=============================================================================
// EvtDeviceReleaseHardware
//=============================================================================

NTSTATUS
CXLAccelEvtDeviceReleaseHardware(
    IN WDFDEVICE Device,
    IN WDFCMRESLIST ResourcesTranslated
)
/*++
Routine Description:
    EvtDeviceReleaseHardware is called when the device is stopping.
    Unmaps BAR0 and BAR2.

Arguments:
    Device - Handle to the device object
    ResourcesTranslated - Translated resource list

Return Value:
    STATUS_SUCCESS
--*/
{
    UNREFERENCED_PARAMETER(ResourcesTranslated);

    KdPrint(("CXLAccel: EvtDeviceReleaseHardware\n"));

    CXLAccelUnmapBars(Device);

    return STATUS_SUCCESS;
}

//=============================================================================
// EvtIoDeviceControl
//=============================================================================

VOID
CXLAccelEvtIoDeviceControl(
    IN WDFQUEUE Queue,
    IN WDFREQUEST Request,
    IN size_t OutputBufferLength,
    IN size_t InputBufferLength,
    IN ULONG IoControlCode
)
/*++
Routine Description:
    Handles IOCTL requests for the accelerator driver.

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
    PCXL_ACCEL_CONTEXT accelContext;

    UNREFERENCED_PARAMETER(Queue);

    device = WdfIoQueueGetDevice(Queue);
    accelContext = GetAccelContext(device);

    switch (IoControlCode) {
    case IOCTL_CXL_GET_ACCEL_INFO: {
        PCXL_ACCEL_INFO accelInfo;

        KdPrint(("CXLAccel: IOCTL_CXL_GET_ACCEL_INFO\n"));

        if (OutputBufferLength < sizeof(CXL_ACCEL_INFO)) {
            status = STATUS_BUFFER_TOO_SMALL;
            break;
        }

        status = WdfRequestRetrieveOutputBuffer(
            Request,
            sizeof(CXL_ACCEL_INFO),
            &accelInfo,
            NULL
        );

        if (NT_SUCCESS(status)) {
            status = CXLAccelGetAccelInfo(device, accelInfo);

            if (NT_SUCCESS(status)) {
                bytesReturned = sizeof(CXL_ACCEL_INFO);
            }
        }

        break;
    }

    case IOCTL_CXL_SUBMIT_WORK: {
        PCXL_WORK_SUBMISSION workSubmission;

        KdPrint(("CXLAccel: IOCTL_CXL_SUBMIT_WORK\n"));

        if (InputBufferLength < sizeof(CXL_WORK_SUBMISSION)) {
            status = STATUS_INVALID_BUFFER_SIZE;
            break;
        }

        status = WdfRequestRetrieveInputBuffer(
            Request,
            sizeof(CXL_WORK_SUBMISSION),
            &workSubmission,
            NULL
        );

        if (NT_SUCCESS(status)) {
            status = CXLAccelSubmitWork(device, workSubmission);
        }

        break;
    }

    case IOCTL_CXL_GET_WORK_STATUS: {
        PCXL_WORK_STATUS workStatus;
        PUINT64 workIdPtr;

        KdPrint(("CXLAccel: IOCTL_CXL_GET_WORK_STATUS\n"));

        if (InputBufferLength < sizeof(UINT64)) {
            status = STATUS_INVALID_BUFFER_SIZE;
            break;
        }

        status = WdfRequestRetrieveInputBuffer(
            Request,
            sizeof(UINT64),
            &workIdPtr,
            NULL
        );

        if (!NT_SUCCESS(status)) {
            break;
        }

        if (OutputBufferLength < sizeof(CXL_WORK_STATUS)) {
            status = STATUS_BUFFER_TOO_SMALL;
            break;
        }

        status = WdfRequestRetrieveOutputBuffer(
            Request,
            sizeof(CXL_WORK_STATUS),
            &workStatus,
            NULL
        );

        if (NT_SUCCESS(status)) {
            status = CXLAccelGetWorkStatus(device, *workIdPtr, workStatus);

            if (NT_SUCCESS(status)) {
                bytesReturned = sizeof(CXL_WORK_STATUS);
            }
        }

        break;
    }

    case IOCTL_CXL_RECONFIGURE_FPGA: {
        PCXL_FPGA_RECONFIG_REQUEST reconfigRequest;

        KdPrint(("CXLAccel: IOCTL_CXL_RECONFIGURE_FPGA\n"));

        if (InputBufferLength < sizeof(CXL_FPGA_RECONFIG_REQUEST)) {
            status = STATUS_INVALID_BUFFER_SIZE;
            break;
        }

        status = WdfRequestRetrieveInputBuffer(
            Request,
            sizeof(CXL_FPGA_RECONFIG_REQUEST),
            &reconfigRequest,
            NULL
        );

        if (NT_SUCCESS(status)) {
            status = CXLAccelReconfigureFpga(device, reconfigRequest->BitstreamPath);
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
// CXLAccelMapBar0
//=============================================================================

NTSTATUS
CXLAccelMapBar0(
    IN WDFDEVICE Device
)
/*++
Routine Description:
    Maps BAR0 for FPGA management.

Arguments:
    Device - Handle to the device object

Return Value:
    STATUS_SUCCESS if successful, error code otherwise
--*/
{
    PCXL_ACCEL_CONTEXT accelContext = GetAccelContext(Device);
    PHYSICAL_ADDRESS physicalAddress;

    KdPrint(("CXLAccel: Mapping BAR0 at 0x%llx\n",
            accelContext->Bar0PhysicalAddress.QuadPart));

    physicalAddress.QuadPart = accelContext->Bar0PhysicalAddress.QuadPart;

    accelContext->Bar0BaseAddress = MmMapIoSpace(
        physicalAddress,
        accelContext->Bar0Length,
        MmNonCached
    );

    if (accelContext->Bar0BaseAddress == NULL) {
        KdPrint(("CXLAccel: MmMapIoSpace failed for BAR0\n"));
        return STATUS_INSUFFICIENT_RESOURCES;
    }

    KdPrint(("CXLAccel: BAR0 mapped to 0x%p\n", accelContext->Bar0BaseAddress));

    return STATUS_SUCCESS;
}

//=============================================================================
// CXLAccelMapBar2
//=============================================================================

NTSTATUS
CXLAccelMapBar2(
    IN WDFDEVICE Device
)
/*++
Routine Description:
    Maps BAR2 for DMA engine.

Arguments:
    Device - Handle to the device object

Return Value:
    STATUS_SUCCESS if successful, error code otherwise
--*/
{
    PCXL_ACCEL_CONTEXT accelContext = GetAccelContext(Device);
    PHYSICAL_ADDRESS physicalAddress;

    KdPrint(("CXLAccel: Mapping BAR2 at 0x%llx\n",
            accelContext->Bar2PhysicalAddress.QuadPart));

    physicalAddress.QuadPart = accelContext->Bar2PhysicalAddress.QuadPart;

    accelContext->Bar2BaseAddress = MmMapIoSpace(
        physicalAddress,
        accelContext->Bar2Length,
        MmNonCached
    );

    if (accelContext->Bar2BaseAddress == NULL) {
        KdPrint(("CXLAccel: MmMapIoSpace failed for BAR2\n"));
        return STATUS_INSUFFICIENT_RESOURCES;
    }

    KdPrint(("CXLAccel: BAR2 mapped to 0x%p\n", accelContext->Bar2BaseAddress));

    return STATUS_SUCCESS;
}

//=============================================================================
// CXLAccelUnmapBars
//=============================================================================

VOID
CXLAccelUnmapBars(
    IN WDFDEVICE Device
)
/*++
Routine Description:
    Unmaps BAR0 and BAR2.

Arguments:
    Device - Handle to the device object

Return Value:
    None
--*/
{
    PCXL_ACCEL_CONTEXT accelContext = GetAccelContext(Device);

    if (accelContext->Bar0BaseAddress != NULL) {
        KdPrint(("CXLAccel: Unmapping BAR0\n"));
        MmUnmapIoSpace(
            accelContext->Bar0BaseAddress,
            accelContext->Bar0Length
        );
        accelContext->Bar0BaseAddress = NULL;
    }

    if (accelContext->Bar2BaseAddress != NULL) {
        KdPrint(("CXLAccel: Unmapping BAR2\n"));
        MmUnmapIoSpace(
            accelContext->Bar2BaseAddress,
            accelContext->Bar2Length
        );
        accelContext->Bar2BaseAddress = NULL;
    }
}

//=============================================================================
// CXLAccelGetAccelInfo
//=============================================================================

NTSTATUS
CXLAccelGetAccelInfo(
    IN WDFDEVICE Device,
    OUT PCXL_ACCEL_INFO AccelInfo
)
/*++
Routine Description:
    Gets accelerator device information.

Arguments:
    Device - Handle to the device object
    AccelInfo - Pointer to receive accelerator info

Return Value:
    STATUS_SUCCESS
--*/
{
    PCXL_ACCEL_CONTEXT accelContext = GetAccelContext(Device);

    RtlZeroMemory(AccelInfo, sizeof(CXL_ACCEL_INFO));

    RtlStringCchCopyNW(AccelInfo->Name, 32, L"accelerator0", 12);
    RtlStringCchCopyNW(AccelInfo->PciBdf, 16, L"0000:05:00.0", 14);

    AccelInfo->Bar0PhysicalAddress = accelContext->Bar0PhysicalAddress.QuadPart;
    AccelInfo->Bar0Size = accelContext->Bar0Length;
    AccelInfo->Bar2PhysicalAddress = accelContext->Bar2PhysicalAddress.QuadPart;
    AccelInfo->Bar2Size = accelContext->Bar2Length;

    AccelInfo->FpgaConfigured = accelContext->FpgaConfigured;
    RtlStringCchCopyNW(
        AccelInfo->BitstreamVersion,
        32,
        accelContext->BitstreamVersion,
        (USHORT)wcslen(accelContext->BitstreamVersion)
    );

    AccelInfo->WorkQueueDepth = 16;  // Simulated

    return STATUS_SUCCESS;
}

//=============================================================================
// CXLAccelSubmitWork
//=============================================================================

NTSTATUS
CXLAccelSubmitWork(
    IN WDFDEVICE Device,
    IN PCXL_WORK_SUBMISSION WorkSubmission
)
/*++
Routine Description:
    Submits work to the accelerator.

Arguments:
    Device - Handle to the device object
    WorkSubmission - Pointer to work submission structure

Return Value:
    STATUS_SUCCESS if successful, error code otherwise
--*/
{
    PCXL_ACCEL_CONTEXT accelContext = GetAccelContext(Device);
    PCXL_WORK_ENTRY workEntry;

    //
    // Allocate work entry
    //
    workEntry = (PCXL_WORK_ENTRY)ExAllocatePoolWithTag(
        NonPagedPool,
        sizeof(CXL_WORK_ENTRY),
        'AWXC'  // CXWA - CXL Work Accelerator
    );

    if (workEntry == NULL) {
        return STATUS_INSUFFICIENT_RESOURCES;
    }

    //
    // Initialize work entry
    //
    workEntry->WorkId = accelContext->NextWorkId++;
    workEntry->Submission = *WorkSubmission;
    workEntry->Completed = FALSE;
    workEntry->Status = STATUS_PENDING;
    workEntry->BytesProcessed = 0;
    workEntry->CompletionRequest = NULL;

    //
    // Add to work queue
    //
    WdfSpinLockAcquire(accelContext->WorkQueueLock);
    InsertTailList(&accelContext->WorkQueue, &workEntry->ListEntry);
    WdfSpinLockRelease(accelContext->WorkQueueLock);

    KdPrint(("CXLAccel: Submitted work %lld\n", workEntry->WorkId));

    //
    // In a real implementation, we would:
    // 1. Program DMA engine via BAR2
    // 2. Start accelerator operation
    // 3. Return work ID to caller
    //
    // For now, simulate immediate completion
    //
    workEntry->Completed = TRUE;
    workEntry->Status = STATUS_SUCCESS;
    workEntry->BytesProcessed = WorkSubmission->InputSize;

    return STATUS_SUCCESS;
}

//=============================================================================
// CXLAccelGetWorkStatus
//=============================================================================

NTSTATUS
CXLAccelGetWorkStatus(
    IN WDFDEVICE Device,
    IN UINT64 WorkId,
    OUT PCXL_WORK_STATUS WorkStatus
)
/*++
Routine Description:
    Gets the status of a submitted work item.

Arguments:
    Device - Handle to the device object
    WorkId - Work ID to query
    WorkStatus - Pointer to receive work status

Return Value:
    STATUS_SUCCESS if found, STATUS_NOT_FOUND otherwise
--*/
{
    PCXL_ACCEL_CONTEXT accelContext = GetAccelContext(Device);
    PLIST_ENTRY listEntry;
    PCXL_WORK_ENTRY workEntry = NULL;
    BOOLEAN found = FALSE;

    //
    // Search work queue
    //
    WdfSpinLockAcquire(accelContext->WorkQueueLock);

    for (listEntry = accelContext->WorkQueue.Flink;
         listEntry != &accelContext->WorkQueue;
         listEntry = listEntry->Flink) {

        workEntry = CONTAINING_RECORD(listEntry, CXL_WORK_ENTRY, ListEntry);

        if (workEntry->WorkId == WorkId) {
            found = TRUE;
            break;
        }
    }

    WdfSpinLockRelease(accelContext->WorkQueueLock);

    if (!found) {
        return STATUS_NOT_FOUND;
    }

    //
    // Fill in status
    //
    WorkStatus->WorkId = workEntry->WorkId;
    WorkStatus->Completed = workEntry->Completed;
    WorkStatus->Status = workEntry->Status;
    WorkStatus->BytesProcessed = workEntry->BytesProcessed;

    return STATUS_SUCCESS;
}

//=============================================================================
// CXLAccelReconfigureFpga
//=============================================================================

NTSTATUS
CXLAccelReconfigureFpga(
    IN WDFDEVICE Device,
    IN PCWSTR BitstreamPath
)
/*++
Routine Description:
    Reconfigures the FPGA with a new bitstream.

Arguments:
    Device - Handle to the device object
    BitstreamPath - Path to the bitstream file

Return Value:
    STATUS_SUCCESS if successful, error code otherwise
--*/
{
    PCXL_ACCEL_CONTEXT accelContext = GetAccelContext(Device);
    PUCHAR registerBase;

    KdPrint(("CXLAccel: Reconfiguring FPGA with %ws\n", BitstreamPath));

    if (accelContext->Bar0BaseAddress == NULL) {
        return STATUS_INVALID_DEVICE_STATE;
    }

    registerBase = (PUCHAR)accelContext->Bar0BaseAddress;

    //
    // In a real implementation, we would:
    // 1. Load bitstream from file
    // 2. Program FPGA via management interface
    // 3. Monitor reconfiguration status
    // 4. Verify successful configuration
    //
    // For now, simulate the operation
    //

    //
    // Simulated: Write to reconfig trigger register
    //
    // WRITE_REGISTER_ULONG((PULONG)(registerBase + FPGA_MGMT_RECONFIG), 1);
    //

    //
    // Wait for simulated reconfiguration
    //
    LARGE_INTEGER delay;
    delay.QuadPart = -10000 * 100;  // 100 milliseconds
    KeDelayExecutionThread(KernelMode, FALSE, &delay);

    //
    // Update state
    //
    accelContext->FpgaConfigured = TRUE;
    RtlStringCchCopyNW(accelContext->BitstreamVersion, 32, L"2.0.0", 5);

    KdPrint(("CXLAccel: FPGA reconfigured successfully\n"));

    return STATUS_SUCCESS;
}

//=============================================================================
// Interrupt Handlers
//=============================================================================

BOOLEAN
CXLAccelEvtInterruptIsr(
    IN WDFINTERRUPT Interrupt,
    IN ULONG MessageID
)
/*++
Routine Description:
    ISR for accelerator interrupts.

Arguments:
    Interrupt - Handle to the interrupt object
    MessageID - Message ID (MSI)

Return Value:
    TRUE if interrupt is handled, FALSE otherwise
--*/
{
    WDFDEVICE device;
    PCXL_ACCEL_CONTEXT accelContext;

    UNREFERENCED_PARAMETER(MessageID);

    device = WdfInterruptGetDevice(Interrupt);
    accelContext = GetAccelContext(device);

    //
    // In a real implementation, we would:
    // 1. Read interrupt status register
    // 2. Determine if our device generated the interrupt
    // 3. Clear interrupt condition
    // 4. Queue DPC if needed
    //
    // For now, return FALSE (not our interrupt)
    //

    return FALSE;
}

VOID
CXLAccelEvtInterruptDpc(
    IN WDFINTERRUPT Interrupt,
    IN PVOID Context
)
/*++
Routine Description:
    DPC for accelerator interrupt processing.

Arguments:
    Interrupt - Handle to the interrupt object
    Context - Context pointer (not used)

Return Value:
    None
--*/
{
    WDFDEVICE device;
    PCXL_ACCEL_CONTEXT accelContext;

    UNREFERENCED_PARAMETER(Context);

    device = WdfInterruptGetDevice(Interrupt);
    accelContext = GetAccelContext(device);

    //
    // In a real implementation, we would:
    // 1. Process completed work items
    // 2. Complete waiting requests
    // 3. Check for errors
    //

    KdPrint(("CXLAccel: DPC executed\n"));
}

NTSTATUS
CXLAccelEvtInterruptEnable(
    IN WDFINTERRUPT Interrupt,
    IN WDFDEVICE Device
)
/*++
Routine Description:
    Called when interrupt is enabled.

Arguments:
    Interrupt - Handle to the interrupt object
    Device - Handle to the device object

Return Value:
    STATUS_SUCCESS
--*/
{
    UNREFERENCED_PARAMETER(Interrupt);
    UNREFERENCED_PARAMETER(Device);

    KdPrint(("CXLAccel: Interrupt enabled\n"));

    return STATUS_SUCCESS;
}
