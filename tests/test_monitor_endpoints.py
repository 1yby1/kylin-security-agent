# tests/test_monitor_endpoints.py
import os
import unittest
from unittest import mock

from fastapi.testclient import TestClient

import backend.main as main
from backend.monitor.alerts import Alert


class AlertsEndpointTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        main._alert_store.reset()

    def test_viewer_forbidden(self):
        self.assertEqual(self.client.get("/api/alerts").status_code, 403)

    def test_operator_gets_alerts(self):
        main._alert_store.add(Alert(severity="critical", source="disk", metric="used_percent",
                                    value=99, threshold=90, message="磁盘快满了"))
        with mock.patch.dict(os.environ, {"AGENT_OPERATOR_TOKEN": "optok"}, clear=True):
            resp = self.client.get("/api/alerts", headers={"Authorization": "Bearer optok"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(len(body["alerts"]), 1)
        self.assertEqual(body["alerts"][0]["source"], "disk")


class MonitorStatusEndpointTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def test_status_open_and_shaped(self):
        resp = self.client.get("/api/monitor/status")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        for key in ("enabled", "running", "interval_seconds", "checks"):
            self.assertIn(key, body)


if __name__ == "__main__":
    unittest.main()
