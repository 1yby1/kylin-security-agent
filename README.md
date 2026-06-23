# Software Cup Ops Assistant

软件杯智能运维 Agent 原型项目。系统接收自然语言运维问题，调用
DeepSeek/Qwen 或本地规则完成意图规划，再通过后端安全校验执行白名单
工具，最终返回结构化诊断结论和完整审计链路。

本项目面向麒麟高级服务器 V11 与 LoongArch 部署场景，开发环境可在
Windows 上运行。所有系统命令必须通过后端命令模板执行，不能直接拼接
用户输入。

## 核心能力

- 智能运维对话：提交自然语言排查请求并返回诊断结论。
- MCP-like 工具注册：统一暴露工具名称、参数 schema、风险等级和命令模板。
- 系统感知工具：系统概览、进程、端口、日志、服务、磁盘等只读诊断。
- 受控操作工具：服务重启、临时目录清理、进程终止等中风险操作。
- 工具编排（多步链路）：多个工具按顺序协作，后一步可用 `"${stepId.path}"` 引用前一步输出；逐步安全校验，任一步被拦即快速失败中断。
- 安全校验链路：工具白名单、参数 schema、危险路径、危险命令、角色权限和二次确认。
- 审计追踪：按 `trace_id` 记录请求、规划、安全校验、工具调用、执行结果和最终回答。
- 前端页面：智能对话、系统看板、MCP 工具列表、审计日志查询。

## 目录结构

- `frontend/`：静态 Vue 3 前端页面。
- `backend/main.py`：FastAPI 应用入口。
- `backend/agent/`：规划、调度、执行和结果总结。
- `backend/mcp_tools/`：本地诊断工具和命令模板。
- `backend/security/`：安全规则、权限策略和最小权限执行。
- `backend/audit/`：JSONL 审计日志模型和读写逻辑。
- `backend/database/`：SQLite 初始化逻辑。
- `deploy/`：Linux/Kylin 部署脚本和 systemd 服务模板。
- `docs/`：阶段设计文档、测试报告和项目状态说明。
- `tests/`：Python `unittest` 回归测试。

## 本地运行

安装依赖：

```bash
pip install -r requirements.txt
```

启动后端和静态前端：

```bash
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

打开：

```text
http://localhost:8000
```

未配置大模型时，系统会自动使用本地关键词规则完成规划。

## 前端访问令牌

HTTP 入口不信任请求体里的 `user_role`，角色由服务端环境变量中的令牌绑定：

```bash
export AGENT_DEFAULT_ROLE=viewer
export AGENT_VIEWER_TOKEN=your_viewer_token
export AGENT_OPERATOR_TOKEN=your_operator_token
export AGENT_ADMIN_TOKEN=your_admin_token
```

前端页面右侧的“访问令牌”输入框填写其中一个令牌后，请求会以
`Authorization: Bearer <token>` 发送。留空或令牌不匹配时默认是 `viewer`，
只能执行低风险只读操作；执行服务重启、清理临时目录等中风险操作时，需要
填写 `operator` 或 `admin` 令牌并勾选二次确认。

Linux/systemd 部署时，`deploy/install.sh` 会在首次 root 安装时生成
`/etc/software-cup-ops/software-cup-ops.env`，`deploy/systemd.service` 会加载
这个文件。可在服务器上查看该文件，将对应令牌粘贴到前端输入框。

## 大模型配置

DeepSeek 示例：

```bash
export LLM_PROVIDER=deepseek
export DEEPSEEK_API_KEY=your_api_key
export LLM_MODEL=deepseek-chat
```

Qwen 示例：

```bash
export LLM_PROVIDER=qwen
export QWEN_API_KEY=your_api_key
export LLM_MODEL=qwen-plus
```

可选通用配置：

```bash
export LLM_API_KEY=your_api_key
export LLM_BASE_URL=https://example.com/chat/completions
export LLM_TIMEOUT_SECONDS=20
```

## 测试

默认测试入口：

```bash
python -m unittest discover -v
```

测试包会禁用真实 LLM 环境变量，避免单元测试误触发外部 API 调用。

## 主要 API

- `POST /api/agent/execute`：完整 Agent 执行链路。
- `POST /api/agent/plan`：只生成规划，不执行工具。
- `POST /api/security/evaluate`：只执行安全校验。
- `GET /api/mcp/tools`：查看 MCP-like 工具 manifest。
- `POST /api/tools/{tool_name}`：直接调用指定工具，仍会经过安全校验。
- `GET /api/audit/recent`：查询最近审计事件。
- `GET /api/llm/status`：查看 LLM 配置和可用状态。
- `GET /api/security/runtime`：查看运行身份和最小权限状态。

## 部署

服务器部署说明见：

```text
deploy/README.md
```

生产环境建议通过 `software-cup-agent` 低权限用户运行 systemd 服务，并将
状态、日志和临时目录限制到部署文档中声明的路径。

## 设计文档

- `ARCHITECTURE.md`：整体架构、运行流和关键约束。
- `docs/system-perception-tools.md`：系统感知工具设计。
- `docs/mcp-tool-registration.md`：MCP-like 工具注册设计。
- `docs/security-intent-validator.md`：安全意图校验设计。
- `docs/least-privilege-execution.md`：最小权限执行设计。
- `docs/llm-agent-json-contract.md`：LLM 固定 JSON 合约。
- `docs/audit-tracing.md`：全链路审计追踪。
- `docs/controlled-operation-tools.md`：受控操作工具设计。
- `docs/project-status.md`：当前已实现能力和后续扩展计划。
