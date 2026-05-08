from __future__ import annotations

from typing import Any

from backend.mcp_tools.command_runner import run_optional_template, run_template


def run(arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        result = run_template("process.list", timeout=5)
    except Exception as exc:  # pragma: no cover - platform dependent
        return {"error": str(exc)}
    output = result.to_dict(limit=int(arguments.get("limit", 20)))
    output["analysis"] = _analyze_process_rows(result.stdout)
    if arguments.get("include_tree"):
        output["tree"] = run_optional_template("process.tree", timeout=5)
    return output


def _analyze_process_rows(rows: list[str]) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for row in rows[1:]:
        parts = row.split()
        if len(parts) < 5:
            continue
        try:
            candidates.append(
                {
                    "pid": parts[0],
                    "ppid": parts[1],
                    "command": parts[2],
                    "cpu_percent": float(parts[3]),
                    "memory_percent": float(parts[4]),
                }
            )
        except ValueError:
            continue
    return {
        "process_count": len(candidates),
        "top_cpu": candidates[:5],
        "top_memory": sorted(candidates, key=lambda item: item["memory_percent"], reverse=True)[:5],
    }
