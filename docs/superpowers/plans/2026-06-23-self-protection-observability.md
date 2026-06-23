# 自我保护与自我可观测 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给后端补请求限流/并发保护（Q6）与自我可观测 JSON metrics（Q8），不改动安全校验链路。

**Architecture:** 进程内：`RateLimiter`（每 key 滑动窗口）+ `ConcurrencyGate`（信号量）由一个 HTTP 中间件对重端点强制；`MetricsCollector` 单例在中间件/executor/llm_client 打点，经角色门控的 `GET /api/metrics` 以 JSON 暴露。

**Tech Stack:** Python 3 标准库（threading/collections/time）、FastAPI、`unittest`。

## Global Constraints

- 限流/并发是 `SecurityGuard` 之外的额外闸，不替代也不前置于安全校验；被限流请求不进入工具执行。
- metrics 只读采集，打点不得阻塞或改变主链路结果。
- 单进程内存态，进程重启清零（与现有会话存储一致），文档说明、不假装持久化。
- 匿名（无令牌）限流键按客户端 IP；有令牌按 `session_principal` 主体。
- `/api/metrics` 仅 `operator`/`admin` 可访问，其余返回 403。
- metrics 格式为 JSON。
- 文档默认中文；标识符、API 路径、env 变量、JSON 字段保持原文。
- 测试入口：`python -m unittest discover -v`（标准库 unittest）。
- 提交信息结尾附：`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。

---

### Task 1: 限流与并发组件 + 配置

**Files:**
- Create: `backend/security/rate_limit.py`
- Modify: `backend/config.py`（追加 `RateLimitSettings` + `get_rate_limit_settings`）
- Test: `tests/test_rate_limit.py`

**Interfaces:**
- Consumes: `backend.security.auth.session_principal`（已存在）。
- Produces:
  - `RateLimiter(limit_per_window: int, window_seconds: float = 60.0, clock=None)`，方法 `allow(key) -> bool`、`retry_after(key) -> int`。
  - `ConcurrencyGate(max_concurrent: int)`，方法 `try_acquire() -> bool`、`release() -> None`。
  - `rate_limit_key(token: str | None, client_host: str | None) -> str`。
  - `RateLimitSettings(enabled: bool, per_minute: int, max_concurrent: int)` 与 `get_rate_limit_settings()`。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_rate_limit.py
import os
import unittest
from unittest import mock

from backend.config import get_rate_limit_settings
from backend.security.auth import session_principal
from backend.security.rate_limit import ConcurrencyGate, RateLimiter, rate_limit_key


class RateLimiterTest(unittest.TestCase):
    def test_allows_up_to_limit_then_blocks(self):
        clock = [0.0]
        limiter = RateLimiter(limit_per_window=3, window_seconds=60, clock=lambda: clock[0])
        self.assertTrue(limiter.allow("k"))
        self.assertTrue(limiter.allow("k"))
        self.assertTrue(limiter.allow("k"))
        self.assertFalse(limiter.allow("k"))

    def test_keys_are_independent(self):
        limiter = RateLimiter(limit_per_window=1, window_seconds=60, clock=lambda: 0.0)
        self.assertTrue(limiter.allow("a"))
        self.assertTrue(limiter.allow("b"))
        self.assertFalse(limiter.allow("a"))

    def test_window_slides(self):
        clock = [0.0]
        limiter = RateLimiter(limit_per_window=1, window_seconds=10, clock=lambda: clock[0])
        self.assertTrue(limiter.allow("k"))
        self.assertFalse(limiter.allow("k"))
        clock[0] = 11.0
        self.assertTrue(limiter.allow("k"))

    def test_retry_after_positive_when_limited(self):
        clock = [0.0]
        limiter = RateLimiter(limit_per_window=1, window_seconds=10, clock=lambda: clock[0])
        limiter.allow("k")
        self.assertFalse(limiter.allow("k"))
        self.assertGreater(limiter.retry_after("k"), 0)


class ConcurrencyGateTest(unittest.TestCase):
    def test_blocks_when_full_and_recovers(self):
        gate = ConcurrencyGate(2)
        self.assertTrue(gate.try_acquire())
        self.assertTrue(gate.try_acquire())
        self.assertFalse(gate.try_acquire())
        gate.release()
        self.assertTrue(gate.try_acquire())


class RateLimitKeyTest(unittest.TestCase):
    def test_token_uses_principal(self):
        self.assertEqual(rate_limit_key("tok", "1.2.3.4"), session_principal("tok"))

    def test_anon_uses_ip(self):
        self.assertEqual(rate_limit_key(None, "1.2.3.4"), "ip:1.2.3.4")
        self.assertEqual(rate_limit_key(None, None), "ip:unknown")


class RateLimitSettingsTest(unittest.TestCase):
    def test_defaults(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            settings = get_rate_limit_settings()
            self.assertTrue(settings.enabled)
            self.assertEqual(settings.per_minute, 30)
            self.assertEqual(settings.max_concurrent, 8)

    def test_env_override_and_disable(self):
        with mock.patch.dict(os.environ, {
            "AGENT_RATE_LIMIT_PER_MIN": "5",
            "AGENT_MAX_CONCURRENT": "2",
            "AGENT_RATE_LIMIT_ENABLED": "false",
        }, clear=True):
            settings = get_rate_limit_settings()
            self.assertFalse(settings.enabled)
            self.assertEqual(settings.per_minute, 5)
            self.assertEqual(settings.max_concurrent, 2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_rate_limit -v`
Expected: FAIL — `ModuleNotFoundError: backend.security.rate_limit`.

- [ ] **Step 3a: Create `backend/security/rate_limit.py`**

```python
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable

from backend.security.auth import session_principal


class RateLimiter:
    """Per-key sliding-window rate limiter (in-process, thread-safe)."""

    def __init__(
        self,
        limit_per_window: int,
        window_seconds: float = 60.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._limit = max(1, int(limit_per_window))
        self._window = float(window_seconds)
        self._clock = clock or time.monotonic
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.RLock()

    def allow(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            bucket = self._hits.setdefault(key, deque())
            self._trim(bucket, now)
            if len(bucket) >= self._limit:
                return False
            bucket.append(now)
            return True

    def retry_after(self, key: str) -> int:
        now = self._clock()
        with self._lock:
            bucket = self._hits.get(key)
            if not bucket:
                return 0
            self._trim(bucket, now)
            if len(bucket) < self._limit:
                return 0
            return max(1, int(self._window - (now - bucket[0])) + 1)

    def _trim(self, bucket: "deque[float]", now: float) -> None:
        cutoff = now - self._window
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()


class ConcurrencyGate:
    """Non-blocking global concurrency cap backed by a bounded semaphore."""

    def __init__(self, max_concurrent: int) -> None:
        self._sem = threading.BoundedSemaphore(max(1, int(max_concurrent)))

    def try_acquire(self) -> bool:
        return self._sem.acquire(blocking=False)

    def release(self) -> None:
        try:
            self._sem.release()
        except ValueError:
            # Released more times than acquired; ignore to stay robust.
            pass


def rate_limit_key(token: str | None, client_host: str | None) -> str:
    """Authenticated callers are keyed by principal; anonymous by client IP."""
    if token:
        return session_principal(token)
    return f"ip:{client_host or 'unknown'}"
```

- [ ] **Step 3b: Append to `backend/config.py`**

```python
@dataclass(frozen=True)
class RateLimitSettings:
    enabled: bool
    per_minute: int
    max_concurrent: int


def get_rate_limit_settings() -> RateLimitSettings:
    enabled = os.getenv("AGENT_RATE_LIMIT_ENABLED", "true").strip().lower() not in {"0", "false", "no"}
    try:
        per_minute = int(os.getenv("AGENT_RATE_LIMIT_PER_MIN", "30"))
    except ValueError:
        per_minute = 30
    try:
        max_concurrent = int(os.getenv("AGENT_MAX_CONCURRENT", "8"))
    except ValueError:
        max_concurrent = 8
    return RateLimitSettings(
        enabled=enabled,
        per_minute=max(1, per_minute),
        max_concurrent=max(1, max_concurrent),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_rate_limit -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/security/rate_limit.py backend/config.py tests/test_rate_limit.py
git commit -m "feat(security): 新增限流与并发保护组件及配置

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: 指标采集器

**Files:**
- Create: `backend/observability/__init__.py`
- Create: `backend/observability/metrics.py`
- Test: `tests/test_metrics.py`

**Interfaces:**
- Produces:
  - `MetricsCollector(sample_size: int = 200)`，方法 `record_request(endpoint)`、`record_rate_limited()`、`record_blocked()`、`record_tool(tool, duration_ms)`、`record_llm(success: bool)`、`snapshot() -> dict`、`reset()`。
  - `get_metrics() -> MetricsCollector`（全局单例）。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_metrics.py
import unittest

from backend.observability.metrics import MetricsCollector, get_metrics


class MetricsCollectorTest(unittest.TestCase):
    def test_counts(self):
        m = MetricsCollector()
        m.record_request("/api/agent/execute")
        m.record_request("/api/agent/execute")
        m.record_request("/api/agent/plan")
        m.record_blocked()
        m.record_rate_limited()
        snap = m.snapshot()
        self.assertEqual(snap["requests"], {"/api/agent/execute": 2, "/api/agent/plan": 1})
        self.assertEqual(snap["blocked"], 1)
        self.assertEqual(snap["rate_limited"], 1)

    def test_tool_percentiles_nearest_rank(self):
        m = MetricsCollector()
        for duration in [10, 20, 30, 40, 100]:
            m.record_tool("system", duration)
        tool = m.snapshot()["tools"]["system"]
        self.assertEqual(tool["count"], 5)
        self.assertEqual(tool["p50_ms"], 30)
        self.assertEqual(tool["p95_ms"], 100)

    def test_llm_success_rate(self):
        m = MetricsCollector()
        m.record_llm(True)
        m.record_llm(True)
        m.record_llm(False)
        llm = m.snapshot()["llm"]
        self.assertEqual(llm["success"], 2)
        self.assertEqual(llm["failure"], 1)
        self.assertEqual(llm["success_rate"], 0.667)

    def test_empty_llm_rate_is_none(self):
        self.assertIsNone(MetricsCollector().snapshot()["llm"]["success_rate"])

    def test_reset(self):
        m = MetricsCollector()
        m.record_request("/x")
        m.reset()
        self.assertEqual(m.snapshot()["requests"], {})

    def test_get_metrics_is_singleton(self):
        self.assertIs(get_metrics(), get_metrics())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_metrics -v`
Expected: FAIL — `ModuleNotFoundError: backend.observability.metrics`.

- [ ] **Step 3a: Create `backend/observability/__init__.py`** (empty file)

```python
```

- [ ] **Step 3b: Create `backend/observability/metrics.py`**

```python
from __future__ import annotations

import math
import threading
from collections import defaultdict, deque
from typing import Any


class MetricsCollector:
    """In-process, thread-safe counters + bounded per-tool latency samples."""

    def __init__(self, sample_size: int = 200) -> None:
        self._sample_size = max(1, int(sample_size))
        self._requests: dict[str, int] = defaultdict(int)
        self._blocked = 0
        self._rate_limited = 0
        self._tool_counts: dict[str, int] = defaultdict(int)
        self._tool_samples: dict[str, deque[float]] = defaultdict(self._new_sample_buffer)
        self._llm_success = 0
        self._llm_failure = 0
        self._lock = threading.RLock()

    def _new_sample_buffer(self) -> "deque[float]":
        return deque(maxlen=self._sample_size)

    def record_request(self, endpoint: str) -> None:
        with self._lock:
            self._requests[endpoint] += 1

    def record_rate_limited(self) -> None:
        with self._lock:
            self._rate_limited += 1

    def record_blocked(self) -> None:
        with self._lock:
            self._blocked += 1

    def record_tool(self, tool: str, duration_ms: float) -> None:
        with self._lock:
            self._tool_counts[tool] += 1
            self._tool_samples[tool].append(float(duration_ms))

    def record_llm(self, success: bool) -> None:
        with self._lock:
            if success:
                self._llm_success += 1
            else:
                self._llm_failure += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            tools = {
                tool: {
                    "count": self._tool_counts[tool],
                    "p50_ms": self._percentile(self._tool_samples[tool], 50),
                    "p95_ms": self._percentile(self._tool_samples[tool], 95),
                }
                for tool in self._tool_counts
            }
            total = self._llm_success + self._llm_failure
            return {
                "requests": dict(self._requests),
                "blocked": self._blocked,
                "rate_limited": self._rate_limited,
                "tools": tools,
                "llm": {
                    "success": self._llm_success,
                    "failure": self._llm_failure,
                    "success_rate": round(self._llm_success / total, 3) if total else None,
                },
            }

    def reset(self) -> None:
        with self._lock:
            self._requests.clear()
            self._blocked = 0
            self._rate_limited = 0
            self._tool_counts.clear()
            self._tool_samples.clear()
            self._llm_success = 0
            self._llm_failure = 0

    @staticmethod
    def _percentile(samples: "deque[float]", pct: float) -> float | None:
        if not samples:
            return None
        ordered = sorted(samples)
        rank = max(1, math.ceil(pct / 100 * len(ordered)))
        return round(ordered[rank - 1], 3)


_collector = MetricsCollector()


def get_metrics() -> MetricsCollector:
    return _collector
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_metrics -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/observability/__init__.py backend/observability/metrics.py tests/test_metrics.py
git commit -m "feat(observability): 新增进程内指标采集器

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: 在 executor 与 llm_client 打点

**Files:**
- Modify: `backend/agent/executor.py`（工具耗时 + 拦截打点）
- Modify: `backend/agent/llm_client.py`（LLM 成功/失败打点）
- Test: `tests/test_metrics_instrumentation.py`

**Interfaces:**
- Consumes: `backend.observability.metrics.get_metrics`（Task 2）。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_metrics_instrumentation.py
import unittest

from backend.agent.executor import ToolExecutor
from backend.agent.llm_client import LLMClient
from backend.agent.planner import Plan
from backend.config import LLMSettings
from backend.observability.metrics import get_metrics


class ExecutorInstrumentationTest(unittest.TestCase):
    def test_records_tool_latency(self):
        executor = ToolExecutor()
        executor._registry.call = lambda tool, arguments: {"source": tool, "analysis": {}}  # type: ignore[assignment]
        get_metrics().reset()
        executor.execute(plan=Plan(intent="inspection", tools=["system"], arguments={}), user_id="u", raw_query="q", role="viewer")
        tools = get_metrics().snapshot()["tools"]
        self.assertIn("system", tools)
        self.assertEqual(tools["system"]["count"], 1)

    def test_records_blocked_step(self):
        # disk.large_files with path="/" is medium for viewer -> guard blocks it.
        executor = ToolExecutor()
        get_metrics().reset()
        execution = executor.execute(
            plan=Plan(intent="inspection", tools=["disk.large_files"], arguments={"path": "/"}),
            user_id="u", raw_query="scan root", role="viewer",
        )
        self.assertTrue(execution.blocked)
        self.assertGreaterEqual(get_metrics().snapshot()["blocked"], 1)


class LLMInstrumentationTest(unittest.TestCase):
    def _client(self) -> LLMClient:
        return LLMClient(LLMSettings(provider="deepseek", api_key="x", base_url="http://x", model="m"))

    def test_records_llm_success(self):
        client = self._client()
        client._post_chat = lambda payload: {"choices": [{"message": {"content": "{}"}}]}  # type: ignore[assignment]
        get_metrics().reset()
        client._chat_json(system_prompt="s", user_payload={})
        self.assertEqual(get_metrics().snapshot()["llm"]["success"], 1)

    def test_records_llm_failure(self):
        client = self._client()
        client._post_chat = lambda payload: None  # type: ignore[assignment]
        get_metrics().reset()
        client._chat_json(system_prompt="s", user_payload={})
        self.assertEqual(get_metrics().snapshot()["llm"]["failure"], 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_metrics_instrumentation -v`
Expected: FAIL — tool/blocked/llm metrics stay 0 (no instrumentation yet).

- [ ] **Step 3a: Instrument `backend/agent/executor.py`**

Add imports near the top (after the existing imports):

```python
import time

from backend.observability.metrics import get_metrics
```

In the blocked branch inside `execute` (where `if safety.blocked:` handles a blocked step), add `get_metrics().record_blocked()` as the first line of that block:

```python
            if safety.blocked:
                get_metrics().record_blocked()
                step_records.append(self._step_record(step.id, step.tool, "blocked", resolved, {}, security, []))
```

Wrap the tool call with timing — replace the line `tool_result = self._registry.call(step.tool, resolved)` with:

```python
            started = time.perf_counter()
            tool_result = self._registry.call(step.tool, resolved)
            get_metrics().record_tool(step.tool, (time.perf_counter() - started) * 1000.0)
```

- [ ] **Step 3b: Instrument `backend/agent/llm_client.py`**

Add the import near the top:

```python
from backend.observability.metrics import get_metrics
```

In `_chat_json`, after computing `body` (and the optional retry without `response_format`), record one LLM metric per logical call. Replace the tail of `_chat_json`:

```python
        body = self._post_chat(payload)
        if body is None and "response_format" in payload:
            payload.pop("response_format", None)
            body = self._post_chat(payload)
        get_metrics().record_llm(body is not None)
        if body is None:
            return None
        return body.get("choices", [{}])[0].get("message", {}).get("content", "")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_metrics_instrumentation -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Run full suite (no regression)**

Run: `python -m unittest discover -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/agent/executor.py backend/agent/llm_client.py tests/test_metrics_instrumentation.py
git commit -m "feat(observability): executor 工具耗时/拦截打点 + llm_client 调用打点

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: 限流中间件 + `GET /api/metrics`

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_middleware_metrics.py`

**Interfaces:**
- Consumes: `RateLimiter`/`ConcurrencyGate`/`rate_limit_key`（Task 1）、`get_rate_limit_settings`（Task 1）、`get_metrics`（Task 2）、`parse_bearer`/`resolve_role`（已存在）。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_middleware_metrics.py
import os
import unittest
from unittest import mock

from fastapi.testclient import TestClient

import backend.main as main
from backend.security.rate_limit import RateLimiter


class RateLimitMiddlewareTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self._orig = main._rate_limiter
        main._rate_limiter = RateLimiter(limit_per_window=2, window_seconds=60)

    def tearDown(self):
        main._rate_limiter = self._orig

    def test_blocks_after_limit_on_heavy_endpoint(self):
        body = {"query": "看系统状态"}
        self.assertEqual(self.client.post("/api/agent/plan", json=body).status_code, 200)
        self.assertEqual(self.client.post("/api/agent/plan", json=body).status_code, 200)
        resp = self.client.post("/api/agent/plan", json=body)
        self.assertEqual(resp.status_code, 429)
        self.assertIn("Retry-After", resp.headers)

    def test_health_is_not_rate_limited(self):
        for _ in range(5):
            self.assertEqual(self.client.get("/health").status_code, 200)


class MetricsEndpointTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def test_viewer_forbidden(self):
        self.assertEqual(self.client.get("/api/metrics").status_code, 403)

    def test_operator_gets_snapshot(self):
        with mock.patch.dict(os.environ, {"AGENT_OPERATOR_TOKEN": "optok"}, clear=True):
            resp = self.client.get("/api/metrics", headers={"Authorization": "Bearer optok"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        for key in ("requests", "blocked", "rate_limited", "tools", "llm"):
            self.assertIn(key, body)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_middleware_metrics -v`
Expected: FAIL — `AttributeError: module 'backend.main' has no attribute '_rate_limiter'` / `/api/metrics` returns 404.

- [ ] **Step 3a: Edit imports in `backend/main.py`**

Change the FastAPI import to add `HTTPException`, and the responses import to add `JSONResponse`:

```python
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
```

Add new imports alongside the existing `backend.*` imports:

```python
from backend.config import get_rate_limit_settings
from backend.observability.metrics import get_metrics
from backend.security.rate_limit import ConcurrencyGate, RateLimiter, rate_limit_key
```

- [ ] **Step 3b: Add module-level limiter/gate and the middleware**

After `app = FastAPI(...)` and the existing `app.add_middleware(CORSMiddleware, ...)` block, add:

```python
_rl_settings = get_rate_limit_settings()
_rate_limiter = RateLimiter(_rl_settings.per_minute, 60.0)
_concurrency = ConcurrencyGate(_rl_settings.max_concurrent)

_HEAVY_PATHS = {"/api/agent/execute", "/api/agent/plan", "/api/security/evaluate"}


def _is_heavy(method: str, path: str) -> bool:
    if method != "POST":
        return False
    if path in _HEAVY_PATHS:
        return True
    return path.startswith("/api/tools/") and path != "/api/tools"


@app.middleware("http")
async def rate_limit_middleware(request, call_next):
    path = request.url.path
    if path.startswith("/api/") and path != "/api/metrics":
        get_metrics().record_request(path)
    if _rl_settings.enabled and _is_heavy(request.method, path):
        token = parse_bearer(request.headers.get("authorization"))
        client_host = request.client.host if request.client else None
        key = rate_limit_key(token, client_host)
        if not _rate_limiter.allow(key):
            get_metrics().record_rate_limited()
            return JSONResponse(
                status_code=429,
                content={"detail": "请求过于频繁，请稍后重试"},
                headers={"Retry-After": str(_rate_limiter.retry_after(key))},
            )
        if not _concurrency.try_acquire():
            return JSONResponse(status_code=503, content={"detail": "服务繁忙，请稍后重试"})
        try:
            return await call_next(request)
        finally:
            _concurrency.release()
    return await call_next(request)
```

> 注：`@app.middleware("http")` 在 CORS 之后注册，包在 CORS 内层；两者都生效。被限流的请求在中间件直接短路，不进入下游 endpoint，因此不进入安全校验/工具执行。

- [ ] **Step 3c: Add the `GET /api/metrics` endpoint**

Add near the other `/api/*` GET endpoints:

```python
@app.get("/api/metrics")
def metrics_endpoint(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    role = _role_from_header(authorization)
    if role not in {"operator", "admin"}:
        raise HTTPException(status_code=403, detail="metrics 仅 operator/admin 可访问")
    return get_metrics().snapshot()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_middleware_metrics -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Run full suite (no regression)**

Run: `python -m unittest discover -v`
Expected: PASS. (If a pre-existing test floods a heavy endpoint > 30×/window it could 429; none do — the default limiter only applies with real settings, and tests that need many calls patch `main._rate_limiter`.)

- [ ] **Step 6: Commit**

```bash
git add backend/main.py tests/test_middleware_metrics.py
git commit -m "feat(api): 重端点限流/并发中间件 + GET /api/metrics（operator/admin）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: 文档同步

**Files:**
- Create: `docs/self-protection-observability.md`
- Modify: `CLAUDE.md`（API 表面 + 关键不变量）

**Interfaces:** 无代码接口；文档需与 Task 1-4 实际行为一致。

- [ ] **Step 1: 写 `docs/self-protection-observability.md`**

覆盖：限流（每主体/匿名按 IP 滑动窗口、`AGENT_RATE_LIMIT_PER_MIN` 默认 30）、并发闸
（`AGENT_MAX_CONCURRENT` 默认 8、`AGENT_RATE_LIMIT_ENABLED` 开关）、重端点白名单、`429`/`503`
语义；metrics 采集点（请求/限流/拦截/工具耗时 P50/P95/LLM 成功率）、`GET /api/metrics`
JSON 结构与 operator/admin 门控；单进程内存态、重启清零、限流不替代安全校验的不变量。

- [ ] **Step 2: 更新 `CLAUDE.md`**

- 「API 表面」新增 `GET /api/metrics`（operator/admin），并说明重端点有限流/并发保护。
- 「关键不变量」补充一条：限流/并发是 guard 之外的额外闸、不替代安全校验；metrics 只读采集、进程内、重启清零。

- [ ] **Step 3: 验证未改代码 + 提交**

Run: `python -m unittest discover -v`（应仍全绿，确认未误改代码）
然后：

```bash
git add docs/self-protection-observability.md CLAUDE.md
git commit -m "docs: 同步自我保护与自我可观测说明

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review（计划自检）

- **Spec coverage:** 限流+并发（Task 1+4）、限流键 IP/主体（Task 1）、配置（Task 1）、metrics 采集器（Task 2）、executor/llm_client 打点（Task 3）、中间件请求/限流计数（Task 4）、`/api/metrics` 角色门控 JSON（Task 4）、文档（Task 5）——spec 各节均有对应任务。
- **Placeholder scan:** 无 TBD/占位；每个代码步骤给出完整代码。
- **Type consistency:** `RateLimiter.allow/retry_after`、`ConcurrencyGate.try_acquire/release`、`rate_limit_key`、`get_metrics`、`MetricsCollector.record_*/snapshot/reset` 在 Task 1-2 定义、Task 3-4 调用一致；`main._rate_limiter` 在 Task 4 定义、测试中 patch 同名。
- **不变量:** 中间件短路在 guard 之前但只做"拒绝"（不放行额外权限）；打点不改链路结果；进程内内存态。
