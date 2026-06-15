# System Perception Tools

The first implementation stage provides five safe perception tools for Kylin
Advanced Server V11 on LoongArch. Tools are Python wrappers and all OS commands
must pass through `backend/mcp_tools/command_runner.py`.

Tools are registered through `backend/mcp_tools/builtin.py`; see
`docs/mcp-tool-registration.md` for the registry mechanism.

## Tools

| Tool | File | Purpose | Kylin/Linux command templates |
| --- | --- | --- | --- |
| `system` | `backend/mcp_tools/system_tool.py` | System overview: kernel, host, uptime, CPU, memory, disk | `uname`, `hostnamectl`, `uptime`, `lscpu`, `free`, `df` |
| `process` | `backend/mcp_tools/process_tool.py` | Process list and basic CPU/memory ranking | `ps` |
| `process.top` | `backend/mcp_tools/process_top_tool.py` | Fine-grained high CPU or high memory process lookup | `ps` |
| `process.detail` | `backend/mcp_tools/process_detail_tool.py` | Fine-grained process detail lookup by PID | `ps` |
| `network` | `backend/mcp_tools/network_tool.py` | Listening ports and socket context | `ss`, optional `lsof` |
| `network.port_lookup` | `backend/mcp_tools/network_port_lookup_tool.py` | Fine-grained lookup of PID/process by port number | `ss` |
| `log` | `backend/mcp_tools/log_tool.py` | Recent journal or file log inspection and keyword counts | `journalctl` |
| `log.search` | `backend/mcp_tools/log_search_tool.py` | Keyword search in journal or a specific log file | `journalctl` |
| `service` | `backend/mcp_tools/service_tool.py` | Service list/status and state counts | `systemctl` |

## API

List tools:

```bash
curl http://127.0.0.1:8000/api/tools
```

Run one tool:

```bash
curl -X POST http://127.0.0.1:8000/api/tools/system \
  -H 'Content-Type: application/json' \
  -d '{"arguments":{}}'
```

Fine-grained examples:

```bash
curl -X POST http://127.0.0.1:8000/api/tools/process.top \
  -H 'Content-Type: application/json' \
  -d '{"arguments":{"metric":"cpu","limit":5,"min_percent":1}}'

curl -X POST http://127.0.0.1:8000/api/tools/process.detail \
  -H 'Content-Type: application/json' \
  -d '{"arguments":{"pid":1234}}'

curl -X POST http://127.0.0.1:8000/api/tools/network.port_lookup \
  -H 'Content-Type: application/json' \
  -d '{"arguments":{"port":8080,"protocol":"tcp"}}'

curl -X POST http://127.0.0.1:8000/api/tools/log.search \
  -H 'Content-Type: application/json' \
  -d '{"arguments":{"keyword":"error","source":"journal","lines":200,"limit":20}}'
```

Run through natural language planning:

```bash
curl -X POST http://127.0.0.1:8000/api/agent/execute \
  -H 'Content-Type: application/json' \
  -d '{"query":"查看系统概览、进程、网络端口、日志和服务状态","context":{"lines":50}}'
```

## Safety Notes

- Tools do not execute user-provided shell strings.
- Parameters are rendered only into named whitelist templates.
- Service operations in this stage are read-only: list and status.
- All calls through `ToolExecutor` are audited by `backend/audit/logger.py`.
