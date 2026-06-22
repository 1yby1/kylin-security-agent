from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.config import get_audit_settings
from backend.audit.store import AuditStore, get_audit_store, reset_audit_stores


def _event(stage="received_instruction", trace_id="t1", user_id="u1", status="ok", data=None):
    return {
        "timestamp": "2026-06-22T00:00:00+00:00",
        "trace_id": trace_id,
        "stage": stage,
        "user_id": user_id,
        "status": status,
        "data": data or {"k": "v"},
    }


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


if __name__ == "__main__":
    unittest.main()
