# CXLAgent Windows Tools

This directory contains PowerShell scripts for building, signing, and installing CXL drivers on Windows.

## Prerequisites

- **Windows 11** (Build 22621 or later)
- **Administrator privileges** (required for driver operations)
- **Windows SDK** (for signtool)
- **PowerShell** (included with Windows)

## Scripts

### install_drivers.ps1

Installs CXL kernel-mode drivers on Windows.

#### Usage

```powershell
# Basic installation
.\install_drivers.ps1

# Specify build path
.\install_drivers.ps1 -BuildPath "C:\build\drivers"

# Enable test signing mode
.\install_drivers.ps1 -TestMode

# Force reinstall
.\install_drivers.ps1 -Force
```

#### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `BuildPath` | String | Path to driver build files (default: `..\drivers`) |
| `TestMode` | Switch | Enable test signing mode if not enabled |
| `Force` | Switch | Reinstall drivers even if already installed |

#### What It Does

1. Checks for administrator privileges
2. Verifies/enables test signing mode
3. Installs drivers in dependency order:
   - PhysMem.sys (physical memory access)
   - CXLBus.sys (bus enumeration)
   - CXLCache.sys (cache control)
   - CXLMemory.sys (memory management)
   - CXLAccel.sys (accelerator control)
4. Scans for hardware changes
5. Verifies installation

#### Verification

After installation, verify drivers are loaded:

```powershell
# Check installed drivers
Get-WindowsDriver -Online | Where-Object { $_.ProviderName -like "*CXL*" }

# Check driver status
sc query PhysMem
sc query CXLBus
sc query CXLCache
sc query CXLMemory
sc query CXLAccel

# Check in Device Manager
devmgmt.msc
```

### sign_drivers.ps1

Signs CXL driver files with a code signing certificate.

#### Usage

```powershell
# Create test certificate and sign
.\sign_drivers.ps1 -CreateTestCert

# Sign with existing certificate
.\sign_drivers.ps1 -CertificateName "MyCert"

# Verify signatures only
.\sign_drivers.ps1 -Verify
```

#### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `BuildPath` | String | Path to driver build files (default: `..\drivers`) |
| `CertificateName` | String | Name of certificate (default: `CXLTestCert`) |
| `TimestampServer` | String | Timestamp server URL |
| `CreateTestCert` | Switch | Create new test certificate |
| `Verify` | Switch | Only verify existing signatures |

#### Test Certificate vs Production

**For Development/Testing:**
```powershell
# Enable test signing
bcdedit /set testsigning on

# Create test certificate
.\sign_drivers.ps1 -CreateTestCert

# Sign drivers
.\sign_drivers.ps1

# Restart computer
# Install drivers
.\install_drivers.ps1
```

**For Production:**
1. Obtain an EV Code Signing Certificate from a trusted CA
2. Complete WHQL/HLK certification
3. Sign with production certificate:
```powershell
.\sign_drivers.ps1 -CertificateName "CompanyName EV Cert"
```

## Workflow

### Development Workflow

```powershell
# 1. Build drivers in Visual Studio
# (Build -> Build Solution)

# 2. Create test certificate (first time only)
.\sign_drivers.ps1 -CreateTestCert

# 3. Sign drivers
.\sign_drivers.ps1

# 4. Enable test signing (first time only)
bcdedit /set testsigning on

# 5. Restart computer

# 6. Install drivers
.\install_drivers.ps1

# 7. Verify in Device Manager
devmgmt.msc
```

### Production Workflow

```powershell
# 1. Build drivers in Release configuration

# 2. Complete HLK testing
# (See drivers\README.md for HLK setup)

# 3. Sign with EV certificate
.\sign_drivers.ps1 -CertificateName "ProductionCert"

# 4. Verify signatures
.\sign_drivers.ps1 -Verify

# 5. Package for distribution
# (Create MSI or installer package)
```

## Troubleshooting

### "Test signing is not enabled"

```powershell
# Enable test signing
bcdedit /set testsigning on

# Restart computer
shutdown /r /t 0

# Run install script again
.\install_drivers.ps1 -TestMode
```

### "Driver signature verification failed"

```powershell
# Check test signing status
bcdedit | Select-String testsigning

# Should show "Yes"

# Verify driver signature
.\sign_drivers.ps1 -Verify
```

### "Drivers won't load"

```powershell
# Check Event Viewer for errors
Get-WinEvent -LogName System | Where-Object { $_.Message -like "*CXL*" }

# Enable Driver Verifier for debugging
verifier /standard /driver PhysMem.sys CXLBus.sys CXLCache.sys CXLMemory.sys CXLAccel.sys

# Restart computer
```

### "Device Manager shows unknown device"

```powershell
# Scan for hardware changes
pnputil /scan-devices

# Check for driver updates
# Windows Update may have better drivers

# Manually install driver for specific device
pnputil /add-driver drivers\CXLBus\CXLBus.inf /install
```

## Security Considerations

⚠️ **Important Security Notes:**

1. **Test Certificates**: Only use for development/testing
   - Never use test-signed drivers in production
   - Test certificates provide no security guarantees

2. **Physical Memory Access**: PhysMem.sys provides raw memory access
   - Only install on trusted systems
   - Requires administrator privileges
   - Audited in Windows Event Logs

3. **Driver Verification**: Always verify signatures before installation
   ```powershell
   .\sign_drivers.ps1 -Verify
   ```

4. **Certificate Management**: Protect EV certificates carefully
   - Store in secure hardware token (HSM)
   - Limit access to authorized personnel
   - Rotate certificates periodically

## Advanced Usage

### Installing Specific Driver

```powershell
# Install only PhysMem driver
pnputil /add-driver drivers\PhysMem\PhysMem.inf /install

# Install only CXLBus driver
pnputil /add-driver drivers\CXLBus\CXLBus.inf /install
```

### Uninstalling Drivers

```powershell
# Uninstall all CXL drivers
Get-WindowsDriver -Online | Where-Object { $_.ProviderName -like "*CXL*" } | ForEach-Object {
    pnputil /delete-driver $_.OriginalFileName /uninstall
}

# Or use Device Manager to uninstall specific devices
```

### Checking Driver Status

```powershell
# Check if driver is running
sc query PhysMem

# Check driver configuration
sc qc PhysMem

# Check driver events in Event Log
Get-WinEvent -LogName System -MaxEvents 100 | Where-Object { $_.ProviderName -like "*CXL*" }
```

## References

- [Driver Signing](https://learn.microsoft.com/en-us/windows-hardware/drivers/install/driver-signing)
- [Test Signing](https://learn.microsoft.com/en-us/windows-hardware/drivers/develop/test-signing)
- [PnPUtil](https://learn.microsoft.com/en-us/windows-hardware/drivers/devtest/pnputil-command-syntax)
- [SignTool](https://learn.microsoft.com/en-us/windows/win32/seccrypto/signtool)
