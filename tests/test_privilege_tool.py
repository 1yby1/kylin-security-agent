import unittest
from unittest import mock

from backend.agent.llm_client import LLMClient
from backend.agent.planner import Planner
from backend.config import LLMSettings
from backend.mcp_tools import privilege_tool
from backend.mcp_tools.builtin import build_registry
from backend.security.rules import LOW_RISK_TOOLS


def _disabled_planner() -> Planner:
    return Planner(LLMClient(LLMSettings(provider="disabled", api_key="", base_url="", model="")))


class PrivilegeAnalyzeTest(unittest.TestCase):
    def test_counts_suid_and_extra_uid0_and_shadow(self):
        suid = {"stdout": ["/usr/bin/passwd", "/usr/bin/sudo"]}
        sgid = {"stdout": ["/usr/bin/wall"]}
        uid0 = {"stdout": ["root", "backdoor"]}
        empty_pw = {"error": "permission denied"}
        analysis = privilege_tool._analyze(suid, sgid, uid0, empty_pw)
        self.assertEqual(analysis["suid_count"], 2)
        self.assertEqual(analysis["sgid_count"], 1)
        self.assertEqual(analysis["extra_uid0_accounts"], ["backdoor"])
        self.assertFalse(analysis["shadow_readable"])
        self.assertEqual(analysis["empty_password_accounts"], [])

    def test_clean_system_has_no_extra_uid0(self):
        analysis = privilege_tool._analyze({"stdout": []}, {"stdout": []}, {"stdout": ["root"]}, {"stdout": []})
        self.assertEqual(analysis["extra_uid0_accounts"], [])
        self.assertTrue(analysis["shadow_readable"])


class PrivilegeWindowsDegradeTest(unittest.TestCase):
    def test_windows_returns_message_without_commands(self):
        with mock.patch("backend.mcp_tools.privilege_tool.os.name", "nt"), \
             mock.patch("backend.mcp_tools.privilege_tool.run_optional_template") as runner:
            result = privilege_tool.run({})
            runner.assert_not_called()
        self.assertEqual(result["platform"], "windows")
        self.assertIn("麒麟", result["message"])


class PrivilegeWiringTest(unittest.TestCase):
    def test_registered_and_low_risk(self):
        registry = build_registry()
        self.assertIn("privilege", registry.names())
        self.assertIn("privilege", LOW_RISK_TOOLS)

    def test_planner_keyword_selects_privilege(self):
        plan = _disabled_planner().plan("扫一下有没有提权风险的 SUID 文件", {}, None)
        self.assertIn("privilege", plan.tools)


if __name__ == "__main__":
    unittest.main()
