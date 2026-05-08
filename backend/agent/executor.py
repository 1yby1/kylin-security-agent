from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.agent.planner import Plan
from backend.audit.logger import AuditLogger
from backend.mcp_tools import ToolRegistry, build_registry
from backend.security.guard import SecurityGuard


@dataclass(frozen=True)
class ExecutionResult:
    approved_required: bool
    blocked: bool
    message: str
    result: dict[str, Any]
    security: dict[str, Any]
    executed_commands: list[dict[str, Any]]


class ToolExecutor:
    def __init__(self, registry: ToolRegistry | None = None) -> None:
        self._registry = registry or build_registry()
        self._guard = SecurityGuard()
        self._audit = AuditLogger()

    def available_tools(self) -> list[str]:
        return self._registry.names()

    def tool_metadata(self, tool_name: str) -> dict[str, Any] | None:
        return self._registry.describe(tool_name)

    def tool_manifest(self) -> dict[str, Any]:
        return self._registry.manifest()

    def evaluate_security(self, plan: Plan, user_id: str, raw_query: str, approved: bool = False) -> dict[str, Any]:
        return self._guard.check(
            raw_query=raw_query,
            tools=plan.tools,
            arguments=plan.arguments,
            user_id=user_id,
            registry=self._registry,
            approved=approved,
        ).to_dict()

    def execute(
        self,
        plan: Plan,
        user_id: str,
        raw_query: str,
        approved: bool = False,
        trace_id: str | None = None,
    ) -> ExecutionResult:
        safety = self._guard.check(
            raw_query=raw_query,
            tools=plan.tools,
            arguments=plan.arguments,
            user_id=user_id,
            registry=self._registry,
            approved=approved,
        )
        security = safety.to_dict()
        if trace_id:
            self._audit.event(
                trace_id=trace_id,
                stage="security_validation",
                user_id=user_id,
                status="blocked" if safety.blocked else "passed",
                data={"security": security},
            )
        if safety.blocked:
            status = "approval_required" if safety.confirmation_required and not approved and safety.risk_level != "high" else "blocked"
            self._audit.write(user_id, raw_query, plan, status, {"security": security, "executed_commands": []})
            return ExecutionResult(
                approved_required=safety.confirmation_required and not approved,
                blocked=True,
                message=safety.reason or "security validation failed",
                result={},
                security=security,
                executed_commands=[],
            )

        output: dict[str, Any] = {}
        executed_commands: list[dict[str, Any]] = []
        for tool_name in plan.tools:
            if trace_id:
                self._audit.event(
                    trace_id=trace_id,
                    stage="tool_call",
                    user_id=user_id,
                    status="started",
                    data={"tool": tool_name, "arguments": plan.arguments},
                )
            output[tool_name] = self._registry.call(tool_name, plan.arguments)
            tool_commands = self._extract_executed_commands(tool_name, output[tool_name])
            executed_commands.extend(tool_commands)
            if trace_id:
                self._audit.event(
                    trace_id=trace_id,
                    stage="tool_call",
                    user_id=user_id,
                    status="completed",
                    data={
                        "tool": tool_name,
                        "executed_commands": tool_commands,
                        "result": output[tool_name],
                    },
                )

        self._audit.write(
            user_id,
            raw_query,
            plan,
            "success",
            {"security": security, "executed_commands": executed_commands, "output": output},
        )
        return ExecutionResult(
            approved_required=False,
            blocked=False,
            message="Execution completed.",
            result=output,
            security=security,
            executed_commands=executed_commands,
        )

    @classmethod
    def _extract_executed_commands(cls, tool_name: str, result: dict[str, Any]) -> list[dict[str, Any]]:
        commands: list[dict[str, Any]] = []
        cls._collect_command_dicts(tool_name, result, "", commands)
        return commands

    @classmethod
    def _collect_command_dicts(
        cls,
        tool_name: str,
        value: Any,
        path: str,
        commands: list[dict[str, Any]],
    ) -> None:
        if isinstance(value, dict):
            command = value.get("command")
            if isinstance(command, str) and "exit_code" in value:
                item: dict[str, Any] = {
                    "tool": tool_name,
                    "path": path or "$",
                    "command": command,
                    "exit_code": value.get("exit_code"),
                }
                if "execution_identity" in value:
                    item["execution_identity"] = value["execution_identity"]
                commands.append(item)
            for key, nested in value.items():
                nested_path = f"{path}.{key}" if path else str(key)
                cls._collect_command_dicts(tool_name, nested, nested_path, commands)
        elif isinstance(value, list):
            for index, nested in enumerate(value):
                cls._collect_command_dicts(tool_name, nested, f"{path}[{index}]", commands)
