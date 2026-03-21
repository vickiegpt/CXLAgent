"""SQLite timeline storage for CXL snapshots and analysis results."""

import json
import sqlite3
import time
from pathlib import Path
from typing import Optional

from .patterns import Pattern
from .snapshot import DiffRegion, Snapshot


DB_PATH = Path("~/.cxlagent/timeline.db").expanduser()


class TimelineDB:
    """SQLite-backed timeline of CXL memory snapshots and LLM analyses."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY,
                timestamp REAL NOT NULL,
                capture_duration_ms REAL,
                total_bytes INTEGER,
                non_empty_chunks INTEGER,
                wbinvd_caches TEXT,
                cache_states TEXT,
                summary TEXT
            );

            CREATE TABLE IF NOT EXISTS patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER NOT NULL,
                pattern_type TEXT NOT NULL,
                offset INTEGER,
                phys_addr INTEGER,
                size INTEGER,
                confidence REAL,
                description TEXT,
                data_preview BLOB,
                metadata TEXT,
                FOREIGN KEY (snapshot_id) REFERENCES snapshots(id)
            );

            CREATE TABLE IF NOT EXISTS diffs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                old_snapshot_id INTEGER NOT NULL,
                new_snapshot_id INTEGER NOT NULL,
                window_index INTEGER,
                offset INTEGER,
                phys_addr INTEGER,
                size INTEGER,
                changed_bytes INTEGER,
                change_ratio REAL,
                FOREIGN KEY (old_snapshot_id) REFERENCES snapshots(id),
                FOREIGN KEY (new_snapshot_id) REFERENCES snapshots(id)
            );

            CREATE TABLE IF NOT EXISTS analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER,
                timestamp REAL NOT NULL,
                mode TEXT,
                prompt_summary TEXT,
                response TEXT,
                tokens_used INTEGER,
                FOREIGN KEY (snapshot_id) REFERENCES snapshots(id)
            );

            CREATE TABLE IF NOT EXISTS trace_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER,
                timestamp REAL,
                cpu INTEGER,
                pid INTEGER,
                event_type TEXT,
                memdev TEXT,
                transaction_type TEXT,
                dpa INTEGER,
                hpa INTEGER,
                FOREIGN KEY (snapshot_id) REFERENCES snapshots(id)
            );

            CREATE INDEX IF NOT EXISTS idx_patterns_snapshot ON patterns(snapshot_id);
            CREATE INDEX IF NOT EXISTS idx_patterns_type ON patterns(pattern_type);
            CREATE INDEX IF NOT EXISTS idx_diffs_new ON diffs(new_snapshot_id);
            CREATE INDEX IF NOT EXISTS idx_trace_snapshot ON trace_events(snapshot_id);
        """)
        self._conn.commit()

    def save_snapshot(self, snap: Snapshot) -> int:
        """Save a snapshot record (without raw chunk data)."""
        self._conn.execute(
            """INSERT INTO snapshots
               (id, timestamp, capture_duration_ms, total_bytes,
                non_empty_chunks, wbinvd_caches, cache_states, summary)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                snap.snapshot_id,
                snap.timestamp,
                snap.capture_duration_ms,
                snap.total_bytes,
                len(snap.non_empty_chunks),
                json.dumps(snap.wbinvd_triggered),
                json.dumps(snap.cache_states),
                snap.summary(),
            ),
        )
        self._conn.commit()
        return snap.snapshot_id

    def save_patterns(self, snapshot_id: int, patterns: list[Pattern],
                      base_phys_addr: int = 0):
        """Save detected patterns for a snapshot."""
        for p in patterns:
            self._conn.execute(
                """INSERT INTO patterns
                   (snapshot_id, pattern_type, offset, phys_addr, size,
                    confidence, description, data_preview, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    snapshot_id,
                    p.pattern_type.value,
                    p.offset,
                    base_phys_addr + p.offset,
                    p.size,
                    p.confidence,
                    p.description,
                    p.data_preview,
                    json.dumps(p.metadata, default=str),
                ),
            )
        self._conn.commit()

    def save_diffs(self, old_id: int, new_id: int, diffs: list[DiffRegion]):
        for d in diffs:
            self._conn.execute(
                """INSERT INTO diffs
                   (old_snapshot_id, new_snapshot_id, window_index,
                    offset, phys_addr, size, changed_bytes, change_ratio)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (old_id, new_id, d.window_index, d.offset,
                 d.phys_addr, d.size, d.changed_bytes, d.change_ratio),
            )
        self._conn.commit()

    def save_analysis(self, snapshot_id: Optional[int], mode: str,
                      prompt_summary: str, response: str,
                      tokens_used: int = 0) -> int:
        cur = self._conn.execute(
            """INSERT INTO analyses
               (snapshot_id, timestamp, mode, prompt_summary, response, tokens_used)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (snapshot_id, time.time(), mode, prompt_summary, response, tokens_used),
        )
        self._conn.commit()
        return cur.lastrowid

    def get_recent_snapshots(self, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM snapshots ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_patterns_for_snapshot(self, snapshot_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM patterns WHERE snapshot_id = ? ORDER BY confidence DESC",
            (snapshot_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_recent_analyses(self, limit: int = 10) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM analyses ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def search_patterns(self, pattern_type: Optional[str] = None,
                        min_confidence: float = 0.5) -> list[dict]:
        query = "SELECT * FROM patterns WHERE confidence >= ?"
        params: list = [min_confidence]
        if pattern_type:
            query += " AND pattern_type = ?"
            params.append(pattern_type)
        query += " ORDER BY confidence DESC LIMIT 100"
        rows = self._conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def close(self):
        self._conn.close()
