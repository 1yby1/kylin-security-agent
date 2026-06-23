from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace
from typing import Any, Callable


@dataclass(frozen=True)
class Alert:
    severity: str
    source: str
    metric: str
    value: Any
    threshold: Any
    message: str
    timestamp: float = 0.0


class AlertStore:
    """Thread-safe in-process alert buffer, bounded by count and TTL."""

    def __init__(
        self,
        max_alerts: int = 500,
        ttl_seconds: float = 86400.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._max_alerts = max(1, int(max_alerts))
        self._ttl = float(ttl_seconds)
        self._clock = clock or time.time
        self._alerts: list[Alert] = []
        self._lock = threading.RLock()

    def add(self, alert: Alert) -> Alert:
        now = self._clock()
        stamped = replace(alert, timestamp=now)
        with self._lock:
            self._alerts.append(stamped)
            self._prune(now)
        return stamped

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            self._prune(self._clock())
            chosen = self._alerts[-max(1, int(limit)):]
        return [self._to_dict(alert) for alert in reversed(chosen)]

    def reset(self) -> None:
        with self._lock:
            self._alerts.clear()

    def _prune(self, now: float) -> None:
        cutoff = now - self._ttl
        self._alerts = [a for a in self._alerts if a.timestamp > cutoff]
        if len(self._alerts) > self._max_alerts:
            self._alerts = self._alerts[-self._max_alerts:]

    @staticmethod
    def _to_dict(alert: Alert) -> dict[str, Any]:
        return {
            "severity": alert.severity,
            "source": alert.source,
            "metric": alert.metric,
            "value": alert.value,
            "threshold": alert.threshold,
            "message": alert.message,
            "timestamp": alert.timestamp,
        }
