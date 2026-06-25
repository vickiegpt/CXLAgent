#++
#   Copyright (c) 2025 CXLAgent Project
#
#   Module Name:
#
#       install_drivers.ps1
#
#   Abstract:
#
#       PowerShell script to install CXL drivers on Windows
#
#   Usage:
#       .\install_drivers.ps1 [-BuildPath <path>] [-TestMode]
#--

<#
.SYNOPSIS
    Install CXL drivers on Windows

.DESCRIPTION
    This script installs the CXL kernel-mode drivers in the correct order.
    Drivers are installed via pnputil and the device stack is rescanned.

.PARAMETER BuildPath
    Path to the driver build files (default: ..\drivers)

.PARAMETER TestMode
    Enable test signing mode if not already enabled

.PARAMETER Force
    Force reinstall even if drivers are already installed

.EXAMPLE
    .\install_drivers.ps1

.EXAMPLE
    .\install_drivers.ps1 -BuildPath "C:\build\drivers" -TestMode

.EXAMPLE
    .\install_drivers.ps1 -Force
#>

param(
    [string]$BuildPath = "..\drivers",
    [switch]$TestMode,
    [switch]$Force
)

# Require administrator privileges
if (-NOT ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    Write-Error "This script must be run as Administrator"
    exit 1
}

Write-Host "CXL Driver Installation Script" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

# Resolve build path
$BuildPath = Resolve-Path $BuildPath -ErrorAction Stop
Write-Host "Build Path: $BuildPath" -ForegroundColor Gray

# Check for test signing
$testSigning = (bcdedit.exe | Select-String "testsigning" | Select-String -Pattern "Yes").Matches.Success
if (-not $testSigning) {
    Write-Warning "Test signing is not enabled"
    if ($TestMode) {
        Write-Host "Enabling test signing mode..." -ForegroundColor Yellow
        bcdedit.exe /set testsigning on | Out-Null
        Write-Host "Test signing enabled. Please restart the computer." -ForegroundColor Green
        Write-Host "After restart, run this script again." -ForegroundColor Green
        exit 0
    } else {
        Write-Host "Use -TestMode to enable test signing, or provide signed drivers." -ForegroundColor Yellow
    }
} else {
    Write-Host "Test signing: Enabled" -ForegroundColor Green
}

# Function to install a driver
function Install-Driver {
    param(
        [string]$DriverPath,
        [string]$DriverName
    )

    $infPath = Join-Path $BuildPath $DriverPath "$DriverName.inf"

    if (-not (Test-Path $infPath)) {
        Write-Warning "  $DriverName.inf not found at $infPath"
        return $false
    }

    Write-Host "  Installing $DriverName..." -ForegroundColor Gray

    try {
        $output = pnputil /add-driver $infPath /install 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  $DriverName installed successfully" -ForegroundColor Green
            return $true
        } else {
            if ($output -match "already installed") {
                if ($Force) {
                    Write-Host "  $DriverName already installed (use -Force to reinstall)" -ForegroundColor Yellow
                    return $true
                } else {
                    Write-Host "  $DriverName already installed" -ForegroundColor Yellow
                    return $true
                }
            } else {
                Write-Error "  Failed to install $DriverName"
                Write-Host "  Output: $output" -ForegroundColor Gray
                return $false
            }
        }
    } catch {
        Write-Error "  Error installing $DriverName: $_"
        return $false
    }
}

# Check if drivers are already installed
$installedDrivers = Get-WindowsDriver -Online | Where-Object { $_.ProviderName -like "*CXL*" }

if ($installedDrivers -and -not $Force) {
    Write-Host "Found existing CXL drivers:" -ForegroundColor Yellow
    $installedDrivers | ForEach-Object {
        Write-Host "  - $($_.Driver) (Version: $($_.Version))" -ForegroundColor Gray
    }

    $response = Read-Host "Reinstall drivers? (Y/N)"
    if ($response -ne "Y" -and $response -ne "y") {
        Write-Host "Installation cancelled." -ForegroundColor Yellow
        exit 0
    }
}

# Install drivers in dependency order
Write-Host ""
Write-Host "Installing drivers (in dependency order):" -ForegroundColor Cyan
Write-Host ""

$drivers = @(
    "PhysMem",
    "CXLBus",
    "CXLCache",
    "CXLMemory",
    "CXLAccel"
)

$success = $true
foreach ($driver in $drivers) {
    if (-not (Install-Driver -DriverPath $BuildPath -DriverName $driver)) {
        $success = $false
    }
}

if ($success) {
    Write-Host ""
    Write-Host "All drivers installed successfully!" -ForegroundColor Green
    Write-Host ""
    Write-Host "Scanning for hardware changes..." -ForegroundColor Gray
    pnputil /scan-devices | Out-Null

    Write-Host ""
    Write-Host "Installation complete. Check Device Manager for CXL devices." -ForegroundColor Green
    Write-Host ""
    Write-Host "Next steps:" -ForegroundColor Cyan
    Write-Host "1. Open Device Manager (devmgmt.msc)" -ForegroundColor Gray
    Write-Host "2. Look for 'CXL Devices' category" -ForegroundColor Gray
    Write-Host "3. Verify all drivers are loaded without errors" -ForegroundColor Gray
    Write-Host ""
    Write-Host "To verify:" -ForegroundColor Cyan
    Write-Host "  Get-WindowsDriver -Online | Where-Object { `$_.ProviderName -like '*CXL*' }" -ForegroundColor Gray
} else {
    Write-Host ""
    Write-Error "Some drivers failed to install. Check the output above for details."
    exit 1
}
