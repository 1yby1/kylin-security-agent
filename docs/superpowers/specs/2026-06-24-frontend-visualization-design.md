# 前端可视化 设计文档

- 日期：2026-06-24
- 分支：`feature/frontend-visualization`
- 范围：在现有 Vue 3（CDN）单页前端上，把已实现的后端深度能力（多步推理闭环、修复建议、角色脱敏、自我可观测指标、主动巡检告警）做成可视化界面。

## 1. 背景与目标

后端已具备多步推理闭环、`suggested_actions`、角色脱敏、`/api/metrics`、`/api/alerts` 等能力，
但前端只展示了 trace/风险/结论，且 `steps` 渲染形状与后端实际返回不一致（merge 遗留）。本设计
补齐三块可视化，让这些深度能力"看得见"。

现有前端：`frontend/index.html`（Vue 模板 + 内联 SVG 图标）、`frontend/app.js`（`createApp`，4 个
页面 chat/dashboard/tools/audit）、`frontend/styles.css`。Vue 3 走 CDN，无构建、无前端测试框架。

### 已锁定的设计决策

| 决策点 | 取定 |
|---|---|
| `suggested_actions` 确认执行 | 预填自然语言指令 + 勾选 approved + 跳回对话页，人工复核后发送（零后端改动、人在环路） |
| 验证方式 | agent-browser 起本地后端逐页截图核对（无前端单测） |

## 2. 非目标（YAGNI）

- 不引入前端构建工具/打包/框架升级（仍 CDN Vue 3）。
- 不改任何后端代码（除非发现纯前端无法完成——目前评估不需要）。
- 不做实时刷新/WebSocket/轮询，指标与告警靠手动刷新按钮。
- 不做图表库（用纯 CSS 条形/数字呈现 P50/P95，避免再引 CDN 依赖）。
- `suggested_actions` 不自动执行操作类工具——只预填、由人确认发送。

## 3. 三块可视化

### 3.1 对话结果增强（改 chat 页）

**(a) 修 `steps` 渲染**：`AgentResponse.steps` 是多步推理闭环步骤，形状为
`{step:int, tools:list[str], source:"llm"|"rules", observation_summary:str, injection_suspected:bool}`
（来源 `backend/agent/orchestrator.py` `_run_loop`；单次路径 `_run_single` 返回空列表）。现有模板
按 `step.id/tool/status` 渲染（executor 编排步形状），与之不符。改为按闭环形状渲染时间线：
- 标题"多步推理闭环 · N 步"；每步：`第 {step} 步`、`tools` 工具 chips、`source` 徽标（llm/rules）、
  `observation_summary` 文本；`injection_suspected===true` 时显示红色"⚠ 疑似注入(已隔离)"标记。
- 实现前再读一次 `orchestrator.py` 确认字段名。

**(b) `suggested_actions` 面板**：`AgentResponse.suggested_actions` 为
`[{tool, arguments, reason}]`。新增面板"建议的修复动作（需确认）"，每条展示工具名、原因、参数；
带"确认执行"按钮。点击 → `applySuggestion(action)`：
- 按工具生成自然语言指令填入 `query`：`service.restart`→`重启 {service_name} 服务`；
  `process.kill`→`终止 {pid} 号进程`；`temp.clean`→`清理临时目录 {path}`；其它 →
  `执行 {tool}`（附参数）。
- 勾选 `approved=true`，`page="chat"`，滚动/聚焦到输入框。**不自动发送**，由用户复核后点发送。

**(c) `detail_redacted` 徽标**：当 `chatResult.result` 任一工具结果含 `detail_redacted===true` 时，
在结果区显示徽标"明细已按角色脱敏 · 需 operator 令牌查看全量"。

### 3.2 指标仪表盘（新页 `metrics`）

新增导航项"指标看板"。`loadMetrics()` 调 `GET /api/metrics`（带令牌；非 operator/admin 返回
`403` → 显示"需 operator/admin 令牌"提示，不报错）。渲染 `snapshot()` JSON：
- 顶部 KPI 卡：总请求数（`requests` 求和）、`blocked`、`rate_limited`、`concurrency_rejected`
  （若存在）。
- LLM 卡：`llm.success`/`failure`/`success_rate`。
- 工具耗时表：每工具 `count` / `p50_ms` / `p95_ms`（用 CSS 条形按 p95 相对长度呈现）。
- 端点请求表：`requests` 的 endpoint → count。
- 刷新按钮。

> 字段以 `backend/observability/metrics.py` `snapshot()` 实际结构为准，实现前核对；对缺失字段
> （如某些版本无 `concurrency_rejected`）做存在性判断，不硬假设。

### 3.3 巡检告警面板（新页 `monitor`）

新增导航项"巡检告警"。两个请求：
- `loadMonitorStatus()` → `GET /api/monitor/status`（开放）：状态卡显示
  `enabled`/`running`/`interval_seconds`/`last_run_at`/`last_alert_count`/`checks`。
- `loadAlerts()` → `GET /api/alerts?limit=`（带令牌；`403` → 提示需 operator/admin）：告警列表，
  每条按 `severity`（critical 红 / warning 黄）配色，显示 `source`、`message`、`value` vs
  `threshold`、`timestamp`。
- 刷新按钮；空告警时友好空态（"暂无告警" / "巡检未开启时无告警"）。

## 4. 改动文件

| 文件 | 改动 |
|---|---|
| `frontend/app.js` | `navItems` 加 `metrics`/`monitor`；`data` 加 `metrics`/`alerts`/`monitorStatus`/loading 标志；方法 `loadMetrics`/`loadAlerts`/`loadMonitorStatus`/`applySuggestion`/`hasRedaction`；`switchPage` 分发；修 steps 相关（若有 helper 不匹配则调整） |
| `frontend/index.html` | chat 页：重写 steps 模板、加 suggested_actions 面板、加 redaction 徽标；新增 metrics 页、monitor 页模板；导航项图标 |
| `frontend/styles.css` | 新增 KPI 卡、工具耗时条、告警条、suggested-action、redaction 徽标、注入告警标记的样式（沿用现有设计语言/变量） |

## 5. 数据流与错误处理

- 所有请求复用现有 `api(path, options)`（自动带 `Authorization: Bearer <token>`）。
- 受限端点（`/api/metrics`、`/api/alerts`）在无令牌/viewer 时返回 `403`：前端捕获并显示
  "需 operator/admin 令牌"的友好提示，而不是把 `403` 当异常红字。需要把 `api()` 的非 2xx 处理
  调整为：对受限页能区分 403 与其它错误（方案：受限加载方法里 `try/catch`，对 `403` 显示提示态）。
- 其它网络错误沿用现有 `{ error: String(error) }` 兜底显示。

## 6. 验证（agent-browser）

无前端单测。实现后由控制方起本地后端（配 `AGENT_OPERATOR_TOKEN`、可选
`AGENT_MONITOR_ENABLED=true`），用 agent-browser 打开 `http://127.0.0.1:8000`，逐页截图核对：
1. chat：发一条诊断 query，看闭环 steps 时间线、（若有）suggested_actions 面板、脱敏徽标。
2. metrics：填 operator 令牌，看 KPI/工具耗时/LLM 成功率渲染；不填令牌看 403 提示态。
3. monitor：看状态卡 + 告警列表（可先用 viewer 看 status，再用 operator 看 alerts）。
截图留档于 `docs/`（或附在完成说明）。

## 7. 不变量 / 约束

- 纯前端改动，不改后端 API 或安全模型；脱敏/角色门控仍由后端决定，前端只如实呈现。
- `suggested_actions` 绝不自动执行——仅预填，由人确认发送（与"二次确认"安全主线一致）。
- 沿用现有 CSS 设计语言与 Vue 单文件结构，不引入新构建依赖。
- 受限端点 `403` 视为正常的"权限不足"提示态，不是错误。

## 8. 风险与缓解

- **steps 形状以后端为准**：实现前读 `orchestrator.py` 确认 `_run_loop` 步骤字段，避免再次错位。
- **metrics/alerts 字段漂移**：实现前读 `metrics.py`/`alerts.py` 的实际 JSON；对可选字段做存在性判断。
- **无单测**：靠 agent-browser 截图 + 控制方目视核对；前端逻辑尽量薄（取数 + 模板渲染）。
- **与用户并行改动冲突**：仅改 frontend/ 三个文件；若遇用户对同文件的未提交改动，先合并不回滚。
