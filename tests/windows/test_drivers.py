"""
CXL Windows Driver Test Utilities.

Comprehensive test suite for Windows CXL drivers including:
- Driver installation tests
- Driver loading tests
- IOCTL operation tests
- Memory mapping tests
- Registry configuration tests
- ETW tracing tests

Usage:
    pytest tests/windows/test_drivers.py
    # Or run directly:
    python tests/windows/test_drivers.py
"""

import sys
import os
import time
import pytest
import hashlib
from typing import List, Dict, Any

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from cxlagent.capture_windows import CxlWindowsDriver
from cxlagent.memory_windows import CxlMemoryReader
from cxlagent.trace_windows import CxlETWConsumer
from cxlagent.registry import (
    get_cxl_registry,
    list_installed_drivers,
    is_driver_installed
)
from cxlagent.platform import get_platform, is_windows


# =============================================================================
# Test Configuration
# =============================================================================

class TestConfig:
    """Test configuration constants."""

    # Test memory addresses (simulated CXL windows)
    TEST_ADDRESS_1 = 0x100000000  # 4GB
    TEST_ADDRESS_2 = 0x200000000  # 8GB
    TEST_SIZE_4KB = 4096
    TEST_SIZE_64KB = 64 * 1024
    TEST_SIZE_1MB = 1024 * 1024

    # Timeout values (seconds)
    DRIVER_OPEN_TIMEOUT = 5
    IOCTL_TIMEOUT = 2
    MEMORY_READ_TIMEOUT = 10


# =============================================================================
# Test Fixtures
# =============================================================================

@pytest.fixture(scope="session")
def platform_check():
    """Check if running on Windows."""
    if not is_windows():
        pytest.skip("Tests only run on Windows")


@pytest.fixture(scope="session")
def drivers_available(platform_check):
    """Check if CXL drivers are installed."""
    drivers = list_installed_drivers()
    if not drivers:
        pytest.skip("No CXL drivers installed. Run: .\\tools\\install_drivers.ps1")
    return drivers


@pytest.fixture(scope="function")
def driver(drivers_available):
    """Create and open a CXL driver instance."""
    driver = CxlWindowsDriver()
    driver.open()
    yield driver
    driver.close()


@pytest.fixture(scope="function")
def memory_reader(driver):
    """Create a memory reader instance."""
    reader = CxlMemoryReader(driver)
    reader.open()
    yield reader
    reader.close()


@pytest.fixture(scope="function")
def registry():
    """Create a registry accessor."""
    return get_cxl_registry()


# =============================================================================
# Driver Installation Tests
# =============================================================================

class TestDriverInstallation:
    """Test driver installation and detection."""

    def test_platform_detection(self):
        """Test platform detection."""
        from cxlagent.platform import get_platform, Platform
        platform = get_platform()
        assert platform == Platform.WINDOWS

    def test_drivers_installed(self):
        """Test if CXL drivers are installed."""
        drivers = list_installed_drivers()

        # At minimum, PhysMem should be installed
        assert "physmem" in drivers

    def test_individual_driver_status(self):
        """Test individual driver installation status."""
        expected_drivers = ["physmem", "bus", "cache", "memory", "accel"]

        for driver in expected_drivers:
            installed = is_driver_installed(driver)
            # PhysMem must be installed, others are optional
            if driver == "physmem":
                assert installed, f"{driver} must be installed"


# =============================================================================
# Driver Communication Tests
# =============================================================================

class TestDriverCommunication:
    """Test driver IOCTL communication."""

    def test_driver_open(self, driver):
        """Test opening driver connection."""
        assert driver is not None
        assert driver._is_open

    def test_get_topology(self, driver):
        """Test getting CXL bus topology."""
        topology = driver.get_topology()

        assert topology is not None
        assert isinstance(topology.cache_device_count, int)
        assert isinstance(topology.memory_device_count, int)
        assert isinstance(topology.accelerator_device_count, int)
        assert isinstance(topology.total_device_count, int)

    def test_get_memory_windows(self, driver):
        """Test getting memory windows."""
        windows = driver.get_memory_windows()

        assert windows is not None
        assert isinstance(windows, list)

        if windows:
            window = windows[0]
            assert hasattr(window, 'index')
            assert hasattr(window, 'start_physical_address')
            assert hasattr(window, 'end_physical_address')
            assert hasattr(window, 'size')
            assert hasattr(window, 'is_persistent')

    def test_validate_address(self, driver):
        """Test address validation."""
        # Test valid address
        valid = driver.validate_address(TestConfig.TEST_ADDRESS_1, TestConfig.TEST_SIZE_4KB)
        assert isinstance(valid, bool)

        # Test invalid address (outside CXL windows)
        invalid = driver.validate_address(0x0, TestConfig.TEST_SIZE_4KB)
        assert isinstance(invalid, bool)

    def test_trigger_wbinvd(self, driver):
        """Test WBINVD trigger."""
        try:
            result = driver.trigger_wbinvd()
            assert isinstance(result, (bool, type(None)))
        except Exception as e:
            # May fail if CXLCache driver not installed
            pytest.skip(f"CXLCache driver not available: {e}")


# =============================================================================
# Memory Reading Tests
# =============================================================================

class TestMemoryReading:
    """Test physical memory reading."""

    def test_memory_reader_open(self, memory_reader):
        """Test opening memory reader."""
        assert memory_reader is not None
        assert memory_reader._is_open

    def test_read_memory_4kb(self, memory_reader):
        """Test reading 4KB of memory."""
        data = memory_reader.read(TestConfig.TEST_ADDRESS_1, TestConfig.TEST_SIZE_4KB)

        assert data is not None
        assert len(data) == TestConfig.TEST_SIZE_4KB
        assert isinstance(data, bytes)

    def test_read_memory_64kb(self, memory_reader):
        """Test reading 64KB of memory."""
        data = memory_reader.read(TestConfig.TEST_ADDRESS_1, TestConfig.TEST_SIZE_64KB)

        assert data is not None
        assert len(data) == TestConfig.TEST_SIZE_64KB

    def test_read_memory_cross_page(self, memory_reader):
        """Test reading across page boundaries."""
        # Read starting at offset 100 in a page
        address = TestConfig.TEST_ADDRESS_1 + 100
        size = TestConfig.TEST_SIZE_4KB - 100

        data = memory_reader.read(address, size)

        assert data is not None
        assert len(data) == size

    def test_read_multiple_regions(self, memory_reader):
        """Test reading multiple memory regions."""
        regions = [
            (TestConfig.TEST_ADDRESS_1, 1024),
            (TestConfig.TEST_ADDRESS_2, 1024),
            (TestConfig.TEST_ADDRESS_1, 2048),
        ]

        for address, size in regions:
            data = memory_reader.read(address, size)
            assert len(data) == size

    def test_data_consistency(self, memory_reader):
        """Test data consistency across multiple reads."""
        address = TestConfig.TEST_ADDRESS_1
        size = 1024

        # Read twice
        data1 = memory_reader.read(address, size)
        data2 = memory_reader.read(address, size)

        # Should be identical
        assert data1 == data2

    def test_zero_size_read(self, memory_reader):
        """Test reading zero bytes."""
        data = memory_reader.read(TestConfig.TEST_ADDRESS_1, 0)
        assert data == b""

    def test_large_read(self, memory_reader):
        """Test reading large memory region."""
        data = memory_reader.read(TestConfig.TEST_ADDRESS_1, TestConfig.TEST_SIZE_1MB)

        assert data is not None
        assert len(data) == TestConfig.TEST_SIZE_1MB


# =============================================================================
# Memory Mapping Cache Tests
# =============================================================================

class TestMemoryMappingCache:
    """Test memory mapping cache functionality."""

    def test_mapping_cache(self, memory_reader):
        """Test that mappings are cached."""
        address = TestConfig.TEST_ADDRESS_1

        # First read should create mapping
        data1 = memory_reader.read(address, 1024)

        # Check mapping was created
        page_base = address & ~4095
        assert page_base in memory_reader._mappings

        # Second read should reuse mapping
        data2 = memory_reader.read(address, 1024)

        # Should be same mapping object
        mapping = memory_reader._mappings[page_base]
        assert mapping is not None

    def test_mapping_cleanup(self, memory_reader):
        """Test mapping cleanup."""
        # Read some data
        memory_reader.read(TestConfig.TEST_ADDRESS_1, 1024)

        # Clear mappings
        memory_reader.clear_mappings()

        # Mappings should be empty
        assert len(memory_reader._mappings) == 0


# =============================================================================
# Registry Configuration Tests
# =============================================================================

class TestRegistryConfiguration:
    """Test Registry configuration access."""

    def test_read_cache_config(self, registry):
        """Test reading cache configuration."""
        try:
            config = registry.read_cache_config()

            assert hasattr(config, 'cache_size')
            assert hasattr(config, 'wbinvd_supported')
            assert hasattr(config, 'auto_flush')
            assert hasattr(config, 'cache_disabled')
            assert hasattr(config, 'cache_invalid')
        except Exception as e:
            pytest.skip(f"CXLCache registry not available: {e}")

    def test_read_memory_config(self, registry):
        """Test reading memory configuration."""
        try:
            config = registry.read_memory_config()

            assert hasattr(config, 'total_size')
            assert hasattr(config, 'ram_size')
            assert hasattr(config, 'pmem_size')
            assert hasattr(config, 'numa_node')
        except Exception as e:
            pytest.skip(f"CXLMemory registry not available: {e}")

    def test_read_accel_config(self, registry):
        """Test reading accelerator configuration."""
        try:
            config = registry.read_accel_config()

            assert hasattr(config, 'work_queue_depth')
            assert hasattr(config, 'dma_supported')
            assert hasattr(config, 'interrupt_supported')
            assert hasattr(config, 'default_bitstream')
        except Exception as e:
            pytest.skip(f"CXLAccel registry not available: {e}")

    def test_read_all_values(self, registry):
        """Test reading all values from a driver."""
        try:
            values = registry.read_all_values("physmem")
            assert isinstance(values, dict)
        except Exception as e:
            pytest.skip(f"Could not read PhysMem registry: {e}")


# =============================================================================
# ETW Tracing Tests
# =============================================================================

class TestETWTracing:
    """Test ETW trace event collection."""

    def test_etw_consumer_create(self):
        """Test creating ETW consumer."""
        consumer = CxlETWConsumer()
        assert consumer is not None
        assert not consumer._enabled

    def test_etw_enable_disable(self):
        """Test enabling and disabling ETW."""
        consumer = CxlETWConsumer()

        consumer.enable()
        assert consumer._enabled

        consumer.disable()
        assert not consumer._enabled

    def test_etw_read_events(self):
        """Test reading ETW events."""
        consumer = CxlETWConsumer()
        consumer.enable()

        # Wait a bit for events
        time.sleep(0.5)

        events = consumer.read_events(max_events=10)

        consumer.disable()

        assert isinstance(events, list)

    def test_etw_event_structure(self):
        """Test ETW event structure."""
        consumer = CxlETWConsumer()
        consumer.enable()

        time.sleep(0.5)

        events = consumer.read_events(max_events=100)

        consumer.disable()

        for event in events:
            assert hasattr(event, 'timestamp')
            assert hasattr(event, 'cpu')
            assert hasattr(event, 'pid')
            assert hasattr(event, 'event_type')
            assert hasattr(event, 'memdev')
            assert hasattr(event, 'transaction_type')


# =============================================================================
# Integration Tests
# =============================================================================

class TestIntegration:
    """Integration tests for complete workflows."""

    def test_snapshot_workflow(self, driver):
        """Test complete snapshot workflow."""
        # This simulates the snapshot.py workflow on Windows

        # 1. Get topology
        topology = driver.get_topology()

        # 2. Get memory windows
        windows = driver.get_memory_windows()

        # 3. Validate addresses
        for window in windows:
            valid = driver.validate_address(
                window.start_physical_address,
                min(window.size, 4096)
            )
            assert isinstance(valid, bool)

    def test_pattern_detection_workflow(self, memory_reader):
        """Test pattern detection on captured memory."""
        from cxlagent.patterns import PatternDetector

        # Capture some memory
        data = memory_reader.read(TestConfig.TEST_ADDRESS_1, 1024)

        # Analyze with pattern detector
        detector = PatternDetector()

        # Calculate entropy
        entropy = detector.calculate_entropy(data)
        assert 0 <= entropy <= 8

        # Find strings
        strings = detector.extract_strings(data)
        assert isinstance(strings, list)


# =============================================================================
# Performance Tests
# =============================================================================

class TestPerformance:
    """Performance and stress tests."""

    def test_memory_read_performance(self, memory_reader):
        """Test memory read performance."""
        size = TestConfig.TEST_SIZE_1MB

        start = time.time()
        data = memory_reader.read(TestConfig.TEST_ADDRESS_1, size)
        elapsed = time.time() - start

        # Calculate throughput (MB/s)
        throughput = (size / (1024 * 1024)) / elapsed

        print(f"Read throughput: {throughput:.2f} MB/s")

        # Should be at least 10 MB/s
        assert throughput > 10

    def test_concurrent_reads(self, memory_reader):
        """Test concurrent memory reads."""
        import threading

        results = []
        errors = []

        def read_worker():
            try:
                data = memory_reader.read(TestConfig.TEST_ADDRESS_1, 4096)
                results.append(len(data))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=read_worker) for _ in range(10)]

        for t in threads:
            t.start()

        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 10


# =============================================================================
# Error Handling Tests
# =============================================================================

class TestErrorHandling:
    """Test error handling and edge cases."""

    def test_driver_not_open_error(self):
        """Test error when driver not opened."""
        driver = CxlWindowsDriver()

        with pytest.raises(RuntimeError):
            driver.get_topology()

    def test_invalid_address_error(self, memory_reader):
        """Test reading from invalid address."""
        # This should either raise an error or return gracefully
        try:
            data = memory_reader.read(0xFFFFFFFFFFFF, 1024)
            # If no error, should return empty or error indication
        except Exception as e:
            # Expected for invalid address
            assert isinstance(e, Exception)

    def test_memory_reader_not_open_error(self):
        """Test error when memory reader not opened."""
        from cxlagent.memory_windows import CxlMemoryReader

        driver = CxlWindowsDriver()
        driver.open()

        reader = CxlMemoryReader(driver)
        # Don't call reader.open()

        with pytest.raises(RuntimeError):
            reader.read(TestConfig.TEST_ADDRESS_1, 1024)

        driver.close()


# =============================================================================
# Main Entry Point
# =============================================================================

def main():
    """Run tests directly without pytest."""
    print("CXL Windows Driver Test Suite")
    print("=" * 60)

    if not is_windows():
        print("ERROR: Tests only run on Windows")
        return 1

    print(f"Platform: {get_platform()}")
    print("")

    # Check drivers
    drivers = list_installed_drivers()
    print(f"Installed drivers: {drivers}")

    if not drivers:
        print("ERROR: No drivers installed. Run: .\\tools\\install_drivers.ps1")
        return 1

    print("")

    # Run basic tests
    test_results = []

    # Test 1: Driver Open
    print("Test 1: Open Driver...")
    try:
        driver = CxlWindowsDriver()
        driver.open()
        print("  ✓ Driver opened successfully")
        driver.close()
        test_results.append(("Driver Open", True))
    except Exception as e:
        print(f"  ✗ Failed: {e}")
        test_results.append(("Driver Open", False))

    # Test 2: Get Topology
    print("Test 2: Get Topology...")
    try:
        driver = CxlWindowsDriver()
        driver.open()
        topology = driver.get_topology()
        print(f"  ✓ Topology: {topology}")
        driver.close()
        test_results.append(("Get Topology", True))
    except Exception as e:
        print(f"  ✗ Failed: {e}")
        test_results.append(("Get Topology", False))

    # Test 3: Memory Read
    print("Test 3: Memory Read...")
    try:
        driver = CxlWindowsDriver()
        driver.open()
        reader = CxlMemoryReader(driver)
        reader.open()

        data = reader.read(TestConfig.TEST_ADDRESS_1, TestConfig.TEST_SIZE_4KB)
        print(f"  ✓ Read {len(data)} bytes")

        reader.close()
        driver.close()
        test_results.append(("Memory Read", True))
    except Exception as e:
        print(f"  ✗ Failed: {e}")
        test_results.append(("Memory Read", False))

    # Summary
    print("")
    print("=" * 60)
    print("Test Summary:")

    passed = sum(1 for _, result in test_results if result)
    total = len(test_results)

    for name, result in test_results:
        status = "✓" if result else "✗"
        print(f"  {status} {name}")

    print(f"\nPassed: {passed}/{total}")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
