# Audit Tracing

Every user request is written as a JSONL audit trace. A request receives one
`trace_id`, and each stage appends one event to the audit log.

## Audit Path

Development default:

```text
backend/audit/logs/audit.log
```

Production default from systemd:

```text
/var/log/software-cup-ops/audit.log
```

Configured by:

```text
AGENT_AUDIT_LOG_PATH
```

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

Recent audit events:

```bash
curl "http://127.0.0.1:8000/api/audit/recent?limit=100"
```

Events for one request:

```bash
curl "http://127.0.0.1:8000/api/audit/recent?trace_id=<TRACE_ID>"
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
