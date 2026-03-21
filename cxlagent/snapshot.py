"""Snapshot Engine — periodic CXL memory captures with binary diffing.

Flow:
1. Trigger WBINVD on each cache device (flush dirty lines to CXL memory)
2. Read CXL memory window regions
3. Compute binary diff against previous snapshot
4. Run pattern detection on changed regions
5. Store in SQLite timeline
"""

import hashlib
import time
from dataclasses import dataclass, field
from typing import Optional

from .capture import (
    CxlCacheController,
    CxlMemoryReader,
    CxlTopology,
    CxlTraceEvent,
    CxlWindow,
)


@dataclass
class MemoryChunk:
    """A chunk of captured CXL memory."""
    window_index: int
    offset: int           # offset within the CXL window
    size: int
    data: bytes
    phys_addr: int        # absolute physical address
    sha256: str = ""

    def __post_init__(self):
        if not self.sha256:
            self.sha256 = hashlib.sha256(self.data).hexdigest()[:16]

    @property
    def is_zero(self) -> bool:
        return all(b == 0 for b in self.data)

    @property
    def is_ones(self) -> bool:
        return all(b == 0xFF for b in self.data)

    @property
    def is_empty(self) -> bool:
        return self.is_zero or self.is_ones


@dataclass
class DiffRegion:
    """A region that changed between two snapshots."""
    window_index: int
    offset: int
    size: int
    phys_addr: int
    old_data: bytes
    new_data: bytes
    changed_bytes: int     # count of bytes that differ

    @property
    def change_ratio(self) -> float:
        return self.changed_bytes / self.size if self.size else 0.0


@dataclass
class Snapshot:
    """A point-in-time capture of CXL memory state."""
    snapshot_id: int
    timestamp: float
    chunks: list[MemoryChunk] = field(default_factory=list)
    trace_events: list[CxlTraceEvent] = field(default_factory=list)
    cache_states: dict[str, dict] = field(default_factory=dict)  # cache_name -> state
    wbinvd_triggered: list[str] = field(default_factory=list)
    capture_duration_ms: float = 0.0

    @property
    def total_bytes(self) -> int:
        return sum(c.size for c in self.chunks)

    @property
    def non_empty_chunks(self) -> list[MemoryChunk]:
        return [c for c in self.chunks if not c.is_empty]

    def summary(self) -> str:
        non_empty = len(self.non_empty_chunks)
        return (
            f"Snapshot #{self.snapshot_id} @ {time.strftime('%H:%M:%S', time.localtime(self.timestamp))}: "
            f"{len(self.chunks)} chunks ({non_empty} non-empty), "
            f"{self.total_bytes // 1024}KB captured in {self.capture_duration_ms:.1f}ms, "
            f"{len(self.trace_events)} trace events, "
            f"WBINVD on {self.wbinvd_triggered}"
        )


class SnapshotEngine:
    """Takes CXL memory snapshots and computes diffs."""

    def __init__(
        self,
        topology: Optional[CxlTopology] = None,
        scan_size: int = 64 * 1024 * 1024,  # how much of each window to scan
        chunk_size: int = 4096,               # granularity
    ):
        self.topology = topology or CxlTopology.discover()
        self.scan_size = scan_size
        self.chunk_size = chunk_size
        self._reader = CxlMemoryReader()
        self._cache_controllers: list[CxlCacheController] = [
            CxlCacheController(c) for c in self.topology.caches
        ]
        self._snapshot_counter = 0
        self._last_snapshot: Optional[Snapshot] = None

    def take_snapshot(
        self,
        windows: Optional[list[int]] = None,
        trigger_wbinvd: bool = True,
        scan_offset: int = 0,
        scan_size: Optional[int] = None,
    ) -> Snapshot:
        """Take a coherent snapshot of CXL memory.

        Args:
            windows: Which CXL window indices to capture. None = all.
            trigger_wbinvd: Whether to flush caches first (recommended).
            scan_offset: Starting offset within each window.
            scan_size: How many bytes to scan per window.
        """
        t0 = time.monotonic()
        self._snapshot_counter += 1
        scan_size = scan_size or self.scan_size

        snap = Snapshot(
            snapshot_id=self._snapshot_counter,
            timestamp=time.time(),
        )

        # Step 1: Trigger WBINVD to flush dirty cache lines
        if trigger_wbinvd:
            for ctrl in self._cache_controllers:
                if ctrl.trigger_wbinvd():
                    snap.wbinvd_triggered.append(ctrl.cache.name)

            # Record cache state after flush
            for ctrl in self._cache_controllers:
                snap.cache_states[ctrl.cache.name] = {
                    "invalid": ctrl.is_invalid(),
                    "size": ctrl.cache.size,
                    "disabled": ctrl.cache.disabled,
                }

        # Step 2: Read CXL memory windows
        target_windows = self.topology.windows
        if windows is not None:
            target_windows = [w for w in target_windows if w.index in windows]

        self._reader.open()
        for window in target_windows:
            actual_size = min(scan_size, window.size - scan_offset)
            if actual_size <= 0:
                continue

            for off in range(0, actual_size, self.chunk_size):
                try:
                    data = self._reader.read(
                        window.start + scan_offset + off,
                        min(self.chunk_size, actual_size - off),
                    )
                    snap.chunks.append(MemoryChunk(
                        window_index=window.index,
                        offset=scan_offset + off,
                        size=len(data),
                        data=data,
                        phys_addr=window.start + scan_offset + off,
                    ))
                except OSError:
                    continue

        snap.capture_duration_ms = (time.monotonic() - t0) * 1000

        self._last_snapshot = snap
        return snap

    def diff(self, old: Snapshot, new: Snapshot) -> list[DiffRegion]:
        """Compute binary diff between two snapshots."""
        diffs = []

        # Build lookup: (window_index, offset) -> chunk
        old_map = {(c.window_index, c.offset): c for c in old.chunks}
        new_map = {(c.window_index, c.offset): c for c in new.chunks}

        # Find changed chunks
        for key, new_chunk in new_map.items():
            old_chunk = old_map.get(key)
            if old_chunk is None:
                # New region not in old snapshot
                diffs.append(DiffRegion(
                    window_index=new_chunk.window_index,
                    offset=new_chunk.offset,
                    size=new_chunk.size,
                    phys_addr=new_chunk.phys_addr,
                    old_data=b"\x00" * new_chunk.size,
                    new_data=new_chunk.data,
                    changed_bytes=new_chunk.size,
                ))
                continue

            if old_chunk.sha256 == new_chunk.sha256:
                continue  # identical

            # Count changed bytes
            changed = sum(
                1 for a, b in zip(old_chunk.data, new_chunk.data) if a != b
            )
            if changed > 0:
                diffs.append(DiffRegion(
                    window_index=new_chunk.window_index,
                    offset=new_chunk.offset,
                    size=new_chunk.size,
                    phys_addr=new_chunk.phys_addr,
                    old_data=old_chunk.data,
                    new_data=new_chunk.data,
                    changed_bytes=changed,
                ))

        # Sort by physical address
        diffs.sort(key=lambda d: d.phys_addr)
        return diffs

    @property
    def last_snapshot(self) -> Optional[Snapshot]:
        return self._last_snapshot
