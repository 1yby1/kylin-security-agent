---
name: mcp-tool-development
description: Use when Codex needs to add, modify, or review MCP-like tools in this safety operations Agent project. Covers reusable workflow for backend/mcp_tools tool modules, ToolDefinition registry metadata, command whitelist templates, input schemas, security guard integration, audit trace behavior, least-privilege command execution, and FastAPI verification endpoints.
---

# MCP Tool Development

Use this skill to develop reusable MCP-like tools for this project.

## Core Rule

Never execute user-generated shell strings. Every OS command must be represented as a named whitelist template in `backend/mcp_tools/command_runner.py`, and every tool must be registered in `backend/mcp_tools/builtin.py`.

## Workflow

1. Clarify the tool purpose: perception, diagnosis, or controlled operation.
2. Choose a stable tool name using lowercase letters and hyphens only when needed.
3. Define risk level before coding:
   - `low`: read-only status, process, network, log, service, or disk inspection.
   - `medium`: controlled operations such as restart allowlisted service or clean safe temp directory.
   - `high`: config changes, permission changes, user changes, or protected service operations.
   - `prohibited`: never implement as executable behavior.
4. Add or reuse command templates in `backend/mcp_tools/command_runner.py`.
5. Create a tool module in `backend/mcp_tools/<name>_tool.py` with `run(arguments: dict[str, Any]) -> dict[str, Any]`.
6. Register the tool in `backend/mcp_tools/builtin.py` using `ToolDefinition`.
7. Update `backend/security/rules.py` if the tool changes risk categories, protected resources, or allowlists.
8. Update `backend/agent/prompt.py` and `backend/agent/llm_client.py` only if the tool should be selectable from natural language.
9. Verify direct tool API and Agent flow.
10. Document behavior only when the tool introduces a new capability or safety boundary.

## Tool Module Pattern

```python
from __future__ import annotations

from typing import Any

from backend.mcp_tools.command_runner import run_template


def run(arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        result = run_template("example.command", timeout=5)
    except Exception as exc:
        return {"error": str(exc)}
    return result.to_dict(limit=int(arguments.get("limit", 40)))
```

## Registry Pattern

```python
registry.register(
    ToolDefinition(
        name="example",
        title="Example Tool",
        description="Describe what this tool observes or executes.",
        category="perception",
        handler=example_tool.run,
        command_templates=["example.command"],
        input_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 200}
            },
        },
        risk_level="low",
        read_only=True,
    )
)
```

## Validation Commands

Run these after adding a tool:

```powershell
python -m compileall backend
python -B -c "from backend.agent.executor import ToolExecutor; print(ToolExecutor().available_tools())"
```

When the API server is running:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/mcp/tools
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/tools/<tool-name> -Method Post -ContentType 'application/json' -Body '{"arguments":{}}'
```

## Safety Checklist

Read `references/tool-safety-checklist.md` when a tool touches system commands, files, services, users, permissions, or process control.
