import unittest
from typing import Any

from backend.agent.executor import ExecutionResult
from backend.config import MonitorSettings
from backend.monitor.alerts import AlertStore
from backend.monitor.scheduler import MonitorScheduler


class FakeExecutor:
    def __init__(self, results: dict[str, dict[str, Any]], raise_on: set[str] | None = None):
        self._results = results
        self._raise_on = raise_on or set()
        self.plans: list[tuple[list[str], dict[str, Any], str | None]] = []

    def execute(self, plan, user_id, raw_query, approved=False, trace_id=None, role=None) -> ExecutionResult:
        self.plans.append((list(plan.tools), dict(plan.arguments), role))
        tool = plan.tools[0]
        if tool in self._raise_on:
            raise RuntimeError("boom")
        return ExecutionResult(
            approved_required=False, blocked=False, message="ok",
            result={tool: self._results.get(tool, {})}, security={"blocked": False}, executed_commands=[],
        )


class _NullAudit:
    def event(self, **kwargs):
        pass


def _settings(**kw):
    base = dict(enabled=True, interval_seconds=300, disk_percent=90, failed_login=20, auth_lines=100)
    base.update(kw)
    return MonitorSettings(**base)


class MonitorSchedulerRunOnceTest(unittest.TestCase):
    def test_run_once_produces_alerts_and_uses_readonly_plans(self):
        executor = FakeExecutor({
            "disk": {"used_percent": 99.0},
            "service": {"analysis": {"failed_count": 2}},
            "auth": {"analysis": {"failed_login_count": 50}},
        })
        store = AlertStore(clock=lambda: 0.0)
        scheduler = MonitorScheduler(executor, store, _settings(), _NullAudit(), clock=lambda: 0.0)
        alerts = scheduler.run_once()
        self.assertEqual({a.source for a in alerts}, {"disk", "service", "auth"})
        self.assertEqual(len(store.recent()), 3)
        # only fixed read-only tools, with correct args, role admin
        called_tools = [tools[0] for tools, _, _ in executor.plans]
        self.assertEqual(set(called_tools), {"disk", "service", "auth"})
        for tools, args, role in executor.plans:
            self.assertIn(tools[0], {"disk", "service", "auth"})
            self.assertEqual(role, "admin")
            if tools[0] == "auth":
                self.assertEqual(args.get("lines"), 100)
            if tools[0] == "disk":
                self.assertEqual(args.get("path"), "/")

    def test_run_once_isolates_tool_exception(self):
        executor = FakeExecutor(
            {"service": {"analysis": {"failed_count": 1}}, "auth": {"analysis": {"failed_login_count": 0}}},
            raise_on={"disk"},
        )
        store = AlertStore(clock=lambda: 0.0)
        scheduler = MonitorScheduler(executor, store, _settings(), _NullAudit(), clock=lambda: 0.0)
        alerts = scheduler.run_once()  # must not raise
        self.assertEqual({a.source for a in alerts}, {"service"})

    def test_status_shape(self):
        scheduler = MonitorScheduler(FakeExecutor({}), AlertStore(), _settings(), _NullAudit())
        status = scheduler.status()
        for key in ("enabled", "running", "interval_seconds", "last_run_at", "last_alert_count", "checks"):
            self.assertIn(key, status)
        self.assertEqual(status["checks"], ["disk", "service", "auth"])
        self.assertFalse(status["running"])


class MonitorSchedulerLifecycleTest(unittest.TestCase):
    def test_start_runs_at_least_once_then_stop(self):
        executor = FakeExecutor({"disk": {"used_percent": 99.0}})
        store = AlertStore(clock=lambda: 0.0)
        scheduler = MonitorScheduler(executor, store, _settings(interval_seconds=10), _NullAudit(), clock=lambda: 0.0)
        scheduler.start()
        # give the daemon thread a brief moment to run the first tick
        import time
        for _ in range(50):
            if store.recent():
                break
            time.sleep(0.02)
        scheduler.stop()
        self.assertFalse(scheduler.running())
        self.assertGreaterEqual(len(store.recent()), 1)


if __name__ == "__main__":
    unittest.main()
