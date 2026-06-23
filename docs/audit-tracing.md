# Audit Tracing

Every user request is persisted to a SQLite audit store
(`backend/audit/store.py`). A request receives one `trace_id`, and each stage
appends one event. SQLite is the authoritative source; `AuditLogger` is a thin
facade that delegates to a process-wide shared `AuditStore` (one instance per DB
path), so concurrent loggers never fork the hash chain.

## Tamper Evidence

Each event is linked into a hash chain (`prev_hash` + event fields →
`hash = sha256(...)`), and an `audit_meta` row tracks `last_hash` and
`event_count` in the same transaction. `verify_chain()` recomputes every hash to
detect content tampering / mid-chain deletion, and compares row count and the
last hash against `audit_meta` to detect tail truncation.

Threat model: this is tamper-evident, not cryptographically tamper-proof. An
attacker with full write access who can rebuild the entire chain and `audit_meta`
together is not stopped by this scheme (that requires external signing/anchoring,
left as future work).

## Audit Path

Development default:

```text
backend/audit/logs/audit.db
```

Production default from systemd:

```text
/var/lib/software-cup-ops/audit.db
```

Configured by:

```text
AGENT_AUDIT_DB_PATH
```

The store runs in WAL mode, so `audit.db-wal` / `audit.db-shm` sidecar files are
created alongside the DB and must live in a writable path. `AGENT_AUDIT_FAIL_CLOSED`
(default `false`) controls behavior on write failure: best-effort when `false`,
fail-closed (request errors out so an un-audited action is not executed) when
`true`.

## Stages

| Stage | Meaning |
| --- | --- |
| `received_instruction` | User query was received, including context and approval flag |
| `llm_decision` | LLM or local fallback selected intent, tools, and parameters |
| `security_validation` | Security intent validator evaluated tool whitelist, parameters, paths, commands, permissions, confirmation, and audit requirement |
| `environment_perception` | Tool results used as environment perception context |
| `tool_call` | Individual tool invocation started or completed, including extracted executed commands on completion |
| `execution_result` | Overall execution status, extracted executed commands, and raw tool output |
| `final_answer` | Final user-facing conclusion |
| `trace_complete` | End-of-request summary, including extracted executed commands |

For multi-step orchestration, `security_validation` and `tool_call` events are
emitted once per step and carry a `step_id` in their event data, so the full
chain (which step was checked, ran, or was blocked) can be reconstructed from a
single `trace_id`.

## Event Shape

```json
{
  "timestamp": "2026-04-25T10:00:00+00:00",
  "trace_id": "abc123",
  "stage": "security_validation",
  "user_id": "viewer",
  "status": "passed",
  "data": {}
}
```

When a tool result contains command execution output, the audit data also
includes an `executed_commands` list:

```json
[
  {
    "tool": "service.restart",
    "path": "restart",
    "command": "systemctl restart nginx",
    "exit_code": 0,
    "execution_identity": {
      "runs_as_user": "software-cup-agent"
    }
  }
]
```

Blocked requests keep `executed_commands` empty because no tool command was run.

## Query API

Recent audit events (now backed by indexed SQL queries; the response shape is
unchanged and additionally includes each event's `hash`):

```bash
curl "http://127.0.0.1:8000/api/audit/recent?limit=100"
```

Events for one request, or filtered by `user_id` / `status`:

```bash
curl "http://127.0.0.1:8000/api/audit/recent?trace_id=<TRACE_ID>"
curl "http://127.0.0.1:8000/api/audit/recent?user_id=operator&status=blocked"
```

Verify chain integrity (returns `ok` / `broken_at` / `count` / `tail_ok`):

```bash
curl "http://127.0.0.1:8000/api/audit/verify"
```

Export events as NDJSON (one JSON object per line):

```bash
curl "http://127.0.0.1:8000/api/audit/export?limit=1000"
```

## Coverage

The following request types create audit traces:

- `POST /api/agent/execute`
- `POST /api/agent/plan`
- `POST /api/security/evaluate`
- `POST /api/tools/{tool_name}`

The main Agent execution records the full chain:

1. 接收指令
2. 大模型决策
3. 安全校验
4. 工具调用
5. 环境感知
6. 执行结果
7. 最终回答
