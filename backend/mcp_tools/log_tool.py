from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from backend.mcp_tools.command_runner import run_optional_template
from backend.security.rules import SAFE_LOG_DIRS


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

    path, error = resolve_log_path(log_path)
    if error:
        return {"error": error}

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


def resolve_log_path(value: Any) -> tuple[Path, str]:
    path = Path(str(value)).expanduser()
    if not path.exists() or not path.is_file():
        return path, f"log file not found: {path}"

    resolved = path.resolve(strict=True)
    allowed_dirs = _allowed_log_dirs()
    if not any(_is_relative_to(resolved, allowed_dir) for allowed_dir in allowed_dirs):
        allowed = ", ".join(str(item) for item in allowed_dirs)
        return resolved, f"log file is outside allowed log directories: {resolved}; allowed: {allowed}"
    return resolved, ""


def _allowed_log_dirs() -> list[Path]:
    configured = os.getenv("AGENT_ALLOWED_LOG_DIRS", "")
    values = [item for item in configured.split(os.pathsep) if item.strip()] if configured else list(SAFE_LOG_DIRS)
    return [Path(value).expanduser().resolve(strict=False) for value in values]


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


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
