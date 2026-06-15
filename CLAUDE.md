# CLAUDE.md

本文档用于指导 Claude Code 或其他代码助手在本仓库中工作。后续修改代码、更新文档或新增报告时，应优先遵守这里的项目约束。

## 项目概述

本项目是软件杯智能运维 Agent 原型：系统接收自然语言运维问题，选择合适工具，执行白名单 Linux/Windows 命令模板，并返回结构化结论和完整审计追踪。

目标部署环境是麒麟高级服务器 V11 + LoongArch；开发环境可能是 Windows，因此命令执行、路径处理和最小权限逻辑都需要保持平台感知。

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
3. **工具执行**：`backend/agent/executor.py` 通过 `ToolRegistry` 调用工具 handler。工具注册集中在 `backend/mcp_tools/builtin.py`。
4. **结果总结**：`LLMClient.conclude()` 将工具结果总结为结构化结论；LLM 不可用时由 `AgentOrchestrator._fallback_conclusion()` 本地兜底。
5. **审计追踪**：`backend/audit/logger.py` 按 `trace_id` 写入 JSONL 审计事件。

## 关键不变量

- **所有系统命令必须通过命令模板执行。**  
  只能使用 `backend/mcp_tools/command_runner.py` 中的 `run_template()` 或 `run_optional_template()`。不要在其他模块直接调用 `subprocess`，也不要拼接 shell 字符串。

- **Windows 和 Linux 模板不是同一套。**  
  Windows 模板主要服务开发调试，Linux/Kylin 模板服务生产部署。新增工具时必须考虑 `os.name` 分支和不同平台命令可用性。

- **安全校验必须早于工具执行。**  
  链路顺序是规划 -> 安全校验 -> 工具执行。不要把风险决策下放给 LLM 或前端确认。

- **风险策略以后端为准。**  
  工具定义中的 `risk_level`、`backend/security/rules.py` 中的 `RISK_POLICIES`、危险路径和危险命令模式是权威策略。LLM 的 `risk_hint` 只能作为参考字段。

- **最小权限逻辑只在 Linux 上降权。**  
  `backend/security/least_privilege.py` 会在 root 进程且目标用户可解析时为子进程设置 `user`、`group` 和 `extra_groups`。`AGENT_STRICT_LEAST_PRIVILEGE=true` 时，如果 root 进程无法解析低权限用户，应拒绝启动命令。

- **LLM JSON 合约需要稳定。**  
  Prompt 在 `backend/agent/prompt.py`。规划 JSON 必须包含 `intent`、`tools`、`arguments` 等字段；分析 JSON 必须包含 `conclusion`、`status`、`evidence`、`recommendations` 等字段。调用方需要容忍代码块包裹 JSON 和不支持 `response_format` 的模型。

## API 表面

- `POST /api/agent/execute`：完整 Agent 链路。
- `POST /api/agent/plan`：只规划，不执行工具。
- `POST /api/security/evaluate`：只执行安全校验。
- `GET /api/tools`：查看工具列表和 manifest。
- `GET /api/mcp/tools`：查看 MCP-like manifest。
- `GET /api/tools/{tool_name}`：查看工具元数据。
- `POST /api/tools/{tool_name}`：直接调用工具，仍需通过安全校验。
- `GET /api/llm/status`：查看 LLM 配置状态。
- `POST /api/llm/test`：测试 LLM 规划能力。
- `GET /api/security/runtime`：查看运行身份和最小权限状态。
- `GET /api/audit/recent?limit=&trace_id=`：查询审计事件。

## 修改建议

- 修改某个阶段前，先阅读 `docs/` 下对应设计文档。
- 变更工具时，同步检查 `backend/mcp_tools/builtin.py`、工具 handler、参数 schema、安全规则和测试。
- 变更安全策略时，补充能覆盖允许、阻断、二次确认和角色权限的测试。
- 变更 LLM 规划字段时，同步更新 prompt、解析逻辑、测试和相关文档。
- 遇到无关的未提交改动，不要回滚；只处理当前任务需要的文件。
