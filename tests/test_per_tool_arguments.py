from __future__ import annotations

import os
import tempfile
import unittest

from backend.agent.orchestrator import AgentOrchestrator
from backend.agent.planner import Plan


class PerToolArgumentsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["AGENT_AUDIT_LOG_PATH"] = os.path.join(self._tmp.name, "audit.log")
        self.agent = AgentOrchestrator()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_args_for_returns_shared_when_no_override(self) -> None:
        plan = Plan(
            intent="inspection",
            tools=["process"],
            arguments={"user_role": "viewer", "limit": 50},
        )
        self.assertEqual(plan.args_for("process")["limit"], 50)
        self.assertEqual(plan.args_for("process")["user_role"], "viewer")

    def test_per_tool_override_wins_over_shared(self) -> None:
        plan = Plan(
            intent="inspection",
            tools=["process", "process.top"],
            arguments={"user_role": "viewer", "limit": 150},
            arguments_by_tool={"process.top": {"limit": 10}},
        )
        self.assertEqual(plan.args_for("process")["limit"], 150)
        self.assertEqual(plan.args_for("process.top")["limit"], 10)
        self.assertEqual(plan.args_for("process.top")["user_role"], "viewer")

    def test_schema_validation_isolates_conflicting_limit(self) -> None:
        plan = Plan(
            intent="inspection",
            tools=["process", "process.top"],
            arguments={"user_role": "viewer"},
            arguments_by_tool={
                "process": {"limit": 150},
                "process.top": {"limit": 30},
            },
        )

        security = self.agent.executor.evaluate_security(
            plan,
            "viewer1",
            "列出前 150 个进程并定位高 CPU 进程",
            approved=False,
        )

        self.assertFalse(security["blocked"], msg=security["reasons"])

    def test_shared_limit_that_exceeds_one_tool_still_blocks(self) -> None:
        plan = Plan(
            intent="inspection",
            tools=["process", "process.top"],
            arguments={"user_role": "viewer", "limit": 150},
        )

        security = self.agent.executor.evaluate_security(
            plan,
            "viewer1",
            "列出前 150 个进程并定位高 CPU 进程",
            approved=False,
        )

        self.assertTrue(security["blocked"])
        self.assertTrue(
            any("process.top: limit exceeds maximum 50" in reason for reason in security["reasons"]),
            msg=security["reasons"],
        )

    def test_per_tool_required_missing_still_blocks(self) -> None:
        plan = Plan(
            intent="inspection",
            tools=["process.detail"],
            arguments={"user_role": "viewer"},
            arguments_by_tool={"process.detail": {}},
        )

        security = self.agent.executor.evaluate_security(
            plan,
            "viewer1",
            "查看进程详情",
            approved=False,
        )

        self.assertTrue(security["blocked"])
        self.assertIn("process.detail: pid is required", security["reasons"])

    def test_per_tool_args_satisfy_required(self) -> None:
        plan = Plan(
            intent="inspection",
            tools=["process.detail"],
            arguments={"user_role": "viewer"},
            arguments_by_tool={"process.detail": {"pid": 1234}},
        )

        security = self.agent.executor.evaluate_security(
            plan,
            "viewer1",
            "查看 PID 1234 的进程详情",
            approved=False,
        )

        self.assertFalse(security["blocked"], msg=security["reasons"])


if __name__ == "__main__":
    unittest.main()
