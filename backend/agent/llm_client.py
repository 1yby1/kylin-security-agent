from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from backend.agent.prompt import ANALYSIS_SYSTEM_PROMPT, PLANNING_SYSTEM_PROMPT
from backend.config import LLMSettings, get_llm_settings


@dataclass(frozen=True)
class LLMDecision:
    intent: str
    tools: list[str]
    arguments: dict[str, Any]
    arguments_by_tool: dict[str, dict[str, Any]] = field(default_factory=dict)
    summary: str = ""
    risk_hint: str = "low"
    need_confirmation: bool = False
    reasoning: list[str] | None = None


@dataclass(frozen=True)
class LLMConclusion:
    conclusion: str
    status: str
    root_cause: str
    evidence: list[str]
    recommendations: list[str]
    needs_more_info: bool
    follow_up_questions: list[str]
    source: str = "llm"


class LLMClient:
    def __init__(self, settings: LLMSettings | None = None) -> None:
        self._settings = settings or get_llm_settings()
        self._last_error = ""

    @property
    def enabled(self) -> bool:
        return self._settings.enabled

    @property
    def last_error(self) -> str:
        return self._last_error

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "provider": self._settings.provider,
            "model": self._settings.model,
            "base_url": self._settings.base_url,
            "api_key_configured": bool(self._settings.api_key),
            "api_key_preview": self._mask_key(self._settings.api_key),
            "timeout_seconds": self._settings.timeout_seconds,
            "last_error": self._last_error,
        }

    def analyze(self, query: str, context: dict[str, Any], tool_manifest: dict[str, Any] | None = None) -> LLMDecision | None:
        if not self.enabled:
            self._last_error = "LLM disabled: set LLM_PROVIDER and matching API key environment variable"
            return None

        content = self._chat_json(
            system_prompt=PLANNING_SYSTEM_PROMPT,
            user_payload={"query": query, "context": context, "tool_manifest": tool_manifest or {}},
        )
        data = self._parse_json(content or "")
        if not data:
            self._last_error = "LLM returned empty or non-JSON planning content"
            return None

        allowed_tools = self._allowed_tool_names(tool_manifest or {})
        tools = [tool for tool in data.get("tools", []) if isinstance(tool, str) and tool in allowed_tools]
        if not tools:
            self._last_error = "LLM planning JSON did not contain valid registered tools"
            return None
        self._last_error = ""
        schemas = self._tool_schemas(tool_manifest or {})
        return LLMDecision(
            intent=data.get("intent", "inspection"),
            tools=tools,
            arguments=data.get("arguments", {}) or {},
            arguments_by_tool=self._coerce_arguments_by_tool(data.get("arguments_by_tool"), tools, schemas),
            summary=data.get("summary", ""),
            risk_hint=data.get("risk_hint", "low"),
            need_confirmation=bool(data.get("need_confirmation", False)),
            reasoning=data.get("reasoning", []),
        )

    def conclude(
        self,
        *,
        query: str,
        plan: dict[str, Any],
        security: dict[str, Any],
        tool_result: dict[str, Any],
    ) -> LLMConclusion | None:
        if not self.enabled:
            self._last_error = "LLM disabled: set LLM_PROVIDER and matching API key environment variable"
            return None

        content = self._chat_json(
            system_prompt=ANALYSIS_SYSTEM_PROMPT,
            user_payload={
                "query": query,
                "plan": plan,
                "security": security,
                "tool_result": tool_result,
            },
        )
        data = self._parse_json(content or "")
        if not data:
            self._last_error = "LLM returned empty or non-JSON analysis content"
            return None
        self._last_error = ""
        return LLMConclusion(
            conclusion=str(data.get("conclusion", "")),
            status=str(data.get("status", "unknown")),
            root_cause=str(data.get("root_cause", "无法确认")),
            evidence=[str(item) for item in data.get("evidence", [])],
            recommendations=[str(item) for item in data.get("recommendations", [])],
            needs_more_info=bool(data.get("needs_more_info", False)),
            follow_up_questions=[str(item) for item in data.get("follow_up_questions", [])],
        )

    def _chat_json(self, system_prompt: str, user_payload: dict[str, Any]) -> str | None:
        payload = {
            "model": self._settings.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        body = self._post_chat(payload)
        if body is None and "response_format" in payload:
            payload.pop("response_format", None)
            body = self._post_chat(payload)
        if body is None:
            return None
        return body.get("choices", [{}])[0].get("message", {}).get("content", "")

    def _post_chat(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        request = urllib.request.Request(
            self._settings.base_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._settings.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._settings.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="ignore")[:500]
            self._last_error = f"HTTP {exc.code}: {error_body}"
            return None
        except urllib.error.URLError as exc:
            self._last_error = f"URL error: {exc.reason}"
            return None
        except TimeoutError:
            self._last_error = "request timed out"
            return None
        except json.JSONDecodeError as exc:
            self._last_error = f"response was not valid JSON: {exc}"
            return None

    @staticmethod
    def _parse_json(content: str) -> dict[str, Any] | None:
        stripped = content.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
            stripped = re.sub(r"```$", "", stripped).strip()
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _allowed_tool_names(tool_manifest: dict[str, Any]) -> set[str]:
        tools = tool_manifest.get("tools", [])
        if not isinstance(tools, list):
            return set()
        return {
            str(tool.get("name"))
            for tool in tools
            if isinstance(tool, dict) and tool.get("name")
        }

    @staticmethod
    def _tool_schemas(tool_manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
        tools = tool_manifest.get("tools", [])
        if not isinstance(tools, list):
            return {}
        schemas: dict[str, dict[str, Any]] = {}
        for tool in tools:
            if isinstance(tool, dict) and tool.get("name"):
                schema = tool.get("input_schema")
                schemas[str(tool.get("name"))] = schema if isinstance(schema, dict) else {}
        return schemas

    @staticmethod
    def _coerce_arguments_by_tool(
        value: Any,
        tools: list[str],
        schemas: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, dict[str, Any]]:
        if not isinstance(value, dict):
            return {}
        allowed = set(tools)
        schemas = schemas or {}
        coerced: dict[str, dict[str, Any]] = {}
        for tool_name, tool_args in value.items():
            if tool_name not in allowed or not isinstance(tool_args, dict):
                continue
            properties = schemas.get(tool_name, {}).get("properties", {})
            # Hard rule: drop hallucinated placeholder values (None, empty strings,
            # or values that violate the tool's own schema such as pid=0). The model
            # is told not to emit these, but we never trust planning output.
            clean = {
                key: item
                for key, item in tool_args.items()
                if LLMClient._value_is_concrete(item, properties.get(key))
            }
            if clean:
                coerced[tool_name] = clean
        return coerced

    @staticmethod
    def _value_is_concrete(value: Any, rule: dict[str, Any] | None) -> bool:
        if value is None:
            return False
        if isinstance(value, str) and not value.strip():
            return False
        if not isinstance(rule, dict):
            return True
        expected = rule.get("type")
        if expected == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
            return False
        if expected == "boolean" and not isinstance(value, bool):
            return False
        if expected == "string" and not isinstance(value, str):
            return False
        if "enum" in rule and value not in rule["enum"]:
            return False
        if isinstance(value, int) and not isinstance(value, bool):
            if "minimum" in rule and value < rule["minimum"]:
                return False
            if "maximum" in rule and value > rule["maximum"]:
                return False
        return True

    @staticmethod
    def _mask_key(api_key: str) -> str:
        if not api_key:
            return ""
        if len(api_key) <= 8:
            return "***"
        return f"{api_key[:4]}...{api_key[-4:]}"
