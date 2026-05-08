from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from backend.mcp_tools.command_runner import run_optional_template


def run(arguments: dict[str, Any]) -> dict[str, Any]:
    source = str(arguments.get("source", "journal"))
    if source == "journal":
        if os.name == "nt":
            return {
                "source": "journal",
                "message": "journalctl is available on Kylin/Linux. Use source=file and log_path on Windows.",
                "analysis": _analyze_logs([]),
            }
        return _run_journal(arguments)

    log_path = arguments.get("log_path")
    if not log_path:
        return {
            "message": "No log_path provided. Pass source=file and context.log_path to inspect a specific log file.",
            "lines": [],
        }

    path = Path(str(log_path)).expanduser()
    if not path.exists() or not path.is_file():
        return {"error": f"log file not found: {path}"}

    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    recent = lines[-_clamp_lines(arguments.get("lines", 100)) :]
    return {"source": "file", "path": str(path), "lines": recent, "analysis": _analyze_logs(recent)}


def _run_journal(arguments: dict[str, Any]) -> dict[str, Any]:
    lines = str(_clamp_lines(arguments.get("lines", 100)))
    priority = arguments.get("priority")
    unit = arguments.get("unit") or arguments.get("service_name")
    if unit:
        result = run_optional_template("log.journal_unit", {"unit": unit, "lines": lines}, timeout=8)
    elif priority:
        result = run_optional_template("log.journal_priority", {"priority": priority, "lines": lines}, timeout=8)
    else:
        result = run_optional_template("log.journal", {"lines": lines}, timeout=8)

    stdout = result.get("stdout", [])
    return {"source": "journal", **result, "analysis": _analyze_logs(stdout if isinstance(stdout, list) else [])}


def _clamp_lines(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 100
    return max(1, min(parsed, 500))


def _analyze_logs(lines: list[str]) -> dict[str, Any]:
    keywords = {
        "error": ["error", "failed", "failure", "异常", "错误", "失败"],
        "warning": ["warning", "warn", "告警", "警告"],
        "permission": ["permission", "denied", "权限", "拒绝"],
    }
    lowered = [line.lower() for line in lines]
    return {
        name: sum(1 for line in lowered if any(keyword in line for keyword in variants))
        for name, variants in keywords.items()
    }
