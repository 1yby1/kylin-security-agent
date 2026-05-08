from __future__ import annotations

import os
import shutil
import time
from pathlib import Path, PurePosixPath
from typing import Any

from backend.security.rules import SAFE_TEMP_DIRS


def run(arguments: dict[str, Any]) -> dict[str, Any]:
    target_path = str(arguments.get("path", "")).strip()
    if not target_path:
        return {"error": "path is required"}
    if os.name == "nt":
        return {"error": "temp.clean is only supported on Kylin/Linux"}

    normalized = _normalize_posix_path(target_path)
    if not _is_safe_temp_path(normalized):
        return {
            "error": f"path is not under safe temp directories: {normalized}",
            "safe_temp_dirs": list(SAFE_TEMP_DIRS),
        }

    path = Path(normalized)
    if not path.exists():
        return {"error": f"path does not exist: {normalized}"}
    if path.is_symlink():
        return {"error": f"path is a symbolic link and cannot be cleaned: {normalized}"}
    if not path.is_dir():
        return {"error": f"path is not a directory: {normalized}"}
    resolved = _normalize_posix_path(str(path.resolve(strict=True)))
    if not _is_safe_temp_path(resolved):
        return {"error": f"path resolves outside safe temp directories: {normalized}"}
    path = Path(resolved)

    dry_run = bool(arguments.get("dry_run", False))
    max_age_hours = _clamp_int(arguments.get("max_age_hours", 24), minimum=1, maximum=720)
    limit = _clamp_int(arguments.get("limit", 200), minimum=1, maximum=2000)
    cutoff = time.time() - max_age_hours * 3600

    deleted: list[str] = []
    skipped: list[dict[str, str]] = []
    scanned = 0
    limit_reached = False

    for item in path.iterdir():
        if scanned >= limit:
            limit_reached = True
            skipped.append({"path": str(item), "reason": "limit reached"})
            break
        scanned += 1

        ok, reason = _is_clean_candidate(item, cutoff)
        if not ok:
            skipped.append({"path": str(item), "reason": reason})
            continue

        if dry_run:
            deleted.append(str(item))
            continue

        try:
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
            deleted.append(str(item))
        except Exception as exc:  # pragma: no cover - filesystem dependent
            skipped.append({"path": str(item), "reason": str(exc)})

    return {
        "path": normalized,
        "dry_run": dry_run,
        "max_age_hours": max_age_hours,
        "limit": limit,
        "scanned_count": scanned,
        "deleted_count": len(deleted) if not dry_run else 0,
        "candidate_count": len(deleted),
        "skipped_count": len(skipped),
        "limit_reached": limit_reached,
        "deleted_sample": deleted[:50],
        "skipped_sample": skipped[:50],
        "analysis": {
            "succeeded": True,
            "mode": "dry_run" if dry_run else "delete",
            "safe_temp_dir": True,
        },
    }


def _is_clean_candidate(path: Path, cutoff: float) -> tuple[bool, str]:
    try:
        if path.is_symlink():
            return False, "skip symlink"
        stat = path.stat()
        if stat.st_mtime > cutoff:
            return False, "not old enough"
        return True, "ok"
    except Exception as exc:  # pragma: no cover - filesystem dependent
        return False, str(exc)


def _clamp_int(value: Any, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = minimum
    return max(minimum, min(parsed, maximum))


def _normalize_posix_path(path: str) -> str:
    normalized = str(PurePosixPath(path.replace("\\", "/")))
    if normalized != "/" and normalized.endswith("/"):
        normalized = normalized.rstrip("/")
    return normalized


def _is_safe_temp_path(path: str) -> bool:
    normalized = _normalize_posix_path(path)
    parts = PurePosixPath(normalized).parts
    if not normalized.startswith("/") or ".." in parts:
        return False
    return any(normalized == safe or normalized.startswith(safe + "/") for safe in SAFE_TEMP_DIRS)
