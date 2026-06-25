# CXL Windows Examples

This directory contains example scripts demonstrating how to use the CXL Windows drivers and Python modules.

## Prerequisites

### Software Requirements
- **Windows 11** (Build 22621 or later)
- **Python 3.10+**
- **CXL Drivers installed** (see `../../tools/install_drivers.ps1`)
- **Administrator privileges**

### Hardware Requirements
- **CXL Type 2 FPGA Accelerator** (or simulated environment)
- **Motherboard with CXL support**
- **At least 16GB RAM**

## Examples

### Python Examples

#### example_capture.py
Comprehensive Python example demonstrating all CXL driver features:

```bash
python example_capture.py
```

**Demonstrates:**
1. Driver communication via IOCTL
2. Memory reading from CXL windows
3. Cache control (WBINVD trigger)
4. Registry configuration access
5. ETW trace event collection
6. Complete snapshot workflow

### PowerShell Examples

#### example_cxl.ps1
PowerShell example script for Windows administrators:

```powershell
# Must run as Administrator
.\example_cxl.ps1
```

**Demonstrates:**
1. Checking driver installation status
2. Querying memory windows
3. Getting CXL topology
4. Reading memory
5. Registry configuration
6. Device Manager status
7. Driver service status

## Quick Start

### 1. Verify Driver Installation

```python
from cxlagent.registry import list_installed_drivers

drivers = list_installed_drivers()
print(f"Installed drivers: {drivers}")
```

### 2. Open Driver and Read Memory

```python
from cxlagent.capture_windows import CxlWindowsDriver
from cxlagent.memory_windows import CxlMemoryReader

# Open driver
driver = CxlWindowsDriver()
driver.open()

# Create memory reader
reader = CxlMemoryReader(driver)
reader.open()

# Read 4KB from CXL memory
data = reader.read(0x100000000, 4096)

# Close
reader.close()
driver.close()
```

### 3. Capture Snapshot

```python
from cxlagent.capture_unified import CxlCapture

with CxlCapture() as capture:
    # Trigger cache flush
    capture.trigger_wbinvd()

    # Capture all memory windows
    snapshot = capture.capture_snapshot()

    # Process captured data
    for addr, data in snapshot.items():
        print(f"Captured {len(data)} bytes from 0x{addr:x}")
```

## Example Workflows

### Memory Snapshot Workflow

```python
from cxlagent.capture_windows import CxlWindowsDriver
from cxlagent.memory_windows import CxlMemoryReader

def capture_snapshot():
    """Capture a complete CXL memory snapshot."""

    # 1. Open driver
    driver = CxlWindowsDriver()
    driver.open()

    # 2. Get memory windows
    windows = driver.get_memory_windows()
    print(f"Found {len(windows)} memory windows")

    # 3. Create memory reader
    reader = CxlMemoryReader(driver)
    reader.open()

    # 4. Trigger cache flush
    driver.trigger_wbinvd()
    print("Cache flushed")

    # 5. Capture each window
    snapshot = {}
    for window in windows:
        addr = window.start_physical_address
        size = window.size

        print(f"Capturing window {window.index}...")
        data = reader.read(addr, size)
        snapshot[addr] = data

    # 6. Close
    reader.close()
    driver.close()

    print(f"Captured {len(snapshot)} windows")
    return snapshot
```

### Event Monitoring Workflow

```python
from cxlagent.trace_windows import CxlETWConsumer
import time

def monitor_events(duration_seconds=60):
    """Monitor CXL trace events via ETW."""

    # Create ETW consumer
    consumer = CxlETWConsumer()
    consumer.enable()

    print(f"Monitoring CXL events for {duration_seconds} seconds...")

    start_time = time.time()
    event_count = 0

    while time.time() - start_time < duration_seconds:
        events = consumer.read_events(max_events=100)

        for event in events:
            event_count += 1
            print(f"Event: {event.event_type}, "
                  f"Device: {event.memdev}, "
                  f"Transaction: {event.transaction_type}")

        time.sleep(1)

    consumer.disable()
    print(f"Collected {event_count} total events")
```

### Configuration Workflow

```python
from cxlagent.registry import get_cxl_registry, write_cache_disabled

def configure_cxl():
    """Configure CXL drivers via Registry."""

    registry = get_cxl_registry()

    # Read current configuration
    cache_config = registry.read_cache_config()
    print(f"Cache size: {cache_config.cache_size} bytes")
    print(f"WBINVD supported: {cache_config.wbinvd_supported}")

    # Modify configuration
    registry.write_cache_disabled(False)
    print("Cache enabled")

    # Read memory configuration
    mem_config = registry.read_memory_config()
    print(f"Total memory: {mem_config.total_size} bytes")
```

## Advanced Examples

### Pattern Detection on Captured Memory

```python
from cxlagent.capture_unified import CxlCapture
from cxlagent.patterns import PatternDetector

def detect_patterns_in_snapshot():
    """Detect patterns in a CXL memory snapshot."""

    with CxlCapture() as capture:
        # Capture snapshot
        snapshot = capture.capture_snapshot()

        # Analyze with pattern detector
        detector = PatternDetector()

        for addr, data in snapshot.items():
            print(f"Analyzing 0x{addr:x}...")

            # Detect encryption keys
            keys = detector.find_aes_keys(data)
            if keys:
                print(f"  Found {len(keys)} potential AES keys")

            # Detect pointers
            pointers = detector.find_pointer_chains(data)
            if pointers:
                print(f"  Found {len(pointers)} pointer chains")

            # Calculate entropy
            entropy = detector.calculate_entropy(data)
            print(f"  Entropy: {entropy:.2f}")
```

### Batch Memory Capture

```python
from cxlagent.capture_windows import CxlWindowsDriver
from cxlagent.memory_windows import CxlMemoryReader
import json

def batch_capture(regions, output_file):
    """Capture multiple memory regions to a file."""

    driver = CxlWindowsDriver()
    driver.open()

    reader = CxlMemoryReader(driver)
    reader.open()

    results = []

    for region in regions:
        addr = region['address']
        size = region['size']

        data = reader.read(addr, size)

        results.append({
            'address': addr,
            'size': size,
            'data': data.hex(),  # Store as hex string
            'sha256': hashlib.sha256(data).hexdigest()
        })

    reader.close()
    driver.close()

    # Write results
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)

    print(f"Captured {len(results)} regions to {output_file}")
```

## Troubleshooting

### "Driver not available" Error

**Problem:** Python cannot find the CXL drivers.

**Solution:**
1. Check driver installation: `sc query PhysMem`
2. Verify test signing: `bcdedit | findstr testsigning`
3. Reinstall drivers: `.\..\..\tools\install_drivers.ps1`

### "Access Denied" Error

**Problem:** Insufficient privileges.

**Solution:**
1. Run PowerShell/Command Prompt as Administrator
2. Verify User Account Control (UAC) settings

### "No memory windows found"

**Problem:** CXL memory windows not discovered.

**Solution:**
1. Check if CXL hardware is connected
2. Verify CXLBus driver is running
3. Check Device Manager for CXL devices

## Performance Considerations

### Memory Reading Performance

```python
# For large reads, use chunked reading
def read_large_memory(address, size, chunk_size=1024*1024):
    """Read large memory region in chunks."""

    reader = CxlMemoryReader(driver)
    reader.open()

    data = bytearray()
    remaining = size
    current = address

    while remaining > 0:
        chunk_bytes = min(chunk_size, remaining)
        chunk_data = reader.read(current, chunk_bytes)
        data.extend(chunk_data)

        current += chunk_bytes
        remaining -= chunk_bytes

    reader.close()
    return bytes(data)
```

### Caching Memory Mappings

```python
# Reuse memory reader for multiple reads
class MemoryCache:
    """Cache memory reader for multiple operations."""

    def __init__(self):
        self.driver = CxlWindowsDriver()
        self.driver.open()
        self.reader = CxlMemoryReader(self.driver)
        self.reader.open()

    def read(self, address, size):
        return self.reader.read(address, size)

    def close(self):
        self.reader.close()
        self.driver.close()
```

## Support

For issues or questions:

1. Check `../../drivers/README.md` for driver issues
2. Check `../../tools/README.md` for installation issues
3. Review Python module docstrings for API details
4. Check implementation status in `../../WINDOWS_PORT_STATUS.md`

## License

See LICENSE file in repository root.
