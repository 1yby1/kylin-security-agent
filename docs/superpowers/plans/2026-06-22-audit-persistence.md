# 审计持久化升级 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把审计从扁平 JSONL 升级为 SQLite 权威存储：带 hash 链 + `audit_meta`（篡改/尾删可发现）、索引查询、verify/export 接口；`AuditLogger` 公开签名零改动，调用方（main/orchestrator/executor/mcp_server）不改即继续工作。

**Architecture:** 新增 `backend/audit/store.py::AuditStore` 承载全部 SQLite 逻辑（建表、hash 链 append、query、verify_chain）。同一 DB 路径**进程内共享一个 store**（`get_audit_store(path)` 工厂 + 模块级缓存 + 一把工厂锁），写入用实例锁 + `BEGIN IMMEDIATE` 事务把「取尾 hash → 插入 event → 更新 meta」串成原子操作，避免多个 `AuditLogger()` 实例导致 hash 链分叉。`AuditLogger` 退化为薄门面，经工厂取共享 store 并委托；写入失败按 `AGENT_AUDIT_FAIL_CLOSED` 决定 best-effort 或 fail-closed。

**Tech Stack:** Python 3、`sqlite3`(WAL)、`hashlib`、`threading`、FastAPI、`unittest`。

参考设计：`docs/superpowers/specs/2026-06-21-audit-persistence-design.md`

---

## 文件结构

| 文件 | 责任 | 动作 |
|------|------|------|
| `backend/config.py` | 新增 `AuditSettings` + `get_audit_settings()`（DB 路径 / fail-closed） | Modify |
| `backend/audit/store.py` | `AuditStore`（schema、hash 链 append、query/read_recent、verify_chain）+ `get_audit_store` / `reset_audit_stores` | Create |
| `backend/audit/logger.py` | 改为经 `get_audit_store` 取共享 store 并委托，保持 `event`/`read_recent`/`write` 签名；按 fail-closed 包裹写入 | Modify |
| `backend/database/db.py` | 移除未使用的 `audit_index` 空壳；`init_db()` 经 `get_audit_store` 确保审计表 | Modify |
| `backend/main.py` | 新增 `GET /api/audit/verify`、`GET /api/audit/export`；`/api/audit/recent` 可选 `user_id`/`status` 过滤 | Modify |
| `tests/test_audit_store.py` | store 单元测试（含篡改/尾删/共享/fail-closed） | Create |
| `tests/test_controlled_tools.py` | `setUp` 改指向临时审计 DB；`tearDown` 释放 store | Modify |
| `deploy/systemd.service` | 新增 `AGENT_AUDIT_DB_PATH=/var/lib/software-cup-ops/audit.db` | Modify |
| `deploy/README.md` | 同步新环境变量与 WAL 旁文件路径 | Modify |
| `docs/audit-tracing.md` / `ARCHITECTURE.md` | 文档同步（中文） | Modify |

约束（来自 CLAUDE.md / spec，务必遵守）：
- `AuditLogger` 的 `event` / `read_recent` / `write` 公开签名不变；orchestrator/main/executor/mcp_server 不改。
- 文档用中文，代码标识符/字段/环境变量保留原文。
- 不新增 lint 命令；测试用 `unittest`。仓库路径含非 ASCII，**按模块名跑** `python -m unittest tests.xxx -v`，不要用 `discover`（会因路径报错）。
- 提交只用显式 pathspec，绝不 `git add .`/`-A`；只提交本任务相关文件。

---

## Task 1: 新增审计配置 `get_audit_settings`

**目的：** 集中解析 DB 路径与 fail-closed 开关，供 store/logger 复用。

**Files:**
- Modify: `backend/config.py`
- Test: `tests/test_audit_store.py`

- [ ] **Step 1: 先写失败测试**

创建 `tests/test_audit_store.py`：

```python
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.config import get_audit_settings


class AuditSettingsTests(unittest.TestCase):
    def test_defaults(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENT_AUDIT_DB_PATH", None)
            os.environ.pop("AGENT_AUDIT_FAIL_CLOSED", None)
            settings = get_audit_settings()
            self.assertTrue(str(settings.db_path).endswith("audit.db"))
            self.assertFalse(settings.fail_closed)

    def test_overrides(self):
        env = {"AGENT_AUDIT_DB_PATH": "/tmp/x/audit.db", "AGENT_AUDIT_FAIL_CLOSED": "true"}
        with mock.patch.dict(os.environ, env, clear=False):
            settings = get_audit_settings()
            self.assertEqual(Path(settings.db_path), Path("/tmp/x/audit.db"))
            self.assertTrue(settings.fail_closed)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `python -m unittest tests.test_audit_store -v`
Expected: FAIL — `ImportError: cannot import name 'get_audit_settings'`。

- [ ] **Step 3: 在 backend/config.py 实现**

在文件顶部 import 区补充：

```python
from pathlib import Path
```

在文件末尾追加：

```python
@dataclass(frozen=True)
class AuditSettings:
    db_path: Path
    fail_closed: bool


def get_audit_settings() -> AuditSettings:
    configured = os.getenv("AGENT_AUDIT_DB_PATH")
    db_path = (
        Path(configured)
        if configured
        else Path(__file__).resolve().parent / "audit" / "logs" / "audit.db"
    )
    fail_closed = os.getenv("AGENT_AUDIT_FAIL_CLOSED", "false").strip().lower() in {"1", "true", "yes"}
    return AuditSettings(db_path=db_path, fail_closed=fail_closed)
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `python -m unittest tests.test_audit_store -v`
Expected: PASS（2 tests）。

- [ ] **Step 5: 提交**

```bash
git add backend/config.py tests/test_audit_store.py
git commit -m "feat(audit): 新增 get_audit_settings (DB 路径 / fail-closed)"
```

---

## Task 2: `AuditStore` schema + hash 链 append + 查询 + 共享工厂

**目的：** 落地 SQLite 权威存储与进程内共享。

**Files:**
- Create: `backend/audit/store.py`
- Test: `tests/test_audit_store.py`

- [ ] **Step 1: 追加失败测试**

在 `tests/test_audit_store.py` import 区追加：

```python
from backend.audit.store import AuditStore, get_audit_store, reset_audit_stores
```

新增测试类（注意 `tearDown` 释放 store，否则 Windows 删临时目录会因连接占用失败）：

```python
def _event(stage="received_instruction", trace_id="t1", user_id="u1", status="ok", data=None):
    return {
        "timestamp": "2026-06-22T00:00:00+00:00",
        "trace_id": trace_id,
        "stage": stage,
        "user_id": user_id,
        "status": status,
        "data": data or {"k": "v"},
    }


class AuditStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "audit.db"
        self.store = AuditStore(self.path)

    def tearDown(self):
        self.store.close()
        reset_audit_stores()
        self._tmp.cleanup()

    def test_append_then_read_recent(self):
        self.store.append(_event(data={"a": 1}))
        rows = self.store.read_recent(limit=10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stage"], "received_instruction")
        self.assertEqual(rows[0]["data"], {"a": 1})
        self.assertIn("hash", rows[0])

    def test_read_recent_filters_trace_and_orders_oldest_first(self):
        self.store.append(_event(trace_id="A", data={"n": 1}))
        self.store.append(_event(trace_id="B", data={"n": 2}))
        self.store.append(_event(trace_id="A", data={"n": 3}))
        rows = self.store.read_recent(limit=10, trace_id="A")
        self.assertEqual([r["data"]["n"] for r in rows], [1, 3])

    def test_query_by_user_and_status(self):
        self.store.append(_event(user_id="alice", status="blocked"))
        self.store.append(_event(user_id="bob", status="ok"))
        rows = self.store.query(limit=10, user_id="alice")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["user_id"], "alice")
        rows = self.store.query(limit=10, status="blocked")
        self.assertEqual(len(rows), 1)

    def test_get_audit_store_shared_per_path(self):
        a = get_audit_store(self.path)
        b = get_audit_store(self.path)
        self.assertIs(a, b)
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `python -m unittest tests.test_audit_store -v`
Expected: FAIL — `ModuleNotFoundError: backend.audit.store`。

- [ ] **Step 3: 实现 `backend/audit/store.py`**

```python
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
    prev_hash: str, timestamp: str, trace_id: str, stage: str, user_id: str | None, status: str | None, data_json: str
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
            clauses.append("trace_id = ?"); params.append(trace_id)
        if user_id:
            clauses.append("user_id = ?"); params.append(user_id)
        if status:
            clauses.append("status = ?"); params.append(status)
        if since:
            clauses.append("timestamp >= ?"); params.append(since)
        if until:
            clauses.append("timestamp <= ?"); params.append(until)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM audit_events{where} ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            self._conn.row_factory = sqlite3.Row
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_dict(r) for r in reversed(rows)]

    def read_recent(self, limit: int = 100, trace_id: str | None = None) -> list[dict[str, Any]]:
        return self.query(limit=limit, trace_id=trace_id)

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
```

> 注：`isolation_level=None` 让我们显式 `BEGIN IMMEDIATE`/`COMMIT`/`ROLLBACK`。`audit_meta` 用 `INSERT OR IGNORE` 预置单行，避免依赖 SQLite 3.24+ 的 upsert 语法。`get_audit_store` 直接构造的 `AuditStore` 不入缓存——测试可两用（直接构造做隔离，工厂验证共享）。

- [ ] **Step 4: 运行测试，确认通过**

Run: `python -m unittest tests.test_audit_store -v`
Expected: PASS（6 tests）。

- [ ] **Step 5: 提交**

```bash
git add backend/audit/store.py tests/test_audit_store.py
git commit -m "feat(audit): AuditStore 落地 SQLite hash 链存储与共享工厂"
```

---

## Task 3: `verify_chain` — 篡改 / 中间删除 / 尾删检测

**Files:**
- Modify: `backend/audit/store.py`
- Test: `tests/test_audit_store.py`

- [ ] **Step 1: 追加失败测试**

在 `AuditStoreTests` 中追加：

```python
    def test_clean_chain_verifies(self):
        for i in range(3):
            self.store.append(_event(data={"i": i}))
        result = self.store.verify_chain()
        self.assertTrue(result["ok"])
        self.assertTrue(result["tail_ok"])
        self.assertIsNone(result["broken_at"])
        self.assertEqual(result["count"], 3)

    def test_empty_chain_verifies(self):
        result = self.store.verify_chain()
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 0)

    def test_tampered_row_detected(self):
        for i in range(3):
            self.store.append(_event(data={"i": i}))
        # 绕过 store 直接改库，模拟篡改
        self.store._conn.execute("UPDATE audit_events SET data_json = '{\"i\": 99}' WHERE id = 2")
        result = self.store.verify_chain()
        self.assertFalse(result["ok"])
        self.assertEqual(result["broken_at"], 2)

    def test_tail_deletion_detected(self):
        for i in range(3):
            self.store.append(_event(data={"i": i}))
        # 删最后一行但不动 audit_meta
        self.store._conn.execute("DELETE FROM audit_events WHERE id = 3")
        result = self.store.verify_chain()
        self.assertFalse(result["tail_ok"])
        self.assertFalse(result["ok"])
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `python -m unittest tests.test_audit_store -v`
Expected: FAIL — `AttributeError: 'AuditStore' object has no attribute 'verify_chain'`。

- [ ] **Step 3: 实现 `verify_chain`**

在 `AuditStore` 内（`read_recent` 之后、`close` 之前）追加：

```python
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
                prev, row["timestamp"], row["trace_id"], row["stage"],
                row["user_id"], row["status"], row["data_json"],
            )
            if row["prev_hash"] != prev or row["hash"] != expected:
                return {"ok": False, "broken_at": row["id"], "count": len(rows), "tail_ok": False}
            prev = row["hash"]
        meta_last = meta["last_hash"] if meta else _GENESIS
        meta_count = meta["event_count"] if meta else 0
        actual_last = rows[-1]["hash"] if rows else _GENESIS
        tail_ok = (len(rows) == meta_count) and (actual_last == meta_last)
        return {"ok": tail_ok, "broken_at": None, "count": len(rows), "tail_ok": tail_ok}
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `python -m unittest tests.test_audit_store -v`
Expected: PASS（10 tests）。

- [ ] **Step 5: 提交**

```bash
git add backend/audit/store.py tests/test_audit_store.py
git commit -m "feat(audit): verify_chain 检测内容篡改/中间删除/尾部截断"
```

---

## Task 4: `AuditLogger` 委托共享 store + fail-closed

**目的：** logger 退化为薄门面，保持 `event`/`read_recent`/`write` 签名；写入失败按 `AGENT_AUDIT_FAIL_CLOSED` 处理。`write()` 摘要行映射为 `stage="summary"`、`trace_id=""` 的事件。

**Files:**
- Modify: `backend/audit/logger.py`
- Test: `tests/test_audit_store.py`

- [ ] **Step 1: 追加失败测试**

在 import 区追加：

```python
from backend.audit.logger import AuditLogger
from backend.agent.planner import Plan
```

新增测试类：

```python
class AuditLoggerDelegationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "audit.db"

    def tearDown(self):
        reset_audit_stores()
        self._tmp.cleanup()

    def test_event_and_read_recent_roundtrip(self):
        logger = AuditLogger(path=self.path)
        logger.event(trace_id="t1", stage="received_instruction", user_id="u", status="ok", data={"x": 1})
        rows = logger.read_recent(limit=10, trace_id="t1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["data"], {"x": 1})

    def test_write_creates_summary_row(self):
        logger = AuditLogger(path=self.path)
        plan = Plan(intent="inspection", tools=["system"], arguments={})
        logger.write("u", "q", plan, "completed", {"ok": True})
        rows = logger.read_recent(limit=10)
        summary = [r for r in rows if r["stage"] == "summary"]
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["trace_id"], "")
        self.assertEqual(summary[0]["data"]["intent"], "inspection")

    def test_shared_store_chain_not_forked(self):
        a = AuditLogger(path=self.path)
        b = AuditLogger(path=self.path)
        a.event(trace_id="t", stage="s1", user_id="u", status="ok", data={"n": 1})
        b.event(trace_id="t", stage="s2", user_id="u", status="ok", data={"n": 2})
        a.event(trace_id="t", stage="s3", user_id="u", status="ok", data={"n": 3})
        store = get_audit_store(self.path)
        result = store.verify_chain()
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 3)

    def test_fail_closed_raises_when_enabled(self):
        logger = AuditLogger(path=self.path)
        # 打桩让底层 append 抛错
        with mock.patch.object(logger._store, "append", side_effect=RuntimeError("disk")):
            with mock.patch.dict(os.environ, {"AGENT_AUDIT_FAIL_CLOSED": "true"}, clear=False):
                with self.assertRaises(RuntimeError):
                    logger.event(trace_id="t", stage="s", user_id="u", status="ok", data={})

    def test_best_effort_swallows_when_disabled(self):
        logger = AuditLogger(path=self.path)
        with mock.patch.object(logger._store, "append", side_effect=RuntimeError("disk")):
            with mock.patch.dict(os.environ, {"AGENT_AUDIT_FAIL_CLOSED": "false"}, clear=False):
                logger.event(trace_id="t", stage="s", user_id="u", status="ok", data={})  # 不抛
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `python -m unittest tests.test_audit_store -v`
Expected: FAIL — `AuditLogger` 仍是 JSONL 实现，`logger._store` 不存在 / `read_recent` 返回结构不符。

- [ ] **Step 3: 重写 `backend/audit/logger.py`**

```python
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from backend.agent.planner import Plan
from backend.audit.models import AuditEvent, AuditRecord
from backend.audit.store import get_audit_store
from backend.config import get_audit_settings


class AuditLogger:
    def __init__(self, path: Path | None = None) -> None:
        resolved = path or get_audit_settings().db_path
        self._store = get_audit_store(resolved)

    def _persist(self, event: dict[str, Any]) -> None:
        try:
            self._store.append(event)
        except Exception as exc:  # noqa: BLE001
            if get_audit_settings().fail_closed:
                raise
            print(f"[audit] write failed (best-effort): {exc}", file=sys.stderr)

    def event(self, *, trace_id: str, stage: str, user_id: str, status: str, data: dict[str, Any]) -> None:
        record = AuditEvent.create(
            trace_id=trace_id, stage=stage, user_id=user_id, status=status, data=data
        )
        self._persist(record.to_dict())

    def write(self, user_id: str, query: str, plan: Plan, status: str, result: dict[str, Any]) -> None:
        record = AuditRecord.create(
            user_id=user_id, query=query, intent=plan.intent, tools=plan.tools, status=status, result=result
        )
        self._persist(
            {
                "timestamp": record.timestamp,
                "trace_id": "",
                "stage": "summary",
                "user_id": user_id,
                "status": status,
                "data": {
                    "query": query,
                    "intent": plan.intent,
                    "tools": plan.tools,
                    "result": result,
                },
            }
        )

    def read_recent(self, limit: int = 100, trace_id: str | None = None) -> list[dict[str, Any]]:
        return self._store.read_recent(limit=limit, trace_id=trace_id)

    def verify_chain(self) -> dict[str, Any]:
        return self._store.verify_chain()

    def export(self, limit: int = 1000, trace_id: str | None = None) -> list[dict[str, Any]]:
        return self._store.query(limit=limit, trace_id=trace_id)
```

> 注：`fail_closed` 在 `_persist` 内**每次读取** `get_audit_settings()`，使测试可用 `mock.patch.dict` 临时切换，也贴合"运行期由环境决定"。`AuditRecord` 仍保留——`to_dict` 未用但保持模型完整；摘要行直接构造事件字典，因 `AuditRecord` 字段与事件 schema 不同。

- [ ] **Step 4: 运行测试，确认通过**

Run: `python -m unittest tests.test_audit_store -v`
Expected: PASS（16 tests）。

- [ ] **Step 5: 提交**

```bash
git add backend/audit/logger.py tests/test_audit_store.py
git commit -m "feat(audit): AuditLogger 委托共享 store 并支持 fail-closed"
```

---

## Task 5: 清理 `db.py` 空壳表，`init_db` 确保审计表

**Files:**
- Modify: `backend/database/db.py`

- [ ] **Step 1: 重写 `init_db`**

```python
from __future__ import annotations

import os
from pathlib import Path

from backend.audit.store import get_audit_store
from backend.config import get_audit_settings

DB_PATH = Path(os.getenv("AGENT_DB_PATH", str(Path(__file__).resolve().parent / "app.db")))


def init_db() -> None:
    # 应用级 SQLite（保留路径常量供其它用途）。
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # 审计表 schema 由 AuditStore 负责；启动时构造一次共享 store 即建表。
    get_audit_store(get_audit_settings().db_path)
```

> 注：移除从未被读写的 `audit_index` 空壳表（全仓库无引用）。`sqlite3` import 若不再使用则一并删除。

- [ ] **Step 2: 确认无回归（db + 既有审计相关测试）**

Run: `python -m unittest tests.test_audit_store -v`
Expected: PASS（16 tests）。

如仓库存在引用 `audit_index` 的测试/代码，先 Grep 确认：

Run（仅核对，无引用即可删）: 用 Grep 搜索 `audit_index` 应只剩本次删除点。

- [ ] **Step 3: 提交**

```bash
git add backend/database/db.py
git commit -m "refactor(db): 移除未用的 audit_index 空壳，init_db 改为确保审计表"
```

---

## Task 6: 新增 `/api/audit/verify`、`/api/audit/export`，扩展 `recent` 过滤

**Files:**
- Modify: `backend/main.py`

- [ ] **Step 1: 替换并新增审计接口**

将现有：

```python
@app.get("/api/audit/recent")
def audit_recent(limit: int = 100, trace_id: str | None = None) -> dict[str, Any]:
    return {"records": audit.read_recent(limit=limit, trace_id=trace_id)}
```

替换为：

```python
@app.get("/api/audit/recent")
def audit_recent(
    limit: int = 100,
    trace_id: str | None = None,
    user_id: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    if user_id or status:
        records = audit.export(limit=limit, trace_id=trace_id)  # 经 query 支持富过滤
        records = [
            r for r in records
            if (not user_id or r.get("user_id") == user_id)
            and (not status or r.get("status") == status)
        ]
        return {"records": records}
    return {"records": audit.read_recent(limit=limit, trace_id=trace_id)}


@app.get("/api/audit/verify")
def audit_verify() -> dict[str, Any]:
    return audit.verify_chain()


@app.get("/api/audit/export")
def audit_export(limit: int = 1000, trace_id: str | None = None) -> PlainTextResponse:
    records = audit.export(limit=limit, trace_id=trace_id)
    body = "\n".join(json.dumps(r, ensure_ascii=False) for r in records)
    return PlainTextResponse(body, media_type="application/x-ndjson")
```

> 注：富过滤优先用 `AuditStore.query` 的 SQL 条件最干净；上面用 `export`(=query) 后再按 user/status 过滤是为最小改面。若希望直接走 SQL，可在 logger 暴露 `query(...)` 转调 store——本计划保持最小接口。

- [ ] **Step 2: 补充 import**

在 main.py 顶部 import 区确认/新增：

```python
import json
from fastapi.responses import PlainTextResponse
```

（若 `json` 已 import 则跳过。）

- [ ] **Step 3: 启动冒烟**

Run（终端 A）: `uvicorn backend.main:app --host 127.0.0.1 --port 8000`
Run（终端 B）:

```bash
curl -s http://127.0.0.1:8000/api/audit/verify
curl -s "http://127.0.0.1:8000/api/audit/export?limit=5"
```

Expected: `verify` 返回含 `ok`/`tail_ok`/`count` 的 JSON；`export` 返回每行一条 JSON（NDJSON）。

- [ ] **Step 4: 全量回归**

Run: `python -m unittest tests.test_audit_store tests.test_controlled_tools -v`（test_controlled_tools 将在 Task 7 调整；此处先确认 store 测试不回归）

- [ ] **Step 5: 提交**

```bash
git add backend/main.py
git commit -m "feat(audit): 新增 /api/audit/verify 与 /api/audit/export，recent 支持 user/status 过滤"
```

---

## Task 7: `tests/test_controlled_tools.py` 迁移到 SQLite 审计

**目的：** 原 `setUp` 设 `AGENT_AUDIT_LOG_PATH`（JSONL）。改为设 `AGENT_AUDIT_DB_PATH` 指向临时 DB，并在 `tearDown` 释放 store 后再删临时目录（Windows 必需）。

**Files:**
- Modify: `tests/test_controlled_tools.py`

- [ ] **Step 1: 调整 setUp/tearDown**

将：

```python
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["AGENT_AUDIT_LOG_PATH"] = os.path.join(self._tmp.name, "audit.log")
        self.agent = AgentOrchestrator()

    def tearDown(self) -> None:
        self._tmp.cleanup()
```

替换为：

```python
    def setUp(self) -> None:
        from backend.audit.store import reset_audit_stores
        reset_audit_stores()
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["AGENT_AUDIT_DB_PATH"] = os.path.join(self._tmp.name, "audit.db")
        self.agent = AgentOrchestrator()

    def tearDown(self) -> None:
        from backend.audit.store import reset_audit_stores
        reset_audit_stores()
        os.environ.pop("AGENT_AUDIT_DB_PATH", None)
        self._tmp.cleanup()
```

> 注：`setUp` 先 `reset_audit_stores()` 确保上一个测试用例残留的缓存 store 不会让本例写到旧路径；orchestrator 构造的多个 `AuditLogger()` 在设了 env 后都会共享本例临时 DB。

- [ ] **Step 2: 核对两条审计断言仍成立**

阅读 `test_blocked_request_writes_audit_trace`、`test_executed_commands_are_written_to_audit_trace`，确认它们经 `read_recent()` 读取的字段（`stage`/`status`/`data`...）在 SQLite 后端下结构一致（store 的 `read_recent` 返回 `timestamp/trace_id/stage/user_id/status/data/hash`）。若断言里读 `data` 下子字段，确认仍命中。

- [ ] **Step 3: 运行该文件全测试**

Run: `python -m unittest tests.test_controlled_tools -v`
Expected: 全 PASS。

- [ ] **Step 4: 提交**

```bash
git add tests/test_controlled_tools.py
git commit -m "test: 控制工具审计断言迁移到 SQLite 临时 DB"
```

---

## Task 8: 部署同步（systemd + README）

**目的（评审 P1）：** `ProtectSystem=strict` 下默认 `backend/audit/logs/audit.db`（在 `WorkingDirectory=/opt/...` 内）只读，必须把审计 DB 指到 `ReadWritePaths` 内。

**Files:**
- Modify: `deploy/systemd.service`
- Modify: `deploy/README.md`

- [ ] **Step 1: systemd 新增审计 DB 路径**

在 `deploy/systemd.service` 的 `Environment=AGENT_AUDIT_LOG_PATH=...` 行附近新增：

```ini
Environment=AGENT_AUDIT_DB_PATH=/var/lib/software-cup-ops/audit.db
```

（与既有 `AGENT_DB_PATH=/var/lib/software-cup-ops/app.db` 同目录，已在 `ReadWritePaths`。可选 `Environment=AGENT_AUDIT_FAIL_CLOSED=true` 用于生产强一致审计。）`AGENT_AUDIT_LOG_PATH` 保留与否不影响（已不再使用），如清理则一并删。

- [ ] **Step 2: README 同步**

在 `deploy/README.md` 审计相关段落补充：`AGENT_AUDIT_DB_PATH` 指向 `/var/lib/software-cup-ops/audit.db`；SQLite WAL 模式会在同目录生成 `audit.db-wal`、`audit.db-shm` 旁文件，均需在可写路径内；可选 `AGENT_AUDIT_FAIL_CLOSED=true`。按文件现状语言书写。

- [ ] **Step 3: 提交**

```bash
git add deploy/systemd.service deploy/README.md
git commit -m "deploy: 审计 DB 指向可写路径，避免 ProtectSystem=strict 只读"
```

---

## Task 9: 文档同步

**Files:**
- Modify: `docs/audit-tracing.md`
- Modify: `ARCHITECTURE.md`

- [ ] **Step 1: `docs/audit-tracing.md` 更新存储说明**

说明：审计已从 JSONL 升级为 SQLite 权威存储；hash 链 + `audit_meta` 提供篡改/尾删可发现（tamper-evident），并明确威胁模型（对能同时重建整链+meta 的全写权限攻击者不构成密码学级防篡改，属后续外部签名/锚定）；新增 `GET /api/audit/verify`、`GET /api/audit/export`；环境变量 `AGENT_AUDIT_DB_PATH`、`AGENT_AUDIT_FAIL_CLOSED`。

- [ ] **Step 2: `ARCHITECTURE.md` 审计章节增补**

在审计相关段落补一句：审计落地 `backend/audit/store.py::AuditStore`（SQLite，进程内按 DB 路径共享），`AuditLogger` 为薄门面；查询走索引，支持链校验与 NDJSON 导出。

- [ ] **Step 3: 提交**

```bash
git add docs/audit-tracing.md ARCHITECTURE.md
git commit -m "docs: 同步审计 SQLite 持久化与防篡改说明"
```

---

## 验收标准（对应 spec 第 11 节）

1. 审计事件写入 SQLite；`read_recent` 经 SQL 返回，`/api/audit/recent` 字段向后兼容。（Task 2/4/6）
2. 可按 trace_id/时间/user/status 索引查询。（Task 2/6）
3. 篡改任一行 `data_json` → `verify_chain` 返回 `broken_at`、`ok=false`。（Task 3）
4. 尾删未改 meta → `tail_ok=false`、`ok=false`。（Task 3）
5. `get_audit_store` 同路径同实例；多 `AuditLogger` 并写链不分叉，`event_count` == 总写入数。（Task 2/4）
6. `AGENT_AUDIT_FAIL_CLOSED=true` 前置审计失败使请求报错；`false` best-effort 不中断。（Task 4）
7. systemd 下 `AGENT_AUDIT_DB_PATH` 在 `ReadWritePaths` 内可写（含 WAL 旁文件）。（Task 8）
8. `GET /api/audit/verify` 返回完整性；`GET /api/audit/export` 产出 NDJSON。（Task 6）
9. `tests/test_audit_store.py` 全通过、`tests/test_controlled_tools.py` 调整后全通过、其余不回归。（Task 1–7）
10. orchestrator/main 链路/mcp_server/executor 无需改动。（公开签名不变，Task 4）

## 自查记录（writing-plans self-review）

- **Spec 覆盖**：①SQLite 权威 = Task 2/5；②防篡改 hash 链 + audit_meta + verify = Task 2/3；③不做保留/轮转 = 计划未涉及（YAGNI）；④全新开始不导入旧 JSONL = 计划不读 `audit.log`；⑤共享 store 并发 = Task 2/4；⑥fail-closed = Task 1/4；接口 verify/export = Task 6；部署 = Task 8；文档 = Task 9。无遗漏。
- **占位符**：无 TBD/TODO；每个改码步骤含完整代码或精确改点。
- **签名一致**：`AuditStore(path)` / `append(event)->dict` / `query(limit,trace_id,user_id,status,since,until)` / `read_recent(limit,trace_id)` / `verify_chain()->{ok,broken_at,count,tail_ok}` / `get_audit_store(path)` / `reset_audit_stores()` 全程一致；`AuditLogger.event/write/read_recent` 签名与现状一致（零改动调用方）；新增 `AuditLogger.verify_chain/export` 仅供 main.py 使用。
- **平台/外部风险**：Windows 删临时目录需先 `store.close()`/`reset_audit_stores()`——已在所有 tearDown 覆盖；`audit_meta` 用 `INSERT OR IGNORE` 预置避免依赖较新 SQLite upsert；WAL 旁文件落在可写路径已在 Task 8 处置；仓库非 ASCII 路径用模块名跑测试而非 discover。
- **顺序安全**：Task 5 删 `audit_index` 前先 Grep 确认无引用；Task 7 在 logger 改完（Task 4）后再迁移控制工具测试，避免中途红。
