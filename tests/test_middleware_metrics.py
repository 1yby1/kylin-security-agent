import os
import unittest
from dataclasses import replace
from unittest import mock

from fastapi.testclient import TestClient

import backend.main as main
from backend.security.rate_limit import ConcurrencyGate, RateLimiter


class RateLimitMiddlewareTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self._orig = main._rate_limiter
        self._orig_settings = main._rl_settings
        main._rate_limiter = RateLimiter(limit_per_window=2, window_seconds=60)

    def tearDown(self):
        main._rate_limiter = self._orig
        main._rl_settings = self._orig_settings

    def test_blocks_after_limit_on_heavy_endpoint(self):
        body = {"query": "看系统状态"}
        self.assertEqual(self.client.post("/api/agent/plan", json=body).status_code, 200)
        self.assertEqual(self.client.post("/api/agent/plan", json=body).status_code, 200)
        resp = self.client.post("/api/agent/plan", json=body)
        self.assertEqual(resp.status_code, 429)
        self.assertIn("Retry-After", resp.headers)

    def test_health_is_not_rate_limited(self):
        for _ in range(5):
            self.assertEqual(self.client.get("/health").status_code, 200)

    def test_disabled_rate_limit_bypasses_limiter_but_still_counts(self):
        main._rl_settings = replace(self._orig_settings, enabled=False)
        body = {"query": "看系统状态"}
        for _ in range(5):
            resp = self.client.post("/api/agent/plan", json=body)
            self.assertEqual(resp.status_code, 200)


class ConcurrencyGateMiddlewareTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self._orig_concurrency = main._concurrency
        self._orig_rate_limiter = main._rate_limiter
        main._rate_limiter = RateLimiter(limit_per_window=100, window_seconds=60)
        gate = ConcurrencyGate(1)
        gate.try_acquire()
        main._concurrency = gate

    def tearDown(self):
        main._concurrency = self._orig_concurrency
        main._rate_limiter = self._orig_rate_limiter

    def test_concurrency_cap_returns_503(self):
        body = {"query": "看系统状态"}
        resp = self.client.post("/api/agent/plan", json=body)
        self.assertEqual(resp.status_code, 503)


class MetricsEndpointTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def test_viewer_forbidden(self):
        self.assertEqual(self.client.get("/api/metrics").status_code, 403)

    def test_operator_gets_snapshot(self):
        with mock.patch.dict(os.environ, {"AGENT_OPERATOR_TOKEN": "optok"}, clear=True):
            resp = self.client.get("/api/metrics", headers={"Authorization": "Bearer optok"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        for key in ("requests", "blocked", "rate_limited", "tools", "llm"):
            self.assertIn(key, body)


if __name__ == "__main__":
    unittest.main()
