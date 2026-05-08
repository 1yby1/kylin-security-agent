from __future__ import annotations

from typing import Any

from backend.mcp_tools.command_runner import run_template


def run(arguments: dict[str, Any]) -> dict[str, Any]:
    service_name = arguments.get("service_name")
    try:
        if service_name:
            result = run_template("service.status", {"service_name": service_name}, timeout=8)
        else:
            result = run_template("service.list", timeout=8)
    except Exception as exc:  # pragma: no cover - platform dependent
        return {"error": str(exc)}
    output = result.to_dict(limit=int(arguments.get("limit", 40)))
    output["analysis"] = _analyze_services(result.stdout)
    return output


def _analyze_services(rows: list[str]) -> dict[str, Any]:
    active = 0
    failed = 0
    inactive = 0
    for row in rows:
        lowered = row.lower()
        if " failed " in lowered or lowered.strip().endswith(" failed"):
            failed += 1
        elif " inactive " in lowered:
            inactive += 1
        elif " active " in lowered or " running " in lowered:
            active += 1
    return {
        "active_count": active,
        "inactive_count": inactive,
        "failed_count": failed,
    }
