from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.agent.orchestrator import AgentOrchestrator
from backend.agent.planner import Plan, Planner
from backend.audit.logger import AuditLogger
from backend.database.db import init_db
from backend.mcp_server.server import build_session_manager
from backend.security.auth import parse_bearer, resolve_role
from backend.security.least_privilege import runtime_identity


ROOT_DIR = Path(__file__).resolve().parents[1]
FRONTEND_DIR = ROOT_DIR / "frontend"

agent = AgentOrchestrator()
planner = agent.planner
executor = agent.executor
audit = AuditLogger()

mcp_session_manager = build_session_manager(executor)


async def handle_mcp(scope, receive, send) -> None:
    await mcp_session_manager.handle_request(scope, receive, send)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    async with mcp_session_manager.run():
        yield


app = FastAPI(title="Software Cup Ops Assistant", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

app.mount("/mcp", handle_mcp)


class AgentRequest(BaseModel):
    query: str = Field(..., min_length=1, description="User operation request")
    user_id: str = Field(default="anonymous")
    session_id: str | None = Field(default=None, description="Conversation session id for follow-up context")
    context: dict[str, Any] = Field(default_factory=dict)
    approved: bool = Field(default=False, description="Whether the user approved risky actions")


class AgentResponse(BaseModel):
    trace_id: str
    intent: str
    tools: list[str]
    approved_required: bool
    blocked: bool
    message: str
    result: dict[str, Any]
    security: dict[str, Any] = Field(default_factory=dict)
    executed_commands: list[dict[str, Any]] = Field(default_factory=list)
    conclusion: dict[str, Any] = Field(default_factory=dict)
    plan: dict[str, Any] = Field(default_factory=dict)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    suggested_actions: list[dict[str, Any]] = Field(default_factory=list)
    session_id: str = ""
    context_summary: str = ""


class ToolRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


def _role_from_header(authorization: str | None) -> str:
    """Resolve a trusted role from the Authorization header.

    Role is established server-side from the presented token; any ``user_role``
    in the request body is ignored. Missing/unknown token resolves to viewer.
    """
    return resolve_role(parse_bearer(authorization))


def _strip_client_role(values: dict[str, Any]) -> dict[str, Any]:
    """Drop client-supplied role claims so they cannot influence authorization."""
    return {key: value for key, value in values.items() if key != "user_role"}


@app.get("/", response_model=None)
def index() -> FileResponse | dict[str, str]:
    index_file = FRONTEND_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"message": "Ops assistant backend is running."}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/agent/execute", response_model=AgentResponse)
def execute_agent(request: AgentRequest, authorization: str | None = Header(default=None)) -> AgentResponse:
    role = _role_from_header(authorization)
    run = agent.run(
        query=request.query,
        user_id=request.user_id,
        context=_strip_client_role(request.context),
        approved=request.approved,
        role=role,
        session_id=request.session_id,
    )
    return AgentResponse(
        trace_id=run.trace_id,
        intent=run.intent,
        tools=run.tools,
        approved_required=run.approved_required,
        blocked=run.blocked,
        message=run.message,
        result=run.result,
        security=run.security,
        executed_commands=run.executed_commands,
        conclusion=run.conclusion,
        plan=run.plan,
        steps=run.steps,
        suggested_actions=run.suggested_actions,
        session_id=run.session_id,
        context_summary=run.context_summary,
    )


@app.post("/api/security/evaluate")
def evaluate_security(request: AgentRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    role = _role_from_header(authorization)
    context = _strip_client_role(request.context)
    trace_id = uuid4().hex
    audit.event(
        trace_id=trace_id,
        stage="received_instruction",
        user_id=request.user_id,
        status="received",
        data={"query": request.query, "context": context, "approved": request.approved, "role": role, "mode": "security_evaluate"},
    )
    result = agent.evaluate_security(
        query=request.query,
        user_id=request.user_id,
        context=context,
        approved=request.approved,
        role=role,
    )
    audit.event(
        trace_id=trace_id,
        stage="security_validation",
        user_id=request.user_id,
        status="blocked" if result["security"].get("blocked") else "passed",
        data=result,
    )
    audit.event(
        trace_id=trace_id,
        stage="trace_complete",
        user_id=request.user_id,
        status="completed",
        data=result,
    )
    return {"trace_id": trace_id, **result}


@app.post("/api/agent/plan")
def plan_agent(request: AgentRequest) -> dict[str, Any]:
    context = _strip_client_role(request.context)
    trace_id = uuid4().hex
    audit.event(
        trace_id=trace_id,
        stage="received_instruction",
        user_id=request.user_id,
        status="received",
        data={"query": request.query, "context": context, "mode": "plan_only"},
    )
    plan = planner.plan(request.query, context, executor.tool_manifest())
    result = {
        "intent": plan.intent,
        "tools": plan.tools,
        "arguments": plan.arguments,
        "summary": plan.summary,
        "source": plan.source,
        "reasoning": plan.reasoning,
        "steps": [
            {"id": step.id, "tool": step.tool, "arguments": step.arguments}
            for step in plan.execution_steps()
        ],
    }
    audit.event(
        trace_id=trace_id,
        stage="llm_decision",
        user_id=request.user_id,
        status=plan.source,
        data={"plan": result},
    )
    audit.event(
        trace_id=trace_id,
        stage="trace_complete",
        user_id=request.user_id,
        status="completed",
        data=result,
    )
    return {"trace_id": trace_id, **result}


@app.get("/api/security/runtime")
def security_runtime() -> dict[str, Any]:
    return {"runtime_identity": runtime_identity().to_dict()}


@app.get("/api/llm/status")
def llm_status() -> dict[str, Any]:
    return agent.llm_status()


@app.post("/api/llm/test")
def llm_test() -> dict[str, Any]:
    return agent.test_llm()


@app.get("/api/tools")
def list_tools() -> dict[str, Any]:
    return {"tools": executor.available_tools(), "manifest": executor.tool_manifest()}


@app.get("/api/mcp/tools")
def mcp_tools() -> dict[str, Any]:
    return executor.tool_manifest()


@app.get("/api/tools/{tool_name}")
def describe_tool(tool_name: str) -> dict[str, Any]:
    metadata = executor.tool_metadata(tool_name)
    if metadata is None:
        return {"error": f"unknown tool: {tool_name}"}
    return metadata


@app.post("/api/tools/{tool_name}")
def run_tool(tool_name: str, request: ToolRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    role = _role_from_header(authorization)
    arguments = _strip_client_role(request.arguments)
    trace_id = uuid4().hex
    user_id = str(arguments.get("user_id", "tool-api"))
    audit.event(
        trace_id=trace_id,
        stage="received_instruction",
        user_id=user_id,
        status="received",
        data={"tool": tool_name, "arguments": arguments, "role": role, "mode": "direct_tool"},
    )
    if tool_name not in executor.available_tools():
        result = {"trace_id": trace_id, "error": f"unknown tool: {tool_name}"}
        audit.event(
            trace_id=trace_id,
            stage="trace_complete",
            user_id=user_id,
            status="blocked",
            data=result,
        )
        return result

    plan = Plan(intent="inspection", tools=[tool_name], arguments=arguments)
    execution = executor.execute(
        plan=plan,
        user_id=user_id,
        raw_query=f"tool:{tool_name}",
        approved=False,
        trace_id=trace_id,
        role=role,
    )
    response = {
        "trace_id": trace_id,
        "tool": tool_name,
        "blocked": execution.blocked,
        "message": execution.message,
        "result": execution.result.get(tool_name, {}),
        "security": execution.security,
        "executed_commands": execution.executed_commands,
    }
    audit.event(
        trace_id=trace_id,
        stage="final_answer",
        user_id=user_id,
        status="blocked" if execution.blocked else "completed",
        data=response,
    )
    audit.event(
        trace_id=trace_id,
        stage="trace_complete",
        user_id=user_id,
        status="blocked" if execution.blocked else "completed",
        data=response,
    )
    return response


@app.get("/api/audit/recent")
def audit_recent(
    limit: int = 100,
    trace_id: str | None = None,
    user_id: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    return {
        "records": audit.read_recent(limit=limit, trace_id=trace_id, user_id=user_id, status=status)
    }


@app.get("/api/audit/verify")
def audit_verify() -> dict[str, Any]:
    return audit.verify_chain()


@app.get("/api/audit/export")
def audit_export(limit: int = 1000, trace_id: str | None = None) -> PlainTextResponse:
    records = audit.export(limit=limit, trace_id=trace_id)
    body = "\n".join(json.dumps(record, ensure_ascii=False) for record in records)
    return PlainTextResponse(body, media_type="application/x-ndjson")
