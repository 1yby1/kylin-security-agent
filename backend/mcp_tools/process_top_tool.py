from __future__ import annotations

from typing import Any

from backend.mcp_tools.command_runner import run_template


def run(arguments: dict[str, Any]) -> dict[str, Any]:
    metric = str(arguments.get("metric", "cpu")).strip().lower()
    if metric not in {"cpu", "memory"}:
        return {"error": "metric must be cpu or memory"}

    limit = _clamp_int(arguments.get("limit", 10), minimum=1, maximum=50)
    min_percent = _clamp_int(arguments.get("min_percent", 0), minimum=0, maximum=10000)

    try:
        result = run_template("process.list", timeout=5)
    except Exception as exc:  # pragma: no cover - platform dependent
        return {"error": str(exc)}

    processes = _parse_process_rows(result.stdout)
    sort_key = "cpu_percent" if metric == "cpu" else "memory_percent"
    ranked = sorted(processes, key=lambda item: item[sort_key], reverse=True)
    filtered = [item for item in ranked if item[sort_key] >= min_percent]

    output = result.to_dict(limit=limit + 1)
    output["analysis"] = {
        "metric": metric,
        "process_count": len(processes),
        "matched_count": len(filtered),
        "min_percent": min_percent,
        "top_processes": filtered[:limit],
        "parse_warning": "" if processes else "no structured process rows parsed from command output",
    }
    return output


def _parse_process_rows(rows: list[str]) -> list[dict[str, Any]]:
    processes: list[dict[str, Any]] = []
    for row in rows[1:]:
        parts = row.split()
        if len(parts) < 5:
            continue
        try:
            processes.append(
                {
                    "pid": int(parts[0]),
                    "ppid": int(parts[1]),
                    "command": parts[2],
                    "cpu_percent": float(parts[3]),
                    "memory_percent": float(parts[4]),
                }
            )
        except ValueError:
            continue
    return processes


def _clamp_int(value: Any, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = minimum
    return max(minimum, min(parsed, maximum))
