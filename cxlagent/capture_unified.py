"""
Unified CXL Capture Interface - Cross-Platform Implementation.

Provides a unified interface for CXL memory capture that works on both
Linux and Windows. Automatically selects the appropriate platform-specific
implementation.

Usage:
    from cxlagent.capture_unified import CxlCapture

    capture = CxlCapture()
    capture.open()

    # Trigger cache flush (WBINVD on Linux, IOCTL on Windows)
    capture.trigger_wbinvd()

    # Read memory
    data = capture.read_memory(physical_address, size)

    capture.close()
"""

from typing import Optional, List, Any, Dict, Union
from abc import ABC, abstractmethod
import warnings

from .platform import (
    get_platform, is_linux, is_windows,
    get_capture_module, get_memory_module, Platform
)


# =============================================================================
# Abstract Base Class
# =============================================================================

class CxlCaptureInterface(ABC):
    """
    Abstract base class for CXL capture interfaces.

    Defines the unified interface that must be implemented by all
    platform-specific implementations.
    """

    @abstractmethod
    def open(self):
        """Open the capture interface."""
        pass

    @abstractmethod
    def close(self):
        """Close the capture interface."""
        pass

    @abstractmethod
    def trigger_wbinvd(self) -> bool:
        """
        Trigger WBINVD (Write Back + Invalidate) to flush cache.

        Returns:
            True if successful
        """
        pass

    @abstractmethod
    def get_topology(self) -> Dict[str, int]:
        """
        Get CXL bus topology.

        Returns:
            Dict with device counts
        """
        pass

    @abstractmethod
    def get_cache_devices(self) -> List[Dict[str, Any]]:
        """
        Get list of cache devices.

        Returns:
            List of cache device information dicts
        """
        pass

    @abstractmethod
    def get_memory_devices(self) -> List[Dict[str, Any]]:
        """
        Get list of memory devices.

        Returns:
            List of memory device information dicts
        """
        pass

    @abstractmethod
    def get_memory_windows(self) -> List[Dict[str, Any]]:
        """
        Get CXL memory windows.

        Returns:
            List of memory window information dicts
        """
        pass

    @abstractmethod
    def read_memory(self, physical_address: int, size: int) -> bytes:
        """
        Read from physical memory.

        Args:
            physical_address: Starting physical address
            size: Number of bytes to read

        Returns:
            Bytes read from memory
        """
        pass

    @abstractmethod
    def validate_address(self, physical_address: int, size: int = 1) -> bool:
        """
        Validate if address is in a CXL memory window.

        Args:
            physical_address: Physical address
            size: Size of region

        Returns:
            True if valid
        """
        pass


# =============================================================================
# Linux Implementation
# =============================================================================

class LinuxCxlCapture(CxlCaptureInterface):
    """Linux implementation of CXL capture using /dev/mem and sysfs."""

    def __init__(self):
        """Initialize Linux CXL capture."""
        if not is_linux():
            raise RuntimeError("LinuxCxlCapture is only available on Linux")

        from . import capture as capture_linux
        self._capture_module = capture_linux

        self._reader = None
        self._is_open = False

    def open(self):
        """Open the Linux capture interface."""
        if self._is_open:
            return

        # Initialize memory reader
        self._reader = self._capture_module.CxlMemoryReader()
        self._reader.open()
        self._is_open = True

    def close(self):
        """Close the Linux capture interface."""
        if self._reader:
            self._reader.close()
            self._reader = None
        self._is_open = False

    def trigger_wbinvd(self) -> bool:
        """Trigger WBINVD via sysfs."""
        if not self._is_open:
            raise RuntimeError("Capture interface not open")

        return self._capture_module.trigger_wbinvd()

    def get_topology(self) -> Dict[str, int]:
        """Get topology from sysfs."""
        # Count devices from sysfs
        cache_devices = self._capture_module.get_cache_devices()
        memory_devices = self._capture_module.get_memory_devices()

        return {
            "cache_device_count": len(cache_devices),
            "memory_device_count": len(memory_devices),
            "accelerator_device_count": 0,
            "total_device_count": len(cache_devices) + len(memory_devices)
        }

    def get_cache_devices(self) -> List[Dict[str, Any]]:
        """Get cache devices from sysfs."""
        if not self._is_open:
            raise RuntimeError("Capture interface not open")

        devices = self._capture_module.get_cache_devices()
        return [
            {
                "name": d.name,
                "pci_bdf": d.pci_bdf,
                "size": d.size,
                "unit": d.unit,
                "numa_node": d.numa_node,
                "disabled": d.disabled,
                "invalid": d.invalid,
                "wbinvd_supported": d.wbinvd_supported
            }
            for d in devices
        ]

    def get_memory_devices(self) -> List[Dict[str, Any]]:
        """Get memory devices from sysfs."""
        if not self._is_open:
            raise RuntimeError("Capture interface not open")

        devices = self._capture_module.get_memory_devices()
        return [
            {
                "name": d.name,
                "pci_bdf": d.pci_bdf,
                "total_size": d.total_size,
                "ram_size": d.ram_size,
                "pmem_size": d.pmem_size,
                "numa_node": d.numa_node,
                "firmware_version": d.firmware_version
            }
            for d in devices
        ]

    def get_memory_windows(self) -> List[Dict[str, Any]]:
        """Get memory windows from /proc/iomem."""
        if not self._is_open:
            raise RuntimeError("Capture interface not open")

        windows = self._capture_module.get_memory_windows()
        return [
            {
                "index": w.index,
                "start_physical_address": w.start_physical_address,
                "end_physical_address": w.end_physical_address,
                "size": w.size,
                "is_persistent": w.is_persistent
            }
            for w in windows
        ]

    def read_memory(self, physical_address: int, size: int) -> bytes:
        """Read from physical memory."""
        if not self._is_open:
            raise RuntimeError("Capture interface not open")

        return self._reader.read(physical_address, size)

    def validate_address(self, physical_address: int, size: int = 1) -> bool:
        """Validate physical address."""
        if not self._is_open:
            raise RuntimeError("Capture interface not open")

        return self._reader.is_valid_address(physical_address, size)


# =============================================================================
# Windows Implementation
# =============================================================================

class WindowsCxlCapture(CxlCaptureInterface):
    """Windows implementation of CXL capture using IOCTL and drivers."""

    def __init__(self):
        """Initialize Windows CXL capture."""
        if not is_windows():
            raise RuntimeError("WindowsCxlCapture is only available on Windows")

        from . import capture_windows as capture_windows_module
        from . import memory_windows as memory_windows_module
        self._capture_module = capture_windows_module
        self._memory_module = memory_windows_module

        self._driver = None
        self._memory_reader = None
        self._is_open = False

    def open(self):
        """Open the Windows capture interface."""
        if self._is_open:
            return

        # Open driver
        self._driver = self._capture_module.CxlWindowsDriver()
        self._driver.open()

        # Initialize memory reader
        self._memory_reader = self._memory_module.CxlMemoryReader(self._driver)
        self._memory_reader.open()

        self._is_open = True

    def close(self):
        """Close the Windows capture interface."""
        if self._memory_reader:
            self._memory_reader.close()
            self._memory_reader = None

        if self._driver:
            self._driver.close()
            self._driver = None

        self._is_open = False

    def trigger_wbinvd(self) -> bool:
        """Trigger WBINVD via IOCTL."""
        if not self._is_open:
            raise RuntimeError("Capture interface not open")

        return self._driver.trigger_wbinvd()

    def get_topology(self) -> Dict[str, int]:
        """Get topology via IOCTL."""
        if not self._is_open:
            raise RuntimeError("Capture interface not open")

        topology = self._driver.get_topology()
        return {
            "cache_device_count": topology.cache_device_count,
            "memory_device_count": topology.memory_device_count,
            "accelerator_device_count": topology.accelerator_device_count,
            "total_device_count": topology.total_device_count
        }

    def get_cache_devices(self) -> List[Dict[str, Any]]:
        """Get cache devices via IOCTL."""
        # Would need to implement get_cache_devices in driver
        # For now, return empty list
        return []

    def get_memory_devices(self) -> List[Dict[str, Any]]:
        """Get memory devices via IOCTL."""
        if not self._is_open:
            raise RuntimeError("Capture interface not open")

        info = self._driver.get_memory_devices()[0] if self._driver.get_memory_devices() else None
        if not info:
            return []

        return [
            {
                "name": info.name,
                "pci_bdf": info.pci_bdf,
                "total_size": info.total_size,
                "ram_size": info.ram_size,
                "pmem_size": info.pmem_size,
                "numa_node": info.numa_node,
                "firmware_version": info.firmware_version
            }
        ]

    def get_memory_windows(self) -> List[Dict[str, Any]]:
        """Get memory windows via IOCTL."""
        if not self._is_open:
            raise RuntimeError("Capture interface not open")

        windows = self._driver.get_memory_windows()
        return [
            {
                "index": w.index,
                "start_physical_address": w.start_physical_address,
                "end_physical_address": w.end_physical_address,
                "size": w.size,
                "is_persistent": w.is_persistent
            }
            for w in windows
        ]

    def read_memory(self, physical_address: int, size: int) -> bytes:
        """Read from physical memory."""
        if not self._is_open:
            raise RuntimeError("Capture interface not open")

        return self._memory_reader.read(physical_address, size)

    def validate_address(self, physical_address: int, size: int = 1) -> bool:
        """Validate physical address."""
        if not self._is_open:
            raise RuntimeError("Capture interface not open")

        return self._driver.validate_address(physical_address, size)


# =============================================================================
# Unified Interface
# =============================================================================

class CxlCapture(CxlCaptureInterface):
    """
    Unified CXL capture interface that works on both Linux and Windows.

    Automatically selects the appropriate platform-specific implementation.

    Usage:
        from cxlagent.capture_unified import CxlCapture

        with CxlCapture() as capture:
            capture.trigger_wbinvd()
            data = capture.read_memory(0x100000000, 4096)
    """

    def __init__(self, platform: Optional[Platform] = None):
        """
        Initialize the unified CXL capture interface.

        Args:
            platform: Optional platform override (auto-detected if None)
        """
        self._platform = platform if platform else get_platform()

        if self._platform == Platform.LINUX:
            self._impl = LinuxCxlCapture()
        elif self._platform == Platform.WINDOWS:
            self._impl = WindowsCxlCapture()
        else:
            raise RuntimeError(f"Unsupported platform: {self._platform}")

        self._is_open = False

    def open(self):
        """Open the capture interface."""
        if not self._is_open:
            self._impl.open()
            self._is_open = True

    def close(self):
        """Close the capture interface."""
        if self._is_open:
            self._impl.close()
            self._is_open = False

    def trigger_wbinvd(self) -> bool:
        """Trigger cache flush."""
        return self._impl.trigger_wbinvd()

    def get_topology(self) -> Dict[str, int]:
        """Get topology."""
        return self._impl.get_topology()

    def get_cache_devices(self) -> List[Dict[str, Any]]:
        """Get cache devices."""
        return self._impl.get_cache_devices()

    def get_memory_devices(self) -> List[Dict[str, Any]]:
        """Get memory devices."""
        return self._impl.get_memory_devices()

    def get_memory_windows(self) -> List[Dict[str, Any]]:
        """Get memory windows."""
        return self._impl.get_memory_windows()

    def read_memory(self, physical_address: int, size: int) -> bytes:
        """Read from physical memory."""
        return self._impl.read_memory(physical_address, size)

    def validate_address(self, physical_address: int, size: int = 1) -> bool:
        """Validate physical address."""
        return self._impl.validate_address(physical_address, size)

    # ========================================================================
    # Convenience Methods
    # ========================================================================

    def capture_snapshot(self, regions: Optional[List[Dict[str, int]]] = None) -> Dict[int, bytes]:
        """
        Capture a memory snapshot across regions.

        Args:
            regions: Optional list of {"address": int, "size": int} dicts
                    If None, captures all memory windows

        Returns:
            Dict mapping physical addresses to captured data
        """
        if regions is None:
            # Capture all memory windows
            windows = self.get_memory_windows()
            regions = [
                {"address": w["start_physical_address"], "size": w["size"]}
                for w in windows
            ]

        snapshot = {}
        for region in regions:
            address = region["address"]
            size = region["size"]

            data = self.read_memory(address, size)
            snapshot[address] = data

        return snapshot

    # ========================================================================
    # Context Manager
    # ========================================================================

    def __enter__(self):
        """Enter context manager."""
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context manager."""
        self.close()

    def __repr__(self) -> str:
        """String representation."""
        return f"CxlCapture(platform={self._platform.value}, open={self._is_open})"


# =============================================================================
# Convenience Functions
# =============================================================================

def create_capture() -> CxlCapture:
    """
    Create a CXL capture instance for the current platform.

    Returns:
        CxlCapture instance
    """
    return CxlCapture()


def is_available() -> bool:
    """
    Check if CXL capture is available on this platform.

    Returns:
        True if CXL drivers/hardware are available
    """
    if is_linux():
        # Check if sysfs CXL devices exist
        try:
            import os
            return os.path.exists("/sys/bus/cxl/devices")
        except Exception:
            return False

    elif is_windows():
        # Check if PhysMem driver is available
        try:
            from .capture_windows import is_available as windows_available
            return windows_available()
        except Exception:
            return False

    return False


def quick_capture(address: int, size: int) -> bytes:
    """
    Quick one-shot memory capture.

    Args:
        address: Physical address
        size: Number of bytes

    Returns:
        Captured bytes
    """
    with CxlCapture() as capture:
        return capture.read_memory(address, size)


# =============================================================================
# Module Info
# =============================================================================

__all__ = [
    "CxlCapture",
    "CxlCaptureInterface",
    "LinuxCxlCapture",
    "WindowsCxlCapture",
    "create_capture",
    "is_available",
    "quick_capture",
]
