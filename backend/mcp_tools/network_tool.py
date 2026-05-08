from __future__ import annotations

from typing import Any

from backend.mcp_tools.command_runner import run_optional_template, run_template


def run(arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        result = run_template("network.ports", timeout=5)
    except Exception as exc:  # pragma: no cover - platform dependent
        return {"error": str(exc)}
    output = result.to_dict(limit=int(arguments.get("limit", 30)))
    output["analysis"] = _analyze_port_rows(result.stdout)
    if arguments.get("include_lsof", True):
        output["lsof"] = run_optional_template("network.lsof", timeout=5)
    return output


def _analyze_port_rows(rows: list[str]) -> dict[str, Any]:
    listening = []
    for row in rows:
        if "LISTEN" in row or "LISTENING" in row:
            listening.append(row)
    return {
        "listening_count": len(listening),
        "listening_sample": listening[:10],
    }
