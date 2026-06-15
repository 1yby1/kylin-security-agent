from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any


def run(arguments: dict[str, Any]) -> dict[str, Any]:
    requested_path = str(arguments.get("path", ".")).strip() or "."
    target = Path(_normalize_disk_path(requested_path)).resolve()
    usage = shutil.disk_usage(target)
    return {
        "requested_path": requested_path,
        "path": str(target),
        "total_gb": round(usage.total / 1024**3, 2),
        "used_gb": round(usage.used / 1024**3, 2),
        "free_gb": round(usage.free / 1024**3, 2),
        "used_percent": round(usage.used / usage.total * 100, 2),
    }


def _normalize_disk_path(path: str) -> str:
    value = path.strip().replace("\\", "/")
    if len(value) == 2 and value[1] == ":":
        return f"{value}/"
    return value
