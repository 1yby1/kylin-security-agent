from __future__ import annotations

import os
import tempfile
import unittest

from backend.agent.executor import ToolExecutor
from backend.agent.llm_client import LLMClient, LLMDecision
from backend.audit.logger import AuditLogger
from backend.agent.planner import Plan, PlanStep, Planner
from backend.mcp_tools.registry import ToolDefinition, ToolRegistry


def _build_orchestration_registry() -> ToolRegistry:
    """A deterministic, OS-independent registry for orchestration tests."""
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="probe",
            title="Probe",
            description="Returns a fixed structure with a target pid.",
            category="perception",
            handler=lambda arguments: {
                "value": {"pid": 4321},
                "analysis": {"top": [{"pid": 4321, "name": "worker"}]},
            },
            input_schema={"type": "object", "properties": {}},
        )
    )
    registry.register(
        ToolDefinition(
            name="probe.str",
            title="Probe (string pid)",
            description="Returns a pid as a string to exercise type mismatch.",
            category="perception",
            handler=lambda arguments: {"value": {"pid": "4321"}},
            input_schema={"type": "object", "properties": {}},
        )
    )
    registry.register(
        ToolDefinition(
            name="sink",
            title="Sink",
            description="Echoes the pid it received so chaining can be asserted.",
            category="perception",
            handler=lambda arguments: {"received_pid": arguments.get("pid")},
            input_schema={
                "type": "object",
                "properties": {"pid": {"type": "integer", "minimum": 101}},
            },
        )
    )
    registry.register(
        ToolDefinition(
            name="boom",
            title="Boom",
            description="Always returns a top-level error to exercise fail-fast.",
            category="perception",
            handler=lambda arguments: {"error": "boom"},
            input_schema={"type": "object", "properties": {}},
        )
    )
    registry.register(
        ToolDefinition(
            name="controlled",
            title="Controlled op",
            description="A medium-risk operation that needs secondary confirmation.",
            category="operation",
            handler=lambda arguments: {"done": True},
            input_schema={"type": "object", "properties": {}},
            risk_level="medium",
            read_only=False,
        )
    )
    return registry


class ToolOrchestrationTests(unittest.TestCase):
    def setUp(self) -> None:
        from backend.audit.store import reset_audit_stores

        reset_audit_stores()
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["AGENT_AUDIT_DB_PATH"] = os.path.join(self._tmp.name, "audit.db")
        self.executor = ToolExecutor(_build_orchestration_registry())

    def tearDown(self) -> None:
        from backend.audit.store import reset_audit_stores

        reset_audit_stores()
        os.environ.pop("AGENT_AUDIT_DB_PATH", None)
        self._tmp.cleanup()

    def _execute(self, plan: Plan, approved: bool = True):
        return self.executor.execute(
            plan=plan,
            user_id="operator1",
            raw_query="orchestration test",
            approved=approved,
            trace_id="trace-orchestration",
            role="operator",
        )

    def test_multi_step_runs_in_order(self) -> None:
        plan = Plan(
            intent="inspection",
            tools=["probe", "sink"],
            steps=[
                PlanStep(id="s1", tool="probe", arguments={}),
                PlanStep(id="s2", tool="sink", arguments={"pid": 1234}),
            ],
        )
        result = self._execute(plan)
        self.assertFalse(result.blocked)
        self.assertIn("probe", result.result)
        self.assertIn("sink", result.result)
        self.assertEqual([step["id"] for step in result.steps], ["s1", "s2"])
        self.assertEqual([step["status"] for step in result.steps], ["completed", "completed"])

    def test_step_output_feeds_next_step_input(self) -> None:
        plan = Plan(
            intent="diagnosis",
            tools=["probe", "sink"],
            steps=[
                PlanStep(id="s1", tool="probe", arguments={}),
                PlanStep(id="s2", tool="sink", arguments={"pid": "${s1.value.pid}"}),
            ],
        )
        result = self._execute(plan)
        self.assertFalse(result.blocked)
        # The reference resolved to the real int and reached the sink tool.
        self.assertEqual(result.result["sink"]["received_pid"], 4321)
        self.assertEqual(result.steps[1]["arguments"]["pid"], 4321)

    def test_list_index_reference(self) -> None:
        plan = Plan(
            intent="diagnosis",
            tools=["probe", "sink"],
            steps=[
                PlanStep(id="s1", tool="probe", arguments={}),
                PlanStep(id="s2", tool="sink", arguments={"pid": "${s1.analysis.top[0].pid}"}),
            ],
        )
        result = self._execute(plan)
        self.assertFalse(result.blocked)
        self.assertEqual(result.result["sink"]["received_pid"], 4321)

    def test_unknown_step_reference_fails_fast(self) -> None:
        plan = Plan(
            intent="diagnosis",
            tools=["sink"],
            steps=[PlanStep(id="s1", tool="sink", arguments={"pid": "${sX.value.pid}"})],
        )
        result = self._execute(plan)
        self.assertTrue(result.blocked)
        self.assertIn("未知或尚未执行", result.message)
        self.assertEqual(result.result, {})

    def test_missing_reference_path_fails_fast(self) -> None:
        plan = Plan(
            intent="diagnosis",
            tools=["probe", "sink"],
            steps=[
                PlanStep(id="s1", tool="probe", arguments={}),
                PlanStep(id="s2", tool="sink", arguments={"pid": "${s1.value.missing}"}),
            ],
        )
        result = self._execute(plan)
        self.assertTrue(result.blocked)
        self.assertIn("路径不存在", result.message)
        # Earlier step still ran; its output is preserved for context.
        self.assertIn("probe", result.result)

    def test_blocked_step_stops_chain_after_earlier_step_ran(self) -> None:
        plan = Plan(
            intent="risky_operation",
            tools=["probe", "controlled", "sink"],
            steps=[
                PlanStep(id="s1", tool="probe", arguments={}),
                PlanStep(id="s2", tool="controlled", arguments={}),
                PlanStep(id="s3", tool="sink", arguments={"pid": 4321}),
            ],
        )
        # controlled is medium-risk and needs confirmation; approved=False blocks it.
        result = self._execute(plan, approved=False)
        self.assertTrue(result.blocked)
        self.assertTrue(result.approved_required)
        self.assertIn("probe", result.result)
        self.assertNotIn("sink", result.result)
        self.assertEqual(result.security["blocked_step"], "s2")
        self.assertEqual(result.security["risk_level"], "medium")
        statuses = {step["id"]: step["status"] for step in result.steps}
        self.assertEqual(statuses["s1"], "completed")
        self.assertEqual(statuses["s2"], "blocked")
        self.assertNotIn("s3", statuses)

    def test_string_pid_reference_fails_schema_at_target_step(self) -> None:
        # A reference keeps its native type: a string pid must not satisfy an
        # integer schema, so the chain is blocked at the consuming step.
        plan = Plan(
            intent="diagnosis",
            tools=["probe.str", "sink"],
            steps=[
                PlanStep(id="s1", tool="probe.str", arguments={}),
                PlanStep(id="s2", tool="sink", arguments={"pid": "${s1.value.pid}"}),
            ],
        )
        result = self._execute(plan)
        self.assertTrue(result.blocked)
        self.assertEqual(result.security["blocked_step"], "s2")
        self.assertTrue(any("pid must be integer" in reason for reason in result.security["reasons"]))

    def test_evaluate_security_marks_reference_steps_deferred(self) -> None:
        plan = Plan(
            intent="diagnosis",
            tools=["probe", "sink"],
            steps=[
                PlanStep(id="s1", tool="probe", arguments={}),
                PlanStep(id="s2", tool="sink", arguments={"pid": "${s1.value.pid}"}),
            ],
        )
        security = self.executor.evaluate_security(
            plan=plan, user_id="operator1", raw_query="orchestration test", role="operator"
        )
        self.assertFalse(security["blocked"])
        deferred = {step["step_id"]: step["deferred"] for step in security["steps"]}
        self.assertFalse(deferred["s1"])
        self.assertTrue(deferred["s2"])

    def test_tool_failure_halts_chain(self) -> None:
        plan = Plan(
            intent="diagnosis",
            tools=["boom", "sink"],
            steps=[
                PlanStep(id="s1", tool="boom", arguments={}),
                PlanStep(id="s2", tool="sink", arguments={"pid": 4321}),
            ],
        )
        result = self._execute(plan)
        # The failing tool stops the chain; the later step never runs.
        self.assertIn("执行失败", result.message)
        self.assertIn("boom", result.result)
        self.assertNotIn("sink", result.result)
        statuses = {step["id"]: step["status"] for step in result.steps}
        self.assertEqual(statuses["s1"], "error")
        self.assertNotIn("s2", statuses)

    def test_repeated_tool_preserves_each_result(self) -> None:
        plan = Plan(
            intent="inspection",
            tools=["probe"],
            steps=[
                PlanStep(id="s1", tool="probe", arguments={}),
                PlanStep(id="s2", tool="probe", arguments={}),
            ],
        )
        result = self._execute(plan)
        self.assertFalse(result.blocked)
        # Both runs are preserved under distinct keys instead of overwriting.
        self.assertIn("probe", result.result)
        self.assertIn("probe#2", result.result)
        self.assertEqual([step["id"] for step in result.steps], ["s1", "s2"])

    def test_duplicate_step_id_is_blocked(self) -> None:
        plan = Plan(
            intent="inspection",
            tools=["probe", "sink"],
            steps=[
                PlanStep(id="s1", tool="probe", arguments={}),
                PlanStep(id="s1", tool="sink", arguments={"pid": 4321}),
            ],
        )
        result = self._execute(plan)
        self.assertTrue(result.blocked)
        self.assertIn("id 重复", result.message)
        # Nothing runs when the orchestration is ambiguous.
        self.assertEqual(result.result, {})
        self.assertEqual(result.steps, [])

    def test_legacy_plan_derives_one_step_per_tool(self) -> None:
        plan = Plan(intent="inspection", tools=["probe", "sink"], arguments={"pid": 4321})
        steps = plan.execution_steps()
        self.assertEqual([step.id for step in steps], ["s1", "s2"])
        self.assertEqual([step.tool for step in steps], ["probe", "sink"])
        # Derived steps share the plan-level arguments.
        self.assertEqual(steps[1].arguments["pid"], 4321)


class _FakeLLMClient(LLMClient):
    def __init__(self, decision: LLMDecision) -> None:  # noqa: D401 - test double
        self._decision = decision

    def analyze(self, query, context, tool_manifest=None):  # type: ignore[override]
        return self._decision


class PlannerStepsTests(unittest.TestCase):
    def test_planner_builds_steps_and_injects_context(self) -> None:
        decision = LLMDecision(
            intent="diagnosis",
            tools=["system", "process"],
            arguments={},
            steps=[
                {"id": "s1", "tool": "system", "arguments": {}},
                {"id": "s2", "tool": "process", "arguments": {"limit": 5}},
            ],
        )
        planner = Planner(_FakeLLMClient(decision))
        plan = planner.plan("先看系统再看进程", {"user_role": "operator"})
        self.assertEqual(plan.source, "llm")
        self.assertEqual([step.id for step in plan.steps], ["s1", "s2"])
        self.assertEqual(plan.tools, ["system", "process"])
        # Base context (query + user_role) is merged into every step.
        self.assertEqual(plan.steps[1].arguments["limit"], 5)
        self.assertEqual(plan.steps[1].arguments["user_role"], "operator")
        self.assertEqual(plan.steps[0].arguments["query"], "先看系统再看进程")


class ParseStepsTests(unittest.TestCase):
    def test_valid_steps_are_cleaned(self) -> None:
        steps = LLMClient._parse_steps(
            [
                {"tool": "system"},
                {"id": "kill", "tool": "process.kill", "arguments": {"pid": 200}},
            ]
        )
        assert steps is not None
        self.assertEqual(steps[0]["id"], "s1")
        self.assertEqual(steps[0]["arguments"], {})
        self.assertEqual(steps[1]["id"], "kill")
        self.assertEqual(steps[1]["tool"], "process.kill")

    def test_unregistered_tool_is_dropped(self) -> None:
        steps = LLMClient._parse_steps([{"tool": "rm"}, {"tool": "system"}])
        assert steps is not None
        self.assertEqual([step["tool"] for step in steps], ["system"])

    def test_empty_or_non_list_returns_none(self) -> None:
        self.assertIsNone(LLMClient._parse_steps([]))
        self.assertIsNone(LLMClient._parse_steps(None))
        self.assertIsNone(LLMClient._parse_steps([{"tool": "unknown"}]))

    def test_duplicate_ids_are_rejected(self) -> None:
        steps = LLMClient._parse_steps(
            [
                {"id": "s1", "tool": "system"},
                {"id": "s1", "tool": "process"},
            ]
        )
        self.assertIsNone(steps)


class AuditRecentFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        from backend.audit.store import reset_audit_stores

        reset_audit_stores()
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["AGENT_AUDIT_DB_PATH"] = os.path.join(self._tmp.name, "audit.db")
        self.audit = AuditLogger()

    def tearDown(self) -> None:
        from backend.audit.store import reset_audit_stores

        reset_audit_stores()
        os.environ.pop("AGENT_AUDIT_DB_PATH", None)
        self._tmp.cleanup()

    def test_user_filter_applies_before_limit(self) -> None:
        # alice's events are written first (older), bob's fill the recent window.
        for index in range(5):
            self.audit.event(
                trace_id=f"a{index}", stage="tool_call", user_id="alice", status="completed", data={}
            )
        for index in range(5):
            self.audit.event(
                trace_id=f"b{index}", stage="tool_call", user_id="bob", status="completed", data={}
            )
        # With limit=5 the global recent window is all bob; filtering must happen
        # in SQL so alice's older records are still returned.
        records = self.audit.read_recent(limit=5, user_id="alice")
        self.assertEqual(len(records), 5)
        self.assertTrue(all(record["user_id"] == "alice" for record in records))

    def test_status_filter_applies_before_limit(self) -> None:
        for index in range(5):
            self.audit.event(
                trace_id=f"ok{index}", stage="tool_call", user_id="alice", status="completed", data={}
            )
        for index in range(5):
            self.audit.event(
                trace_id=f"bad{index}", stage="tool_call", user_id="alice", status="blocked", data={}
            )
        records = self.audit.read_recent(limit=5, status="completed")
        self.assertEqual(len(records), 5)
        self.assertTrue(all(record["status"] == "completed" for record in records))


if __name__ == "__main__":
    unittest.main()
