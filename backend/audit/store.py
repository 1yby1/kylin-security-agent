from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

_GENESIS = ""

_CREATE_EVENTS = """
CREATE TABLE IF NOT EXISTS audit_events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    trace_id  TEXT NOT NULL DEFAULT '',
    stage     TEXT NOT NULL,
    user_id   TEXT,
    status    TEXT,
    data_json TEXT NOT NULL,
    prev_hash TEXT,
    hash      TEXT NOT NULL
)
"""
_CREATE_META = """
CREATE TABLE IF NOT EXISTS audit_meta (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    last_hash   TEXT NOT NULL DEFAULT '',
    event_count INTEGER NOT NULL DEFAULT 0
)
"""


def _compute_hash(
    prev_hash: str,
    timestamp: str,
    trace_id: str,
    stage: str,
    user_id: str | None,
    status: str | None,
    data_json: str,
) -> str:
    payload = "|".join(
        [prev_hash, timestamp, trace_id, stage, user_id or "", status or "", data_json]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AuditStore:
    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # isolation_level=None => 自管事务，便于显式 BEGIN IMMEDIATE
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._lock:
            self._conn.execute(_CREATE_EVENTS)
            self._conn.execute(_CREATE_META)
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_trace ON audit_events(trace_id)")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_events(timestamp)")
            self._conn.execute("INSERT OR IGNORE INTO audit_meta (id, last_hash, event_count) VALUES (1, '', 0)")

    def append(self, event: dict[str, Any]) -> dict[str, Any]:
        timestamp = event["timestamp"]
        trace_id = event.get("trace_id", "") or ""
        stage = event["stage"]
        user_id = event.get("user_id")
        status = event.get("status")
        data_json = json.dumps(event.get("data", {}), sort_keys=True, ensure_ascii=False)
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute("SELECT last_hash FROM audit_meta WHERE id = 1").fetchone()
                prev_hash = row[0] if row else _GENESIS
                digest = _compute_hash(prev_hash, timestamp, trace_id, stage, user_id, status, data_json)
                self._conn.execute(
                    "INSERT INTO audit_events "
                    "(timestamp, trace_id, stage, user_id, status, data_json, prev_hash, hash) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (timestamp, trace_id, stage, user_id, status, data_json, prev_hash, digest),
                )
                self._conn.execute(
                    "UPDATE audit_meta SET last_hash = ?, event_count = event_count + 1 WHERE id = 1",
                    (digest,),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return {**event, "trace_id": trace_id, "hash": digest, "prev_hash": prev_hash}

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "timestamp": row["timestamp"],
            "trace_id": row["trace_id"],
            "stage": row["stage"],
            "user_id": row["user_id"],
            "status": row["status"],
            "data": json.loads(row["data_json"]),
            "hash": row["hash"],
        }

    def query(
        self,
        limit: int = 100,
        trace_id: str | None = None,
        user_id: str | None = None,
        status: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if trace_id:
            clauses.append("trace_id = ?")
            params.append(trace_id)
        if user_id:
            clauses.append("user_id = ?")
            params.append(user_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if since:
            clauses.append("timestamp >= ?")
            params.append(since)
        if until:
            clauses.append("timestamp <= ?")
            params.append(until)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM audit_events{where} ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            self._conn.row_factory = sqlite3.Row
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_dict(r) for r in reversed(rows)]

    def read_recent(self, limit: int = 100, trace_id: str | None = None) -> list[dict[str, Any]]:
        return self.query(limit=limit, trace_id=trace_id)

    def verify_chain(self) -> dict[str, Any]:
        with self._lock:
            self._conn.row_factory = sqlite3.Row
            rows = self._conn.execute(
                "SELECT id, timestamp, trace_id, stage, user_id, status, data_json, prev_hash, hash "
                "FROM audit_events ORDER BY id ASC"
            ).fetchall()
            meta = self._conn.execute(
                "SELECT last_hash, event_count FROM audit_meta WHERE id = 1"
            ).fetchone()
        prev = _GENESIS
        for row in rows:
            expected = _compute_hash(
                prev,
                row["timestamp"],
                row["trace_id"],
                row["stage"],
                row["user_id"],
                row["status"],
                row["data_json"],
            )
            if row["prev_hash"] != prev or row["hash"] != expected:
                return {"ok": False, "broken_at": row["id"], "count": len(rows), "tail_ok": False}
            prev = row["hash"]
        meta_last = meta["last_hash"] if meta else _GENESIS
        meta_count = meta["event_count"] if meta else 0
        actual_last = rows[-1]["hash"] if rows else _GENESIS
        tail_ok = (len(rows) == meta_count) and (actual_last == meta_last)
        return {"ok": tail_ok, "broken_at": None, "count": len(rows), "tail_ok": tail_ok}

    def close(self) -> None:
        with self._lock:
            self._conn.close()


_STORES: dict[str, AuditStore] = {}
_FACTORY_LOCK = threading.Lock()


def get_audit_store(path: Path | str) -> AuditStore:
    key = str(Path(path).resolve())
    with _FACTORY_LOCK:
        store = _STORES.get(key)
        if store is None:
            store = AuditStore(path)
            _STORES[key] = store
        return store


def reset_audit_stores() -> None:
    """关闭并清空进程内 store 缓存（测试隔离用）。"""
    with _FACTORY_LOCK:
        for store in _STORES.values():
            try:
                store.close()
            except Exception:
                pass
        _STORES.clear()
