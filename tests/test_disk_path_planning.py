from __future__ import annotations

import os
import tempfile
import unittest

from backend.agent.llm_client import LLMClient, LLMDecision
from backend.agent.orchestrator import AgentOrchestrator
from backend.agent.planner import Planner
from backend.mcp_tools.disk_tool import _normalize_disk_path


class FakeDiskLLM(LLMClient):
    def analyze(self, query, context, tool_manifest=None):
        return LLMDecision(
            intent="inspection",
            tools=["disk"],
            arguments={"path": "E:/"},
            summary="fake disk plan",
            reasoning=["fake model returned the wrong drive"],
        )


class DiskPathPlanningTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["AGENT_AUDIT_LOG_PATH"] = os.path.join(self._tmp.name, "audit.log")
        self.agent = AgentOrchestrator()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_rule_planner_extracts_windows_drive_from_chinese_query(self) -> None:
        plan = self.agent.planner.plan("查看 C 盘使用率", {"user_role": "viewer"})

        self.assertEqual(plan.tools, ["disk"])
        self.assertEqual(plan.arguments["path"], "C:/")

    def test_rule_planner_extracts_windows_drive_without_space(self) -> None:
        plan = self.agent.planner.plan("查看c盘使用率", {"user_role": "viewer"})

        self.assertEqual(plan.tools, ["disk"])
        self.assertEqual(plan.arguments["path"], "C:/")

    def test_rule_planner_extracts_other_windows_drive_letters(self) -> None:
        plan = self.agent.planner.plan("查看d盘使用率", {"user_role": "viewer"})

        self.assertEqual(plan.tools, ["disk"])
        self.assertEqual(plan.arguments["path"], "D:/")

    def test_query_drive_overrides_wrong_llm_disk_path(self) -> None:
        planner = Planner(FakeDiskLLM())
        plan = planner.plan("查看 C 盘使用率", {"user_role": "viewer"})

        self.assertEqual(plan.source, "llm")
        self.assertEqual(plan.tools, ["disk"])
        self.assertEqual(plan.arguments["path"], "C:/")

    def test_windows_backslash_path_passes_security_validation(self) -> None:
        result = self.agent.evaluate_security(
            "查看 C:\\ 磁盘使用率",
            "viewer1",
            {"user_role": "viewer", "path": "C:\\"},
            approved=False,
        )

        self.assertFalse(result["security"]["blocked"])

    def test_disk_path_normalizes_bare_windows_drive(self) -> None:
        self.assertEqual(_normalize_disk_path("C:"), "C:/")
        self.assertEqual(_normalize_disk_path("C:\\"), "C:/")


if __name__ == "__main__":
    unittest.main()
