from __future__ import annotations

import heapq
from datetime import datetime
from pathlib import Path
from typing import Any

# 单次扫描访问的目录项预算上限，超过即停止遍历并在结果中置 budget_exceeded，
# 避免低权限只读扫描长时间遍历整机文件系统（DoS 防护）。
_MAX_SCAN_ENTRIES = 20000


def run(arguments: dict[str, Any]) -> dict[str, Any]:
    root = Path(str(arguments.get("path", "."))).expanduser().resolve()
    if not root.exists():
        return {"error": f"path does not exist: {root}"}
    if not root.is_dir():
        return {"error": f"path is not a directory: {root}"}

    limit = _clamp_int(arguments.get("limit"), default=10, minimum=1, maximum=100)
    min_size_mb = _clamp_int(arguments.get("min_size_mb"), default=100, minimum=0, maximum=1024 * 1024)
    max_depth = _clamp_int(arguments.get("max_depth"), default=4, minimum=0, maximum=20)
    min_size_bytes = min_size_mb * 1024 * 1024

    heap: list[tuple[int, int, dict[str, Any]]] = []
    sequence = 0
    scanned_files = 0
    candidate_count = 0
    candidate_total_bytes = 0
    skipped: list[dict[str, str]] = []
    stack: list[tuple[Path, int]] = [(root, 0)]
    visited = 0
    budget_exceeded = False

    while stack and not budget_exceeded:
        directory, depth = stack.pop()
        try:
            children = list(directory.iterdir())
        except OSError as exc:
            skipped.append({"path": str(directory), "reason": str(exc)})
            continue

        for child in children:
            visited += 1
            if visited > _MAX_SCAN_ENTRIES:
                budget_exceeded = True
                break
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
            if not child.is_file():
                continue

            scanned_files += 1
            size_bytes = int(stat_result.st_size)
            if size_bytes < min_size_bytes:
                continue

            candidate_count += 1
            candidate_total_bytes += size_bytes
            sequence += 1
            record = {
                "path": str(child),
                "size_bytes": size_bytes,
                "size_mb": _bytes_to_mb(size_bytes),
                "modified_at": datetime.fromtimestamp(stat_result.st_mtime).isoformat(timespec="seconds"),
            }
            entry = (size_bytes, sequence, record)
            if len(heap) < limit:
                heapq.heappush(heap, entry)
            elif size_bytes > heap[0][0]:
                heapq.heapreplace(heap, entry)

    largest_files = [
        record
        for _, _, record in sorted(heap, key=lambda item: (item[0], item[1]), reverse=True)
    ]
    largest_size = largest_files[0]["size_bytes"] if largest_files else 0
    return {
        "path": str(root),
        "limit": limit,
        "min_size_mb": min_size_mb,
        "max_depth": max_depth,
        "scanned_files": scanned_files,
        "candidate_count": candidate_count,
        "candidate_total_mb": _bytes_to_mb(candidate_total_bytes),
        "skipped_count": len(skipped),
        "skipped_sample": skipped[:10],
        "budget_exceeded": budget_exceeded,
        "largest_files": largest_files,
        "analysis": {
            "file_count": len(largest_files),
            "candidate_count": candidate_count,
            "largest_size_mb": _bytes_to_mb(largest_size),
            "total_reported_size_mb": _bytes_to_mb(sum(item["size_bytes"] for item in largest_files)),
            "budget_exceeded": budget_exceeded,
            "read_only": True,
        },
    }


def _clamp_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _bytes_to_mb(value: int) -> float:
    return round(value / 1024**2, 3)
