from __future__ import annotations

import mcp.types as types

from backend.agent.executor import ToolExecutor

MCP_SERVER_NAME = "software-cup-ops"


def build_tool_list(executor: ToolExecutor) -> list[types.Tool]:
    tools: list[types.Tool] = []
    for entry in executor.tool_manifest().get("tools", []):
        schema = entry.get("input_schema") or {"type": "object", "properties": {}}
        risk = entry.get("risk_level", "low")
        description = f"{entry.get('description', '')} [risk: {risk}]".strip()
        tools.append(
            types.Tool(
                name=entry["name"],
                description=description,
                inputSchema=schema,
                annotations=types.ToolAnnotations(
                    title=entry.get("title"),
                    readOnlyHint=bool(entry.get("read_only", False)),
                ),
            )
        )
    return tools
