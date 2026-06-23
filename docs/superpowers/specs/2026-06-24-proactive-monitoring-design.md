# 主动巡检与阈值告警 设计文档

- 日期：2026-06-24
- 分支：`feature/proactive-monitoring`
- 范围：把 Agent 从"被动问答"升级为"主动持续监控"——定时跑只读感知工具，按阈值判定，命中产出告警（Q1）。

## 1. 背景与目标

现有 Agent 纯被动：用户问才查。本设计加一个进程内后台巡检循环，周期性运行只读感知工具、按
规则阈值判定主机健康，命中阈值即生成告警并写审计，形成"感知→判定→告警"的持续闭环。这是
"智能运维"从问答式到持续式的关键一步。

### 已锁定的设计决策

| 决策点 | 取定 |
|---|---|
| 调度方式 | 进程内后台守护线程循环（非 asyncio，避免阻塞事件循环；工具是同步 subprocess） |
| 告警判定 | 纯规则阈值（确定、可测、零 LLM 成本） |
| 默认开关 | `AGENT_MONITOR_ENABLED` 默认 false，需显式开启 |
| `/api/alerts` 访问 | 仅 operator/admin（其余 403）；`/api/monitor/status` 开放（仅良性元数据） |

## 2. 非目标（YAGNI）

- 不做外部通知（邮件/webhook/短信）——告警仅内存存储 + 查询 + 审计；外部通知列为路线图。
- 不引入外部调度器/cron/Redis——进程内线程循环，单进程内存态。
- 巡检**只运行只读工具**，绝不触发任何操作类（状态变更）工具或自动修复。
- 不用 LLM 判定告警（纯阈值规则）。
- 不做告警去重/抑制/聚合的复杂策略（v1 仅"命中即记"，靠 TTL + 上限控量）。

## 3. 组件（`backend/monitor/` 新包）

### 3.1 `alerts.py` — 告警模型与存储
- `Alert`（frozen dataclass）：`severity`（`"warning"`/`"critical"`）、`source`（检查项名，如 `"disk"`）、
  `metric`（如 `"disk_usage_percent"`）、`value`、`threshold`、`message`、`timestamp`。
- `AlertStore(max_alerts=500, ttl_seconds=86400, clock=None)`：线程安全（`RLock`）内存存储。
  - `add(alert)`：追加；按 `max_alerts` 上限淘汰最旧、按 TTL 清理过期。
  - `recent(limit=100) -> list[dict]`：最近告警（新→旧）。
  - `reset()`：测试用。
  - 重启清零，不持久化（与 metrics/session 一致）。

### 3.2 `checks.py` — 阈值规则（纯函数）
每个检查是纯函数：输入相关工具的输出 dict + 阈值配置，输出 `list[Alert]`。

- `check_disk(disk_output, threshold_percent) -> list[Alert]`：磁盘使用率超过阈值（默认 90）→ critical。
- `check_service(service_output) -> list[Alert]`：`analysis.failed_count > 0` → warning（附 failed 数）。
- `check_auth(auth_output, threshold) -> list[Alert]`：`analysis.failed_login_count > threshold`（默认 20）→ warning。
  - ⚠️ 关键约束：`auth_tool` 只统计它读到的 `lines` 行（默认 20、上限 200），`failed_login_count`
    至多等于读取行数。若读取行数 ≤ 阈值，`> threshold` 永远无法触发。故巡检调用 auth 时必须传
    `{"lines": settings.auth_lines}`，且 `get_monitor_settings()` 保证 `auth_lines >= 阈值 + 1`
    （并受 auth 的 200 上限约束）。
- `run_all_checks(outputs: dict[str, dict], thresholds) -> list[Alert]`：按工具名分发到各检查并汇总。

> 各工具输出的确切字段名（如 `disk` 工具的使用率字段、`service`/`auth` 的 `analysis` 键）在
> writing-plans 阶段读工具源码核定；检查对缺失/异常字段做空值兜底，不因单项异常整体失败。

### 3.3 `scheduler.py` — 后台巡检调度
- `MonitorScheduler(executor, alert_store, settings, audit, clock=None)`：接收 `ToolExecutor`
  （**不是** `ToolRegistry`），让巡检的工具调用复用 executor 既有的安全 guard 校验与 metrics
  打点路径，而不是绕过它们直调 registry。
  - `run_once() -> list[Alert]`：对固定只读检查工具逐个用
    `executor.execute(Plan(intent="inspection", tools=[tool], arguments=args), user_id="monitor", raw_query="monitor", role="admin")`
    采数（每个工具一个单工具 Plan，带各自参数），取 `execution.result.get(tool, {})` 汇总成
    `outputs` → `run_all_checks(outputs, thresholds)` 判定 → 命中告警 `alert_store.add(...)` +
    写审计（stage `monitor_alert`）→ 返回本轮告警。固定检查工具与参数：
    - `disk` → `{"path": "/"}`
    - `service` → `{}`
    - `auth` → `{"lines": settings.auth_lines}`

    三者全部 ∈ `LOW_RISK_TOOLS`、只读。`role="admin"` 是可信内部系统主体（巡检由系统发起，
    非用户请求），保证只读工具过 guard；`user_id="monitor"` 用于审计归属。每个 `execute` 调用
    用 try/except 隔离，单个工具异常被捕获、记 skip、不影响其余检查。`run_once` 为每轮生成一个
    `trace_id` 串联审计。可独立调用（不依赖定时器），便于测试。
  - `start()`：若已运行则忽略；否则启动 daemon 线程，循环 `run_once()` 后 `Event.wait(interval)`；
    `run_once` 抛错只记录不退出循环。
  - `stop()`：set event + join（带超时）。
  - `status() -> dict`：`enabled`、`running`、`interval_seconds`、`last_run_at`、`last_alert_count`、
    `checks`（检查项名列表）。

## 4. 调度接入（`backend/main.py` lifespan）

现有 `lifespan` 中，`init_db()` 之后：构造 `MonitorScheduler`（用模块级 `executor`（`ToolExecutor`）、
新的 `AlertStore` 单例、`get_monitor_settings()`、`AuditLogger`）。若 `settings.enabled` 则
`scheduler.start()`；`yield` 之后 `scheduler.stop()`（放在 `finally`/`async with` 退出后，保证优雅停）。

## 5. 暴露端点（`backend/main.py`）

- `GET /api/alerts?limit=`：`_role_from_header` 解析角色，非 operator/admin → `403`；否则返回
  `{"alerts": alert_store.recent(limit)}`。
- `GET /api/monitor/status`：开放，返回 `scheduler.status()`（仅 enabled/running/interval/
  last_run_at/last_alert_count/checks，无主机敏感明细）。

## 6. 配置（`backend/config.py` `get_monitor_settings()`）

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `AGENT_MONITOR_ENABLED` | `false` | 是否启用后台巡检 |
| `AGENT_MONITOR_INTERVAL_SECONDS` | `300` | 巡检间隔（clamp `[10, 86400]`） |
| `AGENT_MONITOR_DISK_PERCENT` | `90` | 磁盘使用率告警阈值（clamp `[1, 100]`） |
| `AGENT_MONITOR_FAILED_LOGIN` | `20` | 失败登录数告警阈值（clamp `[1, 199]`，须小于 auth 200 行上限以便能触发） |
| `AGENT_MONITOR_AUTH_LINES` | `100` | 巡检调用 `auth` 时读取的行数；clamp `[1, 200]` |

`MonitorSettings`（frozen dataclass）承载上述值。`get_monitor_settings()` 在 clamp 后强制
`auth_lines = max(auth_lines, failed_login_threshold + 1)`（再对 200 上限取 min），确保
"读取行数 > 阈值"恒成立，否则失败登录告警永远不会触发（见 §3.2 关键约束）。

## 7. 关键不变量

- **巡检只运行只读工具，绝不触发操作类工具或自动修复**——固定只读子集（`disk`/`service`/`auth`），
  与"多步闭环只自动跑只读工具"同一红线，保证无自主状态变更。
- **巡检经 `ToolExecutor.execute` 执行，不绕过安全 guard 与 metrics**——巡检的工具调用与用户
  请求走同一条 executor 路径，复用安全校验与工具耗时打点，而不是直调 `registry.call`。
- 告警判定是确定性规则，不依赖 LLM；`run_once` 不调用 LLM、无 token 成本。
- 后台线程 tick 出错只记录不杀循环；不重叠执行；lifespan 退出时优雅停。
- 告警存储进程内内存态、重启清零；不持久化、多副本不汇总（如需长期趋势接外部系统）。
- `/api/alerts` 含主机健康/失败登录等敏感信息，按 operator/admin 门控，与项目"低权限少暴露"
  主线一致。

## 8. 测试计划（unittest，TDD 先行）

- `checks`：各检查命中/不命中（用工具样本 dict）、缺失字段兜底不崩溃、`run_all_checks` 汇总。
- `AlertStore`：`add`/`recent` 顺序、`max_alerts` 上限淘汰、TTL 过期清理（注入 clock）。
- `MonitorScheduler.run_once`：注入 fake executor（`execute` 返回越阈值样本 result）→ 产出对应
  告警并入库；断言 executor 收到的都是固定只读工具的单工具 Plan（disk/service/auth，含正确参数
  如 `auth` 的 `lines`），不含操作类工具；单工具 `execute` 异常被吞、其余检查照常。
- `get_monitor_settings`：默认值、clamp、以及 `auth_lines >= failed_login_threshold + 1` 强制
  关系（设小 auth_lines + 大阈值时 auth_lines 被抬高）。
- `MonitorScheduler.start/stop`：start 后 running=True、stop 后线程结束（可用极短 interval +
  注入 clock 或直接断言线程生命周期，不依赖真实计时）。
- `/api/alerts`：viewer/无令牌 `403`，operator 返回 `alerts` 列表。
- `/api/monitor/status`：返回含 enabled/running/checks 的结构。

## 9. 风险与缓解

- **后台线程阻塞/泄漏**：daemon 线程 + `Event.wait`（可中断），`stop()` join 带超时；tick 异常隔离。
- **工具在低权限下读不到**（如 auth 的 lastb）：沿用工具自身的优雅降级，检查对空值兜底。
- **巡检与请求争用**：只读工具、间隔默认 300s、并发量低；如需可后续接入 Task(限流) 的并发预算。
- **默认关**：避免开发/测试或演示环境意外跑后台真实命令；显式开启才生效。
