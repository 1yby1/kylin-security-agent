from __future__ import annotations

import os
from typing import Any

from backend.mcp_tools.command_runner import run_optional_template

WINDOWS_MESSAGE = "该安全工具面向麒麟/Linux，开发环境不可用。"
_HIGH_RISK_PORTS = {"23", "21", "3389", "445", "135", "139"}
_HIGH_RISK_SERVICES = {"telnet", "rdp", "ftp", "samba"}


def run(arguments: dict[str, Any]) -> dict[str, Any]:
    if os.name == "nt":
        return {"platform": "windows", "message": WINDOWS_MESSAGE, "analysis": _analyze({}, {})}
    state = run_optional_template("firewall.state", timeout=8)
    listing = run_optional_template("firewall.list_all", timeout=8)
    return {"source": "firewall", "state": state, "list_all": listing, "analysis": _analyze(state, listing)}


def _analyze(state: dict[str, Any], listing: dict[str, Any]) -> dict[str, Any]:
    state_text = " ".join(_stdout(state)).lower()
    running = "running" in state_text and "not running" not in state_text
    ports = _parse_field(listing, "ports:")
    services = _parse_field(listing, "services:")
    high_risk = sorted(
        {port.split("/")[0] for port in ports if port.split("/")[0] in _HIGH_RISK_PORTS}
        | {service for service in services if service in _HIGH_RISK_SERVICES}
    )
    return {
        "running": running,
        "open_port_count": len(ports),
        "open_service_count": len(services),
        "open_ports": ports,
        "open_services": services,
        "high_risk_exposed": high_risk,
        "readable": "error" not in listing,
    }


def _stdout(result: dict[str, Any]) -> list[str]:
    value = result.get("stdout") if isinstance(result, dict) else None
    return value if isinstance(value, list) else []


def _parse_field(listing: dict[str, Any], field: str) -> list[str]:
    for line in _stdout(listing):
        stripped = line.strip()
        if stripped.startswith(field):
            return stripped[len(field):].split()
    return []
