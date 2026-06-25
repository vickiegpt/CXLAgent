#++
#   CXL Windows Driver Example - PowerShell
#
#   This example demonstrates how to interact with CXL drivers
#   from PowerShell using the cxlagent Python modules.
#
#   Requirements:
#   - Windows 11 with CXL drivers installed
#   - Python 3.10+
#   - Administrator privileges
#
#   Usage:
#       .\example_cxl.ps1
#--

#Requires -RunAsAdministrator

Write-Host "CXL Windows Driver Examples - PowerShell" -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host ""

# Check Python installation
Write-Host "Checking Python installation..." -ForegroundColor Gray
$python = Get-Command python -ErrorAction SilentlyContinue

if (-not $python) {
    Write-Warning "Python not found. Please install Python 3.10+"
    exit 1
}

Write-Host "✓ Python found at $($python.Source)" -ForegroundColor Green
Write-Host ""

# Function to run Python examples
function Run-PythonExample {
    param(
        [string]$ScriptPath,
        [string]$Description
    )

    Write-Host "`n$Description" -ForegroundColor Cyan
    Write-Host "-" * 60 -ForegroundColor Gray

    try {
        & python $ScriptPath
    }
    catch {
        Write-Error "Error running example: $_"
    }
}

# Example 1: Check Driver Status
Write-Host "`nExample 1: Check Driver Status" -ForegroundColor Cyan
Write-Host "-" * 60 -ForegroundColor Gray

$pythonCode = @'
import sys
sys.path.insert(0, "../..")
from cxlagent.registry import list_installed_drivers, get_driver_status

drivers = list_installed_drivers()
if drivers:
    print("Installed CXL Drivers:")
    for driver in drivers:
        print(f"  - {driver}")
else:
    print("No CXL drivers installed")
'@

& python -c $pythonCode

# Example 2: Get Memory Windows
Write-Host "`nExample 2: Get Memory Windows" -ForegroundColor Cyan
Write-Host "-" * 60 -ForegroundColor Gray

$pythonCode = @'
import sys
sys.path.insert(0, "../..")
from cxlagent.capture_windows import CxlWindowsDriver

try:
    driver = CxlWindowsDriver()
    driver.open()

    windows = driver.get_memory_windows()
    print(f"Found {len(windows)} memory windows:")

    for window in windows:
        print(f"  Window {window.index}:")
        print(f"    Address: 0x{window.start_physical_address:x} - 0x{window.end_physical_address:x}")
        print(f"    Size: {window.size // (1024**3)} GB")
        print(f"    Type: {'Persistent' if window.is_persistent else 'Volatile'}")

    driver.close()
except Exception as e:
    print(f"Error: {e}")
'@

& python -c $pythonCode

# Example 3: Get CXL Topology
Write-Host "`nExample 3: Get CXL Topology" -ForegroundColor Cyan
Write-Host "-" * 60 -ForegroundColor Gray

$pythonCode = @'
import sys
sys.path.insert(0, "../..")
from cxlagent.capture_windows import CxlWindowsDriver

try:
    driver = CxlWindowsDriver()
    driver.open()

    topology = driver.get_topology()
    print("CXL Bus Topology:")
    print(f"  Cache Devices:    {topology.cache_device_count}")
    print(f"  Memory Devices:   {topology.memory_device_count}")
    print(f"  Accelerators:     {topology.accelerator_device_count}")
    print(f"  Total Devices:    {topology.total_device_count}")

    driver.close()
except Exception as e:
    print(f"Error: {e}")
'@

& python -c $pythonCode

# Example 4: Read Memory
Write-Host "`nExample 4: Read Memory" -ForegroundColor Cyan
Write-Host "-" * 60 -ForegroundColor Gray

$pythonCode = @'
import sys
sys.path.insert(0, "../..")
from cxlagent.capture_windows import CxlWindowsDriver
from cxlagent.memory_windows import CxlMemoryReader

try:
    driver = CxlWindowsDriver()
    driver.open()

    reader = CxlMemoryReader(driver)
    reader.open()

    # Get memory windows
    windows = driver.get_memory_windows()
    if windows:
        # Read first page
        addr = windows[0].start_physical_address
        size = 4096

        print(f"Reading {size} bytes from 0x{addr:x}...")
        data = reader.read(addr, size)
        print(f"✓ Read {len(data)} bytes")

        # Show first 32 bytes
        print(f"First 32 bytes:")
        for i in range(0, min(32, len(data)), 16):
            hex_bytes = " ".join(f"{b:02x}" for b in data[i:i+16])
            print(f"  {addr + i:08x}:  {hex_bytes}")

    reader.close()
    driver.close()

except Exception as e:
    print(f"Error: {e}")
'@

& python -c $pythonCode

# Example 5: Registry Configuration
Write-Host "`nExample 5: Registry Configuration" -ForegroundColor Cyan
Write-Host "-" * 60 -ForegroundColor Gray

$pythonCode = @'
import sys
sys.path.insert(0, "../..")
from cxlagent.registry import get_cxl_registry

try:
    registry = get_cxl_registry()

    # Read memory configuration
    mem_config = registry.read_memory_config()
    print("Memory Configuration:")
    print(f"  Total Size:  {mem_config.total_size // (1024**3)} GB")
    print(f"  RAM Size:    {mem_config.ram_size // (1024**3)} GB")
    print(f"  PMEM Size:   {mem_config.pmem_size // (1024**3)} GB")
    print(f"  NUMA Node:   {mem_config.numa_node}")

except Exception as e:
    print(f"Error: {e}")
'@

& python -c $pythonCode

# Example 6: Check Device Manager
Write-Host "`nExample 6: Check Device Manager" -ForegroundColor Cyan
Write-Host "-" * 60 -ForegroundColor Gray

Write-Host "Checking CXL devices in Device Manager..." -ForegroundColor Gray

# Check for CXL devices using pnputil
try {
    $devices = Get-PnpDevice | Where-Object {
        $_.FriendlyName -like "*CXL*" -or
        $_.FriendlyName -like "*cxl*"
    }

    if ($devices) {
        Write-Host "Found CXL devices:" -ForegroundColor Green
        $devices | Format-Table FriendlyName, Status, InstanceId -AutoSize
    }
    else {
        Write-Host "No CXL devices found in Device Manager" -ForegroundColor Yellow
        Write-Host "This is expected if:" -ForegroundColor Gray
        Write-Host "  - Drivers are not installed" -ForegroundColor Gray
        Write-Host "  - Using simulated environment" -ForegroundColor Gray
        Write-Host "  - Hardware not connected" -ForegroundColor Gray
    }
}
catch {
    Write-Warning "Could not query Device Manager: $_"
}

# Example 7: Driver Service Status
Write-Host "`nExample 7: Driver Service Status" -ForegroundColor Cyan
Write-Host "-" * 60 -ForegroundColor Gray

$services = @("PhysMem", "CXLBus", "CXLCache", "CXLMemory", "CXLAccel")

Write-Host "CXL Driver Services:" -ForegroundColor Gray

foreach ($service in $services) {
    try {
        $status = sc.exe query $service 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  ✓ $service" -ForegroundColor Green
        }
        else {
            Write-Host "  ✗ $service (not installed)" -ForegroundColor Red
        }
    }
    catch {
        Write-Host "  ? $service (unknown)" -ForegroundColor Yellow
    }
}

Write-Host "`n" + "=" * 60 -ForegroundColor Cyan
Write-Host "Examples Complete" -ForegroundColor Cyan
Write-Host "=" * 60 -ForegroundColor Cyan
Write-Host "`nFor more examples, run: python example_capture.py" -ForegroundColor Gray
