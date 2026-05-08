# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

"软件杯" (Software Cup) ops-assistant prototype: an LLM agent that takes a natural-language ops question, picks tools, runs whitelisted Linux commands, and returns a structured conclusion plus full audit trail. Target deployment is Kylin Advanced Server V11 on LoongArch; development happens on Windows so the command runner and least-privilege code are platform-aware.

## Run

```bash
pip install -r requirements.txt
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

The FastAPI app serves `frontend/index.html` at `/` and mounts `frontend/` at `/static`. The HTML loads Vue 3 from a CDN, so the frontend works without a build step; the `frontend/package.json` Vite scripts exist but the runtime path is the CDN-loaded SPA, not a Vite build.

LLM is opt-in through env vars (default planner falls back to local keyword rules):

```bash
export LLM_PROVIDER=deepseek      # or qwen
export DEEPSEEK_API_KEY=...        # or QWEN_API_KEY / generic LLM_API_KEY
export LLM_MODEL=deepseek-chat
# Optional: LLM_BASE_URL, LLM_TIMEOUT_SECONDS
```

Automated tests use Python `unittest`:

```bash
python -m unittest tests.test_controlled_tools -v
```

There is no lint/format config — don't invent lint commands.

## Architecture

Request flow for `POST /api/agent/execute` (see [backend/main.py](backend/main.py) → [backend/agent/orchestrator.py](backend/agent/orchestrator.py)):

1. **Plan** — [Planner](backend/agent/planner.py) calls [LLMClient.analyze](backend/agent/llm_client.py) for a JSON plan; on disabled/error/non-JSON it falls back to keyword rules. Plans are validated against the registered tool set (`system|process|process.kill|network|log|service|service.restart|temp.clean|disk`).
2. **Security gate** — [SecurityGuard.check](backend/security/guard.py) runs seven checks (tool whitelist, parameter schema, parameter values, dangerous paths, dangerous commands, user permission, secondary confirmation) using rule tables in [backend/security/rules.py](backend/security/rules.py). Risk level (`low|medium|high|prohibited`) drives `RISK_POLICIES` which decide allowed roles, confirmation, and default-block. **No tool runs until this passes.**
3. **Execute** — [ToolExecutor](backend/agent/executor.py) calls each tool's handler via [ToolRegistry](backend/mcp_tools/registry.py). Tools are registered in [backend/mcp_tools/builtin.py](backend/mcp_tools/builtin.py) and shape (name/title/category/risk/schema/templates) is exposed at `GET /api/mcp/tools` as an MCP-like manifest.
4. **Conclude** — [LLMClient.conclude](backend/agent/llm_client.py) turns tool output into `{conclusion, status, root_cause, evidence, recommendations, ...}`. If LLM is off or fails, [`AgentOrchestrator._fallback_conclusion`](backend/agent/orchestrator.py) synthesizes a conclusion from tool results.
5. **Audit** — [AuditLogger.event](backend/audit/logger.py) writes a JSONL line per stage (`received_instruction`, `llm_decision`, `security_validation`, `tool_call`, `environment_perception`, `execution_result`, `final_answer`, `trace_complete`) all keyed by a single `trace_id`. Default path is `backend/audit/logs/audit.log` (override via `AGENT_AUDIT_LOG_PATH`).

### Critical invariants

- **All shell access goes through [`run_template`](backend/mcp_tools/command_runner.py).** It looks up a named template in `COMMAND_TEMPLATES["windows"|"linux"]`, validates each `{param}` against `SAFE_PARAM`, and runs `subprocess.run` with `close_fds=True`. Never build shell strings from user input or call `subprocess` directly elsewhere — add a new template instead.
- **Windows ≠ Linux template sets.** The Windows table is intentionally smaller (developer convenience). Tool handlers in [`backend/mcp_tools/*_tool.py`](backend/mcp_tools/) check `os.name` and dispatch to OS-appropriate templates; preserve this when adding tools or the dev experience on Windows breaks.
- **Least-privilege drop is Linux-only.** [`subprocess_security_options`](backend/security/least_privilege.py) sets `user`/`group`/`extra_groups` only when the current process is root and the `software-cup-agent` user resolves. `AGENT_STRICT_LEAST_PRIVILEGE=true` (default) makes a root process with no resolvable agent user **refuse to spawn subprocesses** — this is intentional. Tunable via `AGENT_RUN_USER`, `AGENT_RUN_GROUP`, `AGENT_SAFE_WORKDIR` (see [backend/config.py](backend/config.py)).
- **The plan→guard→execute order is load-bearing.** `LOW/MEDIUM/HIGH_RISK_TOOLS` and `PROHIBITED_PATTERNS` in [`backend/security/rules.py`](backend/security/rules.py) are the authoritative policy; the LLM's `risk_hint` is advisory only. Don't move risk decisions into the planner or LLM.
- **LLM JSON contract is fixed.** Prompts live in [backend/agent/prompt.py](backend/agent/prompt.py); planning JSON must include `intent/tools/arguments` and analysis JSON must include `conclusion/status/...`. Both call sites tolerate missing `response_format` (retried without it) and code-fenced JSON. Keep these contracts stable when changing prompts.

### API surface

`/api/agent/execute` (full pipeline), `/api/agent/plan` (plan only, no execution), `/api/security/evaluate` (gate only), `/api/tools` + `/api/mcp/tools` + `/api/tools/{name}` (manifest + direct invocation, still gated), `/api/llm/status` + `/api/llm/test`, `/api/security/runtime`, `/api/audit/recent?limit=&trace_id=`.

### Docs

Stage-by-stage design notes are in [docs/](docs/) — `system-perception-tools.md`, `mcp-tool-registration.md`, `security-intent-validator.md`, `least-privilege-execution.md`, `llm-agent-json-contract.md`, `audit-tracing.md`. Read the relevant one before changing that stage.
