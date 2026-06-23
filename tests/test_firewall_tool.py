import unittest
from unittest import mock

from backend.agent.llm_client import LLMClient
from backend.agent.planner import Planner
from backend.config import LLMSettings
from backend.mcp_tools import firewall_tool
from backend.mcp_tools.builtin import build_registry
from backend.security.rules import LOW_RISK_TOOLS


def _disabled_planner() -> Planner:
    return Planner(LLMClient(LLMSettings(provider="disabled", api_key="", base_url="", model="")))


class FirewallAnalyzeTest(unittest.TestCase):
    def test_parses_ports_services_and_high_risk(self):
        state = {"stdout": ["running"]}
        listing = {"stdout": [
            "public (active)",
            "  target: default",
            "  services: ssh dhcpv6-client",
            "  ports: 22/tcp 23/tcp 8000/tcp",
        ]}
        analysis = firewall_tool._analyze(state, listing)
        self.assertTrue(analysis["running"])
        self.assertEqual(analysis["open_port_count"], 3)
        self.assertEqual(analysis["open_service_count"], 2)
        self.assertIn("23", analysis["high_risk_exposed"])
        self.assertTrue(analysis["readable"])

    def test_not_running_and_unreadable(self):
        analysis = firewall_tool._analyze({"stdout": ["not running"]}, {"error": "permission denied"})
        self.assertFalse(analysis["running"])
        self.assertFalse(analysis["readable"])
        self.assertEqual(analysis["open_port_count"], 0)


class FirewallWindowsDegradeTest(unittest.TestCase):
    def test_windows_returns_message_without_commands(self):
        with mock.patch("backend.mcp_tools.firewall_tool.os.name", "nt"), \
             mock.patch("backend.mcp_tools.firewall_tool.run_optional_template") as runner:
            result = firewall_tool.run({})
            runner.assert_not_called()
        self.assertEqual(result["platform"], "windows")
        self.assertIn("麒麟", result["message"])


class FirewallWiringTest(unittest.TestCase):
    def test_registered_and_low_risk(self):
        registry = build_registry()
        self.assertIn("firewall", registry.names())
        self.assertIn("firewall", LOW_RISK_TOOLS)

    def test_planner_keyword_selects_firewall(self):
        plan = _disabled_planner().plan("防火墙有没有开放危险端口", {}, None)
        self.assertIn("firewall", plan.tools)


if __name__ == "__main__":
    unittest.main()
