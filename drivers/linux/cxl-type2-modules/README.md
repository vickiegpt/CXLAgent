# CXL Type 2 Linux Kernel Drivers

Out-of-tree kernel modules providing sysfs interfaces for CXL Type 2 accelerators.

## Overview

These drivers implement Linux kernel support for CXL Type 2 accelerators with:
- **Cache device sysfs** (`/sys/bus/cxl/devices/cache*`) with WBINVD trigger
- **Memory device sysfs** (`/sys/bus/cxl/devices/mem*`) with RAM/PMEM reporting
- **Tracepoints** (`/sys/kernel/tracing/events/cxl/`) for transaction monitoring
- **/proc/iomem** integration for CXL window discovery

## Components

| Module | Purpose | Sysfs Path |
|--------|---------|-------------|
| `cxl_type2_accel` | Main accelerator driver | Probes PCI devices |
| `cxl_type2_cache` | Cache device interface | `/sys/bus/cxl/devices/cache*` |
| `cxl_type2_mem` | Memory device interface | `/sys/bus/cxl/devices/mem*` |
| `cxl_iomem` | /proc/iomem integration | `/proc/iomem` |

## Supported Devices

- **QEMU CXL Type 2** (0x8086:0x0d92) - For emulation/testing
- **Intel IA-780I Agilex 7** (0x8086:0x0ddb) - Hardware FPGA

## Building

### Prerequisites

```bash
# Ubuntu/Debian
sudo apt install linux-headers-$(uname -r) build-essential

# Fedora/RHEL
sudo dnf install kernel-devel kernel-headers gcc make

# Arch
sudo pacman -S linux-headers base-devel
```

### Build Commands

```bash
cd drivers/linux/cxl-type2-modules

# Build all modules
make

# Or specify kernel build directory
make -C /lib/modules/$(uname -r)/build M=$(PWD) modules
```

## Installing

```bash
# Install modules to system
sudo make install

# Reload module dependencies
sudo depmod -a
```

## Loading Modules

```bash
# Load all CXL modules
sudo modprobe cxl_type2_accel
sudo modprobe cxl_type2_cache
sudo modprobe cxl_type2_mem
sudo modprobe cxl_iomem

# Verify loaded
lsmod | grep cxl

# Check sysfs
ls -la /sys/bus/cxl/devices/

# Check tracepoints
ls -la /sys/kernel/tracing/events/cxl/

# Check /proc/iomem
cat /proc/iomem | grep CXL
```

## Unloading Modules

```bash
# Unload in reverse order
sudo modprobe -r cxl_iomem
sudo modprobe -r cxl_type2_mem
sudo modprobe -r cxl_type2_cache
sudo modprobe -r cxl_type2_accel
```

## Sysfs Attributes

### Cache Devices (`/sys/bus/cxl/devices/cache*`)

| Attribute | Type | Description |
|-----------|------|-------------|
| `cache_size` | RO | Cache size in bytes |
| `cache_unit` | RO | Cache line size (bytes) |
| `numa_node` | RO | NUMA node ID |
| `cache_disable` | RW | Disable cache (1=disabled, 0=enabled) |
| `cache_invalid` | RO | Cache invalid state |
| `init_wbinvd` | WO | Trigger WBINVD (write "1") |
| `resource2` | RO | BAR2 MMIO range |

### Memory Devices (`/sys/bus/cxl/devices/mem*`)

| Attribute | Type | Description |
|-----------|------|-------------|
| `serial` | RO | Device serial number |
| `numa_node` | RO | NUMA node ID |
| `firmware_version` | RO | Firmware version string |
| `ram/size` | RO | Volatile memory size (hex) |
| `pmem/size` | RO | Persistent memory size (hex) |

## Tracepoints

Enable CXL transaction tracing:

```bash
# Enable tracepoints
echo 1 > /sys/kernel/tracing/events/cxl/enable
echo 1 > /sys/kernel/tracing/events/cxl/cxl_general_media/enable
echo 1 > /sys/kernel/tracing/events/cxl/cxl_dram/enable
echo 1 > /sys/kernel/tracing/events/cxl/cxl_poison/enable

# Read trace log
cat /sys/kernel/tracing/trace

# Or with trace-cmd
trace-cmd record -e cxl:* -a
trace-cmd report
```

## Python Integration

The existing `cxlagent/capture.py` works with these drivers:

```python
from cxlagent.capture import CxlTopology

# Discover CXL devices
topo = CxlTopology.discover()
print(topo.summary())

# Trigger cache flush
for cache in topo.caches:
    with open(cache.wbinvd_path, 'w') as f:
        f.write('1')

# Read CXL windows
for window in topo.windows:
    data = reader.read(window.start, window.size)
```

## Testing

### Unit Test

```bash
# Check module loading
sudo ./scripts/load_modules.sh

# Verify sysfs
./scripts/verify_sysfs.sh

# Test tracepoints
./scripts/test_tracepoints.sh
```

### Integration Test

```bash
# Run Python capture test
cd ../..
python -m cxlagent.cli capture
```

## Troubleshooting

### Module won't load

```bash
# Check kernel log
dmesg | tail -50

# Check module dependencies
modprobe --show-depends cxl_type2_accel

# Verify kernel version compatibility
uname -r
modinfo cxl_type2_accel.ko
```

### Sysfs not appearing

```bash
# Check if CXL bus exists
ls /sys/bus/

# Check device registration
ls /sys/devices/

# Verify with udev
udevadm info --attribute-walk --name=/dev/cxl*
```

### Tracepoints not working

```bash
# Check ftrace is enabled
cat /proc/sys/kernel/ftrace_enabled

# Verify tracepoint registration
cat /sys/kernel/tracing/available_events | grep cxl
```

## License

GPL-2.0-only

## Author

CXLAgent Project

## References

- [CXL Specification 3.0](https://www.computeexpresslink.org/)
- [Linux CXL Subsystem](https://www.kernel.org/doc/html/latest/driver-api/cxl/)
- [Python capture.py](../../cxlagent/capture.py)
