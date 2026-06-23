# 自我保护与自我可观测

记录两项能力：限流 + 并发闸（防止单一调用方或瞬时高并发压垮后端，是 `backend/security/guard.py`
之外的额外一道闸）；以及指标采集（请求数、限流/拦截次数、工具耗时分位数、LLM 成功率），通过
`GET /api/metrics` 暴露给 operator/admin 排查问题。两者都是进程内内存态，**不持久化、重启即清零**。

## 一、限流与并发闸

### 1.1 限流器：`backend/security/rate_limit.py`

`RateLimiter` 是按 key 的滑动窗口限流器（线程安全，`threading.RLock` 保护）：

- 每个 key 维护一个时间戳 `deque`；`allow(key)` 先裁掉窗口外的旧时间戳（`_trim`），再判断
  当前窗口内命中数是否达到 `limit_per_window`；未达到则记录本次并放行。
- `retry_after(key)` 返回还需等待的整数秒数（窗口内最早一条时间戳到期为止），用于响应头
  `Retry-After`。
- **`max_keys` 上限防内存膨胀**：默认 `10000`。一旦当前 key 数超过上限，`_sweep()` 会先裁剪
  所有桶、删除已空的桶；如果裁剪后仍超过上限，按"最近一次命中时间"升序淘汰最旧的 key，直到
  不超过 `max_keys`。这防止大量一次性匿名 IP（或被刻意构造的不同 key）无限堆积导致内存增长。

`ConcurrencyGate` 是非阻塞的全局并发闸，底层是 `threading.BoundedSemaphore`：

- `try_acquire()` 以 `blocking=False` 方式获取信号量，立即返回成败，不排队等待。
- `release()` 吞掉多余释放可能抛出的 `ValueError`，保证健壮性。

`rate_limit_key(token, client_host)` 决定限流的"主体"：

- 有令牌（`Authorization: Bearer ...` 解析出 token）→ 调用 `backend/security/auth.py` 的
  `session_principal(token)`，即同一令牌的所有请求共享同一限流配额（与会话上下文绑定主体的
  思路一致，详见 `docs/session-context-security.md`）。
- 无令牌（匿名）→ 按客户端 IP 拼成 `ip:<client_host>`，即同一来源 IP 的匿名请求共享配额；
  `client_host` 取不到时退化为 `ip:unknown`（极端情况下所有取不到 IP 的匿名请求会共享同一
  配额，这是已知的保守退化，不是隔离漏洞利用面）。

### 1.2 配置：`backend/config.py` 的 `get_rate_limit_settings()`

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `AGENT_RATE_LIMIT_ENABLED` | `true` | 取值为 `0`/`false`/`no`（大小写不敏感）时关闭限流与并发闸；其余值视为开启。 |
| `AGENT_RATE_LIMIT_PER_MIN` | `30` | 每个限流 key 在 60 秒滑动窗口内允许的请求数；解析失败回退默认值；钳制范围 `[1, 100000]`。 |
| `AGENT_MAX_CONCURRENT` | `8` | 全局并发闸容量；解析失败回退默认值；钳制范围 `[1, 4096]`。 |

窗口固定为 60 秒（`RateLimiter(_rl_settings.per_minute, 60.0)`），目前不可通过环境变量调整窗口
长度，只能调整窗口内的配额。

### 1.3 接入点：`backend/main.py` 的 `rate_limit_middleware`

`@app.middleware("http")` 注册的 `rate_limit_middleware` 在请求进入路由前拦截：

- **请求计数**（与是否限流无关）：只要 `path` 以 `/api/` 开头且不是 `/api/metrics` 本身，就
  调用 `get_metrics().record_request(path)`。`/api/metrics` 自身被显式排除，避免查看指标这个
  动作污染指标。
- **限流与并发闸只作用于"重端点"**（`_is_heavy(method, path)`，且仅当
  `AGENT_RATE_LIMIT_ENABLED` 为真时生效）：
  - `POST /api/agent/execute`
  - `POST /api/agent/plan`
  - `POST /api/security/evaluate`
  - `POST /api/tools/{tool_name}`（即 `path.startswith("/api/tools/")` 且不等于
    `/api/tools` 本身；`GET /api/tools` 列表端点和 `GET /api/tools/{tool_name}` 元数据端点
    都不在此列，因为 `_is_heavy` 先按 `method != "POST"` 短路排除了所有 GET 请求）
- **判定顺序**：先取 `Authorization` 头解析出 token 和 `request.client.host`，算出
  `rate_limit_key`；
  1. `_rate_limiter.allow(key)` 为假 → 记 `record_rate_limited()`，返回 `429`，
     `detail: "请求过于频繁，请稍后重试"`，并带 `Retry-After` 响应头（秒数来自
     `retry_after(key)`）。
  2. 通过限流后，`_concurrency.try_acquire()` 为假（并发闸已满）→ 直接返回 `503`，
     `detail: "服务繁忙，请稍后重试"`（不释放信号量，因为本次没有成功获取）。
  3. 两者都通过 → 调用 `await call_next(request)` 继续走正常链路，`finally` 块保证正常返回或
     抛异常都会 `_concurrency.release()`。
- 非重端点（包括所有 GET 端点和 `/health`）完全跳过限流/并发判断，直接 `call_next`。

### 1.4 不变量

- **限流/并发闸是 `SecurityGuard` 之外的额外一道闸，不替代安全校验**：它在 HTTP 中间件层、
  比安全校验更早拦截，但只做"是否超过频率/并发预算"的判断，不做工具白名单、参数 schema、
  危险路径/命令、角色或二次确认判断；即使关闭限流（`AGENT_RATE_LIMIT_ENABLED=false`），
  `backend/security/guard.py` 的全部校验依然在工具执行前生效，二者职责不重叠、不可互相替代。
- 限流以"主体"（已认证令牌）或"匿名来源 IP"为粒度，不是全局总闸——不同主体/IP 互不影响
  对方的配额。
- 限流命中不计入审计追踪（`trace_id`），因为请求在拿到 `trace_id` 之前就被中间件拦截；
  `429`/`503` 响应本身就是给调用方的明确反馈。

## 二、指标采集：`backend/observability/metrics.py`

`MetricsCollector` 是进程内单例（`get_metrics()` 返回的 `_collector`），线程安全
（`threading.RLock`），不写盘、不跨进程共享。

### 2.1 采集维度

| 维度 | 方法 | 调用点 |
| --- | --- | --- |
| 请求数（按端点路径） | `record_request(endpoint)` | `main.py` 中间件，每个 `/api/*` 请求（`/api/metrics` 除外）记一次 |
| 限流命中数 | `record_rate_limited()` | `main.py` 中间件，限流器拒绝时 |
| 安全校验拦截数 | `record_blocked()` | `backend/agent/executor.py`，`SecurityGuard.check()` 判定 `blocked` 时 |
| 工具调用次数 + 耗时样本 | `record_tool(tool, duration_ms)` | `backend/agent/executor.py`，紧贴 `self._registry.call(step.tool, resolved)` 前后用 `time.perf_counter()` 计时，只统计工具执行本身（不含安全校验、规划等前置耗时） |
| LLM 调用成功/失败 | `record_llm(success)` | `backend/agent/llm_client.py` 的 `_chat_json`，按是否解析出非空响应体判定 |

工具耗时样本是按工具名分桶的有界 `deque`（`maxlen=sample_size`，默认 `200`），超出容量后自动
丢弃最旧样本——这是固定大小的环形缓冲，不会无限增长。

### 2.2 `GET /api/metrics` 响应结构

`snapshot()` 返回的 JSON 形如：

```json
{
  "requests": {"/api/agent/execute": 12, "/api/tools/system": 3},
  "blocked": 2,
  "rate_limited": 1,
  "tools": {
    "system": {"count": 5, "p50_ms": 12.3, "p95_ms": 40.1}
  },
  "llm": {"success": 8, "failure": 1, "success_rate": 0.889}
}
```

- `requests`：端点路径 → 累计请求数（只含命中过的端点，未命中的路径不会出现 key）。
- `blocked` / `rate_limited`：累计计数（整型）。
- `tools.<tool>.count`：该工具被调用次数；`p50_ms` / `p95_ms`：基于当前样本窗口
  （最近 `sample_size` 次调用）计算的分位数耗时，单位毫秒，四舍五入到 3 位小数；样本为空时
  对应字段为 `null`（理论上只要 `count > 0` 就不会为空，因为计数和取样在同一次调用里发生）。
- `llm.success_rate`：`success / (success + failure)`，四舍五入到 3 位小数；
  从未调用过 LLM（`success + failure == 0`）时为 `null`，不会除零。

### 2.3 端点门控：`backend/main.py` 的 `metrics_endpoint`

`GET /api/metrics` 用 `_role_from_header(authorization)`（即
`resolve_role(parse_bearer(authorization))`，与 `auth`/`firewall`/`privilege` 等安全态势工具
脱敏判断同一套角色解析逻辑，参见 `docs/security-posture-tools.md`）解析角色：

- 角色为 `operator` 或 `admin` → 返回 `get_metrics().snapshot()` 的完整 JSON。
- 角色为 `viewer`（或无令牌/令牌不可识别）→ 直接 `403`，
  `detail: "metrics 仅 operator/admin 可访问"`，**不返回任何指标内容**（不是脱敏返回部分
  字段，而是整体拒绝）。

### 2.4 不变量

- **metrics 只读采集，不影响业务结果**：所有 `record_*` 调用都在原有链路的旁路完成，不改变
  请求的执行路径、安全判定或返回给调用方的业务字段；即使指标采集逻辑本身出错，也不应该（且
  当前实现也不会）影响主链路——`record_*` 内部只做计数器自增和 deque 操作，不抛出会向上传播
  的业务异常。
- **进程内内存态、重启清零**：`MetricsCollector` 没有持久化层，多副本部署时各副本指标互相
  独立、不汇总；重启进程或工作进程后所有计数器归零，不依赖也不提供历史趋势查询能力（如需长期
  趋势，应在 `/api/metrics` 之外接入外部时序系统定期拉取快照）。
- **`reset()` 仅用于测试**：生产链路不会调用 `reset()`；测试用它在每个用例前清空单例状态，
  避免用例间指标互相污染。

## 三、测试

`tests/test_rate_limit.py` 覆盖 `RateLimiter` 滑动窗口放行/拒绝/`retry_after`、`max_keys`
淘汰、`ConcurrencyGate` 非阻塞获取/释放、`rate_limit_key` 的令牌/匿名 IP 分支。

`tests/test_metrics.py` 覆盖 `MetricsCollector` 各 `record_*` 方法、`snapshot()` 结构、
P50/P95 计算、`success_rate` 除零保护、`reset()`。

`tests/test_metrics_instrumentation.py` 覆盖 executor 工具耗时/拦截打点、llm_client 成功/失败
打点确实在真实调用链路中触发。

`tests/test_middleware_metrics.py` 覆盖中间件对重端点的限流（`429` + `Retry-After`）、
`/health` 不受限流影响，以及 `GET /api/metrics` 的角色门控（viewer `403`，operator 返回含
`requests`/`blocked`/`rate_limited`/`tools`/`llm` 字段的 JSON）。
