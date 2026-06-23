# 主动巡检与阈值告警

本文档记录在被动 `POST /api/agent/execute` 链路之外新增的**可选后台主动巡检**能力：
一个后台守护线程按固定周期对只读工具采样，套用阈值规则生成告警，写入内存告警池并
落审计日志，最终通过两个只读端点对外暴露。默认关闭，不影响现有单元测试。

## 总体设计

- 巡检逻辑与被动执行链路共用同一套 `ToolExecutor`：调度器不会绕过 `SecurityGuard`
  或 `backend/observability/metrics.py` 的指标采集，只是换了一个固定的、只读的
  调用入口。
- 巡检永远不会触发任何操作类（写/重启/清理）工具，也不会自动修复发现的问题——
  它只产出告警，修复仍由人工经被动链路决策执行。
- 整套能力默认关闭（`AGENT_MONITOR_ENABLED=false`），需要显式开启。
- 告警是进程内内存态，没有持久化；进程重启即清零。

## 配置：`backend/config.py` 的 `get_monitor_settings()`

`MonitorSettings`（frozen dataclass）字段与对应环境变量：

| 字段 | 环境变量 | 默认值 | 取值范围（自动夹紧） |
| --- | --- | --- | --- |
| `enabled` | `AGENT_MONITOR_ENABLED` | `false` | 接受 `1`/`true`/`yes`（大小写不敏感）视为开启 |
| `interval_seconds` | `AGENT_MONITOR_INTERVAL_SECONDS` | `300` | `[10, 86400]` |
| `disk_percent` | `AGENT_MONITOR_DISK_PERCENT` | `90` | `[1, 100]` |
| `failed_login` | `AGENT_MONITOR_FAILED_LOGIN` | `20` | `[1, 199]` |
| `auth_lines` | `AGENT_MONITOR_AUTH_LINES` | `100` | `[1, 200]` |

非法/无法解析的整数环境变量会回退为默认值（不抛异常）。

**`auth_lines` 与 `failed_login` 的约束**：配置加载时会强制
`auth_lines = min(200, max(auth_lines, failed_login + 1))`。也就是说即使
`AGENT_MONITOR_AUTH_LINES` 配置得比阈值还小，最终生效的读取行数也一定大于
`failed_login` 阈值——否则 `lastb` 读取的行数不够，`failed_login_count` 永远
凑不到阈值以上，告警规则形同虚设。这个修正后的值仍受 `200` 上限约束（`auth`
工具 `lines` 参数本身的合法范围是 `[1, 200]`）。

## 调度器：`backend/monitor/scheduler.py` 的 `MonitorScheduler`

```python
MonitorScheduler(executor, alert_store, settings, audit, clock=None)
```

- `CHECK_TOOLS = ("disk", "service", "auth")`：固定且唯一会被调度器调用的三个
  只读工具，不接受动态扩展。
- `run_once()`：单次巡检的核心逻辑。
  1. 生成一个新的 `trace_id`（`uuid4().hex`），与被动链路一致地标识这一轮巡检。
  2. 依次对 `disk{path: "/"}`、`service{}`、`auth{lines: settings.auth_lines}`
     调用 `executor.execute(plan=Plan(intent="inspection", tools=[tool],
     arguments=...), user_id="monitor", raw_query="monitor", trace_id=trace_id,
     role="admin")`——与被动执行路径完全相同的 `ToolExecutor.execute` 入口，
     复用同一套 `SecurityGuard` 校验和 `MetricsCollector` 耗时采集。
  3. 单个工具调用异常会被捕获并记录为 `{"error": str(exc)}`，不会中断其余工具
     的采集，也不会让整轮巡检失败。
  4. 把三个工具的输出交给 `backend/monitor/checks.py` 的 `run_all_checks()`
     生成告警列表。
  5. 每条告警写入 `AlertStore.add()`，并以 `stage="monitor_alert"` 写一条审计
     事件（见下文“审计”一节）。
  6. 记录 `_last_run_at`（采样完成时间）和 `_last_alert_count`（本轮告警数），
     供 `status()` 查询。
- `start()` / `stop()`：以 daemon 线程运行 `_loop()`，内部用
  `threading.Event` 控制退出；`stop()` 会 `join(timeout=5)` 等待线程收尾，
  实现优雅停止。重复调用 `start()`（线程已在运行时）是安全的空操作。
- **tick 异常隔离**：`_loop()` 中的 `run_once()` 包在 `try/except` 里，任何
  一轮巡检抛出的异常都只打日志（`print(..., file=sys.stderr)`），不会杀死
  守护线程；下一轮按 `interval_seconds` 继续执行。
- `running()`：线程是否存活。
- `status()`：返回 `{"enabled", "running", "interval_seconds", "last_run_at",
  "last_alert_count", "checks"}` 的只读快照。

## 阈值规则：`backend/monitor/checks.py`

三个纯函数，输入是工具输出字典，输出是 `Alert` 列表（命中规则才有元素，否则
空列表）：

| 检查函数 | 依据字段 | 触发条件 | 严重级别 | `metric` |
| --- | --- | --- | --- | --- |
| `check_disk(disk_output, threshold_percent)` | `disk_output["used_percent"]` | `used_percent > threshold_percent` | `critical` | `used_percent` |
| `check_service(service_output)` | `service_output["analysis"]["failed_count"]` | `failed_count > 0` | `warning` | `failed_count` |
| `check_auth(auth_output, threshold)` | `auth_output["analysis"]["failed_login_count"]` | `failed_login_count > threshold` | `warning` | `failed_login_count` |

`run_all_checks(outputs, settings)` 依次调用以上三者（`outputs` 是
`{"disk": ..., "service": ..., "auth": ...}`，`settings` 读取
`settings.disk_percent` 和 `settings.failed_login`），把三组告警拼成一个列表
返回。输入字段缺失、类型不符（包括 `bool`，已显式排除）或不是 `dict` 时按未
命中处理，不抛异常。

## 告警数据结构与存储：`backend/monitor/alerts.py`

- `Alert`（frozen dataclass）字段：`severity`、`source`、`metric`、`value`、
  `threshold`、`message`、`timestamp`（默认 `0.0`，由 `AlertStore.add()` 写入
  真实时间戳，调用方不需要也不应该自己填）。
- `AlertStore(max_alerts=500, ttl_seconds=86400.0, clock=None)`：线程安全
  （`threading.RLock`）的内存告警缓冲区。
  - `add(alert)`：盖上当前时间戳后追加，再做一次裁剪（`_prune`）。
  - `recent(limit=100)`：先裁剪过期项，再取最近 `limit` 条，按时间倒序
    （最新的在前）返回字典列表。
  - `reset()`：清空全部告警。
  - `_prune(now)`：先按 `ttl_seconds`（默认 24 小时）过滤掉过期项，再按
    `max_alerts`（默认 500）裁掉最旧的超额项。
  - **没有持久化**：`AlertStore` 完全是进程内列表，没有写文件或数据库，
    **进程重启即清零**，这与审计日志（落 SQLite）的持久语义不同。

## 审计：`monitor_alert` stage

每条告警在写入 `AlertStore` 的同一时刻，调度器会调用
`audit.event(trace_id=trace_id, stage="monitor_alert", user_id="monitor",
status=alert.severity, data={"source", "metric", "value", "threshold",
"message"})`，落入与被动链路相同的审计存储（`backend/audit/logger.py`，
按 `trace_id` 写入 SQLite）。`status` 字段直接复用告警的 `severity`
（`critical`/`warning`），`user_id` 固定为 `"monitor"`，与人工调用的
`user_id` 区分开。可通过既有的 `GET /api/audit/recent?limit=&trace_id=`
端点按 `trace_id` 串联查看某一轮巡检的完整审计记录。

## API 端点

- `GET /api/alerts?limit=100`：返回 `{"alerts": [...]}`，内容是
  `AlertStore.recent(limit)` 的结果。**仅 operator/admin 可访问**——
  `backend/main.py` 用 `_role_from_header(authorization)` 解析角色，不在
  `{"operator", "admin"}` 集合内则返回 `403`（`"alerts 仅 operator/admin 可访问"`）。
  角色解析方式与其余受限端点一致：服务端从 `Authorization` 头部派生角色，
  不信任请求体中的 `user_role`。
- `GET /api/monitor/status`：**对所有角色开放**，无需鉴权，返回
  `MonitorScheduler.status()` 的快照（`enabled`、`running`、
  `interval_seconds`、`last_run_at`、`last_alert_count`、`checks`）。

## 启停集成：`backend/main.py`

- 模块加载时构造 `_monitor_settings = get_monitor_settings()`、
  `_alert_store = AlertStore()`、`_monitor_scheduler = MonitorScheduler(executor,
  _alert_store, _monitor_settings, audit)`——与 `agent`/`executor`/`audit`
  等单例一起在模块级初始化，全应用共享同一个调度器和告警池实例。
- FastAPI 的 `lifespan` 钩子：应用启动时若 `_monitor_settings.enabled` 为真
  才调用 `_monitor_scheduler.start()`（默认关闭则完全不启动后台线程，单元测试
  不受影响）；`finally` 块里无条件调用 `_monitor_scheduler.stop()`，保证应用
  关闭时优雅停止巡检线程，不论它是否真的在运行。

## 不变量

- **只读**：调度器固定且唯一会调用 `disk`/`service`/`auth` 三个只读工具，
  不会、也无法被参数化为调用其他工具，更不会调用任何写/重启/清理类工具。
- **复用既有执行通道**：所有采样调用都经 `ToolExecutor.execute()`，与被动
  `/api/agent/execute` 链路共用同一套 `SecurityGuard` 校验和指标采集，调度器
  本身不绕过、不重新实现任何安全或可观测性逻辑。
- **不自动修复**：巡检只产出告警，从不基于告警结果触发任何修复或操作类调用；
  发现问题后的处置仍需人工经被动链路决策。
- **默认关闭**：`AGENT_MONITOR_ENABLED` 默认 `false`，不开启巡检线程，对现有
  行为和测试零侵入。
- **tick 异常隔离**：单次工具调用异常或单轮 `run_once()` 异常都不会让后台线程
  整体崩溃，下一轮巡检按周期继续。
- **优雅停止**：`stop()` 通过 `threading.Event` 通知循环退出并 `join` 等待，
  应用关闭时 `lifespan` 的 `finally` 块保证调度器一定会被停止。
- **告警内存态、重启清零**：`AlertStore` 没有持久化，进程重启后历史告警全部
  丢失；需要长期追溯应查审计日志中的 `monitor_alert` 事件。

## 相关文件

- `backend/monitor/alerts.py`：`Alert` / `AlertStore`。
- `backend/monitor/checks.py`：三条阈值规则与 `run_all_checks`。
- `backend/monitor/scheduler.py`：`MonitorScheduler`。
- `backend/config.py`：`MonitorSettings` / `get_monitor_settings()`。
- `backend/main.py`：`lifespan` 启停集成、`GET /api/alerts`、
  `GET /api/monitor/status`。
