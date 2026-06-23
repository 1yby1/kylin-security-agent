# CLAUDE.md

本文档用于指导 Claude Code 或其他代码助手在本仓库中工作。后续修改代码、更新文档或新增报告时，应优先遵守这里的项目约束。

## 项目概述

本项目是软件杯智能运维 Agent 原型：系统接收自然语言运维问题，选择合适工具，执行白名单 Linux/Windows 命令模板，并返回结构化结论和完整审计追踪。

目标部署环境是麒麟高级服务器 V11 + LoongArch；开发环境可能是 Windows，因此命令执行、路径处理和最小权限逻辑都需要保持平台感知。

当前 `backend/mcp_tools/builtin.py` 共注册 17 个工具：`system`、`process`、
`process.kill`、`network`、`log`、`service`、`service.restart`、`temp.clean`、
`disk`（系统感知与受控操作，详见 `docs/system-perception-tools.md` 和
`docs/controlled-operation-tools.md`）；`network.diagnostics`、`network.config`、
`disk.large_files`、`disk.top_dirs`、`package.repo`（只读诊断工具）；以及
`auth`、`firewall`、`privilege` 三个只读安全态势感知工具（`category="security"`，
详见 `docs/security-posture-tools.md`）。新增或调整工具时应同步更新相关文档。

## 文档和报告语言

- 根目录文档、`docs/` 下的项目说明、功能报告、测试报告和后续新增报告默认使用中文。
- 代码标识符、API 路径、环境变量、命令和 JSON 字段保持原文。
- 如果引用外部英文术语，应优先给出简洁中文说明。

## 本地运行

```bash
pip install -r requirements.txt
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

FastAPI 应用会在 `/` 返回 `frontend/index.html`，并将 `frontend/` 挂载到 `/static`。前端运行时通过 CDN 加载 Vue 3，不依赖 Vite 构建产物；`frontend/package.json` 中的 Vite 脚本仅作为可选开发入口。

## LLM 配置

LLM 是可选能力。未配置时，Planner 会自动回退到本地关键词规则。

```bash
export LLM_PROVIDER=deepseek
export DEEPSEEK_API_KEY=...
export LLM_MODEL=deepseek-chat
```

或：

```bash
export LLM_PROVIDER=qwen
export QWEN_API_KEY=...
export LLM_MODEL=qwen-plus
```

可选配置：

```bash
export LLM_API_KEY=...
export LLM_BASE_URL=...
export LLM_TIMEOUT_SECONDS=20
```

## 测试

自动化测试使用 Python `unittest`。

默认入口：

```bash
python -m unittest discover -v
```

`tests/__init__.py` 会在测试包加载时禁用真实 LLM 环境变量，避免单元测试误触发外部 API 调用。

当前没有 lint/format 配置，不要凭空添加或声称存在 lint 命令。

## 核心链路

`POST /api/agent/execute` 的请求链路：

1. **规划**：`backend/agent/planner.py` 调用 `LLMClient.analyze()` 生成固定 JSON 规划；如果 LLM 不可用、异常或返回非 JSON，则使用本地关键词规则。
2. **安全校验**：`backend/security/guard.py` 执行工具白名单、参数 schema、参数值、危险路径、危险命令、用户角色和二次确认校验。
3. **闭环分流**：`AgentOrchestrator.run()` 在首次规划之后分流——若请求已带 `approved=True`，或首次规划的工具不全是只读工具（不在 `LOW_RISK_TOOLS` 内），走原有 `_run_single` 单次执行；否则走 `_run_loop` 多步推理闭环（最多 `AGENT_MAX_REASONING_STEPS` 步，默认 3），每步只自动执行只读工具，遇到下一步建议含操作类工具立即停手并产出 `suggested_actions`。详见 `docs/multi-step-reasoning.md`。
4. **工具执行**：`backend/agent/executor.py` 通过 `ToolRegistry` 调用工具 handler。工具注册集中在 `backend/mcp_tools/builtin.py`。单次路径和闭环路径的每一步都要经过这一层。
5. **结果总结**：`LLMClient.conclude()` 将工具结果以隔离包装的 `observed_data` 字段总结为结构化结论；LLM 不可用时由 `AgentOrchestrator._fallback_conclusion()` 本地兜底。详见 `docs/telemetry-injection-defense.md`。
6. **审计追踪**：`backend/audit/logger.py` 按 `trace_id` 写入 SQLite 审计事件；闭环路径额外产生 `reasoning_step`、`injection_scan`、`suggested_action` 阶段。

## 关键不变量

- **所有系统命令必须通过命令模板执行。**  
  只能使用 `backend/mcp_tools/command_runner.py` 中的 `run_template()` 或 `run_optional_template()`。不要在其他模块直接调用 `subprocess`，也不要拼接 shell 字符串。

- **Windows 和 Linux 模板不是同一套。**  
  Windows 模板主要服务开发调试，Linux/Kylin 模板服务生产部署。新增工具时必须考虑 `os.name` 分支和不同平台命令可用性。

- **安全校验必须早于工具执行。**  
  链路顺序是规划 -> 安全校验 -> 工具执行。不要把风险决策下放给 LLM 或前端确认。

- **风险策略以后端为准。**  
  工具定义中的 `risk_level`、`backend/security/rules.py` 中的 `RISK_POLICIES`、危险路径和危险命令模式是权威策略。LLM 的 `risk_hint` 只能作为参考字段。

- **只读扫描工具按目标路径动态评级。**  
  `disk.large_files`、`disk.top_dirs`、`package.repo`（`backend/security/rules.py` 的 `READ_SCAN_TOOLS`）虽是只读低风险工具，但 `SecurityGuard._scan_path_risk` 会校验其目标路径：路径在 `SAFE_SCAN_DIRS` 白名单内时维持 `low`（viewer 可用），否则升级为 `medium`（需 operator/admin + 二次确认），防止低权限用户递归扫描任意路径导致信息泄露或 DoS。两个磁盘扫描工具还有 `_MAX_SCAN_ENTRIES` 遍历预算上限；`package.repo` 对仓库 URL 中的内嵌凭据做脱敏。

- **安全态势工具对低权限调用方按角色脱敏。**  
  `auth`、`firewall`、`privilege` 返回侦察级明细（来源 IP、开放端口清单、SUID 文件、UID0/空密码账户名）。`backend/security/redaction.py` 的 `redact_security_tool_output` 在 `ToolExecutor` 返回结果前按角色脱敏：operator/admin 得全量，viewer 只得计数与风险标志（带 `detail_redacted: true`）。脱敏只作用于返回值，审计与步骤引用仍保留全量。详见 `docs/security-posture-tools.md`。

- **会话上下文按主体绑定，且 ID 由服务端签发。**  
  多轮会话（`backend/agent/session_context.py`）把上一轮实体注入下一轮规划。`ConversationState.owner` 绑定调用方主体（`backend/security/auth.py` 的 `session_principal`：无令牌为 `anon`，有令牌为 token 的 sha256 派生值）。`resolve_session_id` 只认"已存在且 owner 匹配"的 ID，否则签发新随机 uuid——调用方不能自选/猜测/冒用会话；`context` 对 owner 不匹配返回空。存储有 `max_sessions` 上限并按 `updated_at` LRU 淘汰。`/api/agent/plan` 与 `execute` 一致地按 owner 注入会话上下文（只读不写）。详见 `docs/session-context-security.md`。

- **最小权限逻辑只在 Linux 上降权。**  
  `backend/security/least_privilege.py` 会在 root 进程且目标用户可解析时为子进程设置 `user`、`group` 和 `extra_groups`。`AGENT_STRICT_LEAST_PRIVILEGE=true` 时，如果 root 进程无法解析低权限用户，应拒绝启动命令。

- **LLM JSON 合约需要稳定。**  
  Prompt 在 `backend/agent/prompt.py`。规划 JSON 必须包含 `intent`、`tools`、`arguments` 等字段；分析 JSON 必须包含 `conclusion`、`status`、`evidence`、`recommendations` 等字段。调用方需要容忍代码块包裹 JSON 和不支持 `response_format` 的模型。

- **多步推理闭环只能自动执行只读工具。**
  `AgentOrchestrator._run_loop` 每一步都校验下一步规划：只要工具不在 `backend/security/rules.py` 的 `LOW_RISK_TOOLS` 内，就不会自动执行，而是收进 `suggested_actions` 并停手。`Planner.plan_next` 本身允许返回操作类工具——只读边界是编排器强制的，不是规划器的职责，不要把这条边界误移到 `Planner` 里。`LOW_RISK_TOOLS` 目前包含 `system`、`process`、`network`、`log`、`service`、`disk` 以及 `auth`、`firewall`、`privilege` 三个安全感知工具；新增只读工具时记得同步加入该集合。

- **被观测数据（observed_data）隔离且不可信，不得当作指令。**
  工具执行结果在喂给 LLM 前必须经过 `backend/security/sanitizer.py` 的 `build_observation_block()` 清洗、截断并包装为 `<OBSERVED_DATA ... trust="untrusted" ...>` 隔离块；`ANALYSIS_SYSTEM_PROMPT` 显式声明该字段只能作为分析素材。任何模块都不应把工具结果原文直接拼进 prompt，也不应认为 `observed_data` 的内容可以改变角色、跳过校验或代表用户确认。

- **限流/并发闸是 `SecurityGuard` 之外的额外一道闸，不替代安全校验。**
  `backend/main.py` 的 `rate_limit_middleware` 在重端点（`POST /api/agent/execute`、`/api/agent/plan`、`/api/security/evaluate`、`/api/tools/{tool_name}`）前用 `backend/security/rate_limit.py` 的 `RateLimiter`（按主体/匿名 IP 滑动窗口，`max_keys` 防内存膨胀）和 `ConcurrencyGate`（非阻塞并发上限）做频率/并发预算判断，超限返回 `429`/`503`。它只做"是否超预算"判断，不做工具白名单、参数、危险路径/命令、角色或二次确认校验；即使关闭限流（`AGENT_RATE_LIMIT_ENABLED=false`），`backend/security/guard.py` 的全部校验依然在工具执行前生效。`backend/observability/metrics.py` 的 `MetricsCollector` 只读采集请求/限流/拦截计数、工具耗时分位数和 LLM 成功率，进程内内存态、重启即清零，不影响业务结果。详见 `docs/self-protection-observability.md`。

- **主动巡检只跑只读工具、经 executor、不自动修复、默认关。**
  除被动 `POST /api/agent/execute` 链路外，`backend/monitor/scheduler.py` 的 `MonitorScheduler` 是一个**可选**的后台守护线程：`AGENT_MONITOR_ENABLED=true` 时在 lifespan 启动，按 `AGENT_MONITOR_INTERVAL_SECONDS` 周期对固定只读工具（`disk`/`service`/`auth`，`CHECK_TOOLS`）经 `ToolExecutor.execute(..., user_id="monitor", role="admin")` 采样（复用 guard + metrics，不直调 registry），用 `backend/monitor/checks.py` 的阈值规则判定，命中即写 `AlertStore`（内存态、上限+TTL、重启清零）并落 `monitor_alert` 审计。巡检**绝不触发操作类工具或自动修复**；`get_monitor_settings()` 强制 `auth_lines >= failed_login + 1`（否则失败登录告警永不触发）；tick 异常隔离、lifespan `finally` 优雅停。默认关闭，对现有行为零侵入。详见 `docs/proactive-monitoring.md`。

## API 表面

- `POST /api/agent/execute`：完整 Agent 链路。响应在原有字段之外新增 `steps`（多步推理闭环每步摘要，单次路径下为空列表）和 `suggested_actions`（闭环中被拦下、未执行的操作类工具建议，需二次确认才能真正执行）。
- `POST /api/agent/plan`：只规划，不执行工具。
- `POST /api/security/evaluate`：只执行安全校验。
- `GET /api/tools`：查看工具列表和 manifest，当前共 17 个工具。
- `GET /api/mcp/tools`：查看 MCP-like manifest。
- `GET /api/tools/{tool_name}`：查看工具元数据。
- `POST /api/tools/{tool_name}`：直接调用工具，仍需通过安全校验。
- `GET /api/llm/status`：查看 LLM 配置状态。
- `POST /api/llm/test`：测试 LLM 规划能力。
- `GET /api/security/runtime`：查看运行身份和最小权限状态。
- `GET /api/audit/recent?limit=&trace_id=`：查询审计事件。
- `GET /api/metrics`：查看进程内指标快照（请求数、限流/拦截计数、工具耗时 P50/P95、LLM 成功率），仅 operator/admin 可访问，viewer 返回 403。详见 `docs/self-protection-observability.md`。
- `GET /api/alerts?limit=`：查看后台主动巡检产生的告警（内存态、重启清零），仅 operator/admin 可访问，否则返回 403。详见 `docs/proactive-monitoring.md`。
- `GET /api/monitor/status`：查看巡检状态（`enabled`/`running`/`interval_seconds`/`last_run_at`/`last_alert_count`/`checks`），开放访问、仅良性元数据。详见 `docs/proactive-monitoring.md`。

`POST /api/agent/execute`、`/api/agent/plan`、`/api/security/evaluate` 以及 `POST /api/tools/{tool_name}` 这几个重端点带限流（按主体/匿名 IP 滑动窗口）和并发闸保护，超限返回 `429`（带 `Retry-After`）或 `503`；详见 `docs/self-protection-observability.md`。

## 修改建议

- 修改某个阶段前，先阅读 `docs/` 下对应设计文档。
- 变更工具时，同步检查 `backend/mcp_tools/builtin.py`、工具 handler、参数 schema、安全规则和测试。
- 变更安全策略时，补充能覆盖允许、阻断、二次确认和角色权限的测试。
- 变更 LLM 规划字段时，同步更新 prompt、解析逻辑、测试和相关文档。
- 遇到无关的未提交改动，不要回滚；只处理当前任务需要的文件。
