# Security Intent Validator

The security intent validator is the mandatory pre-execution gate for every
Agent tool call. It runs before any tool handler is invoked.

## Risk Levels

| Level | Examples | Default Policy |
| --- | --- | --- |
| Low | View system status, processes, ports, logs, service status | Allow and audit |
| Medium | Restart allowlisted services, clean safe temp directories, kill non-system processes | Require `operator` or `admin` role and secondary confirmation |
| High | Delete system directories, modify system config, change permissions, stop protected services, modify users | Block by default |
| Prohibited | `rm -rf /`, `chmod 777 /etc/passwd`, `mkfs`, `dd of=/dev/...`, fork bomb `:(){ :|:& };:`, piping remote scripts to a shell (`curl ... | sh`), redirecting to block devices (`> /dev/sda`), closing firewall, deleting `/etc`, `/bin`, `/usr`, `/var` | Always block |

## Mandatory Checks

The validator records all seven checks:

1. `tool_whitelist`: tool must be registered and enabled in `ToolRegistry`.
2. `parameter_schema`: arguments must match the tool input schema where declared.
3. `parameter_values`: string parameters must use safe characters only.
4. `dangerous_path`: destructive operations cannot touch protected system paths.
5. `dangerous_command`: dangerous and prohibited command patterns are detected before execution.
6. `user_permission`: medium/high operations require privileged roles.
7. `secondary_confirmation` and `audit_logging`: confirmation is required for medium risk; every execution is audited.

## User Roles

- `viewer`: low-risk read-only operations only.
- `operator`: low-risk operations and confirmed medium-risk operations.
- `admin`: low-risk operations and confirmed medium-risk operations; high risk is still blocked by default.

### Trusted role binding (authentication)

Role is established **server-side** and never trusted from the request body.
HTTP callers present a token via `Authorization: Bearer <token>`; the token is
mapped to a role by `resolve_role` (`backend/security/auth.py`) using the
server-configured table in `get_auth_settings` (`backend/config.py`):

| Env var | Role |
| --- | --- |
| `AGENT_ADMIN_TOKEN` | `admin` |
| `AGENT_OPERATOR_TOKEN` | `operator` |
| `AGENT_VIEWER_TOKEN` | `viewer` |

A missing or unknown token resolves to `AGENT_DEFAULT_ROLE` (default `viewer`,
the lowest privilege). At the HTTP boundary (`backend/main.py`) any
client-supplied `user_role` is stripped from the request, and the resolved role
is passed to `SecurityGuard.check(..., role=...)` as the authoritative value —
so a forged `user_role: "admin"` (or a spoofed `user_id`) cannot escalate.

`SecurityGuard.check` accepts an explicit trusted `role`. When it is omitted
(internal callers, unit tests), the guard falls back to the legacy behavior of
reading `user_role` from arguments or deriving it from `user_id`. The MCP
channel binds its own server-side identity via `get_mcp_settings`
(`AGENT_MCP_CLIENT_ROLE`, default `viewer`).

## Safe Paths And Services

Safe temporary cleanup directories:

- `/tmp`
- `/var/tmp`
- `/opt/software-cup-ops/tmp`

Protected services:

- `firewalld`
- `iptables`
- `nftables`
- `sshd`
- `auditd`
- `systemd-journald`
- `dbus`
- `NetworkManager`

Service restart allowlist:

- `nginx`
- `httpd`
- `mysqld`
- `postgresql`
- `redis`
- `software-cup-ops`

## APIs

Evaluate without execution:

```bash
curl -X POST http://127.0.0.1:8000/api/security/evaluate \
  -H 'Content-Type: application/json' \
  -d '{"query":"查看系统状态","user_id":"viewer","context":{}}'
```

Execute with mandatory security gate:

```bash
curl -X POST http://127.0.0.1:8000/api/agent/execute \
  -H 'Content-Type: application/json' \
  -d '{"query":"查看系统状态","user_id":"viewer","context":{}}'
```

Authorized medium-risk operation (operator token + confirmation):

```bash
curl -X POST http://127.0.0.1:8000/api/agent/execute \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $AGENT_OPERATOR_TOKEN" \
  -d '{"query":"重启 nginx 服务","approved":true,"context":{"service_name":"nginx"}}'
```

Without a valid token the same request resolves to `viewer` and is blocked, so a
client cannot self-escalate by sending `user_role`/`user_id` in the body.

The execution response includes a `security` object with risk level, blocked
state, confirmation requirement, reasons, and all check results.

