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
| `network` | `backend/mcp_tools/network_tool.py` | Listening ports and socket context | `ss`, optional `lsof` |
| `log` | `backend/mcp_tools/log_tool.py` | Recent journal or file log inspection and keyword counts | `journalctl` |
| `service` | `backend/mcp_tools/service_tool.py` | Service list/status and state counts | `systemctl` |

## Security Posture Tools

Three additional read-only security perception tools (`category="security"`)
ship alongside the tools above and are also registered through
`backend/mcp_tools/builtin.py`. They are all listed in `LOW_RISK_TOOLS`, so the
multi-step reasoning loop can call them automatically. Full field-level
documentation lives in `docs/security-posture-tools.md`.

| Tool | File | One-line purpose |
| --- | --- | --- |
| `auth` | `backend/mcp_tools/auth_tool.py` | 登录认证审计：成功/失败登录、当前会话、暴力破解迹象，见 `docs/security-posture-tools.md` |
| `firewall` | `backend/mcp_tools/firewall_tool.py` | 防火墙暴露面：firewalld 运行状态与开放端口/服务，见 `docs/security-posture-tools.md` |
| `privilege` | `backend/mcp_tools/privilege_tool.py` | 提权风险扫描：特权目录 SUID/SGID、UID 0 账户、空密码账户，见 `docs/security-posture-tools.md` |

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
