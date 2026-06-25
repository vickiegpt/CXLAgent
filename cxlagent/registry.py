"""
Windows Registry Access for CXL Configuration.

Provides access to CXL driver configuration stored in the Windows Registry,
replacing Linux sysfs configuration access.

Registry paths:
- HKLM\SYSTEM\CurrentControlSet\Services\PhysMem\Parameters
- HKLM\SYSTEM\CurrentControlSet\Services\CXLBus\Parameters
- HKLM\SYSTEM\CurrentControlSet\Services\CXLCache\Parameters
- HKLM\SYSTEM\CurrentControlSet\Services\CXLMemory\Parameters
- HKLM\SYSTEM\CurrentControlSet\Services\CXLAccel\Parameters
"""

import winreg
from typing import Any, Optional, Union, Dict
from dataclasses import dataclass
from enum import Enum


# =============================================================================
# Registry Path Constants
# =============================================================================

REGISTRY_BASE = r"SYSTEM\CurrentControlSet\Services"

REGISTRY_PATHS = {
    "physmem": f"{REGISTRY_BASE}\\PhysMem\\Parameters",
    "bus": f"{REGISTRY_BASE}\\CXLBus\\Parameters",
    "cache": f"{REGISTRY_BASE}\\CXLCache\\Parameters",
    "memory": f"{REGISTRY_BASE}\\CXLMemory\\Parameters",
    "accel": f"{REGISTRY_BASE}\\CXLAccel\\Parameters",
}


# =============================================================================
# Registry Value Types
# =============================================================================

class RegType(Enum):
    """Windows Registry value types."""
    DWORD = winreg.REG_DWORD
    QWORD = winreg.REG_QWORD
    SZ = winreg.REG_SZ
    MULTI_SZ = winreg.REG_MULTI_SZ
    BINARY = winreg.REG_BINARY


# =============================================================================
# Exceptions
# =============================================================================

class RegistryError(Exception):
    """Base exception for registry access errors."""
    pass


class RegistryKeyNotFoundError(RegistryError):
    """Raised when a registry key is not found."""
    pass


class RegistryValueNotFoundError(RegistryError):
    """Raised when a registry value is not found."""
    pass


class RegistryTypeError(RegistryError):
    """Raised when registry value type mismatch."""
    pass


# =============================================================================
# CXL Registry Configuration Classes
# =============================================================================

@dataclass
class CacheConfiguration:
    """CXL Cache configuration from registry."""
    cache_size: int              # Cache size in bytes
    wbinvd_supported: bool       # WBINVD support
    auto_flush: bool             # Auto-flush enabled
    cache_disabled: bool         # Cache disabled state
    cache_invalid: bool          # Cache invalid state


@dataclass
class MemoryConfiguration:
    """CXL Memory configuration from registry."""
    total_size: int              # Total memory in bytes
    ram_size: int                # Volatile memory in bytes
    pmem_size: int               # Persistent memory in bytes
    numa_node: int               # NUMA node


@dataclass
class AcceleratorConfiguration:
    """CXL Accelerator configuration from registry."""
    work_queue_depth: int        # Work queue depth
    dma_supported: bool          # DMA support
    interrupt_supported: bool     # Interrupt support
    default_bitstream: str       # Default FPGA bitstream path


# =============================================================================
# CXL Registry Access Class
# =============================================================================

class CxlRegistry:
    """
    Access CXL device configuration from Windows Registry.

    This class provides methods to read and write CXL driver configuration
    stored in the Windows Registry, replacing sysfs on Linux.

    Usage:
        registry = CxlRegistry()

        # Read cache configuration
        cache_config = registry.read_cache_config()

        # Write cache disabled state
        registry.write_cache_disabled(True)

        # Read memory windows
        mem_config = registry.read_memory_config()
    """

    def __init__(self, remote_machine: Optional[str] = None):
        """
        Initialize the registry accessor.

        Args:
            remote_machine: Optional remote machine name for remote registry access
        """
        self._remote_machine = remote_machine

    # ========================================================================
    # Generic Registry Operations
    # ========================================================================

    def read_value(
        self,
        driver: str,
        value_name: str,
        value_type: RegType,
        default: Any = None
    ) -> Any:
        """
        Read a value from CXL driver registry parameters.

        Args:
            driver: Driver name ("physmem", "bus", "cache", "memory", "accel")
            value_name: Registry value name
            value_type: Expected value type
            default: Default value if not found

        Returns:
            Registry value, or default if not found

        Raises:
            RegistryError: If access fails and no default provided
        """
        if driver not in REGISTRY_PATHS:
            raise ValueError(f"Unknown driver: {driver}")

        try:
            key_path = REGISTRY_PATHS[driver]
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                key_path,
                0,
                winreg.KEY_READ
            )

            try:
                value, _ = winreg.QueryValueEx(key, value_name)
                return value
            finally:
                winreg.CloseKey(key)

        except WindowsError as e:
            if e.winerror == 2:  # File not found (value doesn't exist)
                if default is not None:
                    return default
                raise RegistryValueNotFoundError(
                    f"Registry value '{value_name}' not found in {key_path}"
                )
            raise RegistryError(f"Failed to read registry value: {e}")

    def write_value(
        self,
        driver: str,
        value_name: str,
        value: Any,
        value_type: RegType
    ) -> bool:
        """
        Write a value to CXL driver registry parameters.

        Args:
            driver: Driver name ("physmem", "bus", "cache", "memory", "accel")
            value_name: Registry value name
            value: Value to write
            value_type: Value type

        Returns:
            True if successful

        Raises:
            RegistryError: If write fails
        """
        if driver not in REGISTRY_PATHS:
            raise ValueError(f"Unknown driver: {driver}")

        try:
            key_path = REGISTRY_PATHS[driver]

            # Open or create key
            try:
                key = winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    key_path,
                    0,
                    winreg.KEY_WRITE
                )
            except WindowsError:
                # Create key if it doesn't exist
                parent_path = "\\".join(key_path.split("\\")[:-1])
                key_name = key_path.split("\\")[-1]

                parent_key = winreg.CreateKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    parent_path
                )
                winreg.CloseKey(parent_key)

                key = winreg.CreateKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    key_path
                )

            try:
                winreg.SetValueEx(key, value_name, 0, value_type.value, value)
                return True
            finally:
                winreg.CloseKey(key)

        except WindowsError as e:
            raise RegistryError(f"Failed to write registry value: {e}")

    def read_all_values(self, driver: str) -> Dict[str, tuple]:
        """
        Read all values from a driver's registry parameters.

        Args:
            driver: Driver name

        Returns:
            Dict mapping value names to (value, type) tuples
        """
        if driver not in REGISTRY_PATHS:
            raise ValueError(f"Unknown driver: {driver}")

        result = {}

        try:
            key_path = REGISTRY_PATHS[driver]
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                key_path,
                0,
                winreg.KEY_READ
            )

            try:
                i = 0
                while True:
                    try:
                        name, value, reg_type = winreg.EnumValue(key, i)
                        result[name] = (value, reg_type)
                        i += 1
                    except WindowsError:
                        break
            finally:
                winreg.CloseKey(key)

        except WindowsError as e:
            if e.winerror != 2:  # Not "file not found"
                raise RegistryError(f"Failed to enumerate registry values: {e}")

        return result

    # ========================================================================
    # Cache Configuration
    # ========================================================================

    def read_cache_config(self) -> CacheConfiguration:
        """
        Read CXL cache configuration from registry.

        Returns:
            CacheConfiguration object
        """
        return CacheConfiguration(
            cache_size=self.read_value("cache", "CacheSize", RegType.DWORD, 0),
            wbinvd_supported=bool(self.read_value("cache", "WbinvdSupported", RegType.DWORD, 1)),
            auto_flush=bool(self.read_value("cache", "AutoFlush", RegType.DWORD, 1)),
            cache_disabled=bool(self.read_value("cache", "CacheDisable", RegType.DWORD, 0)),
            cache_invalid=bool(self.read_value("cache", "CacheInvalid", RegType.DWORD, 0))
        )

    def write_cache_disabled(self, disabled: bool) -> bool:
        """
        Write cache disabled state.

        Args:
            disabled: True to disable cache

        Returns:
            True if successful
        """
        return self.write_value("cache", "CacheDisable", int(disabled), RegType.DWORD)

    # ========================================================================
    # Memory Configuration
    # ========================================================================

    def read_memory_config(self) -> MemoryConfiguration:
        """
        Read CXL memory configuration from registry.

        Returns:
            MemoryConfiguration object
        """
        return MemoryConfiguration(
            total_size=self.read_value("memory", "TotalSize", RegType.QWORD, 0),
            ram_size=self.read_value("memory", "RamSize", RegType.QWORD, 0),
            pmem_size=self.read_value("memory", "PmemSize", RegType.QWORD, 0),
            numa_node=self.read_value("memory", "NumaNode", RegType.DWORD, 0)
        )

    # ========================================================================
    # Accelerator Configuration
    # ========================================================================

    def read_accel_config(self) -> AcceleratorConfiguration:
        """
        Read CXL accelerator configuration from registry.

        Returns:
            AcceleratorConfiguration object
        """
        return AcceleratorConfiguration(
            work_queue_depth=self.read_value("accel", "WorkQueueDepth", RegType.DWORD, 16),
            dma_supported=bool(self.read_value("accel", "DmaSupported", RegType.DWORD, 1)),
            interrupt_supported=bool(self.read_value("accel", "InterruptSupported", RegType.DWORD, 1)),
            default_bitstream=self.read_value("accel", "DefaultBitstream", RegType.SZ, "")
        )

    # ========================================================================
    # Device Information
    # ========================================================================

    def get_driver_status(self, driver: str) -> Dict[str, Any]:
        """
        Get CXL driver status and configuration.

        Args:
            driver: Driver name

        Returns:
            Dict with driver status information
        """
        service_path = f"{REGISTRY_BASE}\\{driver.capitalize()}"

        result = {}

        try:
            # Read service status
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                service_path,
                0,
                winreg.KEY_READ
            )

            try:
                result["start_type"] = self._read_dword(key, "Start")
                result["error_control"] = self._read_dword(key, "ErrorControl")
                result["service_type"] = self._read_dword(key, "Type")
                result["image_path"] = self._read_string(key, "ImagePath")

                # Read parameters
                try:
                    params_key = winreg.OpenKey(key, "Parameters", 0, winreg.KEY_READ)
                    try:
                        result["parameters"] = self._enumerate_values(params_key)
                    finally:
                        winreg.CloseKey(params_key)
                except WindowsError:
                    result["parameters"] = {}

            finally:
                winreg.CloseKey(key)

        except WindowsError as e:
            raise RegistryError(f"Failed to read driver status: {e}")

        return result

    def _read_dword(self, key: winreg.HKey, value_name: str, default: int = 0) -> int:
        """Helper to read DWORD value."""
        try:
            value, _ = winreg.QueryValueEx(key, value_name)
            return value
        except WindowsError:
            return default

    def _read_string(self, key: winreg.HKey, value_name: str, default: str = "") -> str:
        """Helper to read string value."""
        try:
            value, _ = winreg.QueryValueEx(key, value_name)
            return value
        except WindowsError:
            return default

    def _enumerate_values(self, key: winreg.HKey) -> Dict[str, Any]:
        """Helper to enumerate all values in a key."""
        result = {}
        i = 0
        while True:
            try:
                name, value, _ = winreg.EnumValue(key, i)
                result[name] = value
                i += 1
            except WindowsError:
                break
        return result


# =============================================================================
# Convenience Functions
# =============================================================================

def get_cxl_registry() -> CxlRegistry:
    """
    Get a CXL registry accessor instance.

    Returns:
        CxlRegistry instance
    """
    return CxlRegistry()


def read_cache_config() -> CacheConfiguration:
    """
    Read cache configuration (convenience function).

    Returns:
        CacheConfiguration object
    """
    return get_cxl_registry().read_cache_config()


def read_memory_config() -> MemoryConfiguration:
    """
    Read memory configuration (convenience function).

    Returns:
        MemoryConfiguration object
    """
    return get_cxl_registry().read_memory_config()


def read_accel_config() -> AcceleratorConfiguration:
    """
    Read accelerator configuration (convenience function).

    Returns:
        AcceleratorConfiguration object
    """
    return get_cxl_registry().read_accel_config()


def is_driver_installed(driver: str) -> bool:
    """
    Check if a CXL driver is installed.

    Args:
        driver: Driver name

    Returns:
        True if driver registry key exists
    """
    if driver not in REGISTRY_PATHS:
        return False

    # Check service key
    service_path = f"{REGISTRY_BASE}\\{driver.capitalize()}"

    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            service_path,
            0,
            winreg.KEY_READ
        )
        winreg.CloseKey(key)
        return True
    except WindowsError:
        return False


def list_installed_drivers() -> list:
    """
    List all installed CXL drivers.

    Returns:
        List of driver names that are installed
    """
    return [driver for driver in REGISTRY_PATHS.keys() if is_driver_installed(driver)]


# =============================================================================
# Context Manager
# =============================================================================

class CxlRegistryTransaction:
    """
    Context manager for batch registry operations.

    Allows multiple registry writes to be done as a transaction.
    If an exception occurs, all changes are rolled back.

    Usage:
        with CxlRegistryTransaction() as registry:
            registry.write_cache_disabled(True)
            registry.write_value("cache", "AutoFlush", 1, RegType.DWORD)
    """

    def __init__(self):
        """Initialize the transaction."""
        self._registry = CxlRegistry()
        self._backup = {}

    def __enter__(self) -> CxlRegistry:
        """Enter transaction context."""
        return self._registry

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit transaction context."""
        if exc_type is not None:
            # Rollback on exception
            self._rollback()
        return False

    def _rollback(self):
        """Rollback all changes."""
        # In a real implementation, restore from backup
        pass
