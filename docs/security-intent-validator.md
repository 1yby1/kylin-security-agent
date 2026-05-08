# Security Intent Validator

The security intent validator is the mandatory pre-execution gate for every
Agent tool call. It runs before any tool handler is invoked.

## Risk Levels

| Level | Examples | Default Policy |
| --- | --- | --- |
| Low | View system status, processes, ports, logs, service status | Allow and audit |
| Medium | Restart allowlisted services, clean safe temp directories, kill non-system processes | Require `operator` or `admin` role and secondary confirmation |
| High | Delete system directories, modify system config, change permissions, stop protected services, modify users | Block by default |
| Prohibited | `rm -rf /`, `chmod 777 /etc/passwd`, `mkfs`, `dd of=/dev/...`, closing firewall, deleting `/etc`, `/bin`, `/usr`, `/var` | Always block |

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

The role can be passed in request context as `user_role`. If omitted, user id
`admin` maps to admin, ids starting with `operator` map to operator, and all
others map to viewer.

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

The execution response includes a `security` object with risk level, blocked
state, confirmation requirement, reasons, and all check results.

