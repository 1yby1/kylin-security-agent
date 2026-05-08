from __future__ import annotations

import os
import tempfile
import unittest

from backend.agent.executor import ToolExecutor
from backend.agent.orchestrator import AgentOrchestrator
from backend.agent.planner import Plan
from backend.audit.logger import AuditLogger
from backend.mcp_tools.registry import ToolDefinition, ToolRegistry


class ControlledToolSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["AGENT_AUDIT_LOG_PATH"] = os.path.join(self._tmp.name, "audit.log")
        self.agent = AgentOrchestrator()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def evaluate(self, query: str, context: dict, approved: bool, user_id: str = "operator1") -> dict:
        return self.agent.evaluate_security(query, user_id, context, approved)

    def test_controlled_tools_are_registered(self) -> None:
        tools = self.agent.executor.available_tools()
        self.assertIn("service.restart", tools)
        self.assertIn("temp.clean", tools)
        self.assertIn("process.kill", tools)

        for tool_name in ["service.restart", "temp.clean", "process.kill"]:
            metadata = self.agent.executor.tool_metadata(tool_name)
            self.assertIsNotNone(metadata)
            self.assertEqual(metadata["risk_level"], "medium")
            self.assertFalse(metadata["read_only"])

    def test_service_restart_security_cases(self) -> None:
        blocked_without_approval = self.evaluate(
            "重启 nginx 服务",
            {"user_role": "operator", "service_name": "nginx"},
            approved=False,
        )
        self.assertTrue(blocked_without_approval["security"]["blocked"])
        self.assertIn("secondary confirmation required", blocked_without_approval["security"]["reasons"])

        blocked_viewer = self.evaluate(
            "重启 nginx 服务",
            {"user_role": "viewer", "service_name": "nginx"},
            approved=True,
            user_id="viewer1",
        )
        self.assertTrue(blocked_viewer["security"]["blocked"])

        missing_name = self.evaluate("重启服务", {"user_role": "operator"}, approved=True)
        self.assertTrue(missing_name["security"]["blocked"])
        self.assertIn("service.restart: service_name is required", missing_name["security"]["reasons"])

        protected_service = self.evaluate(
            "重启 firewalld 服务",
            {"user_role": "admin", "service_name": "firewalld"},
            approved=True,
            user_id="admin",
        )
        self.assertTrue(protected_service["security"]["blocked"])
        self.assertEqual(protected_service["security"]["risk_level"], "high")

        allowed = self.evaluate(
            "重启 nginx 服务",
            {"user_role": "operator", "service_name": "nginx"},
            approved=True,
        )
        self.assertFalse(allowed["security"]["blocked"])
        self.assertEqual(allowed["security"]["risk_level"], "medium")

    def test_temp_clean_security_cases(self) -> None:
        allowed = self.evaluate(
            "清理 /tmp 临时文件",
            {"user_role": "operator", "path": "/tmp", "dry_run": True},
            approved=True,
        )
        self.assertFalse(allowed["security"]["blocked"])

        blocked_without_approval = self.evaluate(
            "清理 /tmp 临时文件",
            {"user_role": "operator", "path": "/tmp"},
            approved=False,
        )
        self.assertTrue(blocked_without_approval["security"]["blocked"])
        self.assertIn("secondary confirmation required", blocked_without_approval["security"]["reasons"])

        blocked_viewer = self.evaluate(
            "清理 /tmp 临时文件",
            {"user_role": "viewer", "path": "/tmp"},
            approved=True,
            user_id="viewer1",
        )
        self.assertTrue(blocked_viewer["security"]["blocked"])

        missing_path = self.evaluate("清理临时文件", {"user_role": "operator"}, approved=True)
        self.assertTrue(missing_path["security"]["blocked"])
        self.assertIn("temp.clean: path is required", missing_path["security"]["reasons"])

        traversal_path = self.evaluate(
            "清理 /tmp/../etc 临时文件",
            {"user_role": "operator", "path": "/tmp/../etc"},
            approved=True,
        )
        self.assertTrue(traversal_path["security"]["blocked"])

        unsafe_path = self.evaluate(
            "清理 /etc 临时文件",
            {"user_role": "operator", "path": "/etc"},
            approved=True,
        )
        self.assertTrue(unsafe_path["security"]["blocked"])

    def test_process_kill_security_cases(self) -> None:
        allowed = self.evaluate(
            "杀死 pid 1234 进程",
            {"user_role": "operator", "pid": 1234},
            approved=True,
        )
        self.assertFalse(allowed["security"]["blocked"])

        blocked_without_approval = self.evaluate(
            "杀死 pid 1234 进程",
            {"user_role": "operator", "pid": 1234},
            approved=False,
        )
        self.assertTrue(blocked_without_approval["security"]["blocked"])
        self.assertIn("secondary confirmation required", blocked_without_approval["security"]["reasons"])

        blocked_viewer = self.evaluate(
            "杀死 pid 1234 进程",
            {"user_role": "viewer", "pid": 1234},
            approved=True,
            user_id="viewer1",
        )
        self.assertTrue(blocked_viewer["security"]["blocked"])

        missing_pid = self.evaluate("杀死进程", {"user_role": "operator"}, approved=True)
        self.assertTrue(missing_pid["security"]["blocked"])
        self.assertIn("process.kill: pid is required", missing_pid["security"]["reasons"])

        protected_pid = self.evaluate(
            "杀死 pid 1 进程",
            {"user_role": "operator", "pid": 1},
            approved=True,
        )
        self.assertTrue(protected_pid["security"]["blocked"])

        kill_9 = self.evaluate(
            "kill -9 pid 1234 process",
            {"user_role": "operator", "pid": 1234},
            approved=True,
        )
        self.assertTrue(kill_9["security"]["blocked"])

        protected_name = self.evaluate(
            "杀死 sshd 进程 pid 1234",
            {"user_role": "operator", "pid": 1234},
            approved=True,
        )
        self.assertTrue(protected_name["security"]["blocked"])

    def test_blocked_request_writes_audit_trace(self) -> None:
        result = self.agent.run(
            "重启 nginx 服务",
            "operator1",
            {"user_role": "operator", "service_name": "nginx"},
            approved=False,
        )
        self.assertTrue(result.blocked)

        records = AuditLogger().read_recent(limit=20, trace_id=result.trace_id)
        stages = [record.get("stage") for record in records]
        self.assertIn("received_instruction", stages)
        self.assertIn("llm_decision", stages)
        self.assertIn("security_validation", stages)
        self.assertIn("execution_result", stages)
        self.assertIn("final_answer", stages)
        self.assertIn("trace_complete", stages)

    def test_executed_commands_are_written_to_audit_trace(self) -> None:
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="fake.command",
                title="Fake Command",
                description="Fake command tool for audit testing.",
                category="perception",
                handler=lambda arguments: {
                    "phase": {
                        "command": "echo ok",
                        "exit_code": 0,
                        "stdout": ["ok"],
                        "stderr": [],
                        "execution_identity": {"runs_as_user": "tester"},
                    }
                },
                input_schema={"type": "object", "properties": {}},
                command_templates=["fake.command"],
            )
        )
        executor = ToolExecutor(registry)
        result = executor.execute(
            plan=Plan(intent="inspection", tools=["fake.command"], arguments={}),
            user_id="operator1",
            raw_query="fake command",
            approved=True,
            trace_id="trace-command-test",
        )

        self.assertFalse(result.blocked)
        self.assertEqual(result.executed_commands[0]["command"], "echo ok")

        records = AuditLogger().read_recent(limit=20, trace_id="trace-command-test")
        completed_calls = [
            record
            for record in records
            if record.get("stage") == "tool_call" and record.get("status") == "completed"
        ]
        self.assertEqual(completed_calls[0]["data"]["executed_commands"][0]["command"], "echo ok")

    @unittest.skipUnless(os.name == "nt", "Windows-only unsupported-platform execution check")
    def test_confirmed_execution_returns_unsupported_on_windows(self) -> None:
        cases = [
            (
                "重启 nginx 服务",
                {"user_role": "operator", "service_name": "nginx"},
                "service.restart",
                "only supported",
            ),
            (
                "清理 /tmp 临时文件",
                {"user_role": "operator", "path": "/tmp", "dry_run": True},
                "temp.clean",
                "only supported",
            ),
            (
                "杀死 pid 1234 进程",
                {"user_role": "operator", "pid": 1234, "dry_run": True},
                "process.kill",
                "only supported",
            ),
        ]
        for query, context, tool_name, expected_error in cases:
            with self.subTest(tool=tool_name):
                result = self.agent.run(query, "operator1", context, approved=True)
                self.assertFalse(result.blocked)
                self.assertIn(tool_name, result.result)
                self.assertIn(expected_error, result.result[tool_name].get("error", ""))


if __name__ == "__main__":
    unittest.main()
