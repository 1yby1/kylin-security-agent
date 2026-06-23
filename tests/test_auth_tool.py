import os
import unittest
from unittest import mock

from backend.agent.llm_client import LLMClient
from backend.agent.planner import Planner
from backend.config import LLMSettings
from backend.mcp_tools import auth_tool
from backend.mcp_tools.builtin import build_registry
from backend.security.rules import LOW_RISK_TOOLS


def _disabled_planner() -> Planner:
    return Planner(LLMClient(LLMSettings(provider="disabled", api_key="", base_url="", model="")))


class AuthAnalyzeTest(unittest.TestCase):
    def test_counts_root_remote_and_top_ips(self):
        last = {"stdout": [
            "root     pts/0   192.168.1.5    Mon Jun 23 10:00   still logged in",
            "alice    pts/1   192.168.1.9    Mon Jun 23 09:00 - 09:30  (00:30)",
            "wtmp begins Mon Jun 1 00:00:00 2026",
        ]}
        failed = {"stdout": [
            "baduser  ssh:notty 10.0.0.9   Mon Jun 23 08:00",
            "baduser  ssh:notty 10.0.0.9   Mon Jun 23 08:01",
        ]}
        who = {"stdout": ["root pts/0 2026-06-23 10:00 (192.168.1.5)", ""]}
        analysis = auth_tool._analyze(last, failed, who)
        self.assertEqual(analysis["success_login_count"], 2)
        self.assertEqual(analysis["failed_login_count"], 2)
        self.assertEqual(analysis["active_sessions"], 1)
        self.assertTrue(analysis["root_remote_login"])
        self.assertEqual(analysis["top_source_ips"]["10.0.0.9"], 2)
        self.assertTrue(analysis["failed_log_readable"])

    def test_failed_log_unreadable_flagged(self):
        analysis = auth_tool._analyze({"stdout": []}, {"error": "permission denied"}, {"stdout": []})
        self.assertFalse(analysis["failed_log_readable"])
        self.assertEqual(analysis["failed_login_count"], 0)


class AuthWindowsDegradeTest(unittest.TestCase):
    def test_windows_returns_message_without_commands(self):
        with mock.patch("backend.mcp_tools.auth_tool.os.name", "nt"), \
             mock.patch("backend.mcp_tools.auth_tool.run_optional_template") as runner:
            result = auth_tool.run({})
            runner.assert_not_called()
        self.assertEqual(result["platform"], "windows")
        self.assertIn("麒麟", result["message"])


class AuthWiringTest(unittest.TestCase):
    def test_registered_and_low_risk(self):
        registry = build_registry()
        self.assertIn("auth", registry.names())
        self.assertIn("auth", LOW_RISK_TOOLS)

    def test_planner_keyword_selects_auth(self):
        plan = _disabled_planner().plan("最近有没有暴力破解登录", {}, None)
        self.assertIn("auth", plan.tools)


if __name__ == "__main__":
    unittest.main()
