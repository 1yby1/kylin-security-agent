from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from backend.mcp_tools.command_runner import run_optional_template


def run(arguments: dict[str, Any]) -> dict[str, Any]:
    include_addr = bool(arguments.get("include_addr", True))
    include_route = bool(arguments.get("include_route", True))
    include_dns = bool(arguments.get("include_dns", True))

    addr = run_optional_template("network.addr", timeout=5) if include_addr else {"skipped": True}
    route = run_optional_template("network.route", timeout=5) if include_route else {"skipped": True}
    dns = _read_resolv_conf() if include_dns else {"skipped": True, "nameservers": [], "search": []}

    route_lines = route.get("stdout", []) if isinstance(route, dict) else []
    addr_lines = addr.get("stdout", []) if isinstance(addr, dict) else []
    default_gateway = _extract_default_gateway(route_lines)
    interfaces = _extract_interfaces(addr_lines)
    dns_servers = dns.get("nameservers", []) if isinstance(dns, dict) else []

    return {
        "addr": addr,
        "route": route,
        "dns": dns,
        "analysis": {
            "default_gateway": default_gateway,
            "dns_servers": dns_servers,
            "interface_count": len(interfaces),
            "interfaces": interfaces[:20],
            "has_ipv4": _has_ipv4(addr_lines),
            "read_only": True,
        },
    }


def _read_resolv_conf(path: str | Path | None = None) -> dict[str, Any]:
    resolv_path = Path(path or ("/etc/resolv.conf" if os.name != "nt" else r"C:\Windows\System32\drivers\etc\hosts"))
    try:
        lines = resolv_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError as exc:
        return {"path": str(resolv_path), "nameservers": [], "search": [], "error": str(exc)}

    nameservers: list[str] = []
    search: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        if parts[0] == "nameserver":
            nameservers.append(parts[1])
        elif parts[0] == "search":
            search.extend(parts[1:])
    return {"path": str(resolv_path), "nameservers": nameservers, "search": search}


def _extract_default_gateway(lines: list[str]) -> str:
    for line in lines:
        match = re.search(r"\bdefault\s+via\s+([^\s]+)", line)
        if match:
            return match.group(1)
        if line.strip().startswith("0.0.0.0"):
            parts = line.split()
            if len(parts) >= 3:
                return parts[2]
    return ""


def _extract_interfaces(lines: list[str]) -> list[dict[str, str]]:
    interfaces: list[dict[str, str]] = []
    current = ""
    for line in lines:
        header = re.match(r"^\d+:\s+([^:@]+)", line)
        if header:
            current = header.group(1)
            interfaces.append({"name": current, "ipv4": ""})
            continue
        if not current:
            continue
        ipv4 = re.search(r"\binet\s+(\d+\.\d+\.\d+\.\d+/\d+)", line)
        if ipv4 and interfaces:
            interfaces[-1]["ipv4"] = ipv4.group(1)
    return interfaces


def _has_ipv4(lines: list[str]) -> bool:
    return any(re.search(r"\binet\s+\d+\.\d+\.\d+\.\d+/\d+", line) for line in lines)
