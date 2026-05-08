from __future__ import annotations

import os
from typing import Any

from backend.mcp_tools.command_runner import run_template
from backend.security.rules import SERVICE_RESTART_ALLOWLIST


def run(arguments: dict[str, Any]) -> dict[str, Any]:
    service_name = str(arguments.get("service_name", "")).strip()
    if not service_name:
        return {"error": "service_name is required"}
    if service_name not in SERVICE_RESTART_ALLOWLIST:
        return {
            "error": f"service is not in restart allowlist: {service_name}",
            "allowlist": sorted(SERVICE_RESTART_ALLOWLIST),
        }
    if os.name == "nt":
        return {"error": "service.restart is only supported on Kylin/Linux with systemd"}

    try:
        restart_result = run_template("service.restart", {"service_name": service_name}, timeout=15)
        status_result = run_template("service.status", {"service_name": service_name}, timeout=8)
    except Exception as exc:  # pragma: no cover - platform dependent
        return {"error": str(exc)}

    output = {
        "service_name": service_name,
        "restart": restart_result.to_dict(limit=40),
        "status": status_result.to_dict(limit=40),
    }
    output["analysis"] = {
        "restart_exit_code": restart_result.exit_code,
        "status_exit_code": status_result.exit_code,
        "succeeded": restart_result.exit_code == 0,
    }
    return output
