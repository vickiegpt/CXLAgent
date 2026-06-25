"""
Platform Detection and Abstraction Layer.

Provides platform detection and unified interfaces for Linux and Windows,
allowing CXLAgent to work on both operating systems.

This module automatically detects the platform and imports the appropriate
platform-specific implementations.
"""

import sys
import os
from enum import Enum
from typing import Optional, Callable, Any
import platform as sys_platform


# =============================================================================
# Platform Enumeration
# =============================================================================

class Platform(Enum):
    """Supported operating systems."""
    LINUX = "linux"
    WINDOWS = "windows"
    UNKNOWN = "unknown"


class Architecture(Enum):
    """System architectures."""
    X86_64 = "x86_64"
    ARM64 = "arm64"
    UNKNOWN = "unknown"


# =============================================================================
# Platform Detection
# =============================================================================

def get_platform() -> Platform:
    """
    Detect the current platform.

    Returns:
        Platform enum value
    """
    if sys_platform.system() == "Linux":
        return Platform.LINUX
    elif sys_platform.system() == "Windows":
        return Platform.WINDOWS
    else:
        return Platform.UNKNOWN


def get_architecture() -> Architecture:
    """
    Detect the system architecture.

    Returns:
        Architecture enum value
    """
    machine = sys_platform.machine().lower()

    if machine in ("x86_64", "amd64"):
        return Architecture.X86_64
    elif machine in ("aarch64", "arm64"):
        return Architecture.ARM64
    else:
        return Architecture.UNKNOWN


def is_linux() -> bool:
    """Check if running on Linux."""
    return get_platform() == Platform.LINUX


def is_windows() -> bool:
    """Check if running on Windows."""
    return get_platform() == Platform.WINDOWS


def is_supported_platform() -> bool:
    """Check if the current platform is supported."""
    return get_platform() in (Platform.LINUX, Platform.WINDOWS)


# =============================================================================
# Platform-Specific Imports
# =============================================================================

# Lazy import of platform-specific modules
_capture_module = None
_memory_module = None
_trace_module = None
_registry_module = None


def get_capture_module():
    """
    Get the platform-specific capture module.

    Returns:
        capture_linux or capture_windows module
    """
    global _capture_module

    if _capture_module is None:
        if is_linux():
            from . import capture as capture_linux
            _capture_module = capture_linux
        elif is_windows():
            from . import capture_windows as capture_windows_module
            _capture_module = capture_windows_module
        else:
            raise ImportError(f"Platform {get_platform()} not supported")

    return _capture_module


def get_memory_module():
    """
    Get the platform-specific memory module.

    Returns:
        Memory module for the current platform
    """
    global _memory_module

    if _memory_module is None:
        if is_linux():
            # Linux uses direct /dev/mem access in capture module
            _memory_module = get_capture_module()
        elif is_windows():
            from . import memory_windows as memory_windows_module
            _memory_module = memory_windows_module
        else:
            raise ImportError(f"Platform {get_platform()} not supported")

    return _memory_module


def get_trace_module():
    """
    Get the platform-specific trace module.

    Returns:
        Trace module for the current platform
    """
    global _trace_module

    if _trace_module is None:
        if is_linux():
            # Linux uses ftrace via /sys/kernel/tracing
            from . import trace_linux as trace_linux_module
            _trace_module = trace_linux_module
        elif is_windows():
            from . import trace_windows as trace_windows_module
            _trace_module = trace_windows_module
        else:
            raise ImportError(f"Platform {get_platform()} not supported")

    return _trace_module


def get_registry_module():
    """
    Get the platform-specific configuration/registry module.

    Returns:
        Configuration module for the current platform
    """
    global _registry_module

    if _registry_module is None:
        if is_linux():
            # Linux uses sysfs for configuration
            from . import config_linux as config_linux_module
            _registry_module = config_linux_module
        elif is_windows():
            from . import registry as registry_module
            _registry_module = registry_module
        else:
            raise ImportError(f"Platform {get_platform()} not supported")

    return _registry_module


# =============================================================================
# Platform Information
# =============================================================================

def get_platform_info() -> dict:
    """
    Get detailed platform information.

    Returns:
        Dict with platform details
    """
    return {
        "platform": get_platform().value,
        "architecture": get_architecture().value,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "system": sys_platform.system(),
        "release": sys_platform.release(),
        "version": sys_platform.version(),
        "machine": sys_platform.machine(),
        "processor": sys_platform.processor(),
    }


def print_platform_info():
    """Print platform information to console."""
    info = get_platform_info()

    print("CXLAgent Platform Information")
    print("=" * 40)
    print(f"Platform:      {info['platform']}")
    print(f"Architecture:  {info['architecture']}")
    print(f"Python:        {info['python_version']}")
    print(f"System:        {info['system']}")
    print(f"Release:       {info['release']}")
    print(f"Machine:       {info['machine']}")
    print(f"Processor:     {info['processor']}")
    print("=" * 40)


# =============================================================================
# Platform-Specific Paths
# =============================================================================

class PlatformPaths:
    """Platform-specific file system paths."""

    @staticmethod
    def get_cxl_bus_path() -> str:
        """Get CXL bus device path."""
        if is_linux():
            return "/sys/bus/cxl/devices"
        elif is_windows():
            return r"\\.\CXLBus0"
        else:
            raise NotImplementedError(f"Platform {get_platform()} not supported")

    @staticmethod
    def get_memory_device_path() -> str:
        """Get memory device path."""
        if is_linux():
            return "/dev/mem"
        elif is_windows():
            return r"\\.\CXLMemory0"
        else:
            raise NotImplementedError(f"Platform {get_platform()} not supported")

    @staticmethod
    def get_trace_path() -> str:
        """Get trace interface path."""
        if is_linux():
            return "/sys/kernel/tracing"
        elif is_windows():
            return "ETW"  # ETW is the interface, not a path
        else:
            raise NotImplementedError(f"Platform {get_platform()} not supported")

    @staticmethod
    def get_config_path(driver: str) -> str:
        """
        Get configuration path for a driver.

        Args:
            driver: Driver name

        Returns:
            Configuration path
        """
        if is_linux():
            return f"/sys/bus/cxl/devices/{driver}"
        elif is_windows():
            # Windows registry path
            return f"HKLM\\SYSTEM\\CurrentControlSet\\Services\\{driver.capitalize()}\\Parameters"
        else:
            raise NotImplementedError(f"Platform {get_platform()} not supported")


# =============================================================================
# Platform Utilities
# =============================================================================

def platform_not_supported(feature: str) -> Callable:
    """
    Decorator to mark functions as not supported on certain platforms.

    Args:
        feature: Name of the feature/function

    Returns:
        Decorator function
    """
    def decorator(func: Callable) -> Callable:
        def wrapper(*args, **kwargs):
            if not is_supported_platform():
                raise NotImplementedError(
                    f"{feature} is not supported on {get_platform().value}"
                )
            return func(*args, **kwargs)
        return wrapper
    return decorator


def requires_platform(required_platform: Platform) -> Callable:
    """
    Decorator to require a specific platform.

    Args:
        required_platform: Required platform

    Returns:
        Decorator function
    """
    def decorator(func: Callable) -> Callable:
        def wrapper(*args, **kwargs):
            if get_platform() != required_platform:
                raise NotImplementedError(
                    f"{func.__name__} requires {required_platform.value}"
                )
            return func(*args, **kwargs)
        return wrapper
    return decorator


# =============================================================================
# Platform Compatibility Layer
# =============================================================================

class PlatformAdapter:
    """
    Platform compatibility adapter.

    Provides a unified interface that works on both Linux and Windows.
    Automatically selects the correct implementation based on platform.
    """

    def __init__(self):
        """Initialize the platform adapter."""
        self._platform = get_platform()
        self._capture = None
        self._memory = None
        self._trace = None
        self._config = None

    def initialize(self):
        """Initialize platform-specific components."""
        self._capture = get_capture_module()
        self._memory = get_memory_module()
        self._trace = get_trace_module()
        self._config = get_registry_module()

    def get_driver(self):
        """Get the CXL driver interface for this platform."""
        if self._capture is None:
            self.initialize()

        if self._platform == Platform.WINDOWS:
            return self._capture.CxlWindowsDriver()
        else:
            # Linux doesn't have a driver class, returns None
            return None

    def get_memory_reader(self, driver=None):
        """
        Get the memory reader for this platform.

        Args:
            driver: Optional driver instance (Windows)

        Returns:
            Memory reader instance
        """
        if self._memory is None:
            self.initialize()

        if self._platform == Platform.WINDOWS:
            return self._memory.CxlMemoryReader(driver)
        else:
            # Linux memory reading is integrated in capture module
            return None

    def get_tracer(self):
        """Get the tracer for this platform."""
        if self._trace is None:
            self.initialize()

        if self._platform == Platform.WINDOWS:
            return self._trace.CxlETWConsumer()
        else:
            return self._trace.CxlFtraceConsumer()

    def get_config(self):
        """Get the configuration accessor for this platform."""
        if self._config is None:
            self.initialize()

        if self._platform == Platform.WINDOWS:
            return self._config.CxlRegistry()
        else:
            return self._config.SysfsConfig()


# =============================================================================
# Auto-Detection on Import
# =============================================================================

# Detect platform on module import
CURRENT_PLATFORM = get_platform()
CURRENT_ARCHITECTURE = get_architecture()

# Print platform info when run as script
if __name__ == "__main__":
    print_platform_info()

    # Test platform detection
    print(f"\nIs Linux: {is_linux()}")
    print(f"Is Windows: {is_windows()}")
    print(f"Is Supported: {is_supported_platform()}")

    # Test paths
    print(f"\nCXL Bus Path: {PlatformPaths.get_cxl_bus_path()}")
    print(f"Memory Path: {PlatformPaths.get_memory_device_path()}")
    print(f"Trace Path: {PlatformPaths.get_trace_path()}")
