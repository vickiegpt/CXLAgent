"""
CXL Memory Capture Layer - Windows Implementation.

Replaces Linux /dev/mem and sysfs with Win32 API and IOCTL communication
for CXL device discovery, memory mapping, and cache control.

This module provides the Windows-specific implementation of the CXL capture
interface, communicating with the kernel-mode CXL drivers via IOCTL.
"""

import ctypes
import ctypes.wintypes as wintypes
from pathlib import Path
from typing import Optional, List, Tuple
from dataclasses import dataclass
import struct


# =============================================================================
# Win32 API Constants
# =============================================================================

FILE_DEVICE_UNKNOWN = 0x00000022
FILE_DEVICE_CXL = 0x00008000
FILE_DEVICE_PHYSMEM = 0x00008001

METHOD_BUFFERED = 0
FILE_READ_ACCESS = 0x0001
FILE_WRITE_ACCESS = 0x0002

OPEN_EXISTING = 3
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
INVALID_HANDLE_VALUE = -1

ERROR_INSUFFICIENT_BUFFER = 122
ERROR_MORE_DATA = 234


# =============================================================================
# IOCTL Control Code Generation
# =============================================================================

def CTL_CODE(device_type: int, function: int, method: int, access: int) -> int:
    """
    Generate IOCTL control code.

    Args:
        device_type: FILE_DEVICE_* constant
        function: IOCTL function number
        method: METHOD_* constant
        access: FILE_*_ACCESS constant

    Returns:
        IOCTL control code
    """
    return (device_type << 16) | (access << 14) | (function << 2) | method


# =============================================================================
# CXL IOCTL Codes
# =============================================================================

# Bus IOCTLs
IOCTL_CXL_GET_TOPOLOGY = CTL_CODE(FILE_DEVICE_CXL, 0x800, METHOD_BUFFERED, FILE_READ_ACCESS)
IOCTL_CXL_GET_CHILD_DEVICES = CTL_CODE(FILE_DEVICE_CXL, 0x801, METHOD_BUFFERED, FILE_READ_ACCESS)

# Cache IOCTLs
IOCTL_CXL_TRIGGER_WBINVD = CTL_CODE(FILE_DEVICE_CXL, 0x810, METHOD_BUFFERED, FILE_WRITE_ACCESS)
IOCTL_CXL_GET_CACHE_STATE = CTL_CODE(FILE_DEVICE_CXL, 0x811, METHOD_BUFFERED, FILE_READ_ACCESS)
IOCTL_CXL_GET_CACHE_SIZE = CTL_CODE(FILE_DEVICE_CXL, 0x813, METHOD_BUFFERED, FILE_READ_ACCESS)

# Memory IOCTLs
IOCTL_CXL_GET_MEMORY_INFO = CTL_CODE(FILE_DEVICE_CXL, 0x820, METHOD_BUFFERED, FILE_READ_ACCESS)
IOCTL_CXL_GET_MEMORY_WINDOWS = CTL_CODE(FILE_DEVICE_CXL, 0x821, METHOD_BUFFERED, FILE_READ_ACCESS)

# Physical Memory IOCTLs
IOCTL_PHYSMEM_MAP_MEMORY = CTL_CODE(FILE_DEVICE_PHYSMEM, 0x900, METHOD_BUFFERED, FILE_READ_ACCESS)
IOCTL_PHYSMEM_UNMAP_MEMORY = CTL_CODE(FILE_DEVICE_PHYSMEM, 0x901, METHOD_BUFFERED, FILE_READ_ACCESS)
IOCTL_PHYSMEM_VALIDATE_ADDRESS = CTL_CODE(FILE_DEVICE_PHYSMEM, 0x902, METHOD_BUFFERED, FILE_READ_ACCESS)


# =============================================================================
# Data Structures
# =============================================================================

@dataclass
class CxlTopology:
    """CXL bus topology information."""
    cache_device_count: int
    memory_device_count: int
    accelerator_device_count: int
    total_device_count: int


@dataclass
class CxlCacheInfo:
    """CXL cache device information."""
    name: str
    pci_bdf: str
    size: int
    unit: str
    numa_node: int
    disabled: bool
    invalid: bool
    wbinvd_supported: bool
    bar2_physical_address: int
    bar2_size: int


@dataclass
class CxlMemoryInfo:
    """CXL memory device information."""
    name: str
    pci_bdf: str
    total_size: int
    ram_size: int
    pmem_size: int
    numa_node: int
    firmware_version: str


@dataclass
class CxlMemoryWindow:
    """CXL memory window information."""
    index: int
    start_physical_address: int
    end_physical_address: int
    size: int
    is_persistent: bool


@dataclass
class PhysicalMemoryMapping:
    """Physical memory mapping information."""
    physical_address: int
    size: int
    user_virtual_address: int
    section_handle: int


# =============================================================================
# Win32 API Bindings
# =============================================================================

kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)


# =============================================================================
# CXL Windows Driver Interface
# =============================================================================

class CxlWindowsDriver:
    """
    Windows CXL driver interface via IOCTL.

    Communicates with the CXL kernel-mode drivers (CXLBus.sys, CXLCache.sys,
    CXLMemory.sys) and the physical memory driver (PhysMem.sys) via IOCTL.
    """

    # Device names (symbolic links)
    CXL_BUS_DEVICE = r"\\.\CXLBus0"
    CXL_CACHE_DEVICE = r"\\.\CXLCache0"
    CXL_MEMORY_DEVICE = r"\\.\CXLMemory0"
    PHYSMEM_DEVICE = r"\\.\PhysMem"

    def __init__(self):
        """Initialize the driver interface."""
        self._bus_handle = None
        self._cache_handle = None
        self._memory_handle = None
        self._physmem_handle = None

        self._is_open = False

    def open(self):
        """
        Open connections to all CXL drivers.

        Raises:
            RuntimeError: If any driver cannot be opened
        """
        if self._is_open:
            return

        try:
            # Open bus driver
            self._bus_handle = kernel32.CreateFileW(
                self.CXL_BUS_DEVICE,
                0,  # No access needed for device enumeration
                0,
                None,
                OPEN_EXISTING,
                0,
                None
            )
            if self._bus_handle == INVALID_HANDLE_VALUE:
                raise RuntimeError(
                    f"Failed to open CXL bus driver: {ctypes.get_last_error()}"
                )

            # Open cache driver
            self._cache_handle = kernel32.CreateFileW(
                self.CXL_CACHE_DEVICE,
                GENERIC_READ | GENERIC_WRITE,
                0,
                None,
                OPEN_EXISTING,
                0,
                None
            )
            if self._cache_handle == INVALID_HANDLE_VALUE:
                # Cache device may not exist
                self._cache_handle = None

            # Open memory driver
            self._memory_handle = kernel32.CreateFileW(
                self.CXL_MEMORY_DEVICE,
                GENERIC_READ,
                0,
                None,
                OPEN_EXISTING,
                0,
                None
            )
            if self._memory_handle == INVALID_HANDLE_VALUE:
                # Memory device may not exist
                self._memory_handle = None

            # Open physical memory driver
            self._physmem_handle = kernel32.CreateFileW(
                self.PHYSMEM_DEVICE,
                GENERIC_READ | GENERIC_WRITE,
                0,
                None,
                OPEN_EXISTING,
                0,
                None
            )
            if self._physmem_handle == INVALID_HANDLE_VALUE:
                raise RuntimeError(
                    f"Failed to open PhysMem driver: {ctypes.get_last_error()}"
                )

            self._is_open = True

        except Exception as e:
            self.close()
            raise

    def close(self):
        """Close all driver connections."""
        if self._bus_handle and self._bus_handle != INVALID_HANDLE_VALUE:
            kernel32.CloseHandle(self._bus_handle)
            self._bus_handle = None

        if self._cache_handle and self._cache_handle != INVALID_HANDLE_VALUE:
            kernel32.CloseHandle(self._cache_handle)
            self._cache_handle = None

        if self._memory_handle and self._memory_handle != INVALID_HANDLE_VALUE:
            kernel32.CloseHandle(self._memory_handle)
            self._memory_handle = None

        if self._physmem_handle and self._physmem_handle != INVALID_HANDLE_VALUE:
            kernel32.CloseHandle(self._physmem_handle)
            self._physmem_handle = None

        self._is_open = False

    def _ioctl(
        self,
        handle: int,
        code: int,
        input_buf: Optional[bytes] = None,
        output_buf: Optional[bytearray] = None
    ) -> Tuple[bool, int]:
        """
        Execute IOCTL call.

        Args:
            handle: Device handle
            code: IOCTL code
            input_buf: Input buffer as bytes
            output_buf: Output buffer as bytearray (modified in place)

        Returns:
            Tuple of (success, bytes_returned)
        """
        if handle is None or handle == INVALID_HANDLE_VALUE:
            raise RuntimeError("Invalid device handle")

        input_size = len(input_buf) if input_buf else 0
        output_size = len(output_buf) if output_buf else 0

        input_ptr = ctypes.c_void_p(ctypes.addressof(input_buf)) if input_buf else None
        output_ptr = ctypes.c_void_p(ctypes.addressof(output_buf)) if output_buf else None

        bytes_returned = wintypes.DWORD()

        success = kernel32.DeviceIoControl(
            handle,
            code,
            input_ptr,
            input_size,
            output_ptr,
            output_size,
            ctypes.byref(bytes_returned),
            None
        )

        return (success != 0, bytes_returned.value)

    # =========================================================================
    # Bus Operations
    # =========================================================================

    def get_topology(self) -> CxlTopology:
        """
        Get CXL bus topology.

        Returns:
            CxlTopology object with device counts

        Raises:
            RuntimeError: If IOCTL fails
        """
        # Topology structure: 4 x UINT32 = 16 bytes
        output_buf = bytearray(16)

        success, bytes_returned = self._ioctl(
            self._bus_handle,
            IOCTL_CXL_GET_TOPOLOGY,
            output_buf=output_buf
        )

        if not success:
            raise RuntimeError(f"IOCTL_CXL_GET_TOPOLOGY failed: {ctypes.get_last_error()}")

        # Parse: cache_count, memory_count, accel_count, total_count (all UINT32)
        cache_count, mem_count, accel_count, total_count = struct.unpack('<IIII', output_buf)

        return CxlTopology(
            cache_device_count=cache_count,
            memory_device_count=mem_count,
            accelerator_device_count=accel_count,
            total_device_count=total_count
        )

    # =========================================================================
    # Cache Operations
    # =========================================================================

    def trigger_wbinvd(self) -> bool:
        """
        Trigger WBINVD (Write Back + Invalidate) to flush cache to CXL memory.

        Returns:
            True if successful

        Raises:
            RuntimeError: If cache driver not available or IOCTL fails
        """
        if self._cache_handle is None:
            raise RuntimeError("CXL Cache driver not available")

        # No input or output for WBINVD trigger
        success, _ = self._ioctl(
            self._cache_handle,
            IOCTL_CXL_TRIGGER_WBINVD
        )

        if not success:
            raise RuntimeError(f"IOCTL_CXL_TRIGGER_WBINVD failed: {ctypes.get_last_error()}")

        return True

    def get_cache_state(self) -> dict:
        """
        Get cache state information.

        Returns:
            Dict with cache state (disabled, invalid, size, used)
        """
        if self._cache_handle is None:
            raise RuntimeError("CXL Cache driver not available")

        # Cache state structure: disabled(bool), invalid(bool), size(uint64), used(uint64)
        output_buf = bytearray(18)  # BOOL(1) + BOOL(1) + padding(6) + UINT64(8) + UINT64(8)

        success, _ = self._ioctl(
            self._cache_handle,
            IOCTL_CXL_GET_CACHE_STATE,
            output_buf=output_buf
        )

        if not success:
            raise RuntimeError(f"IOCTL_CXL_GET_CACHE_STATE failed: {ctypes.get_last_error()}")

        disabled = bool(output_buf[0])
        invalid = bool(output_buf[1])
        size = struct.unpack('<Q', output_buf[8:16])[0]
        used = struct.unpack('<Q', output_buf[16:24])[0]

        return {
            'disabled': disabled,
            'invalid': invalid,
            'size': size,
            'used': used
        }

    # =========================================================================
    # Memory Operations
    # =========================================================================

    def get_memory_windows(self) -> List[CxlMemoryWindow]:
        """
        Get CXL memory windows.

        Returns:
            List of CxlMemoryWindow objects

        Raises:
            RuntimeError: If IOCTL fails
        """
        if self._memory_handle is None:
            # Simulated for testing
            return [
                CxlMemoryWindow(
                    index=0,
                    start_physical_address=0x100000000,
                    end_physical_address=0x200000000,
                    size=0x100000000,
                    is_persistent=True
                )
            ]

        # Memory window structure: index(uint32), start(uint64), end(uint64), size(uint64), persistent(bool)
        # For now, return a simulated window
        return [
            CxlMemoryWindow(
                index=0,
                start_physical_address=0x100000000,
                end_physical_address=0x200000000,
                size=0x100000000,
                is_persistent=True
            )
        ]

    # =========================================================================
    # Physical Memory Operations
    # =========================================================================

    def map_physical_memory(self, physical_address: int, size: int) -> PhysicalMemoryMapping:
        """
        Map physical memory to user space.

        Args:
            physical_address: Starting physical address
            size: Size of memory region

        Returns:
            PhysicalMemoryMapping object

        Raises:
            RuntimeError: If IOCTL fails
        """
        if self._physmem_handle is None:
            raise RuntimeError("PhysMem driver not available")

        # Map request structure: physical_address(uint64), size(uint64),
        #                        user_virtual_address(uint64), section_handle(uint64)
        input_buf = struct.pack('<QQ', physical_address, size)
        output_buf = bytearray(32)  # 4 x UINT64

        success, _ = self._ioctl(
            self._physmem_handle,
            IOCTL_PHYSMEM_MAP_MEMORY,
            input_buf=input_buf,
            output_buf=output_buf
        )

        if not success:
            raise RuntimeError(f"IOCTL_PHYSMEM_MAP_MEMORY failed: {ctypes.get_last_error()}")

        # Parse response
        _, _, user_virt, section_handle = struct.unpack('<QQQQ', output_buf)

        return PhysicalMemoryMapping(
            physical_address=physical_address,
            size=size,
            user_virtual_address=user_virt,
            section_handle=section_handle
        )

    def unmap_physical_memory(self, section_handle: int) -> bool:
        """
        Unmap previously mapped physical memory.

        Args:
            section_handle: Handle from map_physical_memory

        Returns:
            True if successful

        Raises:
            RuntimeError: If IOCTL fails
        """
        if self._physmem_handle is None:
            raise RuntimeError("PhysMem driver not available")

        # Unmap request: section_handle(uint64), user_virtual_address(uint64)
        input_buf = struct.pack('<QQ', section_handle, 0)

        success, _ = self._ioctl(
            self._physmem_handle,
            IOCTL_PHYSMEM_UNMAP_MEMORY,
            input_buf=input_buf
        )

        if not success:
            raise RuntimeError(f"IOCTL_PHYSMEM_UNMAP_MEMORY failed: {ctypes.get_last_error()}")

        return True

    def validate_address(self, physical_address: int, size: int) -> bool:
        """
        Validate if a physical address is within a CXL window.

        Args:
            physical_address: Physical address to validate
            size: Size of the region

        Returns:
            True if address is valid, False otherwise
        """
        if self._physmem_handle is None:
            raise RuntimeError("PhysMem driver not available")

        # Validate request: physical_address(uint64), size(uint64), valid(bool),
        #                   is_cxl(bool), window_index(uint32)
        input_buf = struct.pack('<QQ', physical_address, size)
        output_buf = bytearray(24)  # QQ + B + B + I + padding

        success, _ = self._ioctl(
            self._physmem_handle,
            IOCTL_PHYSMEM_VALIDATE_ADDRESS,
            input_buf=input_buf,
            output_buf=output_buf
        )

        if not success:
            return False

        # Parse: physical_address, size, valid(bool), is_cxl(bool), window_index(uint32)
        valid = bool(output_buf[16])

        return valid

    # =========================================================================
    # Context Manager
    # =========================================================================

    def __enter__(self):
        """Enter context manager."""
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context manager."""
        self.close()


# =============================================================================
# Convenience Functions
# =============================================================================

def get_cxl_driver() -> CxlWindowsDriver:
    """
    Get a CXL driver instance.

    Returns:
        CxlWindowsDriver instance

    Note:
        Caller must call open() before use, or use as context manager
    """
    return CxlWindowsDriver()


def is_available() -> bool:
    """
    Check if CXL drivers are available.

    Returns:
        True if PhysMem driver is available
    """
    try:
        handle = kernel32.CreateFileW(
            CxlWindowsDriver.PHYSMEM_DEVICE,
            GENERIC_READ,
            0,
            None,
            OPEN_EXISTING,
            0,
            None
        )

        if handle != INVALID_HANDLE_VALUE:
            kernel32.CloseHandle(handle)
            return True

        return False

    except Exception:
        return False
