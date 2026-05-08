from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from backend.agent.prompt import ANALYSIS_SYSTEM_PROMPT, PLANNING_SYSTEM_PROMPT
from backend.config import LLMSettings, get_llm_settings


@dataclass(frozen=True)
class LLMDecision:
    intent: str
    tools: list[str]
    arguments: dict[str, Any]
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

        tools = [
            tool
            for tool in data.get("tools", [])
            if tool in {"system", "process", "process.kill", "network", "log", "service", "service.restart", "temp.clean", "disk"}
        ]
        if not tools:
            self._last_error = "LLM planning JSON did not contain valid registered tools"
            return None
        self._last_error = ""
        return LLMDecision(
            intent=data.get("intent", "inspection"),
            tools=tools,
            arguments=data.get("arguments", {}),
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
    def _mask_key(api_key: str) -> str:
        if not api_key:
            return ""
        if len(api_key) <= 8:
            return "***"
        return f"{api_key[:4]}...{api_key[-4:]}"
