"""Tests for cxlagent.snapshot — MemoryChunk, DiffRegion, Snapshot, SnapshotEngine."""

import hashlib
import time

from cxlagent.snapshot import DiffRegion, MemoryChunk, Snapshot, SnapshotEngine
from cxlagent.capture import CxlTopology


# ---------------------------------------------------------------------------
# MemoryChunk
# ---------------------------------------------------------------------------

class TestMemoryChunk:
    def _chunk(self, data: bytes, **kwargs) -> MemoryChunk:
        return MemoryChunk(
            window_index=kwargs.get("window_index", 0),
            offset=kwargs.get("offset", 0),
            size=len(data),
            data=data,
            phys_addr=kwargs.get("phys_addr", 0x4000_0000),
        )

    def test_sha256_auto_computed(self):
        data = b"test data for sha"
        chunk = self._chunk(data)
        expected = hashlib.sha256(data).hexdigest()[:16]
        assert chunk.sha256 == expected

    def test_sha256_manual_override(self):
        data = b"test data"
        chunk = MemoryChunk(
            window_index=0, offset=0, size=len(data),
            data=data, phys_addr=0, sha256="abcdef1234567890"
        )
        assert chunk.sha256 == "abcdef1234567890"

    def test_is_zero_true(self):
        assert self._chunk(bytes(64)).is_zero is True

    def test_is_zero_false(self):
        assert self._chunk(b"\x00" * 63 + b"\x01").is_zero is False

    def test_is_ones_true(self):
        assert self._chunk(b"\xFF" * 64).is_ones is True

    def test_is_ones_false(self):
        assert self._chunk(b"\xFF" * 63 + b"\x00").is_ones is False

    def test_is_empty_zero(self):
        assert self._chunk(bytes(32)).is_empty is True

    def test_is_empty_ones(self):
        assert self._chunk(b"\xFF" * 32).is_empty is True

    def test_is_empty_false(self):
        assert self._chunk(b"\x01" + bytes(31)).is_empty is False

    def test_size_field(self):
        data = bytes(128)
        chunk = self._chunk(data)
        assert chunk.size == 128

    def test_different_data_different_sha256(self):
        c1 = self._chunk(b"AAA")
        c2 = self._chunk(b"BBB")
        assert c1.sha256 != c2.sha256

    def test_same_data_same_sha256(self):
        data = b"same data"
        c1 = self._chunk(data)
        c2 = self._chunk(data)
        assert c1.sha256 == c2.sha256


# ---------------------------------------------------------------------------
# DiffRegion
# ---------------------------------------------------------------------------

class TestDiffRegion:
    def test_change_ratio_all_changed(self):
        diff = DiffRegion(
            window_index=0, offset=0, size=100,
            phys_addr=0, old_data=bytes(100),
            new_data=b"\x01" * 100, changed_bytes=100,
        )
        assert diff.change_ratio == 1.0

    def test_change_ratio_half_changed(self):
        diff = DiffRegion(
            window_index=0, offset=0, size=100,
            phys_addr=0, old_data=bytes(100),
            new_data=b"\x01" * 100, changed_bytes=50,
        )
        assert diff.change_ratio == 0.5

    def test_change_ratio_zero_size(self):
        diff = DiffRegion(
            window_index=0, offset=0, size=0,
            phys_addr=0, old_data=b"", new_data=b"", changed_bytes=0,
        )
        assert diff.change_ratio == 0.0

    def test_change_ratio_no_changes(self):
        diff = DiffRegion(
            window_index=0, offset=0, size=64,
            phys_addr=0, old_data=bytes(64),
            new_data=bytes(64), changed_bytes=0,
        )
        assert diff.change_ratio == 0.0


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

class TestSnapshot:
    def _make_snapshot(self, chunks: list[MemoryChunk]) -> Snapshot:
        return Snapshot(
            snapshot_id=1,
            timestamp=time.time(),
            chunks=chunks,
        )

    def _chunk(self, data: bytes, offset: int = 0) -> MemoryChunk:
        return MemoryChunk(
            window_index=0, offset=offset,
            size=len(data), data=data, phys_addr=0,
        )

    def test_total_bytes(self):
        snap = self._make_snapshot([
            self._chunk(bytes(100)),
            self._chunk(bytes(200)),
        ])
        assert snap.total_bytes == 300

    def test_total_bytes_empty(self):
        snap = self._make_snapshot([])
        assert snap.total_bytes == 0

    def test_non_empty_chunks_excludes_zeros(self):
        snap = self._make_snapshot([
            self._chunk(bytes(64)),            # zero → empty
            self._chunk(b"\x01" * 64),         # non-zero → non-empty
            self._chunk(b"\xFF" * 64),         # all-0xFF → empty
        ])
        assert len(snap.non_empty_chunks) == 1

    def test_non_empty_chunks_all_empty(self):
        snap = self._make_snapshot([self._chunk(bytes(64))])
        assert snap.non_empty_chunks == []

    def test_non_empty_chunks_all_non_empty(self):
        snap = self._make_snapshot([
            self._chunk(b"\x01" + bytes(63)),
            self._chunk(b"\x02" + bytes(63)),
        ])
        assert len(snap.non_empty_chunks) == 2

    def test_summary_contains_snapshot_id(self):
        snap = self._make_snapshot([])
        snap.snapshot_id = 42
        assert "#42" in snap.summary()

    def test_summary_contains_chunk_count(self):
        snap = self._make_snapshot([
            self._chunk(bytes(64)),
            self._chunk(b"\x01" * 64),
        ])
        summary = snap.summary()
        assert "2 chunks" in summary


# ---------------------------------------------------------------------------
# SnapshotEngine.diff
# ---------------------------------------------------------------------------

class TestSnapshotEngineDiff:
    def _make_engine(self) -> SnapshotEngine:
        topo = CxlTopology()  # empty topology, no hardware required
        return SnapshotEngine(topology=topo, scan_size=4096, chunk_size=4096)

    def _make_snapshot(self, chunks: list[MemoryChunk], snap_id: int = 1) -> Snapshot:
        return Snapshot(
            snapshot_id=snap_id,
            timestamp=time.time(),
            chunks=chunks,
        )

    def _chunk(self, data: bytes, window_index: int = 0,
               offset: int = 0, phys_addr: int = 0x4000_0000) -> MemoryChunk:
        return MemoryChunk(
            window_index=window_index,
            offset=offset,
            size=len(data),
            data=data,
            phys_addr=phys_addr,
        )

    def test_no_changes_empty_diff(self):
        engine = self._make_engine()
        data = b"\xAA" * 64
        snap_a = self._make_snapshot([self._chunk(data)])
        snap_b = self._make_snapshot([self._chunk(data)])
        diffs = engine.diff(snap_a, snap_b)
        assert diffs == []

    def test_changed_region_detected(self):
        engine = self._make_engine()
        old_data = bytes(64)
        new_data = b"\x01" * 64
        snap_a = self._make_snapshot([self._chunk(old_data)])
        snap_b = self._make_snapshot([self._chunk(new_data)])
        diffs = engine.diff(snap_a, snap_b)
        assert len(diffs) == 1
        assert diffs[0].changed_bytes == 64

    def test_new_region_not_in_old(self):
        engine = self._make_engine()
        snap_a = self._make_snapshot([])
        snap_b = self._make_snapshot([self._chunk(b"\x01" * 32)])
        diffs = engine.diff(snap_a, snap_b)
        assert len(diffs) == 1
        assert diffs[0].changed_bytes == 32

    def test_partial_change(self):
        engine = self._make_engine()
        old_data = bytes(64)
        new_data = b"\x00" * 32 + b"\x01" * 32
        snap_a = self._make_snapshot([self._chunk(old_data)])
        snap_b = self._make_snapshot([self._chunk(new_data)])
        diffs = engine.diff(snap_a, snap_b)
        assert len(diffs) == 1
        assert diffs[0].changed_bytes == 32

    def test_multiple_windows_independent(self):
        engine = self._make_engine()
        chunk_a0 = self._chunk(bytes(32), window_index=0, offset=0, phys_addr=0x4000_0000)
        chunk_a1 = self._chunk(b"\xAA" * 32, window_index=1, offset=0, phys_addr=0x5000_0000)
        snap_a = self._make_snapshot([chunk_a0, chunk_a1])

        chunk_b0 = self._chunk(b"\x01" * 32, window_index=0, offset=0, phys_addr=0x4000_0000)
        chunk_b1 = self._chunk(b"\xAA" * 32, window_index=1, offset=0, phys_addr=0x5000_0000)
        snap_b = self._make_snapshot([chunk_b0, chunk_b1])

        diffs = engine.diff(snap_a, snap_b)
        assert len(diffs) == 1
        assert diffs[0].window_index == 0

    def test_diff_sorted_by_phys_addr(self):
        engine = self._make_engine()
        snap_a = self._make_snapshot([])
        chunks_b = [
            self._chunk(b"\x01" * 32, window_index=0, offset=0x200, phys_addr=0x5000_0200),
            self._chunk(b"\x02" * 32, window_index=0, offset=0x100, phys_addr=0x5000_0100),
        ]
        snap_b = self._make_snapshot(chunks_b)
        diffs = engine.diff(snap_a, snap_b)
        assert len(diffs) == 2
        assert diffs[0].phys_addr < diffs[1].phys_addr

    def test_diff_old_data_preserved(self):
        engine = self._make_engine()
        old_data = b"\xDE\xAD\xBE\xEF" * 16
        new_data = b"\xCA\xFE\xBA\xBE" * 16
        snap_a = self._make_snapshot([self._chunk(old_data)])
        snap_b = self._make_snapshot([self._chunk(new_data)])
        diffs = engine.diff(snap_a, snap_b)
        assert len(diffs) == 1
        assert diffs[0].old_data == old_data
        assert diffs[0].new_data == new_data
