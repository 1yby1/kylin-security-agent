from __future__ import annotations

import os
import re
import socket
import subprocess
from typing import Any

from backend.config import get_runtime_settings
from backend.security.least_privilege import subprocess_security_options


ALLOWED_TARGETS = {
    "localhost",
    "127.0.0.1",
    "::1",
    "updates.kylinos.cn",
    "mirrors.aliyun.com",
    "repo.huaweicloud.com",
    "mirrors.tuna.tsinghua.edu.cn",
    "www.baidu.com",
    "114.114.114.114",
    "223.5.5.5",
    "8.8.8.8",
}

TARGET_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,253}$")


def run(arguments: dict[str, Any]) -> dict[str, Any]:
    target = _normalize_target(arguments.get("target"))
    if not target:
        return {"error": "target is required"}
    if not TARGET_PATTERN.fullmatch(target):
        return {"error": f"target contains unsafe characters: {target}"}
    if target not in ALLOWED_TARGETS:
        return {
            "error": f"target is not in allowlist: {target}",
            "allowed_targets": sorted(ALLOWED_TARGETS),
        }

    count = _clamp_int(arguments.get("count"), default=3, minimum=1, maximum=5)
    timeout_seconds = _clamp_int(arguments.get("timeout_seconds"), default=3, minimum=1, maximum=10)
    run_dns = bool(arguments.get("dns", True))
    run_ping = bool(arguments.get("ping", True))

    dns_result = _resolve_dns(target) if run_dns else {"skipped": True, "addresses": []}
    ping_result = _ping(target, count=count, timeout_seconds=timeout_seconds) if run_ping else {"skipped": True}

    dns_resolved = bool(dns_result.get("addresses"))
    ping_reachable = ping_result.get("exit_code") == 0 if not ping_result.get("skipped") else None
    return {
        "target": target,
        "allowed_targets": sorted(ALLOWED_TARGETS),
        "dns": dns_result,
        "ping": ping_result,
        "analysis": {
            "dns_resolved": dns_resolved,
            "ping_reachable": ping_reachable,
            "diagnostic_completed": True,
            "read_only": True,
        },
    }


def _resolve_dns(target: str) -> dict[str, Any]:
    try:
        addresses = []
        seen = set()
        for item in socket.getaddrinfo(target, None):
            sockaddr = item[4]
            if not sockaddr:
                continue
            address = str(sockaddr[0])
            if address in seen:
                continue
            seen.add(address)
            addresses.append(address)
        return {"addresses": addresses, "error": ""}
    except socket.gaierror as exc:
        return {"addresses": [], "error": str(exc)}


def _ping(target: str, *, count: int, timeout_seconds: int) -> dict[str, Any]:
    command = _ping_command(target, count=count, timeout_seconds=timeout_seconds)
    try:
        runtime_settings = get_runtime_settings()
        security_options, identity = subprocess_security_options(runtime_settings)
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=count * timeout_seconds + 3,
            check=False,
            cwd=runtime_settings.safe_workdir if os.name != "nt" else None,
            **security_options,
        )
        return {
            "command": " ".join(command),
            "exit_code": completed.returncode,
            "stdout": completed.stdout.splitlines()[:40],
            "stderr": completed.stderr.splitlines()[:40],
            "execution_identity": identity.to_dict(),
        }
    except Exception as exc:  # pragma: no cover - depends on target OS utilities
        return {
            "command": " ".join(command),
            "exit_code": None,
            "stdout": [],
            "stderr": [str(exc)],
        }


def _ping_command(target: str, *, count: int, timeout_seconds: int) -> list[str]:
    if os.name == "nt":
        return ["ping", "-n", str(count), "-w", str(timeout_seconds * 1000), target]
    return ["ping", "-c", str(count), "-W", str(timeout_seconds), target]


def _normalize_target(value: Any) -> str:
    return str(value or "").strip().lower().rstrip(".")


def _clamp_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))
