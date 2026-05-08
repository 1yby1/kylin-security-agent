# MCP Tool Registration

This project uses an MCP-like registry to manage tool discovery, metadata, and
execution. The registry is intentionally local and simple for the first version,
but it keeps the same shape needed for later plugin loading.

## Core Files

- `backend/mcp_tools/registry.py`: generic registry and `ToolDefinition`.
- `backend/mcp_tools/builtin.py`: built-in tool registration list.
- `backend/agent/executor.py`: executes tools through `ToolRegistry`.
- `backend/main.py`: exposes tool discovery APIs.

## ToolDefinition

Each tool declares:

- `name`: stable machine-readable tool id.
- `title`: display name.
- `description`: what the tool does.
- `category`: for grouping, such as `perception` or `operation`.
- `handler`: Python function receiving `dict[str, Any]`.
- `input_schema`: MCP-style JSON schema for arguments.
- `command_templates`: whitelist template ids used by the tool.
- `platforms`: target platforms, defaulting to Kylin/Linux.
- `risk_level`: `low`, `medium`, or `high`.
- `read_only`: whether the tool is observational only.
- `enabled`: whether the tool is discoverable and callable.

## Registering A New Tool

1. Create a Python tool module under `backend/mcp_tools/`.
2. Add command templates to `backend/mcp_tools/command_runner.py` if the tool needs OS commands.
3. Register the tool in `backend/mcp_tools/builtin.py`.

Example:

```python
registry.register(
    ToolDefinition(
        name="example",
        title="示例工具",
        description="说明工具用途。",
        category="perception",
        handler=example_tool.run,
        command_templates=["example.command"],
        input_schema={"type": "object", "properties": {}},
    )
)
```

## Discovery APIs

List tool names and full manifest:

```bash
curl http://127.0.0.1:8000/api/tools
```

MCP-style manifest only:

```bash
curl http://127.0.0.1:8000/api/mcp/tools
```

Describe one tool:

```bash
curl http://127.0.0.1:8000/api/tools/system
```

Call one tool:

```bash
curl -X POST http://127.0.0.1:8000/api/tools/system \
  -H 'Content-Type: application/json' \
  -d '{"arguments":{}}'
```

All calls through `ToolExecutor` still pass security checks, permission policy,
and audit logging.
