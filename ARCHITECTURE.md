# Architecture

## Tech stack

- Frontend: Vue 3 now, React can be swapped later if the team prefers it.
- Backend: Python FastAPI.
- Agent scheduler: Python planner and executor modules.
- MCP tool layer: Python function wrappers for system overview, process analysis, network ports, logs, and services.
- Database: SQLite for the first version, PostgreSQL later.
- LLM: DeepSeek or Qwen through API first, local deployment later.
- System commands: `subprocess` through whitelisted command templates only.
- Deployment target: Kylin Advanced Server V11 on LoongArch.

## Runtime flow

1. The Vue UI sends a user request to `POST /api/agent/execute`.
2. `backend.agent.planner.Planner` chooses intent and tools.
3. If `LLM_PROVIDER`, API key, and model settings are configured, the planner calls DeepSeek/Qwen.
4. If the LLM call is unavailable, the planner uses local keyword rules.
5. `backend.security.guard.SecurityGuard` blocks dangerous requests.
6. `backend.security.permission.PermissionPolicy` asks for approval for risky intent.
7. `backend.agent.executor.ToolExecutor` calls whitelisted Python tool functions.
8. Tool functions call `command_runner.run_template()` for approved system commands.
9. `backend.audit.logger.AuditLogger` writes JSONL audit records.

## LLM environment variables

```bash
export LLM_PROVIDER=deepseek
export DEEPSEEK_API_KEY=...
export LLM_MODEL=deepseek-chat
```

or:

```bash
export LLM_PROVIDER=qwen
export QWEN_API_KEY=...
export LLM_MODEL=qwen-plus
```

Common overrides:

```bash
export LLM_API_KEY=...
export LLM_BASE_URL=...
export LLM_TIMEOUT_SECONDS=20
```

## Command whitelist

Command templates live in `backend/mcp_tools/command_runner.py`.
New commands should be added as named templates, not built from free-form user text.

## Stage 1: system perception tools

See `docs/system-perception-tools.md`.

## Stage 2: MCP tool registration

Tool metadata and handlers are registered in `backend/mcp_tools/builtin.py`
through `ToolRegistry`. FastAPI exposes an MCP-like manifest at
`GET /api/mcp/tools`.

## Stage 3: security intent validation

Every execution passes through `backend/security/guard.py` before a tool handler
is called. The validator checks tool whitelist, parameter schema, parameter
values, dangerous paths, dangerous commands, user permissions, secondary
confirmation, and audit logging. See `docs/security-intent-validator.md`.

## Stage 4: least privilege execution

Production deployment runs the service as `software-cup-agent` instead of root.
The command runner records execution identity for every subprocess and refuses
strict root execution when the dedicated low-privilege user is missing. See
`docs/least-privilege-execution.md`.

## Stage 5: LLM JSON contract

DeepSeek/Qwen is used through a fixed JSON contract: planning JSON selects
intent, tools, and arguments; analysis JSON turns tool results into user-facing
conclusions. Backend security and execution remain authoritative. See
`docs/llm-agent-json-contract.md`.

## Stage 6: audit tracing

Every user request receives a `trace_id` and writes JSONL audit events for
instruction receipt, LLM decision, security validation, tool calls, environment
perception, execution result, and final answer. See `docs/audit-tracing.md`.

## Controlled operations

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
