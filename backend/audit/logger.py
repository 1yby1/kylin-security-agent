from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from backend.agent.planner import Plan
from backend.audit.models import AuditEvent, AuditRecord


class AuditLogger:
    def __init__(self, path: Path | None = None) -> None:
        configured_path = os.getenv("AGENT_AUDIT_LOG_PATH")
        self._path = path or (Path(configured_path) if configured_path else Path(__file__).resolve().parent / "logs" / "audit.log")
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, user_id: str, query: str, plan: Plan, status: str, result: dict[str, Any]) -> None:
        record = AuditRecord.create(
            user_id=user_id,
            query=query,
            intent=plan.intent,
            tools=plan.tools,
            status=status,
            result=result,
        )
        with self._path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

    def event(
        self,
        *,
        trace_id: str,
        stage: str,
        user_id: str,
        status: str,
        data: dict[str, Any],
    ) -> None:
        record = AuditEvent.create(
            trace_id=trace_id,
            stage=stage,
            user_id=user_id,
            status=status,
            data=data,
        )
        self._append(record.to_dict())

    def read_recent(self, limit: int = 100, trace_id: str | None = None) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        lines = self._path.read_text(encoding="utf-8", errors="ignore").splitlines()
        records: list[dict[str, Any]] = []
        for line in reversed(lines):
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if trace_id and item.get("trace_id") != trace_id:
                continue
            records.append(item)
            if len(records) >= limit:
                break
        return list(reversed(records))

    def _append(self, record: dict[str, Any]) -> None:
        with self._path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
