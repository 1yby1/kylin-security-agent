# 自我保护与自我可观测 设计文档

- 日期：2026-06-23
- 分支：`feature/self-protection-observability`
- 范围：给 Agent 后端补两项"生产就绪"能力——请求限流/并发保护（Q6）与自我可观测 metrics（Q8）。

## 1. 背景与目标

当前后端只有 CORS 中间件，没有限流/并发控制：一个 viewer（无令牌）可以狂刷
`/api/agent/execute` 把后端与 LLM 成本打爆。同时审计是逐条事件，没有聚合视图，无法一眼
看到 Agent 自身健康（请求量、拦截率、工具耗时、LLM 成功率）。本设计补齐这两项。

### 已锁定的设计决策

| 决策点 | 取定 |
|---|---|
| 匿名限流键 | 无令牌按客户端 IP（`ip:<host>`）；有令牌按 owner 主体（`session_principal`） |
| metrics 格式 | JSON |
| metrics 访问 | 限 operator/admin（其余 403） |

## 2. 非目标（YAGNI）

- 不引入 Redis/外部存储——限流与 metrics 都是单进程内存态（与现有进程内会话存储一致）。
- 不做分布式限流/多副本一致性。
- 不做 Prometheus exposition 格式（仅 JSON）。
- 不改动安全校验链路——限流是 guard 之外的额外闸，不替代 guard。

## 3. Q6：限流与并发保护

### 3.1 组件
新增 `backend/security/rate_limit.py`：

- `RateLimiter(limit_per_window, window_seconds, clock)`：每 key 滑动窗口计数。
  - `allow(key) -> bool`：记录一次访问；窗口内超过 `limit` 返回 `False`。
  - `retry_after(key) -> int`：距窗口内最早一次访问过期的秒数（给 `Retry-After`）。
  - 内部按 key 存时间戳 deque，访问时丢弃窗口外的旧时间戳。线程安全（`RLock`）。
- `ConcurrencyGate(max_concurrent)`：基于 `threading.BoundedSemaphore` 的非阻塞闸。
  - `try_acquire() -> bool`（非阻塞）/ `release()`。

### 3.2 限流键
`rate_limit_key(token, client_host) -> str`：有 token → `session_principal(token)`；
无 token → `"ip:" + (client_host or "unknown")`。

### 3.3 接入（main.py HTTP 中间件）
新增 `@app.middleware("http")`：

- 仅对**重端点**生效：`POST /api/agent/execute`、`POST /api/agent/plan`、
  `POST /api/security/evaluate`、`POST /api/tools/{name}`。其余路径（`/health`、
  `/api/metrics`、`GET` 只读端点等）直接放行。
- 顺序：① 限流 `RateLimiter.allow(key)` → 超限返回 `429`（含 `Retry-After` 头）；
  ② 并发 `ConcurrencyGate.try_acquire()` → 占满返回 `503`；③ 调用下游；④ `finally`
  释放并发闸。
- 中间件同时记 metrics：`record_request(endpoint)`；`429` 时 `record_rate_limited()`。

### 3.4 配置（backend/config.py）
`get_rate_limit_settings()` 读：

- `AGENT_RATE_LIMIT_PER_MIN`（默认 30，窗口 60s）
- `AGENT_MAX_CONCURRENT`（默认 8）
- `AGENT_RATE_LIMIT_ENABLED`（默认 true；false 时中间件直接放行，便于测试/调试）

## 4. Q8：自我可观测 metrics

### 4.1 组件
新增 `backend/observability/metrics.py`：

- `MetricsCollector`（线程安全单例，`get_metrics()` 取全局实例）：
  - `record_request(endpoint: str)`：按端点累计请求数。
  - `record_rate_limited()`：限流拒绝计数。
  - `record_blocked()`：安全拦截计数。
  - `record_tool(tool: str, duration_ms: float)`：按工具累计次数 + 存入有界耗时样本
    （每工具 `deque(maxlen=200)`）。
  - `record_llm(success: bool)`：LLM 调用成功/失败计数。
  - `snapshot() -> dict`：聚合返回（见 4.3）。
  - `reset()`：清零（测试用）。

### 4.2 打点位置
- `main.py` 中间件：`record_request` / `record_rate_limited`。
- `backend/agent/executor.py`：用 `time.perf_counter()` 包住 `registry.call(...)` →
  `record_tool(tool, duration_ms)`；某步 guard 校验 `safety.blocked` 时 `record_blocked()`。
- `backend/agent/llm_client.py`：`_post_chat` 成功返回 body → `record_llm(True)`；
  捕获到错误 → `record_llm(False)`。

### 4.3 `GET /api/metrics`
限 operator/admin（`resolve_role` 不在 `{operator, admin}` → `403`）。返回 JSON：

```json
{
  "requests": {"/api/agent/execute": 12, "...": 3},
  "blocked": 2,
  "rate_limited": 5,
  "tools": {"system": {"count": 4, "p50_ms": 12.3, "p95_ms": 40.1}, "...": {}},
  "llm": {"success": 9, "failure": 1, "success_rate": 0.9}
}
```

P50/P95 由各工具耗时样本（最近 200 个）排序取分位；样本不足时返回已有样本的分位。

## 5. 接入点汇总

| 文件 | 改动 |
|---|---|
| `backend/security/rate_limit.py` **(新)** | `RateLimiter`、`ConcurrencyGate`、`rate_limit_key` |
| `backend/observability/__init__.py` **(新)** | 包初始化 |
| `backend/observability/metrics.py` **(新)** | `MetricsCollector` + `get_metrics()` |
| `backend/config.py` | `get_rate_limit_settings()` |
| `backend/main.py` | 限流/并发中间件、`record_request`、`GET /api/metrics`（角色门控） |
| `backend/agent/executor.py` | 工具耗时打点 + 拦截打点 |
| `backend/agent/llm_client.py` | LLM 成功/失败打点 |

## 6. 测试计划（unittest，TDD 先行）

- `RateLimiter`：窗口内放行/超限拒绝、不同 key 相互独立、窗口滑动后恢复（注入 clock）。
- `ConcurrencyGate`：占满后 `try_acquire` 返回 False，释放后恢复。
- `rate_limit_key`：有 token → 主体；无 token → `ip:host`。
- `MetricsCollector`：计数累加、percentile 计算（含样本不足）、LLM 成功率、`snapshot` 结构。
- 中间件（TestClient）：连刷重端点触发 `429`；`/health` 不受限；`AGENT_RATE_LIMIT_ENABLED=false` 时放行。
- `/api/metrics`：无令牌 `403`；operator 令牌返回全量 snapshot。
- 集成：经 `/api/tools/{只读工具}` 执行后，`/api/metrics` 出现该工具的 `count`/耗时。

## 7. 关键不变量

- 限流/并发是 guard **之外**的额外闸，不替代也不前置于安全校验；被限流的请求不进入工具执行。
- metrics 只读采集，不改变 Agent 链路结果，不阻塞请求（打点失败不应影响主流程）。
- 单进程内存态，进程重启清零——与现有会话存储一致，文档说明，不假装持久化。
- `/api/metrics` 角色门控与项目"低权限少暴露"主线一致。

## 8. 风险与缓解

- **滑动窗口内存**：每 key 存时间戳 deque，访问时清理过期；key 总量受限流本身约束，
  必要时可加 key 上限（本期默认不加，单进程小规模够用）。
- **中间件识别端点**：按 `request.method` + `request.url.path` 前缀匹配重端点白名单，
  避免误限只读端点。
- **打点开销**：计数 + 定长 deque，O(1)；percentile 仅在 `/api/metrics` 读时计算。
