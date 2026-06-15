from __future__ import annotations

from uuid import uuid4

import mcp.types as types

from backend.agent.executor import ToolExecutor
from backend.agent.planner import Plan
from backend.audit.logger import AuditLogger
from backend.config import get_mcp_settings

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


def run_tool_call(
    executor: ToolExecutor,
    name: str,
    arguments: dict,
    audit: AuditLogger | None = None,
) -> dict:
    audit = audit or AuditLogger()
    settings = get_mcp_settings()
    arguments = dict(arguments or {})
    approved = bool(arguments.get("approved", False))
    trace_id = uuid4().hex

    audit.event(
        trace_id=trace_id,
        stage="received_instruction",
        user_id=settings.client_user_id,
        status="received",
        data={"tool": name, "arguments": arguments, "channel": "mcp"},
    )

    if name not in executor.available_tools():
        audit.event(
            trace_id=trace_id,
            stage="trace_complete",
            user_id=settings.client_user_id,
            status="blocked",
            data={"tool": name, "channel": "mcp", "error": "unknown tool"},
        )
        raise ValueError(f"unknown tool: {name}")

    plan = Plan(
        intent="inspection",
        tools=[name],
        arguments={**arguments, "user_role": settings.client_role},
    )
    execution = executor.execute(
        plan=plan,
        user_id=settings.client_user_id,
        raw_query=f"mcp:{name}",
        approved=approved,
        trace_id=trace_id,
    )
    payload = {
        "trace_id": trace_id,
        "tool": name,
        "blocked": execution.blocked,
        "message": execution.message,
        "result": execution.result.get(name, {}),
        "security": execution.security,
        "executed_commands": execution.executed_commands,
    }
    audit.event(
        trace_id=trace_id,
        stage="trace_complete",
        user_id=settings.client_user_id,
        status="blocked" if execution.blocked else "completed",
        data={"tool": name, "channel": "mcp", "blocked": execution.blocked},
    )
    return payload
