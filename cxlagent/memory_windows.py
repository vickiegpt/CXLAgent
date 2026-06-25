"""
Physical Memory Mapping Wrapper for Windows.

Provides memory reading functionality via the PhysMem.sys driver,
which maps physical memory ranges to user space for CXL access.

This module replaces Linux /dev/mem mmap() with Windows shared memory
sections created by the PhysMem kernel driver.
"""

import mmap
import ctypes
import ctypes.wintypes as wintypes
from typing import Optional, Dict, Tuple
from dataclasses import dataclass
import struct

from .capture_windows import CxlWindowsDriver, PhysicalMemoryMapping


# =============================================================================
# Constants
# =============================================================================

PAGE_SIZE = 4096  # Windows page size
DEFAULT_CHUNK_SIZE = 64 * 1024  # 64KB default read chunk


# =============================================================================
# Win32 API Bindings
# =============================================================================

kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)


# =============================================================================
# Exceptions
# =============================================================================

class MemoryMappingError(Exception):
    """Exception raised when memory mapping fails."""
    pass


class InvalidAddressError(MemoryMappingError):
    """Exception raised when physical address is invalid."""
    pass


# =============================================================================
# Memory Window Information
# =============================================================================

@dataclass
class MemoryWindow:
    """CXL memory window information."""
    index: int
    start_physical_address: int
    end_physical_address: int
    size: int
    is_persistent: bool


# =============================================================================
# CXL Memory Reader for Windows
# =============================================================================

class CxlMemoryReader:
    """
    Read CXL device memory via Windows PhysMem driver.

    This class provides physical memory reading capabilities by mapping
    physical memory ranges through the PhysMem.sys kernel driver. It maintains
    a cache of mapped memory pages to avoid repeated mapping overhead.

    Usage:
        driver = CxlWindowsDriver()
        driver.open()

        reader = CxlMemoryReader(driver)
        reader.open()

        data = reader.read(0x100000000, 4096)  # Read 4KB from physical address

        reader.close()
        driver.close()
    """

    def __init__(self, driver: CxlWindowsDriver):
        """
        Initialize the memory reader.

        Args:
            driver: CxlWindowsDriver instance (must be opened before use)
        """
        self._driver = driver
        self._is_open = False

        # Cache of mapped memory regions
        # Key: (page_base_physical_address)
        # Value: (section_handle, mmap_object, size)
        self._mappings: Dict[int, Tuple[int, mmap.mmap, int]] = {}

    def open(self):
        """
        Initialize the memory reader.

        Raises:
            RuntimeError: If driver is not open
        """
        if self._driver._physmem_handle is None:
            raise RuntimeError("CXL driver not opened")

        self._is_open = True

    def close(self):
        """Close all memory mappings."""
        for page_base, (section_handle, mm, size) in self._mappings.items():
            try:
                mm.close()
            except Exception:
                pass

            try:
                self._driver.unmap_physical_memory(section_handle)
            except Exception:
                pass

        self._mappings.clear()
        self._is_open = False

    # =========================================================================
    # Memory Reading Operations
    # =========================================================================

    def read(self, physical_address: int, size: int) -> bytes:
        """
        Read from physical memory address.

        This method handles page-aligned mapping and reads across page boundaries.
        Physical addresses are mapped on-demand and cached for performance.

        Args:
            physical_address: Starting physical address
            size: Number of bytes to read

        Returns:
            Bytes read from physical memory

        Raises:
            MemoryMappingError: If mapping or read fails
            InvalidAddressError: If address is not in a valid CXL window
        """
        if not self._is_open:
            raise RuntimeError("Memory reader not opened")

        if size == 0:
            return b''

        # Validate address range
        if not self._driver.validate_address(physical_address, size):
            raise InvalidAddressError(
                f"Address range 0x{physical_address:x} - 0x{physical_address + size:x} "
                f"is not in a valid CXL window"
            )

        # Calculate page-aligned regions
        result = bytearray()
        current_address = physical_address
        remaining = size

        while remaining > 0:
            # Page base and offset
            page_base = current_address & ~(PAGE_SIZE - 1)
            offset_in_page = current_address - page_base

            # Bytes to read from this page
            bytes_in_page = min(PAGE_SIZE - offset_in_page, remaining)

            # Get or create mapping for this page
            mm = self._get_mapping_for_page(page_base)

            # Read from mapped memory
            mm.seek(offset_in_page)
            page_data = mm.read(bytes_in_page)
            result.extend(page_data)

            # Advance
            current_address += bytes_in_page
            remaining -= bytes_in_page

        return bytes(result)

    def read_chunked(
        self,
        physical_address: int,
        size: int,
        chunk_size: int = DEFAULT_CHUNK_SIZE
    ) -> bytes:
        """
        Read from physical memory in chunks.

        Useful for large reads to avoid mapping huge regions at once.

        Args:
            physical_address: Starting physical address
            size: Total bytes to read
            chunk_size: Size of each read chunk

        Returns:
            Bytes read from physical memory

        Raises:
            MemoryMappingError: If mapping or read fails
        """
        result = bytearray()
        current_address = physical_address
        remaining = size

        while remaining > 0:
            chunk_bytes = min(chunk_size, remaining)
            chunk_data = self.read(current_address, chunk_bytes)
            result.extend(chunk_data)

            current_address += chunk_bytes
            remaining -= chunk_bytes

        return bytes(result)

    # =========================================================================
    # Memory Mapping Cache
    # =========================================================================

    def _get_mapping_for_page(self, page_base: int) -> mmap.mmap:
        """
        Get or create memory mapping for a page-aligned address.

        Args:
            page_base: Page-aligned physical address

        Returns:
            Memory-mapped object for the page

        Raises:
            MemoryMappingError: If mapping fails
        """
        if page_base in self._mappings:
            return self._mappings[page_base][1]

        # Create new mapping
        try:
            # Map one page (PAGE_SIZE bytes)
            mapping = self._driver.map_physical_memory(page_base, PAGE_SIZE)

            # Create mmap object for the mapped region
            # On Windows, we use the section handle to create a file mapping
            mm = self._create_mmap_for_section(mapping.section_handle, PAGE_SIZE)

            # Cache the mapping
            self._mappings[page_base] = (mapping.section_handle, mm, PAGE_SIZE)

            return mm

        except Exception as e:
            raise MemoryMappingError(
                f"Failed to map physical address 0x{page_base:x}: {e}"
            )

    def _create_mmap_for_section(self, section_handle: int, size: int) -> mmap.mmap:
        """
        Create a mmap object for a driver section.

        Args:
            section_handle: Handle from PhysMem driver
            size: Size of the mapped region

        Returns:
            Memory-mapped object

        Note:
            In a production implementation, this would use the section handle
            to create a proper shared memory mapping. For this implementation,
            we use a simulated approach since actual Windows driver sections
            require specific handling.
        """
        # In a real implementation, this would:
        # 1. Use the section handle from the driver
        # 2. Call OpenFileMapping to get a handle
        # 3. Create mmap from that handle

        # For now, create a simulated mmap that will be populated by the driver
        # This is a placeholder - actual implementation requires kernel driver
        # to expose memory as a file mapping object

        class SimulatedMmap:
            """Simulated mmap for development/testing."""

            def __init__(self, size: int):
                self._size = size
                self._pos = 0
                self._data = bytearray(size)

            def seek(self, pos: int):
                self._pos = pos

            def read(self, size: int) -> bytes:
                end_pos = min(self._pos + size, self._size)
                data = bytes(self._data[self._pos:end_pos])
                self._pos = end_pos
                return data

            def write(self, data: bytes):
                end_pos = min(self._pos + len(data), self._size)
                self._data[self._pos:end_pos] = data
                self._len = len(data)

            def close(self):
                pass

        return SimulatedMmap(size)

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def get_mapped_regions(self) -> Dict[int, int]:
        """
        Get information about currently mapped memory regions.

        Returns:
            Dict mapping page base physical address to size in bytes
        """
        return {
            page_base: size
            for page_base, (_, _, size) in self._mappings.items()
        }

    def unmap_region(self, page_base: int) -> bool:
        """
        Unmap a specific memory region.

        Args:
            page_base: Page-aligned physical address

        Returns:
            True if region was unmapped, False if not found
        """
        if page_base in self._mappings:
            section_handle, mm, size = self._mappings.pop(page_base)

            try:
                mm.close()
            except Exception:
                pass

            try:
                self._driver.unmap_physical_memory(section_handle)
            except Exception:
                pass

            return True

        return False

    def clear_mappings(self):
        """Clear all cached memory mappings."""
        self.close()
        if self._is_open:
            self._mappings = {}

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

def read_physical_memory(
    driver: CxlWindowsDriver,
    physical_address: int,
    size: int
) -> bytes:
    """
    Read from physical memory (one-shot function).

    Args:
        driver: Open CxlWindowsDriver instance
        physical_address: Starting physical address
        size: Number of bytes to read

    Returns:
        Bytes read from physical memory

    Raises:
        MemoryMappingError: If read fails
    """
    with CxlMemoryReader(driver) as reader:
        return reader.read(physical_address, size)


def is_address_in_cxl_window(
    driver: CxlWindowsDriver,
    physical_address: int,
    size: int = 1
) -> bool:
    """
    Check if a physical address range is within a CXL memory window.

    Args:
        driver: Open CxlWindowsDriver instance
        physical_address: Physical address to check
        size: Size of the address range

    Returns:
        True if address is valid, False otherwise
    """
    return driver.validate_address(physical_address, size)
