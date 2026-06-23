import os
import unittest
from unittest import mock

from backend.config import get_monitor_settings


class MonitorSettingsTest(unittest.TestCase):
    def test_defaults(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            s = get_monitor_settings()
            self.assertFalse(s.enabled)
            self.assertEqual(s.interval_seconds, 300)
            self.assertEqual(s.disk_percent, 90)
            self.assertEqual(s.failed_login, 20)
            self.assertEqual(s.auth_lines, 100)

    def test_enabled_flag(self):
        with mock.patch.dict(os.environ, {"AGENT_MONITOR_ENABLED": "true"}, clear=True):
            self.assertTrue(get_monitor_settings().enabled)

    def test_interval_clamped(self):
        with mock.patch.dict(os.environ, {"AGENT_MONITOR_INTERVAL_SECONDS": "1"}, clear=True):
            self.assertEqual(get_monitor_settings().interval_seconds, 10)

    def test_auth_lines_forced_above_threshold(self):
        with mock.patch.dict(os.environ, {
            "AGENT_MONITOR_FAILED_LOGIN": "150",
            "AGENT_MONITOR_AUTH_LINES": "50",
        }, clear=True):
            s = get_monitor_settings()
            self.assertEqual(s.failed_login, 150)
            self.assertEqual(s.auth_lines, 151)  # raised to threshold+1

    def test_auth_lines_capped_at_200(self):
        with mock.patch.dict(os.environ, {
            "AGENT_MONITOR_FAILED_LOGIN": "199",
            "AGENT_MONITOR_AUTH_LINES": "10",
        }, clear=True):
            self.assertEqual(get_monitor_settings().auth_lines, 200)


if __name__ == "__main__":
    unittest.main()
