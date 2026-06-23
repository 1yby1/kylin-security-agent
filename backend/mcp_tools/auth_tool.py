from __future__ import annotations

import os
import re
from collections import Counter
from typing import Any

from backend.mcp_tools.command_runner import run_optional_template

WINDOWS_MESSAGE = "该安全工具面向麒麟/Linux，开发环境不可用。"
_IP_PATTERN = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def run(arguments: dict[str, Any]) -> dict[str, Any]:
    if os.name == "nt":
        return {"platform": "windows", "message": WINDOWS_MESSAGE, "analysis": _analyze({}, {}, {})}
    lines = str(_clamp_lines(arguments.get("lines", 20)))
    last = run_optional_template("auth.last", {"lines": lines}, timeout=8)
    failed = run_optional_template("auth.lastb", {"lines": lines}, timeout=8)
    sessions = run_optional_template("auth.who", timeout=8)
    return {
        "source": "auth",
        "last": last,
        "lastb": failed,
        "who": sessions,
        "analysis": _analyze(last, failed, sessions),
    }


def _analyze(last: dict[str, Any], failed: dict[str, Any], sessions: dict[str, Any]) -> dict[str, Any]:
    last_lines = _entry_lines(last)
    failed_lines = _entry_lines(failed)
    who_lines = [line for line in _stdout(sessions) if line.strip()]
    return {
        "success_login_count": len(last_lines),
        "failed_login_count": len(failed_lines),
        "active_sessions": len(who_lines),
        "root_remote_login": _has_root_remote(last_lines),
        "top_source_ips": _top_source_ips(last_lines + failed_lines),
        "failed_log_readable": "error" not in failed,
    }


def _stdout(result: dict[str, Any]) -> list[str]:
    value = result.get("stdout") if isinstance(result, dict) else None
    return value if isinstance(value, list) else []


def _entry_lines(result: dict[str, Any]) -> list[str]:
    entries: list[str] = []
    for line in _stdout(result):
        stripped = line.strip()
        if not stripped:
            continue
        lowered = stripped.lower()
        if lowered.startswith("wtmp begins") or lowered.startswith("btmp begins"):
            continue
        entries.append(stripped)
    return entries


def _has_root_remote(lines: list[str]) -> bool:
    return any(line.startswith("root") and _IP_PATTERN.search(line) for line in lines)


def _top_source_ips(lines: list[str], top: int = 3) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for line in lines:
        for ip in _IP_PATTERN.findall(line):
            counter[ip] += 1
    return dict(counter.most_common(top))


def _clamp_lines(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 20
    return max(1, min(parsed, 200))
