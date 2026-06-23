from __future__ import annotations

import os
from typing import Any

from backend.mcp_tools.command_runner import run_optional_template

WINDOWS_MESSAGE = "该安全工具面向麒麟/Linux，开发环境不可用。"
_MAX_FILES = 50


def run(arguments: dict[str, Any]) -> dict[str, Any]:
    if os.name == "nt":
        return {"platform": "windows", "message": WINDOWS_MESSAGE, "analysis": _analyze({}, {}, {}, {})}
    suid = run_optional_template("privilege.suid", timeout=8)
    sgid = run_optional_template("privilege.sgid", timeout=8)
    uid0 = run_optional_template("privilege.uid0", timeout=8)
    empty_pw = run_optional_template("privilege.empty_password", timeout=8)
    return {
        "source": "privilege",
        "suid": suid,
        "sgid": sgid,
        "uid0": uid0,
        "empty_password": empty_pw,
        "analysis": _analyze(suid, sgid, uid0, empty_pw),
    }


def _analyze(
    suid: dict[str, Any],
    sgid: dict[str, Any],
    uid0: dict[str, Any],
    empty_pw: dict[str, Any],
) -> dict[str, Any]:
    suid_files = _lines(suid)
    sgid_files = _lines(sgid)
    uid0_accounts = _lines(uid0)
    return {
        "suid_count": len(suid_files),
        "sgid_count": len(sgid_files),
        "suid_files": suid_files[:_MAX_FILES],
        "extra_uid0_accounts": [name for name in uid0_accounts if name != "root"],
        "empty_password_accounts": _lines(empty_pw),
        "shadow_readable": "error" not in empty_pw,
    }


def _lines(result: dict[str, Any]) -> list[str]:
    value = result.get("stdout") if isinstance(result, dict) else None
    if not isinstance(value, list):
        return []
    return [line.strip() for line in value if line.strip()]
