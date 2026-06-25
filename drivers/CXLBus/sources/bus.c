/*++
Copyright (c) 2025 CXLAgent Project

Module Name:
    bus.c

Abstract:
    CXL Bus Driver for Windows.
    Handles PCI device enumeration, CXL capability detection,
    and child device (cache, memory, accelerator) creation.

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

#define CXL_PCI_VENDOR_ID_INTEL    0x8086
#define CXL_PCI_VENDOR_ID_OTHER    0x1E49  // Example CXL device vendor ID

#define PCI_CAPABILITIES_PTR       0x34
#define PCI_CAP_ID_CXL             0x01    // CXL Extended Capability ID
#define CXL_EXT_CAP_OFFSET         0x100   // Extended capability space starts at 0x100

//=============================================================================
// Function Prototypes
//=============================================================================

DRIVER_INITIALIZE DriverEntry;
EVT_WDF_DRIVER_DEVICE_ADD CXLBusEvtDriverDeviceAdd;
EVT_WDF_IO_QUEUE_IO_DEVICE_CONTROL CXLBusEvtIoDeviceControl;
EVT_WDF_CHILD_LIST_CREATE_DEVICE CXLBusEvtChildListCreateDevice;
EVT_WDF_CHILD_LIST_SCAN_FOR_CHILDREN CXLBusEvtChildListScanForChildren;

NTSTATUS CXLBusEnumeratePCIDevices(
    IN WDFDEVICE Device
);

BOOLEAN CXLBusDetectCXLCapability(
    IN USHORT VendorId,
    IN USHORT DeviceId,
    IN UCHAR Bus,
    IN UCHAR Device,
    IN UCHAR Function
);

NTSTATUS CXLBusCreateChildDevice(
    IN WDFDEVICE ParentDevice,
    IN CXL_DEVICE_TYPE DeviceType,
    IN USHORT VendorId,
    IN USHORT DeviceId,
    IN UCHAR Bus,
    IN UCHAR Device,
    IN UCHAR Function
);

VOID CXLBusUpdateTopology(
    IN WDFDEVICE Device
);

//=============================================================================
// Device Context
//=============================================================================

typedef struct _CXL_BUS_CONTEXT {
    WDFDEVICE Device;
    WDFCHILDLIST ChildList;
    CXL_TOPOLOGY Topology;
    KSPIN_LOCK Lock;
} CXL_BUS_CONTEXT, *PCXL_BUS_CONTEXT;

WDF_DECLARE_CONTEXT_TYPE_WITH_NAME(CXL_BUS_CONTEXT, GetBusContext)

//=============================================================================
// Child Device Identification Description
//=============================================================================

typedef struct _CXL_CHILD_IDENTIFICATION {
    WDF_CHILD_IDENTIFICATION_DESCRIPTION_HEADER Header;
    CXL_DEVICE_TYPE DeviceType;
    USHORT VendorId;
    USHORT DeviceId;
    UCHAR Bus;
    UCHAR Device;
    UCHAR Function;
    WCHAR DeviceName[32];
} CXL_CHILD_IDENTIFICATION, *PCXL_CHILD_IDENTIFICATION;

WDF_CHILD_IDENTIFICATION_DESCRIPTION_DESCRIPTION_INIT(
    CXL_CHILD_IDENTIFICATION,
    CXL_CHILD_IDENTIFICATION_DESCRIPTION
);

WDF_CHILD_LIST_CONFIG_ITERATOR_INIT(
    CXL_CHILD_IDENTIFICATION,
    CXL_CHILD_IDENTIFICATION
);

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
    DriverEntry initializes the CXL Bus driver.

Arguments:
    DriverObject - Pointer to the driver object
    RegistryPath - Pointer to the registry path for the driver

Return Value:
    STATUS_SUCCESS if successful, error code otherwise
--*/
{
    WDF_DRIVER_CONFIG config;
    NTSTATUS status;

    KdPrint(("CXLBus: DriverEntry\n"));

    //
    // Initialize the driver configuration
    //
    WDF_DRIVER_CONFIG_INIT(&config, CXLBusEvtDriverDeviceAdd);

    //
    // Set the driver as a bus driver
    //
    config.DriverInitFlags |= WdfDriverInitNonPnpDriver;

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
        KdPrint(("CXLBus: WdfDriverCreate failed: 0x%x\n", status));
    }

    return status;
}

//=============================================================================
// EvtDriverDeviceAdd
//=============================================================================

NTSTATUS
CXLBusEvtDriverDeviceAdd(
    IN WDFDRIVER Driver,
    IN PWDFDEVICE_INIT DeviceInit
)
/*++
Routine Description:
    EvtDriverDeviceAdd is called when a device is added.
    Creates the bus FDO and sets up child enumeration.

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
    PCXL_BUS_CONTEXT busContext;
    WDF_CHILD_LIST_CONFIG childListConfig;
    WDF_IO_QUEUE_CONFIG queueConfig;
    UNICODE_STRING deviceName;
    UNICODE_STRING symbolicLink;

    UNREFERENCED_PARAMETER(Driver);

    KdPrint(("CXLBus: EvtDriverDeviceAdd\n"));

    //
    // Initialize device name and symbolic link
    //
    RtlInitUnicodeString(&deviceName, CXLBUS_DEVICE_NAME);
    RtlInitUnicodeString(&symbolicLink, CXLBUS_SYMBOLIC_LINK);

    //
    // Configure device attributes
    //
    WDF_OBJECT_ATTRIBUTES_INIT(&deviceAttributes);
    WDF_OBJECT_ATTRIBUTES_SET_CONTEXT_TYPE(&deviceAttributes, CXL_BUS_CONTEXT);
    deviceAttributes.SynchronizationScope = WdfSynchronizationScopeDevice;

    //
    // Create the device
    //
    status = WdfDeviceCreate(&DeviceInit, &deviceAttributes, &device);

    if (!NT_SUCCESS(status)) {
        KdPrint(("CXLBus: WdfDeviceCreate failed: 0x%x\n", status));
        return status;
    }

    //
    // Initialize device context
    //
    busContext = GetBusContext(device);
    busContext->Device = device;
    KeInitializeSpinLock(&busContext->Lock);
    RtlZeroMemory(&busContext->Topology, sizeof(CXL_TOPOLOGY));

    //
    // Create symbolic link
    //
    status = WdfDeviceCreateSymbolicLink(device, &symbolicLink);

    if (!NT_SUCCESS(status)) {
        KdPrint(("CXLBus: WdfDeviceCreateSymbolicLink failed: 0x%x\n", status));
        return status;
    }

    //
    // Configure child list for bus enumeration
    //
    WDF_CHILD_LIST_CONFIG_INIT(
        &childListConfig,
        sizeof(CXL_CHILD_IDENTIFICATION),
        CXLBusEvtChildListCreateDevice
    );

    childListConfig.EvtChildListScanForChildren = CXLBusEvtChildListScanForChildren;

    status = WdfFdoAddDefaultChildList(device, &childListConfig);

    if (!NT_SUCCESS(status)) {
        KdPrint(("CXLBus: WdfFdoAddDefaultChildList failed: 0x%x\n", status));
        return status;
    }

    busContext->ChildList = WdfFdoGetDefaultChildList(device);

    //
    // Configure default I/O queue
    //
    WDF_IO_QUEUE_CONFIG_INIT_DEFAULT_QUEUE(
        &queueConfig,
        WdfIoQueueDispatchParallel
    );

    queueConfig.EvtIoDeviceControl = CXLBusEvtIoDeviceControl;

    status = WdfIoQueueCreate(
        device,
        &queueConfig,
        WDF_NO_OBJECT_ATTRIBUTES,
        WDF_NO_HANDLE
    );

    if (!NT_SUCCESS(status)) {
        KdPrint(("CXLBus: WdfIoQueueCreate failed: 0x%x\n", status));
        return status;
    }

    //
    // Initial PCI scan for CXL devices
    //
    CXLBusEnumeratePCIDevices(device);

    KdPrint(("CXLBus: Device initialized successfully\n"));

    return STATUS_SUCCESS;
}

//=============================================================================
// EvtChildListScanForChildren
//=============================================================================

VOID
CXLBusEvtChildListScanForChildren(
    IN WDFCHILDLIST ChildList
)
/*++
Routine Description:
    Scans for CXL devices on the PCI bus and creates child devices.

Arguments:
    ChildList - Handle to the child list

Return Value:
    None
--*/
{
    WDFDEVICE parentDevice;
    PCXL_BUS_CONTEXT busContext;

    KdPrint(("CXLBus: EvtChildListScanForChildren\n"));

    parentDevice = WdfChildListGetParentDevice(ChildList);
    busContext = GetBusContext(parentDevice);

    //
    // Enumerate PCI devices and create child devices
    //
    CXLBusEnumeratePCIDevices(parentDevice);
}

//=============================================================================
// CXLBusEnumeratePCIDevices
//=============================================================================

NTSTATUS
CXLBusEnumeratePCIDevices(
    IN WDFDEVICE Device
)
/*++
Routine Description:
    Enumerates PCI devices looking for CXL capability.

Arguments:
    Device - Handle to the bus device

Return Value:
    STATUS_SUCCESS if successful, error code otherwise
--*/
{
    UCHAR bus, device, func;
    USHORT vendorId, deviceId;
    UINT32 deviceCount = 0;
    PCXL_BUS_CONTEXT busContext = GetBusContext(Device);
    BOOLEAN hasCXL;

    KdPrint(("CXLBus: Scanning PCI bus for CXL devices...\n"));

    //
    // Scan PCI buses 0-255
    //
    for (bus = 0; bus < 4; bus++) {  // Limit to first 4 buses for now
        for (device = 0; device < 32; device++) {
            for (func = 0; func < 8; func++) {
                //
                // Check for device presence
                //
                // In a real implementation, this would use PCI APIs:
                // - HalGetBusData for config space reads
                // - Or IRP_MN_READ_CONFIG via PDO
                //

                //
                // Simulated CXL device detection
                //
                if (bus == 0 && device == 3 && func == 0) {
                    // Simulated CXL Cache device
                    vendorId = CXL_PCI_VENDOR_ID_INTEL;
                    deviceId = 0x3B00;
                    hasCXL = TRUE;

                    if (hasCXL) {
                        KdPrint(("CXLBus: Found CXL device at %02x:%02x.%x\n",
                                bus, device, func));

                        CXLBusCreateChildDevice(
                            Device,
                            CxlDeviceTypeCache,
                            vendorId,
                            deviceId,
                            bus,
                            device,
                            func
                        );

                        deviceCount++;
                    }
                }

                if (bus == 0 && device == 4 && func == 0) {
                    // Simulated CXL Memory device
                    vendorId = CXL_PCI_VENDOR_ID_INTEL;
                    deviceId = 0x3C00;
                    hasCXL = TRUE;

                    if (hasCXL) {
                        KdPrint(("CXLBus: Found CXL Memory device at %02x:%02x.%x\n",
                                bus, device, func));

                        CXLBusCreateChildDevice(
                            Device,
                            CxlDeviceTypeMemory,
                            vendorId,
                            deviceId,
                            bus,
                            device,
                            func
                        );

                        deviceCount++;
                    }
                }

                //
                // If function 0 doesn't exist, skip other functions
                //
                if (func == 0 && vendorId == 0xFFFF) {
                    break;
                }
            }
        }
    }

    KdPrint(("CXLBus: Found %d CXL devices\n", deviceCount));

    //
    // Update topology
    //
    CXLBusUpdateTopology(Device);

    return STATUS_SUCCESS;
}

//=============================================================================
// CXLBusDetectCXLCapability
//=============================================================================

BOOLEAN
CXLBusDetectCXLCapability(
    IN USHORT VendorId,
    IN USHORT DeviceId,
    IN UCHAR Bus,
    IN UCHAR Device,
    IN UCHAR Function
)
/*++
Routine Description:
    Detects if a PCI device has CXL capability.

Arguments:
    VendorId - PCI vendor ID
    DeviceId - PCI device ID
    Bus - PCI bus number
    Device - PCI device number
    Function - PCI function number

Return Value:
    TRUE if device has CXL capability, FALSE otherwise
--*/
{
    //
    // In a real implementation, this would:
    // 1. Read PCI config space capabilities pointer
    // 2. Scan for CXL capability (0x01)
    // 3. Check extended capability space (0x100+) for CXL EP/RP
    //
    // For now, we use a whitelist of known CXL devices
    //

    //
    // Check for known CXL devices
    //
    if (VendorId == CXL_PCI_VENDOR_ID_INTEL) {
        // Intel CXL devices
        if ((DeviceId & 0xFF00) == 0x3B00) {  // Cache devices
            return TRUE;
        }
        if ((DeviceId & 0xFF00) == 0x3C00) {  // Memory devices
            return TRUE;
        }
    }

    return FALSE;
}

//=============================================================================
// CXLBusCreateChildDevice
//=============================================================================

NTSTATUS
CXLBusCreateChildDevice(
    IN WDFDEVICE ParentDevice,
    IN CXL_DEVICE_TYPE DeviceType,
    IN USHORT VendorId,
    IN USHORT DeviceId,
    IN UCHAR Bus,
    IN UCHAR Device,
    IN UCHAR Function
)
/*++
Routine Description:
    Creates a child device for a CXL device found on the PCI bus.

Arguments:
    ParentDevice - Handle to the parent bus device
    DeviceType - Type of CXL device
    VendorId - PCI vendor ID
    DeviceId - PCI device ID
    Bus - PCI bus number
    Device - PCI device number
    Function - PCI function number

Return Value:
    STATUS_SUCCESS if successful, error code otherwise
--*/
{
    NTSTATUS status;
    PCXL_BUS_CONTEXT busContext = GetBusContext(ParentDevice);
    CXL_CHILD_IDENTIFICATION childDesc;
    WCHAR deviceName[32];

    //
    // Initialize child identification description
    //
    WDF_CHILD_IDENTIFICATION_DESCRIPTION_HEADER_INIT(
        &childDesc.Header,
        sizeof(CXL_CHILD_IDENTIFICATION)
    );

    childDesc.DeviceType = DeviceType;
    childDesc.VendorId = VendorId;
    childDesc.DeviceId = DeviceId;
    childDesc.Bus = Bus;
    childDesc.Device = Device;
    childDesc.Function = Function;

    //
    // Create device name based on type
    //
    switch (DeviceType) {
    case CxlDeviceTypeCache:
        swprintf_s(childDesc.DeviceName, 32, L"cache0");
        busContext->Topology.CacheDeviceCount++;
        break;

    case CxlDeviceTypeMemory:
        swprintf_s(childDesc.DeviceName, 32, L"mem0");
        busContext->Topology.MemoryDeviceCount++;
        break;

    case CxlDeviceTypeAccelerator:
        swprintf_s(childDesc.DeviceName, 32, L"accelerator0");
        busContext->Topology.AcceleratorDeviceCount++;
        break;

    default:
        swprintf_s(childDesc.DeviceName, 32, L"cxldev0");
        break;
    }

    KdPrint(("CXLBus: Creating child device %ws\n", childDesc.DeviceName));

    //
    // Add to child list (this will call EvtChildListCreateDevice)
    //
    status = WdfChildListAddOrUpdateChildDescription(
        WdfFdoGetDefaultChildList(ParentDevice),
        &childDesc.Header,
        WDF_NO_HANDLE,
        NULL
    );

    if (!NT_SUCCESS(status)) {
        KdPrint(("CXLBus: WdfChildListAddOrUpdateChildDescription failed: 0x%x\n", status));
    }

    busContext->Topology.TotalDeviceCount++;

    return status;
}

//=============================================================================
// EvtChildListCreateDevice
//=============================================================================

NTSTATUS
CXLBusEvtChildListCreateDevice(
    IN WDFCHILDLIST ChildList,
    IN PWDF_CHILD_IDENTIFICATION_DESCRIPTION_HEADER IdentificationDescription,
    IN PWDFDEVICE_INIT ChildInit
)
/*++
Routine Description:
    Creates a child device (PDO) for a CXL device.

Arguments:
    ChildList - Handle to the child list
    IdentificationDescription - Pointer to child identification
    ChildInit - Pointer to device initialization structure

Return Value:
    STATUS_SUCCESS if successful, error code otherwise
--*/
{
    NTSTATUS status;
    PCXL_CHILD_IDENTIFICATION childId;
    UNICODE_STRING deviceName;
    WDFDEVICE childDevice;
    WDF_OBJECT_ATTRIBUTES childAttributes;

    UNREFERENCED_PARAMETER(ChildList);

    childId = CONTAINING_RECORD(
        IdentificationDescription,
        CXL_CHILD_IDENTIFICATION,
        Header
    );

    KdPrint(("CXLBus: Creating PDO for %ws\n", childId->DeviceName));

    //
    // Assign device name
    //
    RtlInitUnicodeString(&deviceName, childId->DeviceName);

    status = WdfDeviceInitAssignName(ChildInit, &deviceName);

    if (!NT_SUCCESS(status)) {
        KdPrint(("CXLBus: WdfDeviceInitAssignName failed: 0x%x\n", status));
        return status;
    }

    //
    // Set device type
    //
    WdfDeviceInitSetDeviceType(ChildInit, FILE_DEVICE_UNKNOWN);

    //
    // Create child device
    //
    WDF_OBJECT_ATTRIBUTES_INIT(&childAttributes);

    status = WdfDeviceCreate(&ChildInit, &childAttributes, &childDevice);

    if (!NT_SUCCESS(status)) {
        KdPrint(("CXLBus: WdfDeviceCreate failed: 0x%x\n", status));
        return status;
    }

    KdPrint(("CXLBus: PDO created successfully for %ws\n", childId->DeviceName));

    return STATUS_SUCCESS;
}

//=============================================================================
// CXLBusUpdateTopology
//=============================================================================

VOID
CXLBusUpdateTopology(
    IN WDFDEVICE Device
)
/*++
Routine Description:
    Updates the CXL bus topology information.

Arguments:
    Device - Handle to the bus device

Return Value:
    None
--*/
{
    PCXL_BUS_CONTEXT busContext = GetBusContext(Device);

    KdPrint(("CXLBus: Topology: Cache=%d, Memory=%d, Accel=%d, Total=%d\n",
            busContext->Topology.CacheDeviceCount,
            busContext->Topology.MemoryDeviceCount,
            busContext->Topology.AcceleratorDeviceCount,
            busContext->Topology.TotalDeviceCount));
}

//=============================================================================
// EvtIoDeviceControl
//=============================================================================

VOID
CXLBusEvtIoDeviceControl(
    IN WDFQUEUE Queue,
    IN WDFREQUEST Request,
    IN size_t OutputBufferLength,
    IN size_t InputBufferLength,
    IN ULONG IoControlCode
)
/*++
Routine Description:
    Handles IOCTL requests for the CXL bus driver.

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
    PCXL_BUS_CONTEXT busContext;

    UNREFERENCED_PARAMETER(Queue);

    device = WdfIoQueueGetDevice(Queue);
    busContext = GetBusContext(device);

    switch (IoControlCode) {
    case IOCTL_CXL_GET_TOPOLOGY: {
        PCXL_TOPOLOGY topology;

        if (OutputBufferLength < sizeof(CXL_TOPOLOGY)) {
            status = STATUS_BUFFER_TOO_SMALL;
            break;
        }

        status = WdfRequestRetrieveOutputBuffer(
            Request,
            sizeof(CXL_TOPOLOGY),
            &topology,
            NULL
        );

        if (NT_SUCCESS(status)) {
            RtlCopyMemory(topology, &busContext->Topology, sizeof(CXL_TOPOLOGY));
            bytesReturned = sizeof(CXL_TOPOLOGY);
        }

        break;
    }

    case IOCTL_CXL_GET_PCI_INFO: {
        //
        // Return PCI information for child devices
        //
        status = STATUS_NOT_IMPLEMENTED;
        break;
    }

    default:
        status = STATUS_INVALID_DEVICE_REQUEST;
        break;
    }

    WdfRequestCompleteWithInformation(Request, status, bytesReturned);
}
