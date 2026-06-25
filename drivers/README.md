# CXLAgent Windows Drivers

This directory contains the Windows kernel-mode drivers for CXLAgent on Windows 11.

## Overview

These drivers provide CXL (Compute Express Link) device support on Windows 11, which currently lacks native CXL Type 2 accelerator support. The driver stack consists of 5 KMDF/WDM drivers:

### Driver Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    User-Mode Applications                     │
│                   (Python cxlagent + Win32)                  │
└──────────────────────────────┬───────────────────────────────┘
                               │ IOCTL Communication
┌──────────────────────────────┴───────────────────────────────┐
│                   CXLBus Driver (KMDF Bus Driver)            │
│  - PCI device enumeration and FDO creation                   │
│  - Child device PDO management (cache, memory, accelerator)  │
│  - Bus-wide resource arbitration                             │
└──────────────────────┬──────────────────────────────────────┘
                       │
    ┌──────────────────┼──────────────────┐
    │                  │                  │
┌───┴──────────┐  ┌────┴─────────┐  ┌─────┴──────────┐
│ CXL Cache    │  │ CXL Memory   │  │ CXL Type 2     │
│ Driver       │  │ Driver       │  │ Accelerator    │
├──────────────┤  ├──────────────┤  ├────────────────┤
│- WBINVD ctrl │  │- RAM/PMEM    │  │- FPGA config   │
│- BAR2 access │  │  reporting   │  │- DMA engine    │
│- Cache state │  │- Window mgmt │  │- Compute ops   │
└──────────────┘  └──────────────┘  └────────────────┘
        │                  │                  │
        └──────────────────┼──────────────────┘
                           │
              ┌────────────┴─────────────┐
              │  Physical Memory Access  │
              │  Driver (PhysMem.sys)    │
              │  - MmMapIoSpace wrapper  │
              │  - Physical address map  │
              │  - Security validation   │
              └──────────────────────────┘
```

### Driver Components

| Driver | File | Description |
|--------|------|-------------|
| **PhysMem** | `PhysMem/` | Physical memory access driver (equivalent to Linux `/dev/mem`) |
| **CXLBus** | `CXLBus/` | Bus driver for PCI enumeration and device discovery |
| **CXLCache** | `CXLCache/` | Cache driver with WBINVD trigger and BAR2 MMIO access |
| **CXLMemory** | `CXLMemory/` | Memory driver for RAM/PMEM reporting and window management |
| **CXLAccel** | `CXLAccel/` | Accelerator driver for FPGA configuration and DMA control |

## Prerequisites

### Software Requirements

- **Windows 11** (Build 22621 or later recommended)
- **Visual Studio 2022** (Community, Professional, or Enterprise)
- **Windows Driver Kit (WDK) for Windows 11**
  - Download from: https://learn.microsoft.com/en-us/windows-hardware/drivers/download-the-wdk
- **Windows SDK** (installed with WDK)
- **Windows SDK for EWDK** (optional, for Enterprise WDK)

### Hardware Requirements

- CXL Type 2 FPGA accelerator device
- Motherboard/BIOS with CXL support enabled
- Adequate cooling for sustained operation

## Building the Drivers

### Step 1: Install WDK

1. Install Visual Studio 2022
2. Install Windows Driver Kit (WDK) for Windows 11
3. Install Windows SDK

### Step 2: Open the Solution

1. Open `CXLAgent-Windows.sln` in Visual Studio 2022
2. Select the desired configuration:
   - **Debug**: For development and debugging
   - **Release**: For production builds
3. Select **x64** platform

### Step 3: Build the Solution

1. Right-click on the solution in Solution Explorer
2. Select **Build Solution** (or press `Ctrl+Shift+B`)
3. Drivers will be built in the following locations:
   - `PhysMem\x64\Debug\PhysMem.sys` (or Release)
   - `CXLBus\x64\Debug\CXLBus.sys`
   - etc.

### Step 4: Sign the Drivers

#### For Development/Testing

```powershell
# Enable test signing mode
bcdedit /set testsigning on

# Restart computer

# Create test certificate
makecert -pe -ss PrivateCertStore -n CN=CXLTestCert CXLTestCert.cer

# Sign drivers
signtool sign /v /s PrivateCertStore /n CXLTestCert /t http://timestamp.digicert.com drivers\*\x64\Debug\*.sys
```

#### For Production

1. Obtain an EV Code Signing Certificate from a trusted CA
2. Complete WHQL/HLK certification
3. Sign with production certificate:
```powershell
signtool sign /v /fd sha256 /ac MSCV-VSClass3.cer /n "Company Name" /t http://timestamp.digicert.com *.sys
```

## Installing the Drivers

### Manual Installation

```powershell
# Install drivers in correct order (dependencies first)
pnputil /add-driver drivers\PhysMem\PhysMem.inf /install
pnputil /add-driver drivers\CXLBus\CXLBus.inf /install
pnputil /add-driver drivers\CXLCache\CXLCache.inf /install
pnputil /add-driver drivers\CXLMemory\CXLMemory.inf /install
pnputil /add-driver drivers\CXLAccel\CXLAccel.inf /install

# Scan for device changes
pnputil /scan-devices
```

### Using Installation Script

```powershell
.\tools\install_drivers.ps1
```

## Verifying Installation

### Check Device Manager

1. Open Device Manager (`devmgmt.msc`)
2. Look for "CXL Devices" category
3. Verify all drivers are loaded without warning icons

### Check Driver Status

```powershell
# Query driver status
sc query PhysMem
sc query CXLBus
sc query CXLCache
sc query CXLMemory
sc query CXLAccel
```

### Check Event Logs

```powershell
# Check for driver events in System log
Get-WinEvent -LogName System | Where-Object {$_.Message -like "*CXL*"} | Select-Object TimeCreated, Message
```

## Debugging

### Enable Kernel Debugging

```powershell
# Enable debugging on target machine
bcdedit /debug on
bcdedit /dbgsettings net hostip:192.168.1.10 port:50000 key:1.2.3.4

# Restart computer
```

### Connect with WinDbg

```
# On host machine, connect to target
windbg -k net:port=50000,key=1.2.3.4,target=192.168.1.20
```

### Useful WinDbg Commands

```
# Show CXL device tree
!devnode 0 1 CXL

# Show device stack for a device
!devstack <device>

# Show PCI configuration space
!pci <bus> <dev> <func>

# Display data structures
dt CXL_CACHE_INFO
dt CXL_TOPOLOGY

# Trace ETW events
!wmitrace "CXL*"

# Set breakpoint
bp CXLBus!CXLBus_EvtChildListEnumerate
```

### Enable Driver Verifier

```powershell
# Enable for CXL drivers
verifier /standard /driver PhysMem.sys CXLBus.sys CXLCache.sys CXLMemory.sys CXLAccel.sys
```

## IOCTL Interface

All drivers communicate with user-mode applications via IOCTL. See `include/cxlioctl.h` for definitions.

### Common IOCTLs

| IOCTL | Code | Purpose |
|-------|------|---------|
| `IOCTL_CXL_GET_TOPOLOGY` | 0x800 | Get CXL bus topology |
| `IOCTL_CXL_TRIGGER_WBINVD` | 0x810 | Trigger cache flush |
| `IOCTL_PHYSMEM_MAP_MEMORY` | 0x900 | Map physical memory |
| `IOCTL_PHYSMEM_UNMAP_MEMORY` | 0x901 | Unmap physical memory |

### Device Names

| Driver | Device Name | Symbolic Link |
|--------|-------------|---------------|
| CXLBus | `\Device\CXLBus0` | `\\?\CXLBus0` |
| CXLCache | `\Device\CXLCache0` | `\\?\CXLCache0` |
| CXLMemory | `\Device\CXLMemory0` | `\\?\CXLMemory0` |
| CXLAccel | `\Device\CXLAccel0` | `\\?\CXLAccel0` |
| PhysMem | `\Device\PhysMem` | `\\?\PhysMem` |

## Registry Keys

Driver configuration is stored in the Windows Registry:

```
HKLM\SYSTEM\CurrentControlSet\Services\PhysMem\Parameters
HKLM\SYSTEM\CurrentControlSet\Services\CXLBus\Parameters
HKLM\SYSTEM\CurrentControlSet\Services\CXLCache\Parameters
HKLM\SYSTEM\CurrentControlSet\Services\CXLMemory\Parameters
HKLM\SYSTEM\CurrentControlSet\Services\CXLAccel\Parameters
```

## Troubleshooting

### Drivers Won't Load

1. Check test signing is enabled: `bcdedit /set testsigning on`
2. Verify drivers are signed: `signtool verify /pa /v driver.sys`
3. Check Event Viewer for error messages
4. Verify system architecture matches (x64 only)

### Device Not Detected

1. Verify CXL is enabled in BIOS
2. Check Device Manager for unknown devices
3. Verify PCIe device is visible: `Get-PnpDevice | Where-Object {$_.FriendlyName -like "*CXL*"}`
4. Check Windows Update for driver updates

### Memory Access Errors

1. Verify physical address is within CXL window
2. Check PhysMem driver security settings
3. Verify administrator privileges
4. Check for address space conflicts

## Security Considerations

⚠️ **WARNING**: These drivers provide direct access to physical memory.

- Only install on trusted systems
- Require administrator privileges
- Validate all IOCTL parameters
- Audit all memory access requests
- Restrict to CXL window ranges only
- Enable Driver Verifier during development

## Certification

### HLK Testing

For production deployment, drivers must pass Windows Hardware Lab (HLK) tests:

```powershell
# Install HLK client on test machine
# Configure HLK controller
# Run required tests:
# - Device Enumeration Tests
# - PCI Compliance Tests
# - Driver Verifier Tests
# - Power Management Tests
# - I/O Tests
# - Security Tests
```

### WHQL Submission

1. Complete HLK testing successfully
2. Generate HLK package (.hckx)
3. Submit to Windows Hardware Developer Center
4. Obtain WHQL signature
5. Re-sign drivers with WHQL certificate

## License

See LICENSE file in the repository root.

## Support

For issues, questions, or contributions:
- GitHub Issues: [CXLAgent/issues]
- Documentation: [CXLAgent/docs]

## References

- [CXL Specification](https://www.computeexpresslink.org/)
- [Windows Driver Kit Documentation](https://learn.microsoft.com/en-us/windows-hardware/drivers/)
- [KMDF Documentation](https://learn.microsoft.com/en-us/windows-hardware/drivers/wdf/)
- [ETW Documentation](https://learn.microsoft.com/en-us/windows/win32/etw/event-tracing-portal)
