from __future__ import annotations

import os
import platform
from typing import Any

from backend.mcp_tools.command_runner import run_optional_template


def run(arguments: dict[str, Any]) -> dict[str, Any]:
    if os.name == "nt":
        return {
            "platform": _platform_summary(),
            "hostname": run_optional_template("system.hostname", timeout=3),
            "system": run_optional_template("system.info", timeout=8),
        }

    return {
        "platform": _platform_summary(),
        "kernel": run_optional_template("system.uname", timeout=3),
        "host": run_optional_template("system.hostnamectl", timeout=5),
        "uptime": run_optional_template("system.uptime", timeout=3),
        "cpu": run_optional_template("system.cpu", timeout=5),
        "memory": run_optional_template("system.memory", timeout=3),
        "disk": run_optional_template("system.disk", timeout=5),
    }


def _platform_summary() -> dict[str, str]:
    return {
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python": platform.python_version(),
    }
