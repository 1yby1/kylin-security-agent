# MCP 协议服务端 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 FastAPI 应用内新增一个符合 MCP 协议的 Streamable HTTP 服务端，把现有工具 registry 暴露为标准 `tools/list` / `tools/call`，且每次调用强制走现有 `SecurityGuard` + 审计 + 最小权限。

**Architecture:** 用官方 `mcp` SDK 的底层 `Server` 注册 `list_tools`/`call_tool` 两个 handler。`call_tool` 不直接调 `registry.call()`，而是构造 `Plan` → 调用现有 `ToolExecutor.execute()`（与 `POST /api/tools/{name}` 同一受控路径），从而复用 Guard/审计/降权。MCP 通道用一个可配置的默认身份（默认最低权限 `viewer`），受控操作需提权 + 显式 `approved`。服务端通过 `StreamableHTTPSessionManager` 挂载到 FastAPI 的 `/mcp`，并把其生命周期接入 FastAPI 的 `lifespan`。

**Tech Stack:** Python 3、FastAPI、官方 `mcp` Python SDK（`mcp>=1.12,<2`）、`anyio`、`unittest`。

参考设计：`docs/superpowers/specs/2026-06-15-mcp-protocol-design.md`

---

## 文件结构

| 文件 | 责任 | 动作 |
|------|------|------|
| `requirements.txt` | 声明 `mcp` 依赖 | Modify |
| `backend/config.py` | 新增 `MCPSettings` + `get_mcp_settings()`（MCP 通道默认身份/角色） | Modify |
| `backend/mcp_server/__init__.py` | 包标记 | Create |
| `backend/mcp_server/server.py` | 构建 MCP server：`build_tool_list` / `run_tool_call` / `build_mcp_server` / `build_session_manager` | Create |
| `backend/main.py` | 迁移 `startup`→`lifespan`，挂载 `/mcp` | Modify |
| `tests/test_mcp_server.py` | MCP server 单元测试 | Create |
| `ARCHITECTURE.md` / `docs/mcp-tool-registration.md` / `docs/project-status.md` | 文档同步（中文） | Modify |

约束（来自 CLAUDE.md / spec，务必遵守）：
- 不改 registry、guard、rules、least_privilege、任何现有工具、任何现有 REST 接口。
- `call_tool` 必须经 `executor.execute()`，绝不直接 `registry.call()`。
- 文档用中文，代码标识符/字段/环境变量保留原文。
- 不新增 lint 命令；测试用 `unittest`。

---

## Task 1: 引入 mcp 依赖并验证 SDK API（spike）

**目的：** `mcp` 尚未安装。先装并验证后续代码依赖的 API 名称/签名在安装版本里确实存在，避免后面照着写却对不上。

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: 在 requirements.txt 末尾追加依赖**

```text
mcp>=1.12,<2
```

- [ ] **Step 2: 安装**

Run: `pip install -r requirements.txt`
Expected: 成功安装 `mcp` 及其依赖（pydantic/starlette/anyio 等多为已装）。

- [ ] **Step 3: 写一次性 spike 脚本验证 API 表面**

创建临时文件 `scripts/_mcp_spike.py`（验证完即删，不提交）：

```python
import anyio
import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

# 1) 关键类型存在
tool = types.Tool(
    name="demo",
    description="d",
    inputSchema={"type": "object", "properties": {}},
    annotations=types.ToolAnnotations(title="Demo", readOnlyHint=True),
)
content = types.TextContent(type="text", text="ok")

# 2) 底层 Server + 装饰器可注册
server = Server("spike")


@server.list_tools()
async def _list() -> list[types.Tool]:
    return [tool]


@server.call_tool()
async def _call(name: str, arguments: dict) -> list[types.TextContent]:
    return [content]


# 3) session manager 可构造（确认 kwargs）
mgr = StreamableHTTPSessionManager(app=server, event_store=None, json_response=True, stateless=True)
print("OK", type(mgr).__name__, tool.name, content.text)
```

- [ ] **Step 4: 运行 spike**

Run: `python scripts/_mcp_spike.py`
Expected: 打印 `OK StreamableHTTPSessionManager demo ok`，无异常。

如果某个名称/kwarg 报错（例如 `stateless` 实际叫别的、`ToolAnnotations` 字段不同），**以安装版本为准**修正，并在 Task 3–5 的对应代码处同步调整。记录实际可用签名后再继续。

- [ ] **Step 5: 删除 spike 脚本并提交依赖**

```bash
rm scripts/_mcp_spike.py
git add requirements.txt
git commit -m "build: 引入 mcp SDK 依赖 (mcp>=1.12,<2)"
```

---

## Task 2: 新增 MCP 通道身份配置

**Files:**
- Modify: `backend/config.py`
- Test: `tests/test_mcp_server.py`

- [ ] **Step 1: 先写失败测试**

创建 `tests/test_mcp_server.py`：

```python
import os
import unittest
from unittest import mock

from backend.config import get_mcp_settings


class MCPSettingsTests(unittest.TestCase):
    def test_defaults_to_lowest_privilege(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENT_MCP_CLIENT_ROLE", None)
            os.environ.pop("AGENT_MCP_CLIENT_USER", None)
            settings = get_mcp_settings()
            self.assertEqual(settings.client_role, "viewer")
            self.assertEqual(settings.client_user_id, "mcp-client")

    def test_role_override(self):
        with mock.patch.dict(os.environ, {"AGENT_MCP_CLIENT_ROLE": "operator"}, clear=False):
            self.assertEqual(get_mcp_settings().client_role, "operator")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `python -m unittest tests.test_mcp_server -v`
Expected: FAIL —`ImportError: cannot import name 'get_mcp_settings'`。

- [ ] **Step 3: 在 backend/config.py 实现**

在文件末尾追加（紧跟 `get_runtime_settings` 之后）：

```python
@dataclass(frozen=True)
class MCPSettings:
    client_user_id: str
    client_role: str


def get_mcp_settings() -> MCPSettings:
    return MCPSettings(
        client_user_id=os.getenv("AGENT_MCP_CLIENT_USER", "mcp-client"),
        client_role=os.getenv("AGENT_MCP_CLIENT_ROLE", "viewer"),
    )
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `python -m unittest tests.test_mcp_server -v`
Expected: PASS（2 tests）。

- [ ] **Step 5: 提交**

```bash
git add backend/config.py tests/test_mcp_server.py
git commit -m "feat: 新增 MCP 通道默认身份配置 (get_mcp_settings)"
```

---

## Task 3: tools/list 映射（build_tool_list）

**Files:**
- Create: `backend/mcp_server/__init__.py`
- Create: `backend/mcp_server/server.py`
- Test: `tests/test_mcp_server.py`

- [ ] **Step 1: 追加失败测试**

在 `tests/test_mcp_server.py` 顶部 import 区追加：

```python
from backend.agent.executor import ToolExecutor
from backend.mcp_server.server import build_tool_list
```

并新增测试类：

```python
class BuildToolListTests(unittest.TestCase):
    def test_lists_all_registry_tools_with_object_schema(self):
        executor = ToolExecutor()
        tools = build_tool_list(executor)
        names = sorted(tool.name for tool in tools)
        self.assertEqual(names, sorted(executor.available_tools()))
        for tool in tools:
            self.assertIsInstance(tool.inputSchema, dict)
            self.assertEqual(tool.inputSchema.get("type", "object"), "object")
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `python -m unittest tests.test_mcp_server -v`
Expected: FAIL —`ModuleNotFoundError: backend.mcp_server`。

- [ ] **Step 3: 创建包与最小实现**

创建 `backend/mcp_server/__init__.py`（空文件即可）：

```python
```

创建 `backend/mcp_server/server.py`：

```python
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
```

> 注：若 Task 1 spike 发现 `ToolAnnotations` 字段不同，去掉 `annotations=` 参数即可（风险元数据已写进 description 后缀）。

- [ ] **Step 4: 运行测试，确认通过**

Run: `python -m unittest tests.test_mcp_server -v`
Expected: PASS（3 tests）。

- [ ] **Step 5: 提交**

```bash
git add backend/mcp_server/__init__.py backend/mcp_server/server.py tests/test_mcp_server.py
git commit -m "feat: MCP tools/list 映射 registry 工具 (build_tool_list)"
```

---

## Task 4: tools/call 受控执行（run_tool_call）

实现 MCP 调用的核心逻辑：生成 trace_id、注入默认角色、构造 `Plan`、走 `executor.execute()`、写 `received_instruction` 与 `trace_complete` 审计、返回结构化 payload。

**Files:**
- Modify: `backend/mcp_server/server.py`
- Test: `tests/test_mcp_server.py`

- [ ] **Step 1: 追加失败测试**

在 `tests/test_mcp_server.py` import 区追加：

```python
from backend.agent.executor import ExecutionResult
from backend.mcp_server.server import run_tool_call
```

新增测试辅助与测试类：

```python
class _RecordingAudit:
    def __init__(self):
        self.events = []

    def event(self, **kwargs):
        self.events.append(kwargs)


class _FakeExecutor:
    def __init__(self):
        self.calls = []

    def available_tools(self):
        return ["service.restart", "system"]

    def execute(self, *, plan, user_id, raw_query, approved, trace_id=None):
        self.calls.append(
            {"plan": plan, "user_id": user_id, "approved": approved, "trace_id": trace_id}
        )
        return ExecutionResult(
            approved_required=False,
            blocked=False,
            message="ok",
            result={plan.tools[0]: {"ok": True}},
            security={"risk_level": "medium"},
            executed_commands=[],
        )


class RunToolCallTests(unittest.TestCase):
    def test_injects_default_role_and_passes_approved(self):
        fake = _FakeExecutor()
        audit = _RecordingAudit()
        with mock.patch.dict(os.environ, {"AGENT_MCP_CLIENT_ROLE": "operator"}, clear=False):
            payload = run_tool_call(
                fake, "service.restart", {"service_name": "nginx", "approved": True}, audit=audit
            )
        call = fake.calls[0]
        self.assertEqual(call["plan"].arguments.get("user_role"), "operator")
        self.assertTrue(call["approved"])
        self.assertEqual(call["user_id"], "mcp-client")
        self.assertFalse(payload["blocked"])
        self.assertIn("trace_id", payload)
        stages = [event["stage"] for event in audit.events]
        self.assertIn("received_instruction", stages)
        self.assertIn("trace_complete", stages)
        self.assertTrue(all(event["data"].get("channel") == "mcp" for event in audit.events))

    def test_unknown_tool_raises(self):
        with self.assertRaises(ValueError):
            run_tool_call(_FakeExecutor(), "does.not.exist", {}, audit=_RecordingAudit())

    def test_protected_pid_blocked_through_guard(self):
        executor = ToolExecutor()  # 真实 registry + guard
        payload = run_tool_call(
            executor, "process.kill", {"pid": 1, "expected_name": "x"}, audit=_RecordingAudit()
        )
        self.assertTrue(payload["blocked"])
        self.assertEqual(payload["executed_commands"], [])
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `python -m unittest tests.test_mcp_server -v`
Expected: FAIL —`ImportError: cannot import name 'run_tool_call'`。

- [ ] **Step 3: 实现 run_tool_call**

在 `backend/mcp_server/server.py` 顶部 import 区补充：

```python
from uuid import uuid4

from backend.agent.planner import Plan
from backend.audit.logger import AuditLogger
from backend.config import get_mcp_settings
```

在 `build_tool_list` 之后追加：

```python
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
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `python -m unittest tests.test_mcp_server -v`
Expected: PASS（6 tests）。

- [ ] **Step 5: 提交**

```bash
git add backend/mcp_server/server.py tests/test_mcp_server.py
git commit -m "feat: MCP tools/call 经 SecurityGuard 受控执行 (run_tool_call)"
```

---

## Task 5: 组装 MCP server 与 session manager

把两个 handler 接到底层 `Server`，并提供可挂载的 `StreamableHTTPSessionManager`。

**Files:**
- Modify: `backend/mcp_server/server.py`
- Test: `tests/test_mcp_server.py`

- [ ] **Step 1: 追加失败测试**

在 `tests/test_mcp_server.py` import 区追加：

```python
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from backend.mcp_server.server import build_mcp_server, build_session_manager
```

新增测试类：

```python
class ServerAssemblyTests(unittest.TestCase):
    def test_build_mcp_server_returns_server(self):
        server = build_mcp_server(ToolExecutor())
        self.assertIsInstance(server, Server)

    def test_build_session_manager_returns_manager(self):
        manager = build_session_manager(ToolExecutor())
        self.assertIsInstance(manager, StreamableHTTPSessionManager)
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `python -m unittest tests.test_mcp_server -v`
Expected: FAIL —`ImportError: cannot import name 'build_mcp_server'`。

- [ ] **Step 3: 实现组装函数**

在 `backend/mcp_server/server.py` 顶部 import 区补充：

```python
import functools
import json

import anyio
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
```

在文件末尾追加：

```python
def build_mcp_server(executor: ToolExecutor) -> Server:
    server: Server = Server(MCP_SERVER_NAME)

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        return build_tool_list(executor)

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        payload = await anyio.to_thread.run_sync(
            functools.partial(run_tool_call, executor, name, arguments)
        )
        return [types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))]

    return server


def build_session_manager(executor: ToolExecutor) -> StreamableHTTPSessionManager:
    return StreamableHTTPSessionManager(
        app=build_mcp_server(executor),
        event_store=None,
        json_response=True,
        stateless=True,
    )
```

> 注：`anyio.to_thread.run_sync` 把同步的 `executor.execute()`（含 subprocess）放到线程池，避免阻塞事件循环。若 Task 1 spike 记录的 `StreamableHTTPSessionManager` kwargs 不同，按实际签名调整。

- [ ] **Step 4: 运行全部测试，确认通过**

Run: `python -m unittest tests.test_mcp_server -v`
Expected: PASS（8 tests）。

- [ ] **Step 5: 提交**

```bash
git add backend/mcp_server/server.py tests/test_mcp_server.py
git commit -m "feat: 组装 MCP 底层 Server 与 StreamableHTTP session manager"
```

---

## Task 6: 挂载到 FastAPI 并迁移 lifespan

把 `/mcp` 挂进现有应用，并把废弃的 `@app.on_event("startup")` 迁移为 `lifespan`，在其中运行 MCP session manager。

**Files:**
- Modify: `backend/main.py:1-69`

- [ ] **Step 1: 替换 main.py 顶部（import → app 创建 → 中间件 → 挂载）**

把现有第 1–82 行中"import 段 / app 创建 / 中间件 / 静态挂载 / agent 实例 / `on_startup`"整体调整为下面顺序（注意：executor 必须在 session manager 之前创建，app 必须带 `lifespan`）。

替换 `from backend.agent.orchestrator import AgentOrchestrator` 上方的 import 段，新增两个 import：

```python
import contextlib
from backend.mcp_server.server import build_session_manager
```

将原来的：

```python
app = FastAPI(title="Software Cup Ops Assistant", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

agent = AgentOrchestrator()
planner = agent.planner
executor = agent.executor
audit = AuditLogger()
```

替换为：

```python
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
```

- [ ] **Step 2: 删除旧的 startup 钩子**

删除这一段（原 67–69 行）：

```python
@app.on_event("startup")
def on_startup() -> None:
    init_db()
```

- [ ] **Step 3: 运行全部测试，确认无回归**

Run: `python -m unittest discover -v`
Expected: 全部 PASS（含既有测试与新增 `tests.test_mcp_server`）。

- [ ] **Step 4: 启动服务并做 MCP 握手冒烟**

Run（终端 A）: `uvicorn backend.main:app --host 127.0.0.1 --port 8000`

Run（终端 B）:

```bash
curl -i -X POST http://127.0.0.1:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
```

Expected: HTTP 200，响应体是包含 `"result"` 与 `serverInfo`（`software-cup-ops`）的 JSON-RPC 消息（stateless + json_response 模式下为 JSON；若为 SSE 帧，则 body 以 `event:`/`data:` 开头，同样视为成功）。确认 `/mcp` 不是 404 即证明挂载成功。

> `Accept` 同时带 `application/json` 与 `text/event-stream` 是 Streamable HTTP 规范要求，缺一会被服务端拒绝。
> 完整客户端联调请用 MCP Inspector 连接 `http://127.0.0.1:8000/mcp`，执行 `tools/list` 与 `tools/call`（演示用例：对受保护 PID 调 `process.kill` 应被 Guard 拦截）。

- [ ] **Step 5: 提交**

```bash
git add backend/main.py
git commit -m "feat: 将 MCP Streamable HTTP 服务端挂载到 /mcp 并迁移为 lifespan"
```

---

## Task 7: 文档同步

按 CLAUDE.md 要求，文档用中文，代码标识符/路径/环境变量保留原文。

**Files:**
- Modify: `ARCHITECTURE.md`
- Modify: `docs/mcp-tool-registration.md`
- Modify: `docs/project-status.md`

- [ ] **Step 1: ARCHITECTURE.md 增补 MCP 服务端段落**

在 "工具注册" 相关段落后新增一节，说明：

```markdown
## MCP 协议服务端

除人读的 `GET /api/mcp/tools` manifest 外，系统额外提供符合 MCP 协议的 Streamable HTTP 服务端，挂载在 `/mcp`（`backend/mcp_server/server.py`）。

- `tools/list`：由 `build_tool_list()` 把 `ToolRegistry` 的工具映射为 MCP `Tool`（含 inputSchema 与风险标注）。
- `tools/call`：由 `run_tool_call()` 构造 `Plan` 并调用 `ToolExecutor.execute()`，与 `POST /api/tools/{name}` 走同一条受控路径，复用 `SecurityGuard`、审计与最小权限。**MCP 入口不会绕过安全校验。**
- MCP 通道默认身份由 `AGENT_MCP_CLIENT_USER`（默认 `mcp-client`）与 `AGENT_MCP_CLIENT_ROLE`（默认 `viewer`，最低权限）决定。受控操作需把角色提到 `operator`/`admin` 且在 `arguments` 中显式传 `approved: true`，否则被 Guard 拦截。
- 每次 MCP 调用产生完整 trace_id 审计链，事件 `data` 标记 `channel=mcp`。

客户端连接地址：`http://<host>:8000/mcp`。
```

- [ ] **Step 2: docs/mcp-tool-registration.md 补充真 MCP 端点说明**

在文档末尾追加一段，区分两类入口：

```markdown
## 真 MCP 协议端点

`GET /api/mcp/tools` 返回的是人读的 `mcp-like` manifest（兼容旧前端）。符合 MCP 协议（JSON-RPC 2.0，`initialize`/`tools/list`/`tools/call`）的标准端点是 `/mcp`，由 `backend/mcp_server/server.py` 用官方 `mcp` SDK 的底层 `Server` 实现，经 `StreamableHTTPSessionManager` 挂载，生命周期接入 FastAPI 的 `lifespan`。新增工具时无需改 MCP server——只要按现有方式注册到 `ToolRegistry`，`tools/list`/`tools/call` 会自动反映。
```

- [ ] **Step 3: docs/project-status.md 更新已实现/待扩展**

在 "当前已实现功能" 增加：

```markdown
- 符合 MCP 协议的 Streamable HTTP 服务端（`/mcp`）：`tools/list` / `tools/call`，经 SecurityGuard 受控执行，默认最低权限身份。
```

并把 "待扩展" 中与 "真 MCP 协议" 相关的条目（如有）标记为已完成或移除。

- [ ] **Step 4: 提交**

```bash
git add ARCHITECTURE.md docs/mcp-tool-registration.md docs/project-status.md
git commit -m "docs: 同步 MCP 协议服务端说明"
```

---

## 验收标准（对应 spec 第 11 节）

1. `python -m unittest discover -v` 全绿，含新增 `tests.test_mcp_server`。
2. `POST /mcp` 的 `initialize` 握手返回 200 且含 `serverInfo`。
3. MCP Inspector 可 `tools/list` 列出全部工具及 inputSchema。
4. 默认身份下对受保护 PID 调 `process.kill` 经 MCP 被 Guard 拦截并返回原因（`blocked=true`）。
5. 提权（`AGENT_MCP_CLIENT_ROLE=operator` + `approved:true`）后受控操作可通过校验。
6. 每次 MCP 调用在审计日志产生完整 trace_id 链路且标记 `channel=mcp`。

## 自查记录（writing-plans self-review）

- **Spec 覆盖**：①感知工具复用既有 registry（tools/list 自动覆盖）；②MCP 插件化 = Task 3/5；③安全校验 = Task 4（强制走 Guard）；④最小权限 = 复用 executor.execute（Task 4 经过）；⑤审计闭环 = Task 4 received/trace_complete + executor 自动事件；传输/SDK/默认身份 = Task 1/2/5/6。无遗漏。
- **占位符**：无 TBD/TODO；每个改码步骤均含完整代码。
- **类型/命名一致**：`build_tool_list` / `run_tool_call` / `build_mcp_server` / `build_session_manager` / `get_mcp_settings` / `MCPSettings(client_user_id, client_role)` 全程一致；`run_tool_call(executor, name, arguments, audit=None)` 签名在测试与实现一致；`handle_mcp` 与 `mcp_session_manager` 在 main.py 内一致。
- **外部 API 风险**：mcp SDK 的 `ToolAnnotations` 字段与 `StreamableHTTPSessionManager` kwargs 由 Task 1 spike 先行验证，已在对应步骤标注调整点。
