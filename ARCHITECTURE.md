# 架构说明

本文档说明软件杯智能运维 Agent 的核心架构、运行链路和关键约束。

## 技术栈

- 前端：静态 Vue 3 页面，当前通过 CDN 加载 Vue。
- 后端：Python FastAPI。
- Agent 调度：Python Planner、Executor 和 Orchestrator 模块。
- MCP-like 工具层：Python 工具函数封装系统概览、进程、端口、日志、服务和磁盘诊断能力。
- 数据库：当前使用 SQLite 初始化基础表，后续可扩展到 PostgreSQL。
- 审计日志：JSONL 文件，按 `trace_id` 串联全链路事件。
- 大模型：优先通过 DeepSeek 或 Qwen API 接入，后续可评估本地部署。
- 系统命令：只能通过白名单命令模板调用 `subprocess`。
- 部署目标：麒麟高级服务器 V11，LoongArch 架构。

## 运行链路

1. 前端向 `POST /api/agent/execute` 提交用户运维请求。
2. `backend.agent.planner.Planner` 选择意图、工具和参数。
3. 如果配置了 `LLM_PROVIDER`、API Key 和模型参数，Planner 会调用 DeepSeek/Qwen 生成固定 JSON 规划。
4. 如果 LLM 不可用、返回异常或返回非 JSON，Planner 使用本地关键词规则兜底。
5. `backend.security.guard.SecurityGuard` 执行工具白名单、参数 schema、参数值、危险路径、危险命令、角色权限、二次确认和审计要求校验。
6. `backend.agent.executor.ToolExecutor` 在安全校验通过后调用注册工具。
7. 工具函数通过 `backend/mcp_tools/command_runner.py` 中的命令模板执行允许的系统命令。
8. `backend.agent.llm_client.LLMClient.conclude()` 或本地兜底逻辑生成结构化结论。
9. `backend.audit.logger.AuditLogger` 写入 JSONL 审计事件。

## LLM 环境变量

DeepSeek 示例：

```bash
export LLM_PROVIDER=deepseek
export DEEPSEEK_API_KEY=...
export LLM_MODEL=deepseek-chat
```

Qwen 示例：

```bash
export LLM_PROVIDER=qwen
export QWEN_API_KEY=...
export LLM_MODEL=qwen-plus
```

通用覆盖项：

```bash
export LLM_API_KEY=...
export LLM_BASE_URL=...
export LLM_TIMEOUT_SECONDS=20
```

## 命令白名单

命令模板集中在 `backend/mcp_tools/command_runner.py`。

新增系统命令时必须添加具名模板，并通过参数校验渲染；不能从用户输入拼接 shell 字符串，也不能在工具中直接调用 `subprocess`。

## 工具注册

工具元数据和 handler 在 `backend/mcp_tools/builtin.py` 中通过 `ToolRegistry` 注册。

FastAPI 通过 `GET /api/mcp/tools` 暴露 MCP-like manifest，包含工具名称、描述、分类、参数 schema、命令模板、风险等级和是否只读。

当前工具分为两类：

- 只读感知工具：`system`、`process`、`process.top`、`process.detail`、`network`、`network.port_lookup`、`log`、`log.search`、`service`、`disk`。
- 受控操作工具：`service.restart`、`temp.clean`、`process.kill`。

## 安全策略

所有工具调用都必须先经过 `backend/security/guard.py`。

安全校验包括：

- 工具是否注册且启用。
- 工具参数是否满足 schema。
- 参数字符串是否包含危险字符。
- 清理、删除、写入等场景是否触碰危险路径。
- 文件日志读取是否位于允许日志目录内。
- 请求是否匹配禁止或危险命令模式。
- 用户角色是否允许执行当前风险等级操作。
- 中高风险操作是否完成二次确认。

风险等级为 `low`、`medium`、`high`、`prohibited`。工具定义中的 `risk_level` 和 `backend/security/rules.py` 中的 `RISK_POLICIES` 是后端权威策略；LLM 返回的 `risk_hint` 只作为规划信息，不能替代后端安全判断。

权限校验使用的角色由服务端绑定，不信任请求体：HTTP 入口从 `Authorization: Bearer <token>` 解析角色（`backend/security/auth.py` 的 `resolve_role`，令牌→角色映射由 `AGENT_ADMIN_TOKEN`、`AGENT_OPERATOR_TOKEN`、`AGENT_VIEWER_TOKEN` 配置，缺省为 `viewer`），请求体里的 `user_role`/`user_id` 一律忽略，客户端无法自我提权。MCP 通道沿用 `get_mcp_settings` 的服务端默认身份。详见 `docs/security-intent-validator.md`。

`log` 和 `log.search` 在 `source=file` 模式下只允许读取 `SAFE_LOG_DIRS` 内的普通日志文件。部署时如需扩展目录，可通过 `AGENT_ALLOWED_LOG_DIRS` 设置允许目录列表。

## 最小权限执行

生产环境应以 `software-cup-agent` 低权限用户运行服务，不应使用 root 直接运行。

`backend/security/least_privilege.py` 会在 Linux 环境中记录当前执行身份，并在满足条件时将子进程降权到目标用户。`AGENT_STRICT_LEAST_PRIVILEGE=true` 时，如果 root 进程无法解析目标低权限用户，会拒绝启动系统命令。

相关配置：

```bash
export AGENT_RUN_USER=software-cup-agent
export AGENT_RUN_GROUP=software-cup-agent
export AGENT_SAFE_WORKDIR=/
export AGENT_STRICT_LEAST_PRIVILEGE=true
```

## LLM JSON 合约

规划阶段固定返回：

```json
{
  "intent": "inspection|diagnosis|risky_operation",
  "summary": "一句话描述用户意图",
  "tools": ["工具名称"],
  "arguments": {},
  "arguments_by_tool": {},
  "risk_hint": "low|medium|high|prohibited",
  "need_confirmation": false,
  "reasoning": ["简短规划理由"]
}
```

分析阶段固定返回：

```json
{
  "conclusion": "最终结论",
  "status": "normal|warning|critical|unknown",
  "root_cause": "根因或无法确认",
  "evidence": ["关键证据"],
  "recommendations": ["建议"],
  "needs_more_info": false,
  "follow_up_questions": []
}
```

后端必须容忍 LLM 不可用、非 JSON、缺少 `response_format` 支持和代码块包裹 JSON 的情况，并回退到本地规则。

## 审计追踪

每个用户请求都会生成一个 `trace_id`。审计事件默认写入 `backend/audit/logs/audit.log`，可通过 `AGENT_AUDIT_LOG_PATH` 覆盖。

当前审计阶段包括：

- `received_instruction`
- `llm_decision`
- `security_validation`
- `tool_call`
- `environment_perception`
- `execution_result`
- `final_answer`
- `trace_complete`

## 关键约束

- 工具执行顺序必须保持为：规划 -> 安全校验 -> 工具执行 -> 结果总结 -> 审计闭环。
- 所有 shell 访问必须走 `run_template()` 或 `run_optional_template()`。
- Windows 和 Linux 命令模板不同，新增工具时需要保留平台判断。
- 中风险工具必须要求 operator/admin 角色和二次确认。
- 高风险和禁止操作不能仅凭 LLM 或前端确认放行。
- 修改某一阶段逻辑前，应先阅读 `docs/` 下对应设计文档。

## 相关设计文档

Medium-risk tools such as `service.restart` are registered as MCP-like tools but
require security validation, operator/admin role, and secondary confirmation.
See `docs/controlled-operation-tools.md`.

## MCP protocol server (Streamable HTTP)

Besides the human-readable `GET /api/mcp/tools` manifest, the app also serves a
real MCP (Model Context Protocol) endpoint over Streamable HTTP, mounted at
`/mcp` (`backend/mcp_server/server.py`). It is built with the official `mcp`
SDK's low-level `Server` plus `StreamableHTTPSessionManager`, whose lifecycle is
driven by the FastAPI `lifespan` handler (which also runs `init_db`).

- `tools/list`: `build_tool_list()` maps the `ToolRegistry` tools to MCP `Tool`
  objects, including `inputSchema`, a `[risk: ...]` description suffix, and a
  `readOnlyHint` annotation.
- `tools/call`: `run_tool_call()` builds a `Plan` and calls
  `ToolExecutor.execute()` — the same controlled path as `POST /api/tools/{name}`
  — so `SecurityGuard`, audit, and least-privilege all apply. **The MCP entry
  point never bypasses the security gate.** The synchronous executor runs in a
  worker thread via `anyio.to_thread.run_sync`.
- Default MCP identity: `AGENT_MCP_CLIENT_USER` (default `mcp-client`) and
  `AGENT_MCP_CLIENT_ROLE` (default `viewer`, the lowest privilege). Controlled
  operations require raising the role to `operator`/`admin` and passing
  `approved: true` in the call arguments; otherwise `SecurityGuard` blocks them.
- Every MCP call produces a full `trace_id` audit chain with `channel=mcp` in the
  event data.

Clients connect to `http://<host>:8000/mcp` (a `POST /mcp` is `307`-redirected to
`/mcp/`; MCP clients follow this automatically). Two-layer input filtering
applies: the SDK first validates arguments against each tool's `inputSchema`
(e.g. `process.kill` rejects `pid < 101`), then `SecurityGuard` enforces the
risk policy (protected processes/services, roles, confirmation, dangerous
patterns).
