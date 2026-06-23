from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from typing import Any

from backend.config import get_runtime_settings
from backend.security.least_privilege import subprocess_security_options


SAFE_PARAM = re.compile(r"^[A-Za-z0-9_.@:-]{1,128}$")

COMMAND_TEMPLATES: dict[str, dict[str, list[str]]] = {
    "windows": {
        "system.info": ["systeminfo"],
        "system.hostname": ["hostname"],
        "process.list": ["tasklist"],
        "network.ports": ["netstat", "-ano"],
        "network.addr": ["ipconfig", "/all"],
        "network.route": ["route", "print"],
        "service.list": ["sc", "query"],
        "service.status": ["sc", "query", "{service_name}"],
    },
    "linux": {
        "system.uname": ["uname", "-a"],
        "system.hostnamectl": ["hostnamectl"],
        "system.uptime": ["uptime"],
        "system.cpu": ["lscpu"],
        "system.memory": ["free", "-m"],
        "system.disk": ["df", "-h"],
        "process.list": ["ps", "-eo", "pid,ppid,comm,%cpu,%mem", "--sort=-%cpu"],
        "process.tree": ["ps", "-ejH", "-o", "pid,ppid,stat,comm"],
        "process.by_pid": ["ps", "-p", "{pid}", "-o", "pid=,ppid=,user=,stat=,comm=,args="],
        "process.kill": ["kill", "-TERM", "{pid}"],
        "network.ports": ["ss", "-tulpen"],
        "network.lsof": ["lsof", "-i", "-P", "-n"],
        "network.addr": ["ip", "addr", "show"],
        "network.route": ["ip", "route", "show"],
        "log.journal": ["journalctl", "-n", "{lines}", "--no-pager"],
        "log.journal_priority": ["journalctl", "-p", "{priority}", "-n", "{lines}", "--no-pager"],
        "log.journal_unit": ["journalctl", "-u", "{unit}", "-n", "{lines}", "--no-pager"],
        "service.list": ["systemctl", "list-units", "--type=service", "--no-pager"],
        "service.status": ["systemctl", "status", "{service_name}", "--no-pager"],
        "service.restart": ["systemctl", "restart", "{service_name}"],
        "auth.last": ["last", "-n", "{lines}"],
        "auth.lastb": ["lastb", "-n", "{lines}"],
        "auth.who": ["who"],
        "firewall.state": ["firewall-cmd", "--state"],
        "firewall.list_all": ["firewall-cmd", "--list-all"],
        "privilege.suid": ["find", "/usr/bin", "/usr/sbin", "/bin", "/sbin", "/usr/local/bin", "-xdev", "-perm", "-4000", "-type", "f"],
        "privilege.sgid": ["find", "/usr/bin", "/usr/sbin", "/bin", "/sbin", "/usr/local/bin", "-xdev", "-perm", "-2000", "-type", "f"],
        "privilege.uid0": ["awk", "-F:", "($3 == 0) {print $1}", "/etc/passwd"],
        "privilege.empty_password": ["awk", "-F:", "($2 == \"\") {print $1}", "/etc/shadow"],
        "package.repolist.dnf": ["dnf", "repolist", "--enabled"],
        "package.repolist.yum": ["yum", "repolist", "--enabled"],
    },
}


@dataclass(frozen=True)
class CommandResult:
    command: list[str]
    exit_code: int
    stdout: list[str]
    stderr: list[str]
    execution_identity: dict[str, Any]

    def to_dict(self, limit: int = 40) -> dict[str, Any]:
        return {
            "command": " ".join(self.command),
            "exit_code": self.exit_code,
            "stdout": self.stdout[:limit],
            "stderr": self.stderr[:limit],
            "execution_identity": self.execution_identity,
        }


def run_template(name: str, params: dict[str, Any] | None = None, timeout: int = 8) -> CommandResult:
    platform_key = "windows" if os.name == "nt" else "linux"
    templates = COMMAND_TEMPLATES[platform_key]
    if name not in templates:
        raise ValueError(f"Command template is not whitelisted: {name}")

    command = [_render_part(part, params or {}) for part in templates[name]]
    runtime_settings = get_runtime_settings()
    security_options, identity = subprocess_security_options(runtime_settings)
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        cwd=runtime_settings.safe_workdir if os.name != "nt" else None,
        **security_options,
    )
    return CommandResult(
        command=command,
        exit_code=completed.returncode,
        stdout=completed.stdout.splitlines(),
        stderr=completed.stderr.splitlines(),
        execution_identity=identity.to_dict(),
    )


def run_optional_template(name: str, params: dict[str, Any] | None = None, timeout: int = 8) -> dict[str, Any]:
    try:
        return run_template(name, params=params, timeout=timeout).to_dict()
    except Exception as exc:  # pragma: no cover - depends on target OS utilities
        return {"error": str(exc)}


def _render_part(part: str, params: dict[str, Any]) -> str:
    if not part.startswith("{"):
        return part
    key = part.strip("{}")
    value = str(params.get(key, ""))
    if not SAFE_PARAM.fullmatch(value):
        raise ValueError(f"Unsafe command parameter: {key}")
    return value
