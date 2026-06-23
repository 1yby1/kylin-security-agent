from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from backend.agent.planner import Plan, PlanStep
from backend.audit.logger import AuditLogger
from backend.mcp_tools import ToolRegistry, build_registry
from backend.security.guard import SecurityGuard
from backend.security.redaction import redact_security_tool_output


RISK_ORDER = {"low": 1, "medium": 2, "high": 3, "prohibited": 4}

# Whole-value step reference, e.g. "${s1.analysis.top_cpu[0].pid}". Only full
# value references are supported so the resolved value keeps its native type
# (an int pid stays an int) and no string concatenation/injection is possible.
REFERENCE_PATTERN = re.compile(r"^\$\{([^}]+)\}$")
_HAS_REFERENCE = re.compile(r"\$\{[^}]+\}")
# A clean integer literal, used for schema-directed string -> int coercion.
_INT_LITERAL = re.compile(r"-?\d+")


@dataclass(frozen=True)
class ExecutionResult:
    approved_required: bool
    blocked: bool
    message: str
    result: dict[str, Any]
    security: dict[str, Any]
    executed_commands: list[dict[str, Any]]
    steps: list[dict[str, Any]] = field(default_factory=list)


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

    def evaluate_security(self, plan: Plan, user_id: str, raw_query: str, approved: bool = False, role: str | None = None) -> dict[str, Any]:
        """Statically evaluate an orchestration without executing any tool.

        Each step is checked independently. Steps whose arguments still contain
        ``${...}`` references cannot be resolved here (their producing step has
        not run), so they are reported as ``deferred`` and excluded from the
        blocking decision; their real check happens at execution time.
        """
        step_securities: list[dict[str, Any]] = []
        for step in plan.execution_steps():
            if self._has_unresolved_references(step.arguments):
                step_securities.append(self._deferred_decision(step))
                continue
            coerced = self._coerce_arguments(step.tool, step.arguments)
            security = self._check_step(step.tool, coerced, raw_query, user_id, approved, role)
            security["step_id"] = step.id
            step_securities.append(security)
        return self._aggregate_security(step_securities, blocked_step=None)

    def execute(
        self,
        plan: Plan,
        user_id: str,
        raw_query: str,
        approved: bool = False,
        trace_id: str | None = None,
        role: str | None = None,
    ) -> ExecutionResult:
        outputs: dict[str, Any] = {}
        result_by_tool: dict[str, Any] = {}
        executed_commands: list[dict[str, Any]] = []
        step_records: list[dict[str, Any]] = []
        step_securities: list[dict[str, Any]] = []

        steps = plan.execution_steps()
        duplicate = self._duplicate_step_id(steps)
        if duplicate is not None:
            return self._reject_duplicate_step(plan, user_id, raw_query, duplicate, trace_id)

        for step in steps:
            resolved, resolve_error = self._resolve_references(step.arguments, outputs)
            if resolve_error is not None:
                security = self._reference_block_decision(step, resolve_error)
                step_securities.append(security)
                self._audit_step_security(trace_id, user_id, step, security)
                step_records.append(self._step_record(step.id, step.tool, "blocked", step.arguments, {}, security, []))
                return self._blocked_result(
                    plan, user_id, raw_query, step_securities, step_records,
                    executed_commands, result_by_tool, blocked_step=step.id,
                    approved_required=False, message=resolve_error, status="blocked",
                    role=role,
                )

            resolved = self._coerce_arguments(step.tool, resolved)
            safety = self._guard.check(
                raw_query=raw_query,
                tools=[step.tool],
                arguments=resolved,
                user_id=user_id,
                registry=self._registry,
                approved=approved,
                role=role,
            )
            security = safety.to_dict()
            security["step_id"] = step.id
            security["tool"] = step.tool
            step_securities.append(security)
            self._audit_step_security(trace_id, user_id, step, security)

            if safety.blocked:
                step_records.append(self._step_record(step.id, step.tool, "blocked", resolved, {}, security, []))
                status = (
                    "approval_required"
                    if safety.confirmation_required and not approved and safety.risk_level != "high"
                    else "blocked"
                )
                return self._blocked_result(
                    plan, user_id, raw_query, step_securities, step_records,
                    executed_commands, result_by_tool, blocked_step=step.id,
                    approved_required=safety.confirmation_required and not approved,
                    message=safety.reason or "security validation failed", status=status,
                    role=role,
                )

            if trace_id:
                self._audit.event(
                    trace_id=trace_id,
                    stage="tool_call",
                    user_id=user_id,
                    status="started",
                    data={"step_id": step.id, "tool": step.tool, "arguments": resolved},
                )
            tool_result = self._registry.call(step.tool, resolved)
            outputs[step.id] = tool_result
            self._store_result(result_by_tool, step.tool, tool_result)
            tool_commands = self._extract_executed_commands(step.tool, tool_result)
            executed_commands.extend(tool_commands)
            failure = self._tool_failure(tool_result)
            step_records.append(
                self._step_record(
                    step.id, step.tool, "error" if failure else "completed", resolved, tool_result, security, tool_commands
                )
            )
            if trace_id:
                self._audit.event(
                    trace_id=trace_id,
                    stage="tool_call",
                    user_id=user_id,
                    status="failed" if failure else "completed",
                    data={
                        "step_id": step.id,
                        "tool": step.tool,
                        "executed_commands": tool_commands,
                        "result": tool_result,
                    },
                )
            if failure:
                # Fail fast: a failed tool halts the chain; later steps do not run.
                aggregate = self._aggregate_security(step_securities, blocked_step=None)
                message = f"步骤 {step.id}（{step.tool}）执行失败：{failure}"
                self._audit.write(
                    user_id,
                    raw_query,
                    plan,
                    "failed",
                    {"security": aggregate, "executed_commands": executed_commands, "output": result_by_tool, "steps": step_records},
                )
                return ExecutionResult(
                    approved_required=False,
                    blocked=False,
                    message=message,
                    result=self._redact_results(result_by_tool, role),
                    security=aggregate,
                    executed_commands=executed_commands,
                    steps=step_records,
                )

        aggregate = self._aggregate_security(step_securities, blocked_step=None)
        self._audit.write(
            user_id,
            raw_query,
            plan,
            "success",
            {"security": aggregate, "executed_commands": executed_commands, "output": result_by_tool, "steps": step_records},
        )
        return ExecutionResult(
            approved_required=False,
            blocked=False,
            message="Execution completed.",
            result=self._redact_results(result_by_tool, role),
            security=aggregate,
            executed_commands=executed_commands,
            steps=step_records,
        )

    # -- security helpers -------------------------------------------------

    def _redact_results(self, result_by_tool: dict[str, Any], role: str | None) -> dict[str, Any]:
        """Apply role-based redaction to the tool results returned to the caller.

        ``result_by_tool`` keys are tool names, with ``tool#2`` etc. for repeated
        tools; strip the ``#N`` suffix to look up the redaction policy. The
        original ``result_by_tool`` (used for audit) and step ``outputs`` (used
        for reference resolution) stay full — only the returned copy is redacted.
        """
        return {
            key: redact_security_tool_output(key.split("#")[0], value, role)
            for key, value in result_by_tool.items()
        }

    def _check_step(self, tool: str, arguments: dict[str, Any], raw_query: str, user_id: str, approved: bool, role: str | None) -> dict[str, Any]:
        security = self._guard.check(
            raw_query=raw_query,
            tools=[tool],
            arguments=arguments,
            user_id=user_id,
            registry=self._registry,
            approved=approved,
            role=role,
        ).to_dict()
        security["tool"] = tool
        return security

    def _blocked_result(
        self,
        plan: Plan,
        user_id: str,
        raw_query: str,
        step_securities: list[dict[str, Any]],
        step_records: list[dict[str, Any]],
        executed_commands: list[dict[str, Any]],
        result_by_tool: dict[str, Any],
        blocked_step: str,
        approved_required: bool,
        message: str,
        status: str,
        role: str | None = None,
    ) -> ExecutionResult:
        aggregate = self._aggregate_security(step_securities, blocked_step=blocked_step)
        self._audit.write(
            user_id,
            raw_query,
            plan,
            status,
            {"security": aggregate, "executed_commands": executed_commands, "steps": step_records},
        )
        return ExecutionResult(
            approved_required=approved_required,
            blocked=True,
            message=message,
            result=self._redact_results(result_by_tool, role),
            security=aggregate,
            executed_commands=executed_commands,
            steps=step_records,
        )

    def _audit_step_security(self, trace_id: str | None, user_id: str, step: PlanStep, security: dict[str, Any]) -> None:
        if not trace_id:
            return
        self._audit.event(
            trace_id=trace_id,
            stage="security_validation",
            user_id=user_id,
            status="blocked" if security.get("blocked") else "passed",
            data={"step_id": step.id, "tool": step.tool, "security": security},
        )

    @staticmethod
    def _step_record(
        step_id: str,
        tool: str,
        status: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        security: dict[str, Any],
        executed_commands: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "id": step_id,
            "tool": tool,
            "status": status,
            "arguments": arguments,
            "result": result,
            "security": security,
            "executed_commands": executed_commands,
        }

    @staticmethod
    def _duplicate_step_id(steps: list[PlanStep]) -> str | None:
        seen: set[str] = set()
        for step in steps:
            if step.id in seen:
                return step.id
            seen.add(step.id)
        return None

    def _reject_duplicate_step(self, plan: Plan, user_id: str, raw_query: str, duplicate: str, trace_id: str | None) -> ExecutionResult:
        message = f"编排步骤 id 重复：{duplicate}"
        decision = {
            "risk_level": "low",
            "blocked": True,
            "confirmation_required": False,
            "audit_required": True,
            "reasons": [message],
            "checks": [{"name": "step_id_unique", "passed": False, "message": f"duplicate step id: {duplicate}"}],
            "step_id": duplicate,
            "tool": "",
        }
        aggregate = self._aggregate_security([decision], blocked_step=duplicate)
        if trace_id:
            self._audit.event(
                trace_id=trace_id,
                stage="security_validation",
                user_id=user_id,
                status="blocked",
                data={"step_id": duplicate, "security": decision},
            )
        self._audit.write(user_id, raw_query, plan, "blocked", {"security": aggregate, "executed_commands": [], "steps": []})
        return ExecutionResult(
            approved_required=False,
            blocked=True,
            message=message,
            result={},
            security=aggregate,
            executed_commands=[],
            steps=[],
        )

    def _coerce_arguments(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Schema-directed coercion of numeric-string args to int.

        A reference resolves to whatever type the producing tool emitted, and
        some tools report numeric fields as strings (e.g. ``process`` yields a
        string pid). When the target tool's input schema declares a key as
        ``integer`` and the value is a clean integer literal, coerce it so a
        chain like ``process -> process.kill`` type-checks. Non-numeric strings
        are left untouched and will still be rejected by schema validation.
        """
        definition = self._registry.get(tool)
        if definition is None:
            return arguments
        properties = definition.input_schema.get("properties", {})
        coerced = dict(arguments)
        for key, rule in properties.items():
            if rule.get("type") != "integer":
                continue
            value = coerced.get(key)
            if isinstance(value, str) and _INT_LITERAL.fullmatch(value.strip()):
                coerced[key] = int(value.strip())
        return coerced

    @staticmethod
    def _store_result(result_by_tool: dict[str, Any], tool: str, value: Any) -> str:
        """Store a step result without overwriting an earlier same-tool result.

        The first use of a tool keeps the bare tool name as key (so single-tool
        callers can read ``result[tool]``); repeated uses get ``tool#2``,
        ``tool#3`` ... so no step's output is lost.
        """
        key = tool
        if key in result_by_tool:
            index = 2
            while f"{tool}#{index}" in result_by_tool:
                index += 1
            key = f"{tool}#{index}"
        result_by_tool[key] = value
        return key

    @staticmethod
    def _tool_failure(result: Any) -> str | None:
        """Return a failure message if a tool result reports failure, else None.

        Failure is signalled by a top-level ``error`` or by the tool's own
        ``analysis.succeeded``/``succeeded`` verdict being False. Nested command
        exit codes are intentionally not treated as hard failures here, since
        read-only tools can legitimately return non-zero (e.g. an inactive
        service status), and the tool already reflects that in ``analysis``.
        """
        if not isinstance(result, dict):
            return None
        if result.get("error"):
            return str(result["error"])
        analysis = result.get("analysis")
        if isinstance(analysis, dict) and analysis.get("succeeded") is False:
            return str(analysis.get("reason") or analysis.get("error") or "tool reported failure")
        if result.get("succeeded") is False:
            return str(result.get("message") or "tool reported failure")
        return None

    @staticmethod
    def _aggregate_security(step_securities: list[dict[str, Any]], blocked_step: str | None) -> dict[str, Any]:
        risk_level = "low"
        reasons: list[str] = []
        checks: list[dict[str, Any]] = []
        confirmation_required = False
        blocked = blocked_step is not None
        for security in step_securities:
            level = security.get("risk_level", "low")
            if RISK_ORDER.get(level, 1) > RISK_ORDER.get(risk_level, 1):
                risk_level = level
            confirmation_required = confirmation_required or bool(security.get("confirmation_required"))
            reasons.extend(security.get("reasons", []))
            for check in security.get("checks", []):
                checks.append({**check, "step_id": security.get("step_id", "")})
            if security.get("blocked"):
                blocked = True
        return {
            "risk_level": risk_level,
            "blocked": blocked,
            "confirmation_required": confirmation_required,
            "audit_required": True,
            "reasons": list(dict.fromkeys(reasons)),
            "checks": checks,
            "blocked_step": blocked_step,
            "steps": [
                {
                    "step_id": security.get("step_id", ""),
                    "tool": security.get("tool", ""),
                    "risk_level": security.get("risk_level", "low"),
                    "blocked": bool(security.get("blocked")),
                    "deferred": bool(security.get("deferred")),
                    "reasons": security.get("reasons", []),
                }
                for security in step_securities
            ],
        }

    @staticmethod
    def _deferred_decision(step: PlanStep) -> dict[str, Any]:
        message = "参数包含步骤引用，将在执行时解析后再校验"
        return {
            "risk_level": "low",
            "blocked": False,
            "confirmation_required": False,
            "audit_required": True,
            "reasons": [],
            "checks": [{"name": "reference_resolution", "passed": True, "message": message}],
            "step_id": step.id,
            "tool": step.tool,
            "deferred": True,
        }

    @staticmethod
    def _reference_block_decision(step: PlanStep, error: str) -> dict[str, Any]:
        return {
            "risk_level": "low",
            "blocked": True,
            "confirmation_required": False,
            "audit_required": True,
            "reasons": [error],
            "checks": [{"name": "reference_resolution", "passed": False, "message": error}],
            "step_id": step.id,
            "tool": step.tool,
        }

    # -- reference resolution ---------------------------------------------

    @classmethod
    def _has_unresolved_references(cls, value: Any) -> bool:
        if isinstance(value, str):
            return bool(_HAS_REFERENCE.search(value))
        if isinstance(value, dict):
            return any(cls._has_unresolved_references(item) for item in value.values())
        if isinstance(value, list):
            return any(cls._has_unresolved_references(item) for item in value)
        return False

    @classmethod
    def _resolve_references(cls, arguments: dict[str, Any], outputs: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        """Resolve ``${stepId.path}`` references against prior step outputs.

        Returns the resolved arguments and an error string. The first failed
        reference short-circuits with an error so the chain fails fast and no
        unresolved placeholder ever reaches the security guard.
        """
        errors: list[str] = []

        def resolve(value: Any) -> Any:
            if errors:
                return value
            if isinstance(value, str):
                match = REFERENCE_PATTERN.match(value.strip())
                if match is None:
                    return value
                resolved, error = cls._lookup_reference(match.group(1), outputs)
                if error is not None:
                    errors.append(error)
                    return value
                return resolved
            if isinstance(value, dict):
                return {key: resolve(item) for key, item in value.items()}
            if isinstance(value, list):
                return [resolve(item) for item in value]
            return value

        resolved_arguments = {key: resolve(item) for key, item in arguments.items()}
        return resolved_arguments, (errors[0] if errors else None)

    @classmethod
    def _lookup_reference(cls, expression: str, outputs: dict[str, Any]) -> tuple[Any, str | None]:
        tokens = cls._tokenize_path(expression)
        if not tokens:
            return None, f"无效的步骤引用: ${{{expression}}}"
        step_id = tokens[0]
        if step_id not in outputs:
            return None, f"引用了未知或尚未执行的步骤: {step_id}"
        current: Any = outputs[step_id]
        for token in tokens[1:]:
            if isinstance(token, int):
                if not isinstance(current, list) or token >= len(current):
                    return None, f"步骤引用下标越界: ${{{expression}}}"
                current = current[token]
            else:
                if not isinstance(current, dict) or token not in current:
                    return None, f"步骤引用路径不存在: ${{{expression}}}"
                current = current[token]
        return current, None

    @staticmethod
    def _tokenize_path(expression: str) -> list[Any]:
        tokens: list[Any] = []
        for part in expression.split("."):
            match = re.fullmatch(r"([^\[\]]*)((?:\[\d+\])*)", part.strip())
            if match is None:
                return []
            key = match.group(1)
            if key:
                tokens.append(key)
            for index in re.findall(r"\[(\d+)\]", match.group(2)):
                tokens.append(int(index))
        return tokens

    # -- executed-command extraction --------------------------------------

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
