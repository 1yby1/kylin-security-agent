import os
import unittest
from unittest import mock

from fastapi.testclient import TestClient

import backend.main as main
from backend.security.rate_limit import RateLimiter


class RateLimitMiddlewareTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self._orig = main._rate_limiter
        main._rate_limiter = RateLimiter(limit_per_window=2, window_seconds=60)

    def tearDown(self):
        main._rate_limiter = self._orig

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
