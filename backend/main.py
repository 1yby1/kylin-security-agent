from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.agent.orchestrator import AgentOrchestrator
from backend.agent.planner import Plan, Planner
from backend.audit.logger import AuditLogger
from backend.config import get_monitor_settings, get_rate_limit_settings
from backend.database.db import init_db
from backend.mcp_server.server import build_session_manager
from backend.monitor.alerts import AlertStore
from backend.monitor.scheduler import MonitorScheduler
from backend.observability.metrics import get_metrics
from backend.security.auth import parse_bearer, resolve_role, session_principal
from backend.security.least_privilege import runtime_identity
from backend.security.rate_limit import ConcurrencyGate, RateLimiter, rate_limit_key


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
    if _monitor_settings.enabled:
        _monitor_scheduler.start()
    try:
        async with mcp_session_manager.run():
            yield
    finally:
        _monitor_scheduler.stop()


app = FastAPI(title="Software Cup Ops Assistant", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_rl_settings = get_rate_limit_settings()
_rate_limiter = RateLimiter(_rl_settings.per_minute, 60.0)
_concurrency = ConcurrencyGate(_rl_settings.max_concurrent)

_monitor_settings = get_monitor_settings()
_alert_store = AlertStore()
_monitor_scheduler = MonitorScheduler(executor, _alert_store, _monitor_settings, audit)

_HEAVY_PATHS = {"/api/agent/execute", "/api/agent/plan", "/api/security/evaluate"}
_KNOWN_API_METRIC_PATHS = {
    "/api/agent/execute",
    "/api/agent/plan",
    "/api/security/evaluate",
    "/api/security/runtime",
    "/api/alerts",
    "/api/monitor/status",
    "/api/llm/status",
    "/api/llm/test",
    "/api/tools",
    "/api/mcp/tools",
    "/api/audit/recent",
    "/api/audit/verify",
    "/api/audit/export",
}


def _is_mcp_path(path: str) -> bool:
    return path == "/mcp" or path.startswith("/mcp/")


def _metric_endpoint(path: str) -> str | None:
    if path == "/api/metrics":
        return None
    if _is_mcp_path(path):
        return "/mcp"
    if path.startswith("/api/tools/") and path != "/api/tools":
        return "/api/tools/{tool_name}"
    if path.startswith("/api/"):
        return path if path in _KNOWN_API_METRIC_PATHS else "/api/{unknown}"
    return None


def _is_heavy(method: str, path: str) -> bool:
    if method != "POST":
        return False
    if _is_mcp_path(path):
        return True
    if path in _HEAVY_PATHS:
        return True
    return path.startswith("/api/tools/") and path != "/api/tools"


@app.middleware("http")
async def rate_limit_middleware(request, call_next):
    path = request.url.path
    counted = _metric_endpoint(path)
    if counted:
        get_metrics().record_request(counted)
    if _rl_settings.enabled and _is_heavy(request.method, path):
        token = parse_bearer(request.headers.get("authorization"))
        client_host = request.client.host if request.client else None
        key = rate_limit_key(token, client_host)
        if not _rate_limiter.allow(key):
            get_metrics().record_rate_limited()
            return JSONResponse(
                status_code=429,
                content={"detail": "请求过于频繁，请稍后重试"},
                headers={"Retry-After": str(_rate_limiter.retry_after(key))},
            )
        if not _concurrency.try_acquire():
            get_metrics().record_concurrency_rejected()
            return JSONResponse(status_code=503, content={"detail": "服务繁忙，请稍后重试"})
        try:
            return await call_next(request)
        finally:
            _concurrency.release()
    return await call_next(request)


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
    token = parse_bearer(authorization)
    role = resolve_role(token)
    run = agent.run(
        query=request.query,
        user_id=request.user_id,
        context=_strip_client_role(request.context),
        approved=request.approved,
        role=role,
        session_id=request.session_id,
        owner=session_principal(token),
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
def plan_agent(request: AgentRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    context = _strip_client_role(request.context)
    owner = session_principal(parse_bearer(authorization))
    # Stateless endpoint: read the same follow-up context execute would inject,
    # but never create or mutate a session.
    conversation = agent.conversation_context(request.session_id, owner)
    if conversation:
        context = {**context, "conversation": conversation}
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


@app.get("/api/alerts")
def list_alerts(limit: int = 100, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    role = _role_from_header(authorization)
    if role not in {"operator", "admin"}:
        raise HTTPException(status_code=403, detail="alerts 仅 operator/admin 可访问")
    return {"alerts": _alert_store.recent(limit)}


@app.get("/api/monitor/status")
def monitor_status() -> dict[str, Any]:
    return _monitor_scheduler.status()


@app.get("/api/metrics")
def metrics_endpoint(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    role = _role_from_header(authorization)
    if role not in {"operator", "admin"}:
        raise HTTPException(status_code=403, detail="metrics 仅 operator/admin 可访问")
    return get_metrics().snapshot()


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
