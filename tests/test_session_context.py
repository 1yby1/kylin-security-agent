from __future__ import annotations

import unittest
from typing import Any

from backend.agent.executor import ExecutionResult
from backend.agent.orchestrator import AgentOrchestrator
from backend.agent.planner import Plan, Planner
from backend.agent.session_context import ConversationSessionStore
from backend.config import LLMSettings
from backend.agent.llm_client import LLMClient


class FakeExecutor:
    def __init__(self, result: dict[str, Any]) -> None:
        self._result = result

    def tool_manifest(self) -> dict[str, Any]:
        return {}

    def execute(self, *, plan: Plan, user_id, raw_query, approved=False, trace_id=None, role=None) -> ExecutionResult:
        return ExecutionResult(
            approved_required=False,
            blocked=False,
            message="ok",
            result=self._result,
            security={"blocked": False, "risk_level": "low"},
            executed_commands=[],
        )

    def evaluate_security(self, *, plan: Plan, user_id, raw_query, approved=False, role=None) -> dict[str, Any]:
        return {
            "risk_level": "low",
            "blocked": False,
            "confirmation_required": False,
            "audit_required": True,
            "reasons": [],
            "checks": [],
        }


class RecordingPlanner:
    def __init__(self) -> None:
        self.contexts: list[dict[str, Any]] = []

    def plan(self, query, context, manifest) -> Plan:
        self.contexts.append(context)
        return Plan(intent="inspection", tools=["process"], arguments={}, source="rules")

    def plan_next(self, query, context, prior_results, executed_tools, manifest=None) -> Plan | None:
        return None


def _disabled_planner() -> Planner:
    return Planner(LLMClient(LLMSettings(provider="disabled", api_key="", base_url="", model="")))


class ConversationSessionStoreTests(unittest.TestCase):
    def test_extracts_brief_process_context_and_expires_it(self) -> None:
        now = 1000.0
        store = ConversationSessionStore(ttl_seconds=10, clock=lambda: now)
        result = {
            "process": {
                "analysis": {
                    "top_cpu": [
                        {"pid": "4321", "command": "python", "cpu_percent": 92.5},
                    ]
                }
            }
        }
        plan = Plan(intent="inspection", tools=["process"], arguments={}, source="rules")

        store.update("demo", query="查看 CPU 最高进程", plan=plan, result=result, conclusion={"conclusion": "发现 python 占用较高"})
        context = store.context("demo")

        self.assertEqual(context["last_entities"]["pid"], "4321")
        self.assertEqual(context["last_entities"]["process_name"], "python")
        self.assertIn("查看 CPU 最高进程", context["summary"])

        now = 1011.0
        self.assertEqual(store.context("demo"), {})

    def test_generates_session_id_when_missing(self) -> None:
        store = ConversationSessionStore(ttl_seconds=10)

        session_id = store.resolve_session_id(None)

        self.assertTrue(session_id)
        self.assertNotEqual(session_id, store.resolve_session_id(None))


class OrchestratorSessionContextTests(unittest.TestCase):
    def test_injects_previous_session_context_into_next_plan(self) -> None:
        planner = RecordingPlanner()
        executor = FakeExecutor(
            {
                "process": {
                    "analysis": {
                        "top_cpu": [
                            {"pid": "4321", "command": "python", "cpu_percent": 92.5},
                        ]
                    }
                }
            }
        )
        store = ConversationSessionStore(ttl_seconds=60)
        orchestrator = AgentOrchestrator(planner=planner, executor=executor, session_store=store)
        orchestrator._llm_client.conclude = lambda **kwargs: None  # type: ignore

        first = orchestrator.run("查看 CPU 最高进程", "u1", {}, session_id="demo", role="viewer")
        second = orchestrator.run("那再看下那个进程", "u1", {}, session_id="demo", role="viewer")

        self.assertEqual(first.session_id, "demo")
        self.assertEqual(second.session_id, "demo")
        self.assertEqual(planner.contexts[1]["conversation"]["last_entities"]["pid"], "4321")
        self.assertIn("pid=4321", second.context_summary)


class PlannerSessionFollowupTests(unittest.TestCase):
    def test_process_followup_uses_last_pid_without_leaking_conversation_to_tool_args(self) -> None:
        plan = _disabled_planner().plan(
            "那再看下那个进程",
            {"conversation": {"last_entities": {"pid": "4321", "process_name": "python"}}},
        )

        self.assertIn("process", plan.tools)
        self.assertEqual(plan.arguments["pid"], 4321)
        self.assertNotIn("conversation", plan.arguments)

    def test_service_log_followup_uses_last_service_name(self) -> None:
        plan = _disabled_planner().plan(
            "继续看它的日志",
            {"conversation": {"last_entities": {"service_name": "nginx"}}},
        )

        self.assertEqual(plan.tools, ["log"])
        self.assertEqual(plan.arguments["unit"], "nginx")


if __name__ == "__main__":
    unittest.main()
