# LLM Agent JSON Contract

The LLM is used for reasoning only. It never executes shell commands directly.
The backend parses fixed JSON, validates it, runs security checks, executes
registered tools, and then asks the LLM to analyze tool results.

## Responsibilities

1. Understand user intent.
2. Select suitable registered tools.
3. Generate tool parameters.
4. Analyze tool results.
5. Produce a concise conclusion for the user.

## Planning JSON

The planning step must return JSON only:

```json
{
  "intent": "inspection",
  "summary": "查看系统状态",
  "tools": ["system"],
  "arguments": {},
  "arguments_by_tool": {},
  "risk_hint": "low",
  "need_confirmation": false,
  "reasoning": ["系统状态查询需要系统概览工具"]
}
```

Allowed `intent` values:

- `inspection`
- `diagnosis`
- `risky_operation`

Allowed tools are the enabled tools in `tool_manifest.tools`.

The backend does not keep a second LLM whitelist. `LLMClient` filters model
output against the runtime manifest returned by `ToolExecutor.tool_manifest()`.

### Argument routing

`arguments` is shared by every tool in the plan (typical use: `user_role`, `query`).
`arguments_by_tool` is keyed by tool name and only applies to that tool. The
backend merges them per tool:

```
effective_args(tool_name) = {**arguments, **arguments_by_tool.get(tool_name, {})}
```

Use `arguments_by_tool` when multiple tools declare the same parameter name but
expect different values or different valid ranges (for example, the `limit`
parameter on `process` versus `process.top`). Keys missing from
`arguments_by_tool` fall back to `arguments`, so older plans without that field
still work.

### Placeholder filtering (hard rule)

The planning prompt tells the model: if a required parameter (such as `pid`,
`port`, `keyword`, `service_name`, `path`) cannot be determined from the user
input, do not invent a placeholder (`0`, empty string, sample value) — drop the
tool instead.

This is also enforced in code and never trusted to the model alone.
`LLMClient._coerce_arguments_by_tool()` validates every value in
`arguments_by_tool` against that tool's own `input_schema` and drops any value
that is `None`, an empty/whitespace string, or violates the schema (wrong type,
not in `enum`, or out of the declared `minimum`/`maximum`). For example a
hallucinated `pid: 0` for `process.kill` (schema `minimum: 101`) is removed, so
`SecurityGuard` then reports a clean `pid is required` instead of a confusing
out-of-range error. Legitimate boundary values such as `min_percent: 0` (schema
`minimum: 0`) are kept. If every value in a tool's override is dropped, the tool
key itself is removed from `arguments_by_tool`.

## Analysis JSON

After tools run, the result analysis step must return JSON only:

```json
{
  "conclusion": "当前系统状态正常。",
  "status": "normal",
  "root_cause": "未发现明确故障。",
  "evidence": ["system 工具返回主机与资源信息"],
  "recommendations": ["继续观察业务日志"],
  "needs_more_info": false,
  "follow_up_questions": []
}
```

Allowed `status` values:

- `normal`
- `warning`
- `critical`
- `unknown`

## Backend Flow

1. `AgentOrchestrator` receives the user query.
2. `Planner` calls DeepSeek/Qwen and parses planning JSON.
3. If the LLM is disabled or invalid, local rules select tools.
4. `SecurityGuard` validates the selected tools and arguments.
5. `ToolExecutor` runs tools only after security validation.
6. `LLMClient.conclude()` sends tool results back to the LLM.
7. If the LLM is disabled or invalid, a local fallback conclusion is returned.

## Environment Variables

DeepSeek:

```bash
export LLM_PROVIDER=deepseek
export DEEPSEEK_API_KEY=...
export LLM_MODEL=deepseek-chat
```

Qwen:

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

## APIs

Plan only:

```bash
curl -X POST http://127.0.0.1:8000/api/agent/plan \
  -H 'Content-Type: application/json' \
  -d '{"query":"查看系统状态","context":{}}'
```

Full Agent run:

```bash
curl -X POST http://127.0.0.1:8000/api/agent/execute \
  -H 'Content-Type: application/json' \
  -d '{"query":"查看系统状态","context":{}}'
```
