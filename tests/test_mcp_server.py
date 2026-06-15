import os
import unittest
from unittest import mock

from backend.agent.executor import ExecutionResult, ToolExecutor
from backend.config import get_mcp_settings
from backend.mcp_server.server import build_tool_list, run_tool_call


class MCPSettingsTests(unittest.TestCase):
    def test_defaults_to_lowest_privilege(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENT_MCP_CLIENT_ROLE", None)
            os.environ.pop("AGENT_MCP_CLIENT_USER", None)
            settings = get_mcp_settings()
            self.assertEqual(settings.client_role, "viewer")
            self.assertEqual(settings.client_user_id, "mcp-client")

    def test_role_override(self):
        with mock.patch.dict(os.environ, {"AGENT_MCP_CLIENT_ROLE": "operator"}, clear=False):
            self.assertEqual(get_mcp_settings().client_role, "operator")


class BuildToolListTests(unittest.TestCase):
    def test_lists_all_registry_tools_with_object_schema(self):
        executor = ToolExecutor()
        tools = build_tool_list(executor)
        names = sorted(tool.name for tool in tools)
        self.assertEqual(names, sorted(executor.available_tools()))
        for tool in tools:
            self.assertIsInstance(tool.inputSchema, dict)
            self.assertEqual(tool.inputSchema.get("type", "object"), "object")


class _RecordingAudit:
    def __init__(self):
        self.events = []

    def event(self, **kwargs):
        self.events.append(kwargs)


class _FakeExecutor:
    def __init__(self):
        self.calls = []

    def available_tools(self):
        return ["service.restart", "system"]

    def execute(self, *, plan, user_id, raw_query, approved, trace_id=None):
        self.calls.append(
            {"plan": plan, "user_id": user_id, "approved": approved, "trace_id": trace_id}
        )
        return ExecutionResult(
            approved_required=False,
            blocked=False,
            message="ok",
            result={plan.tools[0]: {"ok": True}},
            security={"risk_level": "medium"},
            executed_commands=[],
        )


class RunToolCallTests(unittest.TestCase):
    def test_injects_default_role_and_passes_approved(self):
        fake = _FakeExecutor()
        audit = _RecordingAudit()
        with mock.patch.dict(os.environ, {"AGENT_MCP_CLIENT_ROLE": "operator"}, clear=False):
            payload = run_tool_call(
                fake, "service.restart", {"service_name": "nginx", "approved": True}, audit=audit
            )
        call = fake.calls[0]
        self.assertEqual(call["plan"].arguments.get("user_role"), "operator")
        self.assertTrue(call["approved"])
        self.assertEqual(call["user_id"], "mcp-client")
        self.assertFalse(payload["blocked"])
        self.assertIn("trace_id", payload)
        stages = [event["stage"] for event in audit.events]
        self.assertIn("received_instruction", stages)
        self.assertIn("trace_complete", stages)
        self.assertTrue(all(event["data"].get("channel") == "mcp" for event in audit.events))

    def test_unknown_tool_raises(self):
        with self.assertRaises(ValueError):
            run_tool_call(_FakeExecutor(), "does.not.exist", {}, audit=_RecordingAudit())

    def test_protected_pid_blocked_through_guard(self):
        executor = ToolExecutor()  # real registry + guard
        payload = run_tool_call(
            executor, "process.kill", {"pid": 1, "expected_name": "x"}, audit=_RecordingAudit()
        )
        self.assertTrue(payload["blocked"])
        self.assertEqual(payload["executed_commands"], [])


if __name__ == "__main__":
    unittest.main()
