from __future__ import annotations

import sys
import threading
import time
from typing import Any, Callable
from uuid import uuid4

from backend.agent.planner import Plan
from backend.monitor.alerts import Alert, AlertStore
from backend.monitor.checks import run_all_checks


class MonitorScheduler:
    """Background daemon that periodically runs read-only checks via the executor.

    Tool calls go through ``ToolExecutor.execute`` (reusing guard + metrics),
    never ``registry.call`` directly. Only the fixed read-only check tools are
    ever invoked.
    """

    CHECK_TOOLS = ("disk", "service", "auth")

    def __init__(self, executor, alert_store: AlertStore, settings, audit, clock: Callable[[], float] | None = None) -> None:
        self._executor = executor
        self._alert_store = alert_store
        self._settings = settings
        self._audit = audit
        self._clock = clock or time.time
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_run_at: float | None = None
        self._last_alert_count = 0

    def _check_specs(self) -> list[tuple[str, dict[str, Any]]]:
        return [
            ("disk", {"path": "/"}),
            ("service", {}),
            ("auth", {"lines": self._settings.auth_lines}),
        ]

    def run_once(self) -> list[Alert]:
        trace_id = uuid4().hex
        outputs: dict[str, dict[str, Any]] = {}
        for tool, args in self._check_specs():
            try:
                execution = self._executor.execute(
                    plan=Plan(intent="inspection", tools=[tool], arguments=dict(args)),
                    user_id="monitor",
                    raw_query="monitor",
                    trace_id=trace_id,
                    role="admin",
                )
                outputs[tool] = execution.result.get(tool, {})
            except Exception as exc:  # noqa: BLE001 - one tool must not break the round
                outputs[tool] = {"error": str(exc)}
        alerts = run_all_checks(outputs, self._settings)
        for alert in alerts:
            self._alert_store.add(alert)
            self._audit.event(
                trace_id=trace_id,
                stage="monitor_alert",
                user_id="monitor",
                status=alert.severity,
                data={"source": alert.source, "metric": alert.metric, "value": alert.value,
                      "threshold": alert.threshold, "message": alert.message},
            )
        self._last_run_at = self._clock()
        self._last_alert_count = len(alerts)
        return alerts

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="monitor-scheduler", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception as exc:  # noqa: BLE001 - a bad tick must not kill the loop
                print(f"[monitor] run_once failed (best-effort): {exc}", file=sys.stderr)
            self._stop.wait(self._settings.interval_seconds)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def status(self) -> dict[str, Any]:
        return {
            "enabled": bool(self._settings.enabled),
            "running": self.running(),
            "interval_seconds": self._settings.interval_seconds,
            "last_run_at": self._last_run_at,
            "last_alert_count": self._last_alert_count,
            "checks": list(self.CHECK_TOOLS),
        }
