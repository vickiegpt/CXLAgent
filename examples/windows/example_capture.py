#!/usr/bin/env python3
"""
CXL Memory Capture Example - Windows

This example demonstrates how to use the CXL Windows drivers
to capture memory snapshots from CXL devices.

Requirements:
- Windows 11 with CXL drivers installed
- Administrator privileges
- CXL FPGA hardware (or simulated environment)

Usage:
    python example_capture.py
"""

import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from cxlagent.capture_windows import CxlWindowsDriver
from cxlagent.memory_windows import CxlMemoryReader
from cxlagent.trace_windows import CxlETWConsumer
from cxlagent.registry import get_cxl_registry
from cxlagent.platform import print_platform_info


def example_driver_communication():
    """Example: Communicate with CXL drivers via IOCTL."""
    print("\n" + "=" * 60)
    print("Example 1: Driver Communication")
    print("=" * 60)

    try:
        # Create and open driver
        driver = CxlWindowsDriver()
        driver.open()

        print("✓ Driver opened successfully")

        # Get topology
        topology = driver.get_topology()
        print(f"\nCXL Topology:")
        print(f"  Cache Devices:    {topology.cache_device_count}")
        print(f"  Memory Devices:   {topology.memory_device_count}")
        print(f"  Accelerators:     {topology.accelerator_device_count}")
        print(f"  Total Devices:    {topology.total_device_count}")

        # Get memory windows
        windows = driver.get_memory_windows()
        print(f"\nMemory Windows:")
        for window in windows:
            print(f"  Window {window.index}:")
            print(f"    Address: 0x{window.start_physical_address:x} - 0x{window.end_physical_address:x}")
            print(f"    Size:    {window.size // (1024*1024)} MB")
            print(f"    Type:    {'Persistent' if window.is_persistent else 'Volatile'}")

        # Validate address
        test_addr = windows[0].start_physical_address if windows else 0x100000000
        is_valid = driver.validate_address(test_addr, 4096)
        print(f"\nAddress Validation:")
        print(f"  0x{test_addr:x} is {'valid' if is_valid else 'invalid'}")

        # Close driver
        driver.close()
        print("\n✓ Driver closed successfully")

    except Exception as e:
        print(f"\n✗ Error: {e}")


def example_memory_reading():
    """Example: Read physical memory via CXL drivers."""
    print("\n" + "=" * 60)
    print("Example 2: Memory Reading")
    print("=" * 60)

    try:
        # Create and open driver
        driver = CxlWindowsDriver()
        driver.open()

        # Create memory reader
        reader = CxlMemoryReader(driver)
        reader.open()

        print("✓ Memory reader opened successfully")

        # Get memory windows
        windows = driver.get_memory_windows()
        if not windows:
            print("No memory windows available")
            return

        # Read first page of first window
        test_addr = windows[0].start_physical_address
        test_size = 4096  # 4KB

        print(f"\nReading memory:")
        print(f"  Address: 0x{test_addr:x}")
        print(f"  Size:    {test_size} bytes")

        data = reader.read(test_addr, test_size)

        print(f"\n✓ Read {len(data)} bytes successfully")

        # Display first 64 bytes in hex
        print(f"\nFirst 64 bytes (hex):")
        for i in range(0, min(64, len(data)), 16):
            hex_bytes = " ".join(f"{b:02x}" for b in data[i:i+16])
            ascii = "".join(chr(b) if 32 <= b < 127 else "." for b in data[i:i+16])
            print(f"  {test_addr + i:08x}:  {hex_bytes:48s}  {ascii}")

        # Close reader and driver
        reader.close()
        driver.close()

        print("\n✓ Memory reader closed successfully")

    except Exception as e:
        print(f"\n✗ Error: {e}")


def example_cache_control():
    """Example: Control CXL cache."""
    print("\n" + "=" * 60)
    print("Example 3: Cache Control")
    print("=" * 60)

    try:
        # This example requires the CXLCache driver
        print("Note: CXLCache driver must be installed")

        # Check cache configuration from registry
        registry = get_cxl_registry()
        cache_config = registry.read_cache_config()

        print(f"\nCache Configuration:")
        print(f"  Size:            {cache_config.cache_size // (1024*1024)} MB")
        print(f"  WBINVD Support:  {cache_config.wbinvd_supported}")
        print(f"  Auto Flush:      {cache_config.auto_flush}")
        print(f"  Disabled:        {cache_config.cache_disabled}")
        print(f"  Invalid:         {cache_config.cache_invalid}")

        # Trigger WBINVD (if driver is available)
        print(f"\nTriggering WBINVD...")
        # In a real implementation with hardware:
        # driver = CxlWindowsDriver()
        # driver.open()
        # driver.trigger_wbinvd()
        print("  (Simulated - requires actual CXLCache driver)")

    except Exception as e:
        print(f"\n✗ Error: {e}")


def example_registry_access():
    """Example: Access CXL configuration from Registry."""
    print("\n" + "=" * 60)
    print("Example 4: Registry Configuration")
    print("=" * 60)

    try:
        from cxlagent.registry import list_installed_drivers, get_driver_status

        # List installed drivers
        print("\nInstalled CXL Drivers:")
        drivers = list_installed_drivers()
        for driver in drivers:
            print(f"  ✓ {driver}")

        if not drivers:
            print("  (No CXL drivers installed)")

        # Get detailed status for each driver
        for driver in drivers:
            try:
                status = get_driver_status(driver)
                print(f"\n{driver.capitalize()} Driver Status:")
                print(f"  Start Type:      {status.get('start_type', 'N/A')}")
                print(f"  Error Control:   {status.get('error_control', 'N/A')}")
                print(f"  Service Type:    {status.get('service_type', 'N/A')}")
                print(f"  Image Path:      {status.get('image_path', 'N/A')}")

                params = status.get('parameters', {})
                if params:
                    print(f"  Parameters:")
                    for name, value in params.items():
                        print(f"    {name}: {value}")

            except Exception as e:
                print(f"\n{driver.capitalize()}: Error reading status: {e}")

    except Exception as e:
        print(f"\n✗ Error: {e}")


def example_etw_tracing():
    """Example: Collect CXL trace events via ETW."""
    print("\n" + "=" * 60)
    print("Example 5: ETW Trace Collection")
    print("=" * 60)

    try:
        # Create ETW consumer
        consumer = CxlETWConsumer()
        consumer.enable()

        print("✓ ETW tracing enabled")

        print("\nCollecting events for 2 seconds...")

        # Read some events
        events = consumer.read_events(max_events=10)

        print(f"\nCollected {len(events)} events:")

        for event in events[:5]:  # Show first 5
            print(f"\n  Event: {event.event_type}")
            print(f"    Timestamp:  {event.timestamp:.3f}")
            print(f"    CPU:        {event.cpu}")
            print(f"    PID:        {event.pid}")
            print(f"    Device:     {event.memdev}")
            print(f"    Transaction: {event.transaction_type}")
            print(f"    DPA:        0x{event.dpa:x}")
            print(f"    HPA:        0x{event.hpa:x}")

        # Disable tracing
        consumer.disable()
        print("\n✓ ETW tracing disabled")

    except Exception as e:
        print(f"\n✗ Error: {e}")


def example_snapshot_workflow():
    """Example: Complete snapshot workflow."""
    print("\n" + "=" * 60)
    print("Example 6: Complete Snapshot Workflow")
    print("=" * 60)

    try:
        # This demonstrates the complete workflow for taking a memory snapshot
        # similar to what the Linux version does

        print("\nWorkflow Steps:")
        print("  1. Open CXL driver")
        print("  2. Discover memory windows")
        print("  3. Trigger WBINVD (cache flush)")
        print("  4. Capture memory regions")
        print("  5. Process captured data")
        print("  6. Close driver")

        # Simulated workflow
        print("\nExecuting workflow...")

        # Step 1: Open driver
        driver = CxlWindowsDriver()
        driver.open()
        print("  ✓ Driver opened")

        # Step 2: Discover memory windows
        windows = driver.get_memory_windows()
        print(f"  ✓ Found {len(windows)} memory windows")

        # Step 3: Trigger WBINVD
        print("  ✓ Triggered WBINVD (simulated)")

        # Step 4: Capture memory
        reader = CxlMemoryReader(driver)
        reader.open()

        snapshot = {}
        for window in windows:
            # Capture first page of each window
            data = reader.read(window.start_physical_address, 4096)
            snapshot[window.start_physical_address] = data

        print(f"  ✓ Captured {len(snapshot)} regions")

        # Step 5: Process
        total_bytes = sum(len(d) for d in snapshot.values())
        print(f"  ✓ Processed {total_bytes} bytes")

        # Step 6: Close
        reader.close()
        driver.close()
        print("  ✓ Driver closed")

        print(f"\nSnapshot Complete: {len(snapshot)} regions, {total_bytes} bytes")

    except Exception as e:
        print(f"\n✗ Error: {e}")


def main():
    """Run all examples."""
    print("=" * 60)
    print("CXL Windows Driver Examples")
    print("=" * 60)

    # Show platform information
    print("\nPlatform Information:")
    print_platform_info()

    # Check if drivers are available
    try:
        from cxlagent.capture_windows import is_available as driver_available
        available = driver_available()
        print(f"\nDriver Status: {'Available' if available else 'Not Available'}")
    except Exception as e:
        print(f"\nDriver Status: Error - {e}")

    print("\n" + "=" * 60)
    print("Running Examples")
    print("=" * 60)

    # Run each example
    examples = [
        ("Driver Communication", example_driver_communication),
        ("Memory Reading", example_memory_reading),
        ("Cache Control", example_cache_control),
        ("Registry Access", example_registry_access),
        ("ETW Tracing", example_etw_tracing),
        ("Snapshot Workflow", example_snapshot_workflow),
    ]

    for name, func in examples:
        try:
            func()
        except Exception as e:
            print(f"\n✗ {name} failed: {e}")

    print("\n" + "=" * 60)
    print("Examples Complete")
    print("=" * 60)


if __name__ == "__main__":
    main()
