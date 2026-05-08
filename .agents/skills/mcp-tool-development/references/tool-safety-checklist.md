# MCP Tool Safety Checklist

Use this checklist before committing a new or changed MCP tool.

## Command Execution

- Use `run_template()` or `run_optional_template()` only.
- Add command templates to `COMMAND_TEMPLATES`.
- Do not use `shell=True`.
- Do not build command strings from user input.
- Keep timeout values small.
- Return command, exit code, stdout, stderr, and execution identity.

## Parameters

- Declare every user-facing argument in `input_schema`.
- Use safe names such as `service_name`, `log_path`, `lines`, `limit`, `path`.
- Clamp numeric values such as `lines` and `limit`.
- Validate service names, paths, and process ids before execution.

## Security Integration

- Low-risk read-only tools can remain in `LOW_RISK_TOOLS`.
- Medium-risk tools must require confirmation and operator/admin roles.
- High-risk tools should be blocked by default.
- Prohibited operations must be represented as security rules, not tools.
- Update protected paths, services, and allowlists in `backend/security/rules.py` when needed.

## Audit Integration

- Tools called through `ToolExecutor` automatically emit `tool_call` audit events.
- Return structured results so audit logs are useful.
- Include concise `analysis` fields for dashboards and final answers.

## Natural Language Selection

If the tool should be selected by user `query`:

- Add keywords to `backend/agent/planner.py` fallback rules.
- Add tool description to `backend/agent/prompt.py`.
- Add the tool name to the allowlist in `backend/agent/llm_client.py`.

## Verification

- `python -m compileall backend`
- `GET /api/mcp/tools` shows the new tool metadata.
- `POST /api/tools/<tool-name>` returns a structured result.
- A normal Agent request reaches the tool.
- A dangerous request is blocked before the tool runs.

