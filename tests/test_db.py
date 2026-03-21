"""Tests for cxlagent.db — TimelineDB SQLite storage."""

import time

import pytest

from cxlagent.db import TimelineDB
from cxlagent.patterns import Pattern, PatternType
from cxlagent.snapshot import DiffRegion, MemoryChunk, Snapshot


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    """Provide a TimelineDB backed by a temp directory."""
    db_path = tmp_path / "test_timeline.db"
    d = TimelineDB(db_path=db_path)
    yield d
    d.close()


def _make_snapshot(snap_id: int = 1) -> Snapshot:
    chunks = [
        MemoryChunk(
            window_index=0,
            offset=i * 4096,
            size=4096,
            data=bytes(4096) if i % 2 == 0 else b"\x01" * 4096,
            phys_addr=0x4000_0000 + i * 4096,
        )
        for i in range(4)
    ]
    snap = Snapshot(
        snapshot_id=snap_id,
        timestamp=time.time(),
        chunks=chunks,
        capture_duration_ms=10.5,
    )
    snap.cache_states["cache0"] = {"invalid": False, "size": 128 * 1024 * 1024, "disabled": False}
    snap.wbinvd_triggered = ["cache0"]
    return snap


def _make_pattern(offset: int = 0) -> Pattern:
    return Pattern(
        pattern_type=PatternType.ASCII_STRING,
        offset=offset,
        size=20,
        confidence=0.9,
        description='String: "test string"',
        data_preview=b"test string data123!",
        metadata={"string": "test string data123!"},
    )


# ---------------------------------------------------------------------------
# Schema / initialization
# ---------------------------------------------------------------------------

class TestTimelineDBInit:
    def test_db_file_created(self, tmp_path):
        db_path = tmp_path / "sub" / "nested" / "test.db"
        db = TimelineDB(db_path=db_path)
        assert db_path.exists()
        db.close()

    def test_tables_created(self, db):
        cur = db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in cur.fetchall()}
        assert "snapshots" in tables
        assert "patterns" in tables
        assert "diffs" in tables
        assert "analyses" in tables
        assert "trace_events" in tables


# ---------------------------------------------------------------------------
# save_snapshot / get_recent_snapshots
# ---------------------------------------------------------------------------

class TestSnapshotStorage:
    def test_save_and_retrieve_snapshot(self, db):
        snap = _make_snapshot(snap_id=1)
        db.save_snapshot(snap)
        results = db.get_recent_snapshots(limit=10)
        assert len(results) == 1
        assert results[0]["id"] == 1

    def test_snapshot_fields_stored_correctly(self, db):
        snap = _make_snapshot(snap_id=7)
        db.save_snapshot(snap)
        row = db.get_recent_snapshots(limit=1)[0]
        assert row["id"] == 7
        assert row["non_empty_chunks"] == len(snap.non_empty_chunks)
        assert row["total_bytes"] == snap.total_bytes
        assert abs(row["capture_duration_ms"] - snap.capture_duration_ms) < 0.01

    def test_multiple_snapshots_ordered_by_time(self, db):
        snap1 = _make_snapshot(snap_id=1)
        time.sleep(0.01)
        snap2 = _make_snapshot(snap_id=2)
        db.save_snapshot(snap1)
        db.save_snapshot(snap2)
        results = db.get_recent_snapshots(limit=10)
        # Most recent first
        assert results[0]["id"] == 2
        assert results[1]["id"] == 1

    def test_limit_respected(self, db):
        for i in range(1, 6):
            db.save_snapshot(_make_snapshot(snap_id=i))
        results = db.get_recent_snapshots(limit=3)
        assert len(results) == 3


# ---------------------------------------------------------------------------
# save_patterns / get_patterns_for_snapshot
# ---------------------------------------------------------------------------

class TestPatternStorage:
    def test_save_and_retrieve_patterns(self, db):
        snap = _make_snapshot(snap_id=1)
        db.save_snapshot(snap)
        patterns = [_make_pattern(offset=i * 16) for i in range(3)]
        db.save_patterns(snap.snapshot_id, patterns, base_phys_addr=0x4000_0000)
        rows = db.get_patterns_for_snapshot(snap.snapshot_id)
        assert len(rows) == 3

    def test_pattern_phys_addr_calculated(self, db):
        snap = _make_snapshot(snap_id=1)
        db.save_snapshot(snap)
        p = _make_pattern(offset=0x100)
        db.save_patterns(snap.snapshot_id, [p], base_phys_addr=0x4000_0000)
        rows = db.get_patterns_for_snapshot(snap.snapshot_id)
        assert rows[0]["phys_addr"] == 0x4000_0000 + 0x100

    def test_patterns_ordered_by_confidence(self, db):
        snap = _make_snapshot(snap_id=1)
        db.save_snapshot(snap)
        p_low = Pattern(PatternType.LOW_ENTROPY, 0, 64, 0.3, "low", bytes(32), {})
        p_high = Pattern(PatternType.HIGH_ENTROPY, 0, 64, 0.95, "high", bytes(32), {})
        db.save_patterns(snap.snapshot_id, [p_low, p_high])
        rows = db.get_patterns_for_snapshot(snap.snapshot_id)
        assert rows[0]["confidence"] >= rows[1]["confidence"]

    def test_empty_patterns(self, db):
        snap = _make_snapshot(snap_id=1)
        db.save_snapshot(snap)
        db.save_patterns(snap.snapshot_id, [])
        rows = db.get_patterns_for_snapshot(snap.snapshot_id)
        assert rows == []


# ---------------------------------------------------------------------------
# save_diffs
# ---------------------------------------------------------------------------

class TestDiffStorage:
    def test_save_diffs(self, db):
        snap1 = _make_snapshot(snap_id=1)
        snap2 = _make_snapshot(snap_id=2)
        db.save_snapshot(snap1)
        db.save_snapshot(snap2)

        diffs = [
            DiffRegion(
                window_index=0,
                offset=0,
                size=64,
                phys_addr=0x4000_0000,
                old_data=bytes(64),
                new_data=b"\x01" * 64,
                changed_bytes=64,
            )
        ]
        db.save_diffs(snap1.snapshot_id, snap2.snapshot_id, diffs)
        row = db._conn.execute("SELECT * FROM diffs").fetchone()
        assert row is not None
        assert row["old_snapshot_id"] == 1
        assert row["new_snapshot_id"] == 2
        assert row["changed_bytes"] == 64

    def test_save_empty_diffs(self, db):
        snap1 = _make_snapshot(snap_id=1)
        snap2 = _make_snapshot(snap_id=2)
        db.save_snapshot(snap1)
        db.save_snapshot(snap2)
        db.save_diffs(1, 2, [])
        count = db._conn.execute("SELECT COUNT(*) FROM diffs").fetchone()[0]
        assert count == 0


# ---------------------------------------------------------------------------
# save_analysis / get_recent_analyses
# ---------------------------------------------------------------------------

class TestAnalysisStorage:
    def test_save_and_retrieve_analysis(self, db):
        snap = _make_snapshot(snap_id=1)
        db.save_snapshot(snap)
        db.save_analysis(
            snapshot_id=1,
            mode="snapshot",
            prompt_summary="test prompt",
            response="test response",
            tokens_used=100,
        )
        analyses = db.get_recent_analyses(limit=10)
        assert len(analyses) == 1
        assert analyses[0]["mode"] == "snapshot"
        assert analyses[0]["response"] == "test response"
        assert analyses[0]["tokens_used"] == 100

    def test_save_analysis_without_snapshot(self, db):
        db.save_analysis(
            snapshot_id=None,
            mode="hunt:key",
            prompt_summary="hunt prompt",
            response="found something",
            tokens_used=50,
        )
        analyses = db.get_recent_analyses()
        assert len(analyses) == 1
        assert analyses[0]["snapshot_id"] is None

    def test_analyses_ordered_by_time(self, db):
        snap = _make_snapshot(snap_id=1)
        db.save_snapshot(snap)
        db.save_analysis(1, "snapshot", "prompt1", "response1", 10)
        time.sleep(0.01)
        db.save_analysis(1, "diff", "prompt2", "response2", 20)
        analyses = db.get_recent_analyses()
        assert analyses[0]["mode"] == "diff"  # most recent first


# ---------------------------------------------------------------------------
# search_patterns
# ---------------------------------------------------------------------------

class TestSearchPatterns:
    def test_search_by_type(self, db):
        snap = _make_snapshot(snap_id=1)
        db.save_snapshot(snap)
        p1 = Pattern(PatternType.ASCII_STRING, 0, 20, 0.9, "str", bytes(20), {})
        p2 = Pattern(PatternType.HIGH_ENTROPY, 0, 64, 0.8, "entropy", bytes(32), {})
        db.save_patterns(1, [p1, p2])
        results = db.search_patterns(pattern_type="ascii_string", min_confidence=0.5)
        assert all(r["pattern_type"] == "ascii_string" for r in results)

    def test_search_min_confidence(self, db):
        snap = _make_snapshot(snap_id=1)
        db.save_snapshot(snap)
        p_low = Pattern(PatternType.LOW_ENTROPY, 0, 64, 0.3, "low", bytes(32), {})
        p_high = Pattern(PatternType.HIGH_ENTROPY, 0, 64, 0.9, "high", bytes(32), {})
        db.save_patterns(1, [p_low, p_high])
        results = db.search_patterns(min_confidence=0.5)
        confidences = [r["confidence"] for r in results]
        assert all(c >= 0.5 for c in confidences)

    def test_search_no_type_filter(self, db):
        snap = _make_snapshot(snap_id=1)
        db.save_snapshot(snap)
        db.save_patterns(1, [
            Pattern(PatternType.ASCII_STRING, 0, 20, 0.9, "s", bytes(20), {}),
            Pattern(PatternType.HIGH_ENTROPY, 0, 64, 0.8, "e", bytes(32), {}),
        ])
        results = db.search_patterns(min_confidence=0.0)
        assert len(results) == 2

    def test_search_empty_db(self, db):
        results = db.search_patterns()
        assert results == []
