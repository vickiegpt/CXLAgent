#++
#   Copyright (c) 2025 CXLAgent Project
#
#   Module Name:
#
#       sign_drivers.ps1
#
#   Abstract:
#
#       PowerShell script to sign CXL drivers for Windows
#
#   Usage:
#       .\sign_drivers.ps1 [-BuildPath <path>] [-CertificateName <name>]
#--

<#
.SYNOPSIS
    Sign CXL drivers for Windows

.DESCRIPTION
    This script signs CXL driver files with the specified certificate.
    Supports both test certificates and production EV certificates.

.PARAMETER BuildPath
    Path to the driver build files (default: ..\drivers)

.PARAMETER CertificateName
    Name of the certificate to use for signing (default: CXLTestCert)

.PARAMETER TimestampServer
    Timestamp server URL (default: http://timestamp.digicert.com)

.PARAMETER CreateTestCert
    Create a new test certificate if one doesn't exist

.PARAMETER Verify
    Only verify existing signatures without signing

.EXAMPLE
    .\sign_drivers.ps1

.EXAMPLE
    .\sign_drivers.ps1 -CreateTestCert

.EXAMPLE
    .\sign_drivers.ps1 -CertificateName "MyProductionCert"
#>

param(
    [string]$BuildPath = "..\drivers",
    [string]$CertificateName = "CXLTestCert",
    [string]$TimestampServer = "http://timestamp.digicert.com",
    [switch]$CreateTestCert,
    [switch]$Verify
)

# Require administrator privileges
if (-NOT ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    Write-Error "This script must be run as Administrator"
    exit 1
}

Write-Host "CXL Driver Signing Script" -ForegroundColor Cyan
Write-Host "=========================" -ForegroundColor Cyan
Write-Host ""

# Resolve build path
$BuildPath = Resolve-Path $BuildPath -ErrorAction Stop
Write-Host "Build Path: $BuildPath" -ForegroundColor Gray
Write-Host "Certificate: $CertificateName" -ForegroundColor Gray
Write-Host ""

# Find all .sys files
$driverFiles = Get-ChildItem -Path $BuildPath -Recurse -Filter "*.sys"

if ($driverFiles.Count -eq 0) {
    Write-Error "No driver files (.sys) found in $BuildPath"
    exit 1
}

Write-Host "Found $($driverFiles.Count) driver files to sign:" -ForegroundColor Cyan
$driverFiles | ForEach-Object {
    Write-Host "  - $($_.FullName)" -ForegroundColor Gray
}
Write-Host ""

# Verify mode
if ($Verify) {
    Write-Host "Verifying signatures..." -ForegroundColor Cyan
    Write-Host ""

    foreach ($file in $driverFiles) {
        Write-Host "Checking $($file.Name)..." -ForegroundColor Gray

        $result = & signtool verify /pa /v $file.FullName 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  Signature: Valid" -ForegroundColor Green
        } else {
            Write-Host "  Signature: Invalid or missing" -ForegroundColor Red
            Write-Host "  $result" -ForegroundColor Gray
        }
    }

    exit 0
}

# Create test certificate if requested
if ($CreateTestCert) {
    Write-Host "Creating test certificate..." -ForegroundColor Cyan

    $certs = Get-ChildItem -Path Cert:\LocalMachine\TrustedPublisher | Where-Object { $_.Subject -like "*$CertificateName*" }

    if ($certs) {
        Write-Host "Test certificate already exists" -ForegroundColor Yellow
        $certs | ForEach-Object {
            Write-Host "  - $($_.Subject)" -ForegroundColor Gray
        }

        $response = Read-Host "Create new certificate? (Y/N)"
        if ($response -ne "Y" -and $response -ne "y") {
            Write-Host "Using existing certificate" -ForegroundColor Green
        }
    } else {
        Write-Host "Generating new test certificate..." -ForegroundColor Gray

        $certPath = "$env:TEMP\$CertificateName.cer"
        makecert -pe -ss PrivateCertStore -n "CN=$CertificateName" $certPath

        if ($LASTEXITCODE -eq 0) {
            Write-Host "Test certificate created successfully" -ForegroundColor Green
        } else {
            Write-Error "Failed to create test certificate"
            exit 1
        }
    }

    Write-Host ""
}

# Check if signtool is available
$signtoolPath = Get-Command signtool -ErrorAction SilentlyContinue
if (-not $signtoolPath) {
    Write-Error "signtool not found. Please install Windows SDK."
    exit 1
}

Write-Host "Using signtool from: $($signtoolPath.Source)" -ForegroundColor Gray
Write-Host ""

# Sign each driver
$success = $true
foreach ($file in $driverFiles) {
    Write-Host "Signing $($file.Name)..." -ForegroundColor Gray

    $result = & signtool sign /v /s PrivateCertStore /n $CertificateName /t $TimestampServer /fd sha256 $file.FullName 2>&1

    if ($LASTEXITCODE -eq 0) {
        Write-Host "  $($file.Name): Signed successfully" -ForegroundColor Green
    } else {
        Write-Error "  $($file.Name): Failed to sign"
        Write-Host "  $result" -ForegroundColor Gray
        $success = $false
    }
}

Write-Host ""

if ($success) {
    Write-Host "All drivers signed successfully!" -ForegroundColor Green
    Write-Host ""
    Write-Host "Next steps:" -ForegroundColor Cyan
    Write-Host "1. Verify signatures: .\sign_drivers.ps1 -Verify" -ForegroundColor Gray
    Write-Host "2. Install drivers: .\install_drivers.ps1" -ForegroundColor Gray
} else {
    Write-Error "Some drivers failed to sign. Check the output above for details."
    exit 1
}
