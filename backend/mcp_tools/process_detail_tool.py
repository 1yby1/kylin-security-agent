from __future__ import annotations

from typing import Any

from backend.mcp_tools.command_runner import run_template


def run(arguments: dict[str, Any]) -> dict[str, Any]:
    pid, error = _parse_pid(arguments.get("pid"))
    if error:
        return {"error": error}

    try:
        result = run_template("process.by_pid", {"pid": str(pid)}, timeout=5)
    except Exception as exc:  # pragma: no cover - platform dependent
        return {"error": str(exc), "pid": pid}

    output = result.to_dict(limit=20)
    output["analysis"] = _analyze_process_detail(pid, result.exit_code, result.stdout, result.stderr)
    return output


def _parse_pid(value: Any) -> tuple[int, str]:
    if isinstance(value, bool):
        return 0, "pid must be integer"
    try:
        pid = int(value)
    except (TypeError, ValueError):
        return 0, "pid must be integer"
    if pid <= 0:
        return 0, "pid must be positive"
    return pid, ""


def _analyze_process_detail(
    pid: int,
    exit_code: int,
    stdout: list[str],
    stderr: list[str],
) -> dict[str, Any]:
    if exit_code != 0:
        return {
            "pid": pid,
            "exists": False,
            "parse_warning": "process lookup command returned non-zero exit code",
            "stderr": stderr[:5],
        }

    linux_detail = _parse_linux_ps(stdout)
    if linux_detail:
        return {
            "pid": pid,
            "exists": True,
            "platform": "linux",
            "process": linux_detail,
            "parse_warning": "",
        }

    windows_detail = _parse_windows_tasklist(stdout)
    if windows_detail:
        return {
            "pid": pid,
            "exists": True,
            "platform": "windows",
            "process": windows_detail,
            "parse_warning": "",
        }

    return {
        "pid": pid,
        "exists": False,
        "parse_warning": "no structured process detail parsed from command output",
    }


def _parse_linux_ps(rows: list[str]) -> dict[str, Any]:
    compact_rows = [row.strip() for row in rows if row.strip()]
    if not compact_rows:
        return {}

    parts = compact_rows[0].split(maxsplit=5)
    if len(parts) < 5:
        return {}

    return {
        "pid": _safe_int(parts[0]),
        "ppid": _safe_int(parts[1]),
        "user": parts[2],
        "stat": parts[3],
        "command": parts[4],
        "args": parts[5] if len(parts) > 5 else "",
    }


def _parse_windows_tasklist(rows: list[str]) -> dict[str, Any]:
    fields: dict[str, str] = {}
    for row in rows:
        if ":" not in row:
            continue
        key, value = row.split(":", maxsplit=1)
        fields[key.strip().lower()] = value.strip()

    if not fields:
        return {}

    pid = _safe_int(fields.get("pid", "0"))
    if pid <= 0:
        return {}

    return {
        "pid": pid,
        "image_name": fields.get("image name", ""),
        "session_name": fields.get("session name", ""),
        "session_number": fields.get("session#", ""),
        "memory_usage": fields.get("mem usage", ""),
    }


def _safe_int(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return 0
