from __future__ import annotations

import os
import time
from typing import Any

from backend.mcp_tools.command_runner import run_template
from backend.security.rules import (
    PROTECTED_PID_MAX,
    PROTECTED_PROCESS_NAMES,
    PROTECTED_PROCESS_USERS,
)


def run(arguments: dict[str, Any]) -> dict[str, Any]:
    pid, error = _parse_pid(arguments.get("pid"))
    if error:
        return {"error": error}
    if os.name == "nt":
        return {"error": "process.kill is only supported on Kylin/Linux"}

    expected_name = str(arguments.get("expected_name", "")).strip()
    dry_run = bool(arguments.get("dry_run", False))
    before = _inspect_process(pid)
    allowed, reason = _is_kill_allowed(pid, before, expected_name)
    if not allowed:
        return {
            "error": reason,
            "pid": pid,
            "process": before,
            "analysis": {"succeeded": False, "blocked_by_tool": True},
        }

    if dry_run:
        return {
            "pid": pid,
            "signal": "TERM",
            "dry_run": True,
            "process": before,
            "analysis": {
                "succeeded": True,
                "mode": "dry_run",
                "would_send_signal": "TERM",
            },
        }

    try:
        kill_result = run_template("process.kill", {"pid": str(pid)}, timeout=5)
        time.sleep(0.2)
        after = _inspect_process(pid)
    except Exception as exc:  # pragma: no cover - platform dependent
        return {"error": str(exc), "pid": pid, "process": before}

    return {
        "pid": pid,
        "signal": "TERM",
        "dry_run": False,
        "process_before": before,
        "kill": kill_result.to_dict(limit=20),
        "process_after": after,
        "analysis": {
            "succeeded": kill_result.exit_code == 0,
            "exit_code": kill_result.exit_code,
            "still_running": bool(after.get("exists")),
        },
    }


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


def _inspect_process(pid: int) -> dict[str, Any]:
    try:
        result = run_template("process.by_pid", {"pid": str(pid)}, timeout=5)
    except Exception as exc:  # pragma: no cover - platform dependent
        return {"exists": False, "error": str(exc)}

    rows = [row.strip() for row in result.stdout if row.strip()]
    if result.exit_code != 0 or not rows:
        return {
            "exists": False,
            "exit_code": result.exit_code,
            "stderr": result.stderr[:10],
        }

    parts = rows[0].split(maxsplit=5)
    if len(parts) < 5:
        return {"exists": True, "raw": rows[0], "parse_error": "unexpected ps output"}

    return {
        "exists": True,
        "pid": _safe_int(parts[0]),
        "ppid": _safe_int(parts[1]),
        "user": parts[2],
        "stat": parts[3],
        "command": parts[4],
        "args": parts[5] if len(parts) > 5 else "",
    }


def _is_kill_allowed(pid: int, process: dict[str, Any], expected_name: str) -> tuple[bool, str]:
    if pid <= PROTECTED_PID_MAX:
        return False, f"pid is in protected system range: {pid}"
    if pid in {os.getpid(), os.getppid()}:
        return False, "refuse to kill current agent process or its parent"
    if not process.get("exists"):
        return False, f"process does not exist: {pid}"

    user = str(process.get("user", ""))
    command = str(process.get("command", ""))
    if user in PROTECTED_PROCESS_USERS:
        return False, f"refuse to kill process owned by protected user: {user}"
    if command in PROTECTED_PROCESS_NAMES:
        return False, f"refuse to kill protected process: {command}"
    if expected_name and expected_name != command:
        return False, f"process name mismatch: expected {expected_name}, got {command}"
    return True, "process target is allowed"


def _safe_int(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return 0
