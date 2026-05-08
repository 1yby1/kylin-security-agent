# Functional Test Cases

Use these cases as a starting point. Adjust expected results to actual project behavior.

| Case ID | Feature | Preconditions | Steps | Expected Result |
| --- | --- | --- | --- | --- |
| FT-001 | Health check | Backend started | GET `/health` | Returns `{"status":"ok"}` |
| FT-002 | Agent conversation | Backend started | POST `/api/agent/execute` with query `查看系统状态` | Returns `trace_id`, plan, security, result, conclusion |
| FT-003 | Tool selection | LLM disabled or enabled | POST `/api/agent/plan` with query `查看系统状态、进程和端口情况` | Selects `system`, `process`, `network` or valid equivalent |
| FT-004 | MCP manifest | Backend started | GET `/api/mcp/tools` | Returns registered tools and metadata |
| FT-005 | Direct system tool | Backend started | POST `/api/tools/system` | Returns platform and command result fields |
| FT-006 | Log tool file mode | Existing readable log file | POST `/api/tools/log` with `source=file` | Returns recent log lines and analysis |
| FT-007 | Security low risk | Backend started | Evaluate query `查看系统状态` | Risk `low`, not blocked |
| FT-008 | Security prohibited | Backend started | Evaluate query `rm -rf /` | Risk `prohibited`, blocked |
| FT-009 | Service restart confirmation | User role operator | Query `重启 nginx 服务`, `approved=false` | Blocked, secondary confirmation required |
| FT-010 | Service restart allowlist | User role operator, approved | Query `重启 nginx 服务`, `service_name=nginx` | Passes security; tool executes or reports unsupported platform |
| FT-011 | Service restart protected service | User admin, approved | Query `重启 firewalld 服务` | High/prohibited risk, blocked |
| FT-012 | Audit trace | Any completed request | GET `/api/audit/recent?trace_id=<id>` | Returns received, decision, security, tool, result, final events |
| FT-013 | Runtime identity | Backend started | GET `/api/security/runtime` | Returns current and target execution identity |
| FT-014 | Frontend chat page | Browser open | Submit query on chat page | Displays conclusion, risk, tools, trace ID |
| FT-015 | Frontend audit page | Existing audit trace | Query trace ID | Displays timeline events |

