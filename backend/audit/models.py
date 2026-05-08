from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class AuditRecord:
    timestamp: str
    user_id: str
    query: str
    intent: str
    tools: list[str]
    status: str
    result: dict[str, Any]

    @classmethod
    def create(
        cls,
        user_id: str,
        query: str,
        intent: str,
        tools: list[str],
        status: str,
        result: dict[str, Any],
    ) -> "AuditRecord":
        return cls(
            timestamp=datetime.now(timezone.utc).isoformat(),
            user_id=user_id,
            query=query,
            intent=intent,
            tools=tools,
            status=status,
            result=result,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AuditEvent:
    timestamp: str
    trace_id: str
    stage: str
    user_id: str
    status: str
    data: dict[str, Any]

    @classmethod
    def create(
        cls,
        trace_id: str,
        stage: str,
        user_id: str,
        status: str,
        data: dict[str, Any],
    ) -> "AuditEvent":
        return cls(
            timestamp=datetime.now(timezone.utc).isoformat(),
            trace_id=trace_id,
            stage=stage,
            user_id=user_id,
            status=status,
            data=data,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
