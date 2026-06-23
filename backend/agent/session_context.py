from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Callable
from uuid import uuid4

from backend.agent.planner import Plan
from backend.security.sanitizer import sanitize_output


SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


@dataclass
class ConversationState:
    session_id: str
    owner: str = "anon"
    summary: str = ""
    last_entities: dict[str, Any] = field(default_factory=dict)
    last_tools: list[str] = field(default_factory=list)
    last_arguments: dict[str, Any] = field(default_factory=dict)
    updated_at: float = 0.0


class ConversationSessionStore:
    def __init__(
        self,
        ttl_seconds: int = 1800,
        clock: Callable[[], float] | None = None,
        max_sessions: int = 1000,
    ) -> None:
        self._ttl_seconds = max(1, int(ttl_seconds))
        self._clock = clock or time.time
        self._max_sessions = max(1, int(max_sessions))
        self._sessions: dict[str, ConversationState] = {}
        self._lock = RLock()

    def resolve_session_id(self, session_id: str | None, owner: str = "anon") -> str:
        """Resolve to a session id the caller is allowed to continue.

        A presented id is honored only if it already exists AND was issued to
        the same ``owner`` (principal); otherwise a fresh server-issued random
        id is minted. This blocks callers from choosing/guessing ids or adopting
        another principal's session — they must reuse the id the server returned.
        """
        candidate = str(session_id or "").strip()
        if candidate and SESSION_ID_PATTERN.fullmatch(candidate):
            with self._lock:
                self._purge_expired()
                state = self._sessions.get(candidate)
                if state is not None and state.owner == owner:
                    return candidate
        return uuid4().hex

    def context(self, session_id: str | None, owner: str = "anon") -> dict[str, Any]:
        if not session_id:
            return {}
        with self._lock:
            self._purge_expired()
            state = self._sessions.get(session_id)
            if state is None or state.owner != owner:
                return {}
            return {
                "session_id": state.session_id,
                "summary": state.summary,
                "last_entities": dict(state.last_entities),
                "last_tools": list(state.last_tools),
                "last_arguments": dict(state.last_arguments),
            }

    def update(
        self,
        session_id: str,
        *,
        owner: str = "anon",
        query: str,
        plan: Plan,
        result: dict[str, Any],
        conclusion: dict[str, Any],
    ) -> str:
        entities = self._extract_entities(plan, result)
        summary = self._build_summary(query=query, plan=plan, entities=entities, conclusion=conclusion)
        state = ConversationState(
            session_id=session_id,
            owner=owner,
            summary=summary,
            last_entities=entities,
            last_tools=list(plan.tools),
            last_arguments=self._safe_arguments(plan.arguments),
            updated_at=self._clock(),
        )
        with self._lock:
            self._purge_expired()
            self._sessions[session_id] = state
            self._evict_over_capacity()
        return summary

    def _purge_expired(self) -> None:
        now = self._clock()
        expired = [
            session_id
            for session_id, state in self._sessions.items()
            if now - state.updated_at > self._ttl_seconds
        ]
        for session_id in expired:
            self._sessions.pop(session_id, None)

    def _evict_over_capacity(self) -> None:
        """Bound memory under a session flood by evicting the least-recently
        updated sessions once the global cap is exceeded."""
        while len(self._sessions) > self._max_sessions:
            oldest = min(self._sessions.values(), key=lambda state: state.updated_at)
            self._sessions.pop(oldest.session_id, None)

    @classmethod
    def _extract_entities(cls, plan: Plan, result: dict[str, Any]) -> dict[str, Any]:
        entities: dict[str, Any] = {}
        for key in ["pid", "service_name", "unit", "path", "target", "repo_dir", "log_path"]:
            value = plan.arguments.get(key)
            if value not in {None, ""}:
                entities["service_name" if key == "unit" else key] = value

        for tool_name, payload in result.items():
            if not isinstance(payload, dict):
                continue
            cls._extract_common_payload_entities(payload, entities)
            if tool_name.startswith("process"):
                cls._extract_process_entities(payload, entities)
            if tool_name.startswith("service"):
                cls._extract_service_entities(payload, entities)
        return cls._sanitize_entities(entities)

    @staticmethod
    def _extract_common_payload_entities(payload: dict[str, Any], entities: dict[str, Any]) -> None:
        for key in ["path", "target", "repo_dir"]:
            value = payload.get(key)
            if value not in {None, ""}:
                entities[key] = value

    @staticmethod
    def _extract_process_entities(payload: dict[str, Any], entities: dict[str, Any]) -> None:
        analysis = payload.get("analysis")
        if not isinstance(analysis, dict):
            return
        target = analysis.get("target")
        if isinstance(target, dict):
            if target.get("pid"):
                entities["pid"] = target["pid"]
            if target.get("command"):
                entities["process_name"] = target["command"]
            return
        top_cpu = analysis.get("top_cpu")
        if isinstance(top_cpu, list) and top_cpu and isinstance(top_cpu[0], dict):
            first = top_cpu[0]
            if first.get("pid"):
                entities["pid"] = first["pid"]
            if first.get("command"):
                entities["process_name"] = first["command"]

    @staticmethod
    def _extract_service_entities(payload: dict[str, Any], entities: dict[str, Any]) -> None:
        service_name = payload.get("service_name") or payload.get("unit")
        if service_name:
            entities["service_name"] = service_name

    @staticmethod
    def _sanitize_entities(entities: dict[str, Any]) -> dict[str, Any]:
        clean: dict[str, Any] = {}
        for key, value in entities.items():
            if key == "pid":
                text = str(value).strip()
                if text.isdigit():
                    clean[key] = text
                continue
            clean[key] = sanitize_output(str(value), max_len=120)
        return clean

    @staticmethod
    def _safe_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
        hidden = {"query", "user_id", "user_role", "approved", "conversation"}
        safe: dict[str, Any] = {}
        for key, value in arguments.items():
            if key in hidden:
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                safe[key] = value
        return safe

    @staticmethod
    def _build_summary(
        *,
        query: str,
        plan: Plan,
        entities: dict[str, Any],
        conclusion: dict[str, Any],
    ) -> str:
        entity_text = ", ".join(f"{key}={value}" for key, value in entities.items()) or "无关键实体"
        conclusion_text = str(conclusion.get("conclusion") or conclusion.get("root_cause") or "")
        summary = f"上一轮请求：{query}；工具：{', '.join(plan.tools)}；关键实体：{entity_text}"
        if conclusion_text:
            summary = f"{summary}；结论：{conclusion_text}"
        return sanitize_output(summary, max_len=500)
