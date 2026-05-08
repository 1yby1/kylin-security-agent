# Software Cup Ops Assistant

This repository contains a first-pass architecture for an AI-assisted
operations diagnosis system using Vue 3, FastAPI, Python agent scheduling,
SQLite, and whitelisted `subprocess` command templates.

## Structure

- `frontend/`: Web UI for submitting diagnosis requests.
- `backend/main.py`: FastAPI entry point.
- `backend/agent/`: Intent planning, tool selection, and execution dispatch.
- `backend/mcp_tools/`: Local diagnostic tools.
- `backend/security/`: Dangerous command detection and approval policy.
- `backend/audit/`: Audit record models and JSONL logger.
- `backend/database/`: SQLite bootstrap.
- `deploy/`: Install script and systemd service template.
- `ARCHITECTURE.md`: Tech stack, runtime flow, LLM configuration, and command whitelist notes.
- `docs/system-perception-tools.md`: Stage 1 system perception tool design and API examples.
- `docs/mcp-tool-registration.md`: Stage 2 MCP-like tool registry and discovery APIs.
- `docs/security-intent-validator.md`: Stage 3 risk levels, checks, and security APIs.
- `docs/least-privilege-execution.md`: Stage 4 dedicated user, systemd hardening, and subprocess identity control.
- `docs/llm-agent-json-contract.md`: Stage 5 DeepSeek/Qwen fixed JSON planning and result analysis contract.
- `docs/audit-tracing.md`: Stage 6 full-chain trace audit logging.
- `docs/controlled-operation-tools.md`: Controlled medium-risk MCP operation tools.

## Run

```bash
pip install -r requirements.txt
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Set `LLM_PROVIDER=deepseek` or `LLM_PROVIDER=qwen` plus the matching API key
to enable model-based planning. Without those variables the backend uses the
local fallback planner.
