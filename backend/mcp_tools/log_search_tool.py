from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from backend.mcp_tools.command_runner import run_optional_template
from backend.mcp_tools.log_tool import _analyze_logs


def run(arguments: dict[str, Any]) -> dict[str, Any]:
    keyword = str(arguments.get("keyword", "")).strip()
    if not keyword:
        return {"error": "keyword is required"}

    source = str(arguments.get("source", "journal")).strip() or "journal"
    case_sensitive = bool(arguments.get("case_sensitive", False))
    limit = _clamp_int(arguments.get("limit", 50), minimum=1, maximum=200)
    lines = _clamp_int(arguments.get("lines", 200), minimum=1, maximum=1000)

    if source == "file":
        return _search_file(arguments, keyword, case_sensitive, limit, lines)
    if source == "journal":
        return _search_journal(arguments, keyword, case_sensitive, limit, lines)
    return {"error": "source must be journal or file"}


def _search_file(
    arguments: dict[str, Any],
    keyword: str,
    case_sensitive: bool,
    limit: int,
    lines: int,
) -> dict[str, Any]:
    log_path = str(arguments.get("log_path", "")).strip()
    if not log_path:
        return {"error": "log_path is required when source=file"}

    path = Path(log_path).expanduser()
    if not path.exists() or not path.is_file():
        return {"error": f"log file not found: {path}"}

    content = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    scanned = content[-lines:]
    matches = _find_keyword_matches(scanned, keyword, case_sensitive, limit, line_offset=len(content) - len(scanned))
    return {
        "source": "file",
        "path": str(path),
        "keyword": keyword,
        "case_sensitive": case_sensitive,
        "scanned_line_count": len(scanned),
        "matched_count": len(matches),
        "matches": matches,
        "analysis": _build_analysis(keyword, scanned, matches),
    }


def _search_journal(
    arguments: dict[str, Any],
    keyword: str,
    case_sensitive: bool,
    limit: int,
    lines: int,
) -> dict[str, Any]:
    if os.name == "nt":
        return {"error": "journal search is only supported on Kylin/Linux. Use source=file on Windows."}

    params = {"lines": str(lines)}
    unit = str(arguments.get("unit") or arguments.get("service_name") or "").strip()
    priority = str(arguments.get("priority") or "").strip()
    if unit:
        result = run_optional_template("log.journal_unit", {"unit": unit, **params}, timeout=8)
    elif priority:
        result = run_optional_template("log.journal_priority", {"priority": priority, **params}, timeout=8)
    else:
        result = run_optional_template("log.journal", params, timeout=8)

    stdout = result.get("stdout", [])
    rows = stdout if isinstance(stdout, list) else []
    matches = _find_keyword_matches(rows, keyword, case_sensitive, limit)
    return {
        "source": "journal",
        **result,
        "keyword": keyword,
        "case_sensitive": case_sensitive,
        "scanned_line_count": len(rows),
        "matched_count": len(matches),
        "matches": matches,
        "analysis": _build_analysis(keyword, rows, matches),
    }


def _find_keyword_matches(
    lines: list[str],
    keyword: str,
    case_sensitive: bool = False,
    limit: int = 50,
    line_offset: int = 0,
) -> list[dict[str, Any]]:
    needle = keyword if case_sensitive else keyword.lower()
    matches: list[dict[str, Any]] = []
    for index, line in enumerate(lines, start=1):
        haystack = line if case_sensitive else line.lower()
        if needle not in haystack:
            continue
        matches.append({"line_number": line_offset + index, "line": line})
        if len(matches) >= limit:
            break
    return matches


def _build_analysis(keyword: str, lines: list[str], matches: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "keyword": keyword,
        "matched": bool(matches),
        "matched_count": len(matches),
        "log_signals": _analyze_logs([str(item["line"]) for item in matches] or lines),
    }


def _clamp_int(value: Any, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = minimum
    return max(minimum, min(parsed, maximum))
