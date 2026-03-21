"""CXL Memory Capture Layer.

Hardware interface for CXL Type 2 accelerators:
- mmap CXL memory windows via /dev/mem
- Trigger cache writeback+invalidate via sysfs (init_wbinvd)
- Read cache controller state from BAR2 MMIO
- Parse kernel tracepoints for CXL transactions
"""

import mmap
import os
import re
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# CXL device topology discovery
# ---------------------------------------------------------------------------

@dataclass
class CxlCacheInfo:
    """Cache device metadata from sysfs."""
    name: str                  # e.g. "cache0"
    pci_bdf: str               # e.g. "0000:3b:00.0"
    size: int                  # bytes
    unit: str                  # e.g. "128 MiB"
    numa_node: int
    disabled: bool
    invalid: bool
    sysfs_path: Path
    wbinvd_path: Optional[Path]  # write-only trigger for snapshot

    @classmethod
    def discover(cls, name: str) -> Optional["CxlCacheInfo"]:
        """Discover a cache device from /sys/bus/cxl/devices/{name}."""
        dev = Path(f"/sys/bus/cxl/devices/{name}")
        if not dev.exists():
            return None

        # Resolve actual sysfs path to find PCI BDF
        real = dev.resolve()
        pci_bdf = ""
        for part in real.parts:
            if re.match(r"[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-9]", part):
                pci_bdf = part
                break

        # Find the cache sysfs under the PCI device
        cache_sysfs = None
        if pci_bdf:
            pci_dev = Path(f"/sys/devices").glob(f"**/{pci_bdf}/{name}")
            for p in pci_dev:
                cache_sysfs = p
                break

        if cache_sysfs is None:
            cache_sysfs = dev

        def _read(attr: str) -> str:
            p = cache_sysfs / attr
            if p.exists():
                return p.read_text().strip()
            return ""

        size_val = _read("cache_size")
        wbinvd = cache_sysfs / "init_wbinvd"

        return cls(
            name=name,
            pci_bdf=pci_bdf,
            size=int(size_val) if size_val.isdigit() else 0,
            unit=_read("cache_unit"),
            numa_node=int(_read("numa_node") or "0"),
            disabled=_read("cache_disable") == "1",
            invalid=_read("cache_invalid") == "1",
            sysfs_path=cache_sysfs,
            wbinvd_path=wbinvd if wbinvd.exists() else None,
        )


@dataclass
class CxlMemInfo:
    """CXL memory device metadata."""
    name: str                  # e.g. "mem0"
    pci_bdf: str
    serial: str
    numa_node: int
    ram_size: int              # bytes
    pmem_size: int
    firmware_version: str

    @classmethod
    def discover(cls, name: str) -> Optional["CxlMemInfo"]:
        dev = Path(f"/sys/bus/cxl/devices/{name}")
        if not dev.exists():
            return None

        real = dev.resolve()
        pci_bdf = ""
        for part in real.parts:
            if re.match(r"[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-9]", part):
                pci_bdf = part
                break

        def _read(attr: str) -> str:
            p = dev / attr
            if p.exists():
                try:
                    return p.read_text().strip()
                except PermissionError:
                    return ""
            return ""

        def _size(subdir: str) -> int:
            val = _read(f"{subdir}/size")
            if val.startswith("0x"):
                return int(val, 16)
            return int(val) if val.isdigit() else 0

        return cls(
            name=name,
            pci_bdf=pci_bdf,
            serial=_read("serial"),
            numa_node=int(_read("numa_node") or "0"),
            ram_size=_size("ram"),
            pmem_size=_size("pmem"),
            firmware_version=_read("firmware_version"),
        )


@dataclass
class CxlWindow:
    """A CXL memory window from /proc/iomem."""
    index: int
    start: int    # physical address
    end: int
    size: int

    @staticmethod
    def discover_all() -> list["CxlWindow"]:
        windows = []
        try:
            with open("/proc/iomem") as f:
                for line in f:
                    m = re.match(
                        r"\s*([0-9a-f]+)-([0-9a-f]+)\s+:\s+CXL Window (\d+)",
                        line,
                    )
                    if m:
                        start = int(m.group(1), 16)
                        end = int(m.group(2), 16)
                        windows.append(CxlWindow(
                            index=int(m.group(3)),
                            start=start,
                            end=end,
                            size=end - start + 1,
                        ))
        except PermissionError:
            pass
        return windows


@dataclass
class CxlTopology:
    """Full CXL device topology on this system."""
    caches: list[CxlCacheInfo] = field(default_factory=list)
    mems: list[CxlMemInfo] = field(default_factory=list)
    windows: list[CxlWindow] = field(default_factory=list)

    @classmethod
    def discover(cls) -> "CxlTopology":
        topo = cls()

        # Discover cache devices
        cxl_dev = Path("/sys/bus/cxl/devices")
        if cxl_dev.exists():
            for d in sorted(cxl_dev.iterdir()):
                name = d.name
                if name.startswith("cache"):
                    info = CxlCacheInfo.discover(name)
                    if info:
                        topo.caches.append(info)
                elif name.startswith("mem"):
                    info = CxlMemInfo.discover(name)
                    if info:
                        topo.mems.append(info)

        topo.windows = CxlWindow.discover_all()
        return topo

    def summary(self) -> str:
        lines = ["CXL Topology:"]
        for c in self.caches:
            lines.append(
                f"  {c.name} @ {c.pci_bdf}: {c.size // (1024*1024)}MB "
                f"{'DISABLED' if c.disabled else 'active'} "
                f"wbinvd={'yes' if c.wbinvd_path else 'no'}"
            )
        for m in self.mems:
            lines.append(
                f"  {m.name} @ {m.pci_bdf}: RAM={m.ram_size // (1024*1024)}MB "
                f"PMEM={m.pmem_size // (1024*1024)}MB NUMA={m.numa_node}"
            )
        for w in self.windows:
            lines.append(
                f"  Window {w.index}: 0x{w.start:x}-0x{w.end:x} "
                f"({w.size // (1024*1024)}MB)"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# CXL memory reader (mmap /dev/mem at CXL window addresses)
# ---------------------------------------------------------------------------

class CxlMemoryReader:
    """Read CXL device memory via /dev/mem mmap."""

    def __init__(self):
        self._fd: Optional[int] = None
        self._mappings: dict[int, mmap.mmap] = {}  # base_addr -> mmap

    def open(self):
        if self._fd is None:
            self._fd = os.open("/dev/mem", os.O_RDONLY | os.O_SYNC)

    def close(self):
        for mm in self._mappings.values():
            mm.close()
        self._mappings.clear()
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *args):
        self.close()

    def read(self, phys_addr: int, size: int) -> bytes:
        """Read `size` bytes from physical address in CXL memory."""
        self.open()

        page_size = mmap.PAGESIZE
        page_base = phys_addr & ~(page_size - 1)
        offset_in_page = phys_addr - page_base
        map_size = offset_in_page + size
        # Round up to page boundary
        map_size = ((map_size + page_size - 1) // page_size) * page_size

        mm = mmap.mmap(
            self._fd, map_size, mmap.MAP_SHARED,
            mmap.PROT_READ, offset=page_base,
        )
        mm.seek(offset_in_page)
        data = mm.read(size)
        mm.close()
        return data

    def read_region(self, window: CxlWindow, offset: int = 0,
                    size: Optional[int] = None) -> bytes:
        """Read from a CXL window at given offset."""
        if size is None:
            size = min(window.size - offset, 4096)  # default to one page
        return self.read(window.start + offset, size)

    def scan_region(self, window: CxlWindow, offset: int = 0,
                    size: Optional[int] = None,
                    chunk_size: int = 4096) -> list[tuple[int, bytes]]:
        """Scan a CXL window in chunks, returning (offset, data) for non-empty chunks."""
        if size is None:
            size = window.size
        size = min(size, window.size - offset)
        results = []
        for off in range(0, size, chunk_size):
            try:
                data = self.read(window.start + offset + off, chunk_size)
                # Skip all-zero or all-0xFF pages
                if not all(b == 0 for b in data) and not all(b == 0xFF for b in data):
                    results.append((offset + off, data))
            except OSError:
                continue
        return results


# ---------------------------------------------------------------------------
# Cache snapshot via WBINVD
# ---------------------------------------------------------------------------

class CxlCacheController:
    """Control CXL cache devices for snapshots."""

    def __init__(self, cache: CxlCacheInfo):
        self.cache = cache

    def trigger_wbinvd(self) -> bool:
        """Trigger writeback+invalidate — flushes dirty cache lines to CXL memory.

        This is the core "snapshot" operation: after WBINVD completes,
        CXL memory contains a coherent view of all cached data.
        """
        if self.cache.wbinvd_path is None:
            return False
        try:
            self.cache.wbinvd_path.write_text("1")
            return True
        except (PermissionError, OSError):
            return False

    def is_invalid(self) -> bool:
        try:
            return (self.cache.sysfs_path / "cache_invalid").read_text().strip() == "1"
        except (PermissionError, OSError):
            return False

    def set_disabled(self, disable: bool) -> bool:
        try:
            (self.cache.sysfs_path / "cache_disable").write_text("1" if disable else "0")
            return True
        except (PermissionError, OSError):
            return False

    def read_bar2_state(self) -> Optional[bytes]:
        """Read cache controller MMIO registers from BAR2."""
        pci_bdf = self.cache.pci_bdf
        if not pci_bdf:
            return None
        resource2 = Path(f"/sys/devices").glob(f"**/{pci_bdf}/resource2")
        for res_path in resource2:
            try:
                fd = os.open(str(res_path), os.O_RDONLY | os.O_SYNC)
                size = os.fstat(fd).st_size
                if size == 0:
                    # resource2 is a PCI resource file — size from /resource
                    res_file = res_path.parent / "resource"
                    lines = res_file.read_text().strip().split("\n")
                    if len(lines) > 2:
                        parts = lines[2].split()
                        start, end = int(parts[0], 16), int(parts[1], 16)
                        size = end - start + 1
                mm = mmap.mmap(fd, size, mmap.MAP_SHARED, mmap.PROT_READ)
                data = mm.read(size)
                mm.close()
                os.close(fd)
                return data
            except (OSError, IndexError):
                continue
        return None


# ---------------------------------------------------------------------------
# CXL kernel tracepoint parser
# ---------------------------------------------------------------------------

@dataclass
class CxlTraceEvent:
    """A parsed CXL trace event."""
    timestamp: float
    cpu: int
    pid: int
    event_type: str           # e.g. "cxl_general_media", "cxl_dram"
    memdev: str
    serial: int
    transaction_type: str     # "Host Read", "Host Write", etc.
    dpa: int                  # Device Physical Address
    hpa: int                  # Host Physical Address
    raw: str                  # raw trace line


class CxlTracer:
    """Parse CXL kernel tracepoints from ftrace."""

    TRACE_DIR = Path("/sys/kernel/tracing")
    EVENTS = ["cxl_general_media", "cxl_dram", "cxl_poison"]

    def __init__(self):
        self._enabled: list[str] = []

    def enable(self, events: Optional[list[str]] = None):
        """Enable CXL tracepoints."""
        events = events or self.EVENTS
        for ev in events:
            enable_path = self.TRACE_DIR / "events" / "cxl" / ev / "enable"
            if enable_path.exists():
                try:
                    enable_path.write_text("1")
                    self._enabled.append(ev)
                except PermissionError:
                    pass

    def disable(self):
        """Disable previously enabled tracepoints."""
        for ev in self._enabled:
            enable_path = self.TRACE_DIR / "events" / "cxl" / ev / "enable"
            try:
                enable_path.write_text("0")
            except (PermissionError, OSError):
                pass
        self._enabled.clear()

    def read_trace(self, max_events: int = 1000) -> list[CxlTraceEvent]:
        """Read trace_pipe for CXL events (non-blocking snapshot)."""
        events = []
        trace_file = self.TRACE_DIR / "trace"
        if not trace_file.exists():
            return events

        try:
            text = trace_file.read_text()
        except (PermissionError, OSError):
            return events

        for line in text.split("\n"):
            if not any(ev in line for ev in self.EVENTS):
                continue
            parsed = self._parse_line(line)
            if parsed:
                events.append(parsed)
                if len(events) >= max_events:
                    break
        return events

    def clear_trace(self):
        """Clear the trace buffer."""
        try:
            (self.TRACE_DIR / "trace").write_text("")
        except (PermissionError, OSError):
            pass

    @staticmethod
    def _parse_line(line: str) -> Optional[CxlTraceEvent]:
        """Parse a single ftrace line for CXL events."""
        # Format: <task>-<pid> [<cpu>] <timestamp>: <event>: <fields>
        try:
            # Extract basic fields with regex
            m = re.match(
                r"\s*\S+-(\d+)\s+\[(\d+)\]\s+[\w.]+\s+([\d.]+):\s+"
                r"(cxl_\w+):\s+(.+)",
                line,
            )
            if not m:
                return None

            pid = int(m.group(1))
            cpu = int(m.group(2))
            timestamp = float(m.group(3))
            event_type = m.group(4)
            fields_str = m.group(5)

            # Extract key fields
            def _field(name: str, default: str = "") -> str:
                fm = re.search(rf"{name}=(\S+)", fields_str)
                return fm.group(1) if fm else default

            memdev = _field("memdev")
            serial = int(_field("serial", "0"))
            transaction_type = _field("transaction_type", "Unknown")
            # Clean up quoted transaction types
            transaction_type = transaction_type.strip("'\"")

            dpa_str = _field("dpa", "0")
            dpa = int(dpa_str, 16) if dpa_str.startswith("0x") else int(dpa_str or "0")

            hpa_str = _field("hpa", "0")
            hpa = int(hpa_str, 16) if hpa_str.startswith("0x") else int(hpa_str or "0")

            return CxlTraceEvent(
                timestamp=timestamp,
                cpu=cpu,
                pid=pid,
                event_type=event_type,
                memdev=memdev,
                serial=serial,
                transaction_type=transaction_type,
                dpa=dpa,
                hpa=hpa,
                raw=line.strip(),
            )
        except (ValueError, IndexError):
            return None
