from __future__ import annotations

from pathlib import Path
from typing import Any


def run(arguments: dict[str, Any]) -> dict[str, Any]:
    root = Path(str(arguments.get("path", "."))).expanduser().resolve()
    if not root.exists():
        return {"error": f"path does not exist: {root}"}
    if not root.is_dir():
        return {"error": f"path is not a directory: {root}"}

    limit = _clamp_int(arguments.get("limit"), default=10, minimum=1, maximum=100)
    max_depth = _clamp_int(arguments.get("max_depth"), default=3, minimum=0, maximum=20)
    include_files = bool(arguments.get("include_files", False))
    skipped: list[dict[str, str]] = []
    entries: list[dict[str, Any]] = []

    try:
        children = list(root.iterdir())
    except OSError as exc:
        return {"error": str(exc)}

    for child in children:
        if child.is_symlink():
            skipped.append({"path": str(child), "reason": "symlink skipped"})
            continue
        if child.is_dir():
            size_bytes, file_count = _measure_tree(child, max_depth=max_depth, skipped=skipped)
            entries.append(
                {
                    "path": str(child),
                    "type": "directory",
                    "size_bytes": size_bytes,
                    "size_mb": _bytes_to_mb(size_bytes),
                    "file_count": file_count,
                }
            )
            continue
        if include_files and child.is_file():
            try:
                size_bytes = int(child.stat().st_size)
            except OSError as exc:
                skipped.append({"path": str(child), "reason": str(exc)})
                continue
            entries.append(
                {
                    "path": str(child),
                    "type": "file",
                    "size_bytes": size_bytes,
                    "size_mb": _bytes_to_mb(size_bytes),
                    "file_count": 1,
                }
            )

    entries.sort(key=lambda item: item["size_bytes"], reverse=True)
    top_entries = entries[:limit]
    return {
        "path": str(root),
        "limit": limit,
        "max_depth": max_depth,
        "include_files": include_files,
        "top_entries": top_entries,
        "skipped_count": len(skipped),
        "skipped_sample": skipped[:10],
        "analysis": {
            "entry_count": len(top_entries),
            "largest_entry_mb": top_entries[0]["size_mb"] if top_entries else 0,
            "total_reported_size_mb": _bytes_to_mb(sum(item["size_bytes"] for item in top_entries)),
            "read_only": True,
        },
    }


def _measure_tree(root: Path, *, max_depth: int, skipped: list[dict[str, str]]) -> tuple[int, int]:
    total_size = 0
    file_count = 0
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        try:
            children = list(current.iterdir())
        except OSError as exc:
            skipped.append({"path": str(current), "reason": str(exc)})
            continue
        for child in children:
            if child.is_symlink():
                skipped.append({"path": str(child), "reason": "symlink skipped"})
                continue
            try:
                stat_result = child.stat()
            except OSError as exc:
                skipped.append({"path": str(child), "reason": str(exc)})
                continue
            if child.is_dir():
                if depth < max_depth:
                    stack.append((child, depth + 1))
                continue
            if child.is_file():
                total_size += int(stat_result.st_size)
                file_count += 1
    return total_size, file_count


def _clamp_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _bytes_to_mb(value: int) -> float:
    return round(value / 1024**2, 3)
