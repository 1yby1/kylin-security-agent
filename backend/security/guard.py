from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath
from typing import Any

from backend.mcp_tools import ToolRegistry
from backend.security.rules import (
    ADMIN_ROLES,
    CORE_SYSTEM_PATHS,
    DANGEROUS_COMMAND_PATTERNS,
    HIGH_RISK_TOOLS,
    LOW_RISK_TOOLS,
    MEDIUM_RISK_TOOLS,
    PROHIBITED_PATTERNS,
    PROTECTED_PID_MAX,
    PROTECTED_PROCESS_NAMES,
    PROTECTED_SERVICES,
    RISK_POLICIES,
    SAFE_STRING_PATTERN,
    SAFE_TEMP_DIRS,
    SERVICE_RESTART_ALLOWLIST,
)


RISK_ORDER = {"low": 1, "medium": 2, "high": 3, "prohibited": 4}


@dataclass(frozen=True)
class SecurityCheck:
    name: str
    passed: bool
    message: str


@dataclass(frozen=True)
class SecurityDecision:
    risk_level: str
    blocked: bool
    confirmation_required: bool
    audit_required: bool
    reasons: list[str] = field(default_factory=list)
    checks: list[SecurityCheck] = field(default_factory=list)

    @property
    def reason(self) -> str:
        return "; ".join(self.reasons)

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_level": self.risk_level,
            "blocked": self.blocked,
            "confirmation_required": self.confirmation_required,
            "audit_required": self.audit_required,
            "reasons": self.reasons,
            "checks": [asdict(check) for check in self.checks],
        }


class SecurityGuard:
    def check(
        self,
        *,
        raw_query: str,
        tools: list[str],
        arguments: dict[str, Any],
        user_id: str,
        registry: ToolRegistry,
        approved: bool,
        role: str | None = None,
    ) -> SecurityDecision:
        checks: list[SecurityCheck] = []
        reasons: list[str] = []

        whitelist_ok, whitelist_reason = self._check_tool_whitelist(tools, registry)
        checks.append(SecurityCheck("tool_whitelist", whitelist_ok, whitelist_reason))
        if not whitelist_ok:
            reasons.append(whitelist_reason)

        schema_ok, schema_reason = self._check_argument_schema(tools, arguments, registry)
        checks.append(SecurityCheck("parameter_schema", schema_ok, schema_reason))
        if not schema_ok:
            reasons.append(schema_reason)

        parameter_ok, parameter_reason = self._check_parameter_values(arguments)
        checks.append(SecurityCheck("parameter_values", parameter_ok, parameter_reason))
        if not parameter_ok:
            reasons.append(parameter_reason)

        process_ok, process_reason = self._check_process_target(raw_query, tools, arguments)
        checks.append(SecurityCheck("process_target", process_ok, process_reason))
        if not process_ok:
            reasons.append(process_reason)

        path_ok, path_reason = self._check_dangerous_paths(raw_query, arguments)
        checks.append(SecurityCheck("dangerous_path", path_ok, path_reason))
        if not path_ok:
            reasons.append(path_reason)

        command_ok, command_reason, prohibited = self._check_dangerous_commands(raw_query, arguments)
        checks.append(SecurityCheck("dangerous_command", command_ok, command_reason))
        if not command_ok:
            reasons.append(command_reason)

        risk_level = "prohibited" if prohibited else self._calculate_risk(raw_query, tools, arguments, registry)

        resolved_role = (
            role
            if role is not None
            else str(arguments.get("user_role") or self._role_from_user_id(user_id))
        ).lower()
        permission_ok, permission_reason = self._check_permission(
            role=resolved_role,
            risk_level=risk_level,
        )
        checks.append(SecurityCheck("user_permission", permission_ok, permission_reason))
        if not permission_ok:
            reasons.append(permission_reason)

        policy = RISK_POLICIES[risk_level]
        confirmation_required = policy.confirmation_required
        if confirmation_required and self._confirmation_waived_by_preview(tools, arguments):
            confirmation_required = False
        confirmation_ok = not confirmation_required or approved
        checks.append(
            SecurityCheck(
                "secondary_confirmation",
                confirmation_ok,
                "confirmation accepted" if confirmation_ok else "secondary confirmation required",
            )
        )
        if not confirmation_ok:
            reasons.append("secondary confirmation required")

        audit_required = True
        checks.append(SecurityCheck("audit_logging", audit_required, "audit logging required for every execution"))

        blocked = (
            bool(reasons)
            or policy.blocked_by_default
            or risk_level == "prohibited"
        )
        if policy.blocked_by_default and risk_level == "high":
            reasons.append("high risk operation is blocked by default policy")

        return SecurityDecision(
            risk_level=risk_level,
            blocked=blocked,
            confirmation_required=confirmation_required,
            audit_required=audit_required,
            reasons=list(dict.fromkeys(reasons)),
            checks=checks,
        )

    def _check_tool_whitelist(self, tools: list[str], registry: ToolRegistry) -> tuple[bool, str]:
        unknown = [tool for tool in tools if registry.get(tool) is None]
        if unknown:
            return False, f"tool is not registered or enabled: {', '.join(unknown)}"
        return True, "all tools are registered and enabled"

    def _check_argument_schema(
        self,
        tools: list[str],
        arguments: dict[str, Any],
        registry: ToolRegistry,
    ) -> tuple[bool, str]:
        for tool_name in tools:
            definition = registry.get(tool_name)
            if definition is None:
                continue
            ok, reason = self._validate_schema(arguments, definition.input_schema)
            if not ok:
                return False, f"{tool_name}: {reason}"
        return True, "parameters match declared tool schemas"

    def _validate_schema(self, arguments: dict[str, Any], schema: dict[str, Any]) -> tuple[bool, str]:
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in arguments or arguments.get(key) in {None, ""}:
                return False, f"{key} is required"
        for key, value in arguments.items():
            if key in {"query", "user_id", "user_role", "approved"}:
                continue
            rule = properties.get(key)
            if rule is None:
                continue
            expected = rule.get("type")
            if expected == "integer" and not isinstance(value, int):
                return False, f"{key} must be integer"
            if expected == "boolean" and not isinstance(value, bool):
                return False, f"{key} must be boolean"
            if expected == "string" and not isinstance(value, str):
                return False, f"{key} must be string"
            if "enum" in rule and value not in rule["enum"]:
                return False, f"{key} must be one of {rule['enum']}"
            if isinstance(value, int):
                if "minimum" in rule and value < rule["minimum"]:
                    return False, f"{key} is below minimum {rule['minimum']}"
                if "maximum" in rule and value > rule["maximum"]:
                    return False, f"{key} exceeds maximum {rule['maximum']}"
        return True, "schema ok"

    def _check_parameter_values(self, arguments: dict[str, Any]) -> tuple[bool, str]:
        for key, value in self._walk_values(arguments):
            if key in {"query", "user_id", "user_role", "approved"}:
                continue
            if isinstance(value, str) and not SAFE_STRING_PATTERN.fullmatch(value):
                return False, f"parameter contains unsafe characters: {key}"
        return True, "parameter values contain only safe characters"

    def _check_process_target(
        self,
        raw_query: str,
        tools: list[str],
        arguments: dict[str, Any],
    ) -> tuple[bool, str]:
        if "process.kill" not in tools:
            return True, "no process termination requested"

        pid = arguments.get("pid")
        if not isinstance(pid, int) or isinstance(pid, bool):
            return False, "process.kill requires integer pid"
        if pid <= PROTECTED_PID_MAX:
            return False, f"pid is in protected system range: {pid}"
        if pid in {os.getpid(), os.getppid()}:
            return False, "refuse to kill current agent process or its parent"

        expected_name = str(arguments.get("expected_name", "")).strip()
        protected_names = {name.lower() for name in PROTECTED_PROCESS_NAMES}
        if expected_name.lower() in protected_names:
            return False, f"refuse to kill protected process: {expected_name}"

        text = self._joined_text(raw_query, arguments).lower()
        for process_name in protected_names:
            if process_name and process_name in text:
                return False, f"request mentions protected process: {process_name}"
        return True, "process target passed static safety checks"

    def _check_dangerous_paths(self, raw_query: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        path_candidates = self._extract_paths(raw_query)
        for key, value in self._walk_values(arguments):
            if "path" in key.lower() or key.lower() in {"target", "directory"}:
                if isinstance(value, str):
                    path_candidates.append(value)

        text = self._joined_text(raw_query, arguments).lower()
        destructive_context = any(
            word in text
            for word in [
                "rm",
                "delete",
                "remove",
                "clean",
                "write",
                "modify",
                "chmod",
                "chown",
                "删除",
                "清理",
                "写入",
                "修改",
            ]
        )

        if any(word in text for word in ["clean", "清理"]):
            for path in path_candidates:
                if not self.is_safe_temp_path(path):
                    return False, f"clean operation is only allowed under safe temp directories: {path}"

        for path in path_candidates:
            normalized = self._normalize_posix_path(path)
            if not destructive_context:
                continue
            if self.is_safe_temp_path(normalized):
                continue
            if normalized in CORE_SYSTEM_PATHS:
                return False, f"destructive operation touches protected core path: {normalized}"
            if any(normalized.startswith(core + "/") for core in CORE_SYSTEM_PATHS if core != "/"):
                return False, f"destructive operation touches protected core path: {normalized}"
        return True, "no dangerous path detected"

    def _check_dangerous_commands(
        self,
        raw_query: str,
        arguments: dict[str, Any],
    ) -> tuple[bool, str, bool]:
        text = self._joined_text(raw_query, arguments)
        for pattern in PROHIBITED_PATTERNS:
            if re.search(pattern, text, flags=re.IGNORECASE):
                return False, f"request matched prohibited command pattern: {pattern}", True
        for pattern in DANGEROUS_COMMAND_PATTERNS:
            if re.search(pattern, text, flags=re.IGNORECASE):
                return False, f"request matched dangerous command pattern: {pattern}", False
        return True, "no dangerous command detected", False

    def _calculate_risk(
        self,
        raw_query: str,
        tools: list[str],
        arguments: dict[str, Any],
        registry: ToolRegistry,
    ) -> str:
        risk_level = "low"
        for tool_name in tools:
            if tool_name in MEDIUM_RISK_TOOLS:
                risk_level = self._max_risk(risk_level, "medium")
            elif tool_name in HIGH_RISK_TOOLS:
                risk_level = self._max_risk(risk_level, "high")
            elif tool_name not in LOW_RISK_TOOLS:
                definition = registry.get(tool_name)
                risk_level = self._max_risk(risk_level, definition.risk_level if definition else "high")

        text = self._joined_text(raw_query, arguments).lower()
        if any(word in text for word in ["重启", "restart", "清理", "clean", "kill", "杀死"]):
            risk_level = self._max_risk(risk_level, "medium")
        if any(word in text for word in ["修改配置", "chmod", "chown", "用户", "user", "stop", "停止安全服务"]):
            risk_level = self._max_risk(risk_level, "high")

        service_name = str(arguments.get("service_name", ""))
        if service_name in PROTECTED_SERVICES and any(word in text for word in ["stop", "停止", "disable", "关闭"]):
            risk_level = self._max_risk(risk_level, "high")
        if "restart" in text or "重启" in text:
            if service_name and service_name not in SERVICE_RESTART_ALLOWLIST:
                risk_level = self._max_risk(risk_level, "high")

        return risk_level

    def _confirmation_waived_by_preview(self, tools: list[str], arguments: dict[str, Any]) -> bool:
        """A dry-run ``temp.clean`` preview over a safe temp dir has no side
        effects, so it does not require secondary confirmation.

        Risk stays ``medium`` (the operator/admin role requirement still
        applies); only the confirmation gate is waived. The waiver requires
        ``dry_run`` to be exactly ``True`` and a safe path — and refuses if any
        other controlled operation is bundled in. The executor passes these very
        arguments (with ``dry_run=True``) to the tool, which independently honors
        ``dry_run``, so no deletion can occur. This makes the no-side-effect
        invariant explicit rather than relying on the tool alone.
        """
        if "temp.clean" not in tools:
            return False
        if any(tool != "temp.clean" and (tool in MEDIUM_RISK_TOOLS or tool in HIGH_RISK_TOOLS) for tool in tools):
            return False
        path = arguments.get("path")
        return arguments.get("dry_run") is True and isinstance(path, str) and self.is_safe_temp_path(path)

    def _check_permission(self, role: str, risk_level: str) -> tuple[bool, str]:
        role = (role or "viewer").lower()
        if risk_level == "prohibited":
            return False, "prohibited operations are never allowed"
        policy = RISK_POLICIES[risk_level]
        if not policy.allowed_roles:
            return True, f"risk level {risk_level} allows role {role}"
        if role in policy.allowed_roles:
            return True, f"role {role} is allowed for risk level {risk_level}"
        return False, f"role {role} is not allowed for risk level {risk_level}"

    @staticmethod
    def _role_from_user_id(user_id: str) -> str:
        if user_id in ADMIN_ROLES:
            return "admin"
        if user_id.startswith("operator"):
            return "operator"
        return "viewer"

    @staticmethod
    def _max_risk(left: str, right: str) -> str:
        return left if RISK_ORDER[left] >= RISK_ORDER[right] else right

    @staticmethod
    def _walk_values(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
        if isinstance(value, dict):
            pairs: list[tuple[str, Any]] = []
            for key, nested in value.items():
                nested_key = f"{prefix}.{key}" if prefix else str(key)
                pairs.extend(SecurityGuard._walk_values(nested, nested_key))
            return pairs
        if isinstance(value, list):
            pairs = []
            for index, nested in enumerate(value):
                pairs.extend(SecurityGuard._walk_values(nested, f"{prefix}[{index}]"))
            return pairs
        return [(prefix, value)]

    @staticmethod
    def _joined_text(raw_query: str, arguments: dict[str, Any]) -> str:
        values = [raw_query]
        values.extend(str(value) for _, value in SecurityGuard._walk_values(arguments))
        return " ".join(values)

    @staticmethod
    def _extract_paths(text: str) -> list[str]:
        return re.findall(r"(?<![\w.-])/(?:[\w.+-]+/?)+", text)

    @staticmethod
    def _normalize_posix_path(path: str) -> str:
        normalized = str(PurePosixPath(path.replace("\\", "/")))
        if normalized != "/" and normalized.endswith("/"):
            normalized = normalized.rstrip("/")
        return normalized

    @staticmethod
    def is_safe_temp_path(path: str) -> bool:
        normalized = SecurityGuard._normalize_posix_path(path)
        parts = PurePosixPath(normalized).parts
        if not normalized.startswith("/") or ".." in parts:
            return False
        return any(normalized == safe or normalized.startswith(safe + "/") for safe in SAFE_TEMP_DIRS)
