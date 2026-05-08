# Controlled Operation Tools

This document records medium-risk operation tools. These tools are not pure
perception tools, so they must pass security validation before execution.

## `process.kill`

Purpose:

- Send `TERM` to one specified non-system process.
- Inspect the target process before sending the signal.
- Support `dry_run` preview mode.

Tool module:

- `backend/mcp_tools/process_kill_tool.py`

Command templates:

- `process.by_pid`: `ps -p {pid} -o pid=,ppid=,user=,stat=,comm=,args=`
- `process.kill`: `kill -TERM {pid}`

Required argument:

```json
{
  "pid": 1234
}
```

Optional arguments:

```json
{
  "expected_name": "python",
  "dry_run": true
}
```

Risk policy:

- Risk level: `medium`
- Required role: `operator` or `admin`
- Required confirmation: `approved=true`
- Only `TERM` is supported; `kill -9` is blocked as a dangerous command.

Expected safety behavior:

- Missing `pid`: blocked by parameter schema.
- PID in the protected system range: blocked.
- Viewer role: blocked by permission check.
- `approved=false`: blocked by secondary confirmation.
- Protected process names such as `systemd`, `sshd`, `auditd`, `firewalld`: blocked.
- Root/system-owned process targets: blocked by the tool before signal execution.
- Windows development environment: tool returns unsupported platform and does not kill.

## `service.restart`

Purpose:

- Restart one allowlisted systemd service.
- Query service status after restart.

Tool module:

- `backend/mcp_tools/service_restart_tool.py`

Command templates:

- `service.restart`: `systemctl restart {service_name}`
- `service.status`: `systemctl status {service_name} --no-pager`

Required argument:

```json
{
  "service_name": "nginx"
}
```

Risk policy:

- Risk level: `medium`
- Required role: `operator` or `admin`
- Required confirmation: `approved=true`
- Tool is blocked if `service_name` is not in `SERVICE_RESTART_ALLOWLIST`

Current restart allowlist:

- `nginx`
- `httpd`
- `mysqld`
- `postgresql`
- `redis`
- `software-cup-ops`

Example request:

```json
{
  "query": "重启 nginx 服务",
  "user_id": "operator1",
  "approved": true,
  "context": {
    "user_role": "operator",
    "service_name": "nginx"
  }
}
```

Expected safety behavior:

- Missing `service_name`: blocked by parameter schema.
- Viewer role: blocked by permission check.
- `approved=false`: blocked by secondary confirmation.
- Non-allowlisted service: high risk and blocked by default.
- Protected/firewall service: blocked by safety policy.

## `temp.clean`

Purpose:

- Clean old files or child directories under safe temporary directories.
- Support `dry_run` preview mode.
- Never clean the target directory itself.
- Skip symbolic links.

Tool module:

- `backend/mcp_tools/temp_clean_tool.py`

Required argument:

```json
{
  "path": "/tmp"
}
```

Optional arguments:

```json
{
  "max_age_hours": 24,
  "limit": 200,
  "dry_run": true
}
```

Risk policy:

- Risk level: `medium`
- Required role: `operator` or `admin`
- Required confirmation: `approved=true`
- Allowed paths only:
  - `/tmp`
  - `/var/tmp`
  - `/opt/software-cup-ops/tmp`

Example request:

```json
{
  "query": "清理 /tmp 临时文件",
  "user_id": "operator1",
  "approved": true,
  "context": {
    "user_role": "operator",
    "path": "/tmp",
    "max_age_hours": 24,
    "limit": 200,
    "dry_run": true
  }
}
```

Expected safety behavior:

- Missing `path`: blocked by parameter schema.
- Path outside safe temp directories: blocked.
- Relative paths, `..` traversal paths, and symbolic-link targets escaping the safe temp directories: blocked.
- Viewer role: blocked by permission check.
- `approved=false`: blocked by secondary confirmation.
- Windows development environment: tool returns unsupported platform and does not delete.
