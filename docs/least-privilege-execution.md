# Least Privilege Execution

This stage implements the competition requirement: do not use root unless it is
strictly necessary.

## Dedicated User

Production deployment uses a dedicated system account:

```text
software-cup-agent:software-cup-agent
```

The user is created by:

```bash
sudo bash deploy/setup-agent-user.sh
```

The account has:

- no login shell: `/sbin/nologin`
- state directory: `/var/lib/software-cup-ops`
- audit log directory: `/var/log/software-cup-ops`
- safe temporary directory: `/opt/software-cup-ops/tmp`
- optional membership in `systemd-journal` for read-only journal access

## Runtime Directories

Production paths are configured through environment variables:

```text
AGENT_DB_PATH=/var/lib/software-cup-ops/app.db
AGENT_AUDIT_LOG_PATH=/var/log/software-cup-ops/audit.log
AGENT_SAFE_WORKDIR=/
```

This avoids writing runtime data into the source code directory.

## Command Execution Identity

All OS commands still go through `backend/mcp_tools/command_runner.py`.
Before `subprocess.run()`, the runner calls
`backend/security/least_privilege.py`.

Behavior:

- Windows development: identity switching is not enforced, but identity metadata is returned.
- Kylin/Linux service running as `software-cup-agent`: commands inherit that non-root identity.
- Kylin/Linux process accidentally running as root: commands are dropped to `software-cup-agent`.
- Strict mode with root and missing dedicated user: command execution is refused.

Every command result includes:

```json
{
  "execution_identity": {
    "current_user": "...",
    "target_user": "software-cup-agent",
    "runs_as_user": "...",
    "least_privilege_enforced": true
  }
}
```

## Systemd Hardening

`deploy/systemd.service` sets:

- `User=software-cup-agent`
- `Group=software-cup-agent`
- `NoNewPrivileges=true`
- `PrivateTmp=true`
- `ProtectSystem=strict`
- `ProtectHome=true`
- `ReadWritePaths=/var/lib/software-cup-ops /var/log/software-cup-ops /opt/software-cup-ops/tmp`
- empty `CapabilityBoundingSet`
- empty `AmbientCapabilities`

## Runtime Check API

```bash
curl http://127.0.0.1:8000/api/security/runtime
```

This returns the current process identity, target execution identity, and whether
least privilege enforcement is active.

