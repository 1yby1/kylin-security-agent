# 主动巡检与阈值告警 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 进程内后台巡检循环，周期性经 executor 运行只读工具、按阈值判定主机健康，命中即产出告警并写审计。

**Architecture:** `backend/monitor/` 新包：`alerts.py`（Alert + 线程安全内存 AlertStore）、`checks.py`（纯阈值规则）、`scheduler.py`（守护线程循环 + run_once，经 `ToolExecutor.execute` 跑固定只读 Plan）。`main.py` lifespan 按 `AGENT_MONITOR_ENABLED` 启停，并暴露 `GET /api/alerts`（operator/admin）与 `GET /api/monitor/status`（开放）。

**Tech Stack:** Python 标准库（threading/collections/dataclasses）、FastAPI、`unittest`。

## Global Constraints

- 巡检**只运行只读工具**（`disk`/`service`/`auth`，均 ∈ `LOW_RISK_TOOLS`），绝不触发操作类工具或自动修复。
- 巡检经 `ToolExecutor.execute(Plan(...), user_id="monitor", role="admin")` 执行，复用安全 guard + metrics，不直调 `registry.call`。
- 告警判定为纯规则阈值，不调用 LLM、无 token 成本。
- 后台线程 tick 出错只记录不杀循环；不重叠；lifespan 退出优雅停。
- 告警/状态进程内内存态，重启清零；不持久化。
- `AGENT_MONITOR_ENABLED` 默认 false；`get_monitor_settings()` 强制 `auth_lines >= failed_login + 1`（≤200）。
- `/api/alerts` 仅 operator/admin（否则 403）；`/api/monitor/status` 开放（仅良性元数据）。
- 工具实际输出字段：`disk` → `used_percent`（顶层）；`service` → `analysis.failed_count`；`auth` → `analysis.failed_login_count`。
- 文档中文；标识符/env/JSON 字段原文。测试入口 `python -m unittest discover -v`。
- 提交信息结尾附：`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。

---

### Task 1: 告警模型与存储 `alerts.py`

**Files:**
- Create: `backend/monitor/__init__.py`
- Create: `backend/monitor/alerts.py`
- Test: `tests/test_monitor_alerts.py`

**Interfaces:**
- Produces:
  - `Alert(severity, source, metric, value, threshold, message, timestamp=0.0)`（frozen dataclass）。
  - `AlertStore(max_alerts=500, ttl_seconds=86400, clock=None)`，方法 `add(alert) -> Alert`（盖时间戳、入库、按上限/TTL 清理）、`recent(limit=100) -> list[dict]`（新→旧）、`reset()`。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_monitor_alerts.py
import unittest

from backend.monitor.alerts import Alert, AlertStore


def _alert(source="disk", message="m"):
    return Alert(severity="warning", source=source, metric="x", value=1, threshold=0, message=message)


class AlertStoreTest(unittest.TestCase):
    def test_add_stamps_timestamp_and_recent_is_newest_first(self):
        clock = [100.0]
        store = AlertStore(clock=lambda: clock[0])
        store.add(_alert(message="first"))
        clock[0] = 200.0
        store.add(_alert(message="second"))
        recent = store.recent()
        self.assertEqual([a["message"] for a in recent], ["second", "first"])
        self.assertEqual(recent[0]["timestamp"], 200.0)

    def test_max_alerts_evicts_oldest(self):
        store = AlertStore(max_alerts=2, clock=lambda: 0.0)
        for i in range(5):
            store.add(_alert(message=f"a{i}"))
        messages = [a["message"] for a in store.recent()]
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages, ["a4", "a3"])

    def test_ttl_prunes_expired(self):
        clock = [0.0]
        store = AlertStore(ttl_seconds=10, clock=lambda: clock[0])
        store.add(_alert(message="old"))
        clock[0] = 11.0
        store.add(_alert(message="new"))
        messages = [a["message"] for a in store.recent()]
        self.assertEqual(messages, ["new"])

    def test_reset(self):
        store = AlertStore(clock=lambda: 0.0)
        store.add(_alert())
        store.reset()
        self.assertEqual(store.recent(), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_monitor_alerts -v`
Expected: FAIL — `ModuleNotFoundError: backend.monitor.alerts`.

- [ ] **Step 3a: Create `backend/monitor/__init__.py`** (empty file)

```python
```

- [ ] **Step 3b: Create `backend/monitor/alerts.py`**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_monitor_alerts -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/monitor/__init__.py backend/monitor/alerts.py tests/test_monitor_alerts.py
git commit -m "feat(monitor): 新增告警模型与内存告警存储

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: 阈值规则 `checks.py`

**Files:**
- Create: `backend/monitor/checks.py`
- Test: `tests/test_monitor_checks.py`

**Interfaces:**
- Consumes: `backend.monitor.alerts.Alert`（Task 1）。
- Produces:
  - `check_disk(disk_output: dict, threshold_percent) -> list[Alert]`
  - `check_service(service_output: dict) -> list[Alert]`
  - `check_auth(auth_output: dict, threshold) -> list[Alert]`
  - `run_all_checks(outputs: dict[str, dict], settings) -> list[Alert]`（`settings` 有 `.disk_percent` / `.failed_login` 属性）。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_monitor_checks.py
import unittest
from types import SimpleNamespace

from backend.monitor.checks import check_auth, check_disk, check_service, run_all_checks


class ChecksTest(unittest.TestCase):
    def test_disk_over_threshold_alerts(self):
        alerts = check_disk({"used_percent": 95.0}, 90)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].severity, "critical")
        self.assertEqual(alerts[0].source, "disk")

    def test_disk_under_threshold_silent(self):
        self.assertEqual(check_disk({"used_percent": 80.0}, 90), [])

    def test_disk_missing_field_silent(self):
        self.assertEqual(check_disk({}, 90), [])
        self.assertEqual(check_disk({"error": "x"}, 90), [])

    def test_service_failed_alerts(self):
        alerts = check_service({"analysis": {"failed_count": 3}})
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].value, 3)

    def test_service_no_failed_silent(self):
        self.assertEqual(check_service({"analysis": {"failed_count": 0}}), [])
        self.assertEqual(check_service({}), [])

    def test_auth_over_threshold_alerts(self):
        alerts = check_auth({"analysis": {"failed_login_count": 25}}, 20)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].source, "auth")

    def test_auth_at_or_under_threshold_silent(self):
        self.assertEqual(check_auth({"analysis": {"failed_login_count": 20}}, 20), [])
        self.assertEqual(check_auth({}, 20), [])

    def test_run_all_checks_aggregates(self):
        settings = SimpleNamespace(disk_percent=90, failed_login=20)
        outputs = {
            "disk": {"used_percent": 99.0},
            "service": {"analysis": {"failed_count": 1}},
            "auth": {"analysis": {"failed_login_count": 50}},
        }
        sources = {a.source for a in run_all_checks(outputs, settings)}
        self.assertEqual(sources, {"disk", "service", "auth"})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_monitor_checks -v`
Expected: FAIL — `ModuleNotFoundError: backend.monitor.checks`.

- [ ] **Step 3: Create `backend/monitor/checks.py`**

```python
from __future__ import annotations

from typing import Any

from backend.monitor.alerts import Alert


def check_disk(disk_output: dict[str, Any], threshold_percent: float) -> list[Alert]:
    if not isinstance(disk_output, dict):
        return []
    used = disk_output.get("used_percent")
    if isinstance(used, (int, float)) and not isinstance(used, bool) and used > threshold_percent:
        return [
            Alert(
                severity="critical",
                source="disk",
                metric="used_percent",
                value=used,
                threshold=threshold_percent,
                message=f"磁盘使用率 {used}% 超过阈值 {threshold_percent}%",
            )
        ]
    return []


def check_service(service_output: dict[str, Any]) -> list[Alert]:
    analysis = service_output.get("analysis") if isinstance(service_output, dict) else None
    failed = analysis.get("failed_count", 0) if isinstance(analysis, dict) else 0
    if isinstance(failed, int) and not isinstance(failed, bool) and failed > 0:
        return [
            Alert(
                severity="warning",
                source="service",
                metric="failed_count",
                value=failed,
                threshold=0,
                message=f"有 {failed} 个服务处于 failed 状态",
            )
        ]
    return []


def check_auth(auth_output: dict[str, Any], threshold: int) -> list[Alert]:
    analysis = auth_output.get("analysis") if isinstance(auth_output, dict) else None
    failed = analysis.get("failed_login_count", 0) if isinstance(analysis, dict) else 0
    if isinstance(failed, int) and not isinstance(failed, bool) and failed > threshold:
        return [
            Alert(
                severity="warning",
                source="auth",
                metric="failed_login_count",
                value=failed,
                threshold=threshold,
                message=f"失败登录 {failed} 次超过阈值 {threshold} 次，疑似暴力破解",
            )
        ]
    return []


def run_all_checks(outputs: dict[str, dict[str, Any]], settings: Any) -> list[Alert]:
    alerts: list[Alert] = []
    alerts.extend(check_disk(outputs.get("disk", {}), settings.disk_percent))
    alerts.extend(check_service(outputs.get("service", {})))
    alerts.extend(check_auth(outputs.get("auth", {}), settings.failed_login))
    return alerts
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_monitor_checks -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/monitor/checks.py tests/test_monitor_checks.py
git commit -m "feat(monitor): 新增磁盘/服务/失败登录阈值规则

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: 巡检配置 `get_monitor_settings`

**Files:**
- Modify: `backend/config.py`（追加 `MonitorSettings` + `get_monitor_settings`）
- Test: `tests/test_monitor_settings.py`

**Interfaces:**
- Produces: `MonitorSettings(enabled, interval_seconds, disk_percent, failed_login, auth_lines)`（frozen）+ `get_monitor_settings()`。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_monitor_settings.py
import os
import unittest
from unittest import mock

from backend.config import get_monitor_settings


class MonitorSettingsTest(unittest.TestCase):
    def test_defaults(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            s = get_monitor_settings()
            self.assertFalse(s.enabled)
            self.assertEqual(s.interval_seconds, 300)
            self.assertEqual(s.disk_percent, 90)
            self.assertEqual(s.failed_login, 20)
            self.assertEqual(s.auth_lines, 100)

    def test_enabled_flag(self):
        with mock.patch.dict(os.environ, {"AGENT_MONITOR_ENABLED": "true"}, clear=True):
            self.assertTrue(get_monitor_settings().enabled)

    def test_interval_clamped(self):
        with mock.patch.dict(os.environ, {"AGENT_MONITOR_INTERVAL_SECONDS": "1"}, clear=True):
            self.assertEqual(get_monitor_settings().interval_seconds, 10)

    def test_auth_lines_forced_above_threshold(self):
        with mock.patch.dict(os.environ, {
            "AGENT_MONITOR_FAILED_LOGIN": "150",
            "AGENT_MONITOR_AUTH_LINES": "50",
        }, clear=True):
            s = get_monitor_settings()
            self.assertEqual(s.failed_login, 150)
            self.assertEqual(s.auth_lines, 151)  # raised to threshold+1

    def test_auth_lines_capped_at_200(self):
        with mock.patch.dict(os.environ, {
            "AGENT_MONITOR_FAILED_LOGIN": "199",
            "AGENT_MONITOR_AUTH_LINES": "10",
        }, clear=True):
            self.assertEqual(get_monitor_settings().auth_lines, 200)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_monitor_settings -v`
Expected: FAIL — `ImportError: cannot import name 'get_monitor_settings'`.

- [ ] **Step 3: Append to `backend/config.py`**

```python
@dataclass(frozen=True)
class MonitorSettings:
    enabled: bool
    interval_seconds: int
    disk_percent: int
    failed_login: int
    auth_lines: int


def get_monitor_settings() -> MonitorSettings:
    def _int(name: str, default: int) -> int:
        try:
            return int(os.getenv(name, str(default)))
        except ValueError:
            return default

    enabled = os.getenv("AGENT_MONITOR_ENABLED", "false").strip().lower() in {"1", "true", "yes"}
    interval = max(10, min(_int("AGENT_MONITOR_INTERVAL_SECONDS", 300), 86400))
    disk_percent = max(1, min(_int("AGENT_MONITOR_DISK_PERCENT", 90), 100))
    failed_login = max(1, min(_int("AGENT_MONITOR_FAILED_LOGIN", 20), 199))
    auth_lines = max(1, min(_int("AGENT_MONITOR_AUTH_LINES", 100), 200))
    # 保证读取行数 > 阈值，否则 failed_login_count > threshold 永远无法触发。
    auth_lines = min(200, max(auth_lines, failed_login + 1))
    return MonitorSettings(
        enabled=enabled,
        interval_seconds=interval,
        disk_percent=disk_percent,
        failed_login=failed_login,
        auth_lines=auth_lines,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_monitor_settings -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/config.py tests/test_monitor_settings.py
git commit -m "feat(config): 新增巡检配置 get_monitor_settings（含 auth_lines>阈值 约束）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: 调度器 `scheduler.py`

**Files:**
- Create: `backend/monitor/scheduler.py`
- Test: `tests/test_monitor_scheduler.py`

**Interfaces:**
- Consumes: `run_all_checks`（Task 2）、`AlertStore`（Task 1）、`MonitorSettings`（Task 3）、`backend.agent.planner.Plan`、`backend.agent.executor.ToolExecutor`（`execute(plan, user_id, raw_query, approved=False, trace_id=None, role=None) -> ExecutionResult`，`ExecutionResult.result` 是 `{tool: output}`）。
- Produces: `MonitorScheduler(executor, alert_store, settings, audit, clock=None)`，方法 `run_once() -> list[Alert]`、`start()`、`stop()`、`running() -> bool`、`status() -> dict`；类属性 `CHECK_TOOLS = ("disk", "service", "auth")`。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_monitor_scheduler.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_monitor_scheduler -v`
Expected: FAIL — `ModuleNotFoundError: backend.monitor.scheduler`.

- [ ] **Step 3: Create `backend/monitor/scheduler.py`**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_monitor_scheduler -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Run full suite**

Run: `python -m unittest discover -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/monitor/scheduler.py tests/test_monitor_scheduler.py
git commit -m "feat(monitor): 新增后台巡检调度器（经 executor 跑只读 Plan）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: lifespan 启停 + `/api/alerts` + `/api/monitor/status`

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_monitor_endpoints.py`

**Interfaces:**
- Consumes: `MonitorScheduler`（Task 4）、`AlertStore`（Task 1）、`get_monitor_settings`（Task 3）、`Alert`（Task 1）、已存在的 `executor`/`audit`/`_role_from_header`。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_monitor_endpoints.py
import os
import unittest
from unittest import mock

from fastapi.testclient import TestClient

import backend.main as main
from backend.monitor.alerts import Alert


class AlertsEndpointTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        main._alert_store.reset()

    def test_viewer_forbidden(self):
        self.assertEqual(self.client.get("/api/alerts").status_code, 403)

    def test_operator_gets_alerts(self):
        main._alert_store.add(Alert(severity="critical", source="disk", metric="used_percent",
                                    value=99, threshold=90, message="磁盘快满了"))
        with mock.patch.dict(os.environ, {"AGENT_OPERATOR_TOKEN": "optok"}, clear=True):
            resp = self.client.get("/api/alerts", headers={"Authorization": "Bearer optok"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(len(body["alerts"]), 1)
        self.assertEqual(body["alerts"][0]["source"], "disk")


class MonitorStatusEndpointTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def test_status_open_and_shaped(self):
        resp = self.client.get("/api/monitor/status")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        for key in ("enabled", "running", "interval_seconds", "checks"):
            self.assertIn(key, body)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_monitor_endpoints -v`
Expected: FAIL — `AttributeError: module 'backend.main' has no attribute '_alert_store'` / endpoints 404.

- [ ] **Step 3a: Edit imports in `backend/main.py`**

Add to the `backend.*` imports:

```python
from backend.config import get_monitor_settings, get_rate_limit_settings
from backend.monitor.alerts import AlertStore
from backend.monitor.scheduler import MonitorScheduler
```

(Note: `get_rate_limit_settings` is already imported — merge `get_monitor_settings` into that existing `from backend.config import ...` line rather than duplicating.)

- [ ] **Step 3b: Add module-level monitor singletons**

After the existing rate-limit globals (`_rate_limiter`, `_concurrency`, etc.), add:

```python
_monitor_settings = get_monitor_settings()
_alert_store = AlertStore()
_monitor_scheduler = MonitorScheduler(executor, _alert_store, _monitor_settings, audit)
```

- [ ] **Step 3c: Start/stop in `lifespan`**

Replace the existing `lifespan` body so the scheduler starts (when enabled) and always stops:

```python
@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if _monitor_settings.enabled:
        _monitor_scheduler.start()
    try:
        async with mcp_session_manager.run():
            yield
    finally:
        _monitor_scheduler.stop()
```

- [ ] **Step 3d: Add the two endpoints**

Add near the other `/api/*` GET endpoints:

```python
@app.get("/api/alerts")
def list_alerts(limit: int = 100, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    role = _role_from_header(authorization)
    if role not in {"operator", "admin"}:
        raise HTTPException(status_code=403, detail="alerts 仅 operator/admin 可访问")
    return {"alerts": _alert_store.recent(limit)}


@app.get("/api/monitor/status")
def monitor_status() -> dict[str, Any]:
    return _monitor_scheduler.status()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_monitor_endpoints -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run full suite (no regression)**

Run: `python -m unittest discover -v`
Expected: PASS. (Scheduler does NOT auto-start in tests since `AGENT_MONITOR_ENABLED` defaults false, so no background thread runs during tests.)

- [ ] **Step 6: Commit**

```bash
git add backend/main.py tests/test_monitor_endpoints.py
git commit -m "feat(api): lifespan 启停巡检 + GET /api/alerts（operator/admin）+ /api/monitor/status

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: 文档同步

**Files:**
- Create: `docs/proactive-monitoring.md`
- Modify: `CLAUDE.md`（API 表面 + 关键不变量 + 核心链路提一句）

**Interfaces:** 无代码接口；文档需与 Task 1-5 实际行为一致。

- [ ] **Step 1: 写 `docs/proactive-monitoring.md`**

覆盖：后台守护线程巡检(默认关、`AGENT_MONITOR_*` 配置与默认值)、固定只读检查工具(disk/service/auth)经 executor 执行复用 guard+metrics、三条阈值规则与字段(`used_percent`/`analysis.failed_count`/`analysis.failed_login_count`)、auth_lines>阈值约束、告警内存存储(上限+TTL+重启清零)、`monitor_alert` 审计 stage、`GET /api/alerts`(operator/admin) 与 `GET /api/monitor/status`(开放)、不变量(只读、不自动修复、tick 隔离、优雅停)。

- [ ] **Step 2: 更新 `CLAUDE.md`**

- 「API 表面」新增 `GET /api/alerts`(operator/admin) 与 `GET /api/monitor/status`(开放)。
- 「关键不变量」补一条：主动巡检只跑只读工具、经 executor、不自动修复、默认关、告警内存态重启清零。
- 「核心链路」或概述提一句：除被动 `/api/agent/execute`，还有可选的后台主动巡检。

- [ ] **Step 3: 验证未改代码 + 提交**

Run: `python -m unittest discover -v`（应仍全绿）
然后：

```bash
git add docs/proactive-monitoring.md CLAUDE.md
git commit -m "docs: 同步主动巡检与阈值告警说明

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review（计划自检）

- **Spec coverage:** AlertStore（Task 1）、阈值规则 disk/service/auth（Task 2）、配置 + auth_lines 约束（Task 3）、调度器 run_once 经 executor 跑只读 Plan + start/stop/status + tick 隔离（Task 4）、lifespan 启停 + `/api/alerts` 门控 + `/api/monitor/status`（Task 5）、文档（Task 6）——spec 各节均有对应任务。
- **Placeholder scan:** 无 TBD/占位；每个代码步骤给出完整代码。
- **Type consistency:** `Alert` 字段、`AlertStore.add/recent/reset`、`run_all_checks(outputs, settings)`（读 `.disk_percent`/`.failed_login`）、`MonitorSettings` 字段、`MonitorScheduler(executor, alert_store, settings, audit, clock)` 与 `run_once/start/stop/running/status/CHECK_TOOLS` 跨任务一致；`executor.execute(plan=, user_id=, raw_query=, trace_id=, role=)` 与现有签名一致；`main._alert_store`/`_monitor_scheduler` 在 Task 5 定义、测试中引用一致。
- **不变量:** 仅 disk/service/auth 只读工具、经 executor.execute(role=admin)、不触发操作类；tick 异常隔离；lifespan finally 停；默认关不在测试中起线程。
