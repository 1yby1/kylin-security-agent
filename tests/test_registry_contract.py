from __future__ import annotations

import json
import unittest

from backend.agent.executor import ToolExecutor
from backend.agent.llm_client import LLMClient
from backend.agent.planner import Plan
from backend.config import LLMSettings
from backend.mcp_tools.registry import ToolDefinition, ToolRegistry


class StaticLLMClient(LLMClient):
    def __init__(self, content: dict) -> None:
        super().__init__(
            LLMSettings(
                provider="deepseek",
                api_key="test-key",
                base_url="http://unused.local/chat/completions",
                model="test-model",
            )
        )
        self._content = json.dumps(content, ensure_ascii=False)

    def _chat_json(self, system_prompt, user_payload):
        return self._content


class RegistryContractTests(unittest.TestCase):
    def test_llm_allowed_tools_are_read_from_manifest(self) -> None:
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="custom.dynamic",
                title="Custom Dynamic Tool",
                description="Tool registered only in this test manifest.",
                category="perception",
                handler=lambda arguments: {"ok": True},
                input_schema={"type": "object", "properties": {}},
            )
        )
        client = StaticLLMClient(
            {
                "intent": "inspection",
                "summary": "custom dynamic plan",
                "tools": ["custom.dynamic", "not.registered"],
                "arguments": {},
                "reasoning": ["test manifest filtering"],
            }
        )

        decision = client.analyze("run custom dynamic", {}, registry.manifest())

        self.assertIsNotNone(decision)
        self.assertEqual(decision.tools, ["custom.dynamic"])

    def test_security_risk_is_read_from_tool_definition(self) -> None:
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="custom.medium",
                title="Custom Medium Tool",
                description="Medium-risk tool registered only in this test.",
                category="operation",
                handler=lambda arguments: {"ok": True},
                input_schema={"type": "object", "properties": {}},
                risk_level="medium",
                read_only=False,
            )
        )
        executor = ToolExecutor(registry)
        plan = Plan(
            intent="risky_operation",
            tools=["custom.medium"],
            arguments={"user_role": "viewer"},
        )

        blocked = executor.evaluate_security(plan, "viewer1", "run custom medium", approved=True)

        self.assertEqual(blocked["risk_level"], "medium")
        self.assertTrue(blocked["blocked"])
        self.assertIn("role viewer is not allowed for risk level medium", blocked["reasons"])

    def test_hallucinated_placeholder_args_are_dropped(self) -> None:
        executor = ToolExecutor()
        client = StaticLLMClient(
            {
                "intent": "risky_operation",
                "summary": "kill a process",
                "tools": ["process.kill"],
                "arguments": {},
                "arguments_by_tool": {"process.kill": {"pid": 0, "dry_run": True}},
                "reasoning": ["test placeholder filtering"],
            }
        )

        decision = client.analyze("杀死进程", {}, executor.tool_manifest())

        self.assertIsNotNone(decision)
        # pid=0 violates process.kill schema minimum (101) -> dropped as a placeholder.
        self.assertNotIn("pid", decision.arguments_by_tool["process.kill"])
        self.assertEqual(decision.arguments_by_tool["process.kill"], {"dry_run": True})

    def test_legitimate_zero_is_kept(self) -> None:
        executor = ToolExecutor()
        client = StaticLLMClient(
            {
                "intent": "inspection",
                "summary": "top processes",
                "tools": ["process.top"],
                "arguments": {},
                "arguments_by_tool": {"process.top": {"min_percent": 0, "metric": "cpu"}},
                "reasoning": ["test legitimate zero"],
            }
        )

        decision = client.analyze("高 CPU 进程", {}, executor.tool_manifest())

        self.assertIsNotNone(decision)
        # min_percent allows 0, so it must survive the placeholder filter.
        self.assertEqual(decision.arguments_by_tool["process.top"]["min_percent"], 0)
        self.assertEqual(decision.arguments_by_tool["process.top"]["metric"], "cpu")

    def test_empty_override_after_filtering_is_removed(self) -> None:
        executor = ToolExecutor()
        client = StaticLLMClient(
            {
                "intent": "diagnosis",
                "summary": "search logs",
                "tools": ["log.search"],
                "arguments": {"keyword": "error"},
                "arguments_by_tool": {"log.search": {"keyword": "", "unit": "   "}},
                "reasoning": ["test empty override removal"],
            }
        )

        decision = client.analyze("搜索日志", {}, executor.tool_manifest())

        self.assertIsNotNone(decision)
        # Both override values are blank placeholders, so the tool key is dropped entirely.
        self.assertNotIn("log.search", decision.arguments_by_tool)


if __name__ == "__main__":
    unittest.main()
