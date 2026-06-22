from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from backend.agent.planner import Plan
from backend.audit.models import AuditEvent, AuditRecord
from backend.audit.store import get_audit_store
from backend.config import get_audit_settings


class AuditLogger:
    def __init__(self, path: Path | None = None) -> None:
        resolved = path or get_audit_settings().db_path
        self._store = get_audit_store(resolved)

    def _persist(self, event: dict[str, Any]) -> None:
        try:
            self._store.append(event)
        except Exception as exc:  # noqa: BLE001
            if get_audit_settings().fail_closed:
                raise
            print(f"[audit] write failed (best-effort): {exc}", file=sys.stderr)

    def event(self, *, trace_id: str, stage: str, user_id: str, status: str, data: dict[str, Any]) -> None:
        record = AuditEvent.create(
            trace_id=trace_id, stage=stage, user_id=user_id, status=status, data=data
        )
        self._persist(record.to_dict())

    def write(self, user_id: str, query: str, plan: Plan, status: str, result: dict[str, Any]) -> None:
        record = AuditRecord.create(
            user_id=user_id, query=query, intent=plan.intent, tools=plan.tools, status=status, result=result
        )
        self._persist(
            {
                "timestamp": record.timestamp,
                "trace_id": "",
                "stage": "summary",
                "user_id": user_id,
                "status": status,
                "data": {
                    "query": query,
                    "intent": plan.intent,
                    "tools": plan.tools,
                    "result": result,
                },
            }
        )

    def read_recent(self, limit: int = 100, trace_id: str | None = None) -> list[dict[str, Any]]:
        return self._store.read_recent(limit=limit, trace_id=trace_id)

    def verify_chain(self) -> dict[str, Any]:
        return self._store.verify_chain()

    def export(self, limit: int = 1000, trace_id: str | None = None) -> list[dict[str, Any]]:
        return self._store.query(limit=limit, trace_id=trace_id)
