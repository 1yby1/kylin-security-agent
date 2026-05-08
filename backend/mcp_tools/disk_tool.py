from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any


def run(arguments: dict[str, Any]) -> dict[str, Any]:
    target = Path(str(arguments.get("path", "."))).resolve()
    usage = shutil.disk_usage(target)
    return {
        "path": str(target),
        "total_gb": round(usage.total / 1024**3, 2),
        "used_gb": round(usage.used / 1024**3, 2),
        "free_gb": round(usage.free / 1024**3, 2),
        "used_percent": round(usage.used / usage.total * 100, 2),
    }

