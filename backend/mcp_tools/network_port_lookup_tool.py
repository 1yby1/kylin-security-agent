from __future__ import annotations

import re
from typing import Any

from backend.mcp_tools.command_runner import run_template


def run(arguments: dict[str, Any]) -> dict[str, Any]:
    port, error = _parse_port(arguments.get("port"))
    if error:
        return {"error": error}

    protocol = str(arguments.get("protocol", "all")).strip().lower()
    if protocol not in {"tcp", "udp", "all"}:
        return {"error": "protocol must be tcp, udp, or all"}

    try:
        result = run_template("network.ports", timeout=5)
    except Exception as exc:  # pragma: no cover - platform dependent
        return {"error": str(exc)}

    matches = _find_port_matches(result.stdout, port, protocol)
    pids = sorted({item["pid"] for item in matches if item.get("pid") is not None})
    output = result.to_dict(limit=int(arguments.get("limit", 30)))
    output["port_lookup"] = {
        "port": port,
        "protocol": protocol,
        "matched_count": len(matches),
        "pids": pids,
        "matches": matches[:50],
    }
    output["analysis"] = {
        "port": port,
        "protocol": protocol,
        "has_match": bool(matches),
        "pid_count": len(pids),
        "pids": pids,
        "listening_matches": [item for item in matches if item.get("state") in {"LISTEN", "LISTENING"}][:20],
    }
    return output


def _parse_port(value: Any) -> tuple[int, str]:
    if isinstance(value, bool):
        return 0, "port must be integer"
    try:
        port = int(value)
    except (TypeError, ValueError):
        return 0, "port must be integer"
    if port < 1 or port > 65535:
        return 0, "port must be between 1 and 65535"
    return port, ""


def _find_port_matches(rows: list[str], port: int, protocol: str = "all") -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for row in rows:
        parsed = _parse_port_row(row)
        if not parsed:
            continue
        if protocol != "all" and parsed["protocol"] != protocol:
            continue
        if parsed["local_port"] != port:
            continue
        matches.append(parsed)
    return matches


def _parse_port_row(row: str) -> dict[str, Any]:
    parts = row.split()
    if not parts:
        return {}

    first = parts[0].lower()
    if first in {"tcp", "udp"} and len(parts) >= 5 and parts[2].isdigit() and parts[3].isdigit():
        return _parse_linux_ss_row(parts, row)
    if first in {"tcp", "udp", "tcp6", "udp6"}:
        return _parse_windows_netstat_row(parts, row)
    if first.startswith("tcp") or first.startswith("udp"):
        return _parse_linux_ss_row(parts, row)
    return {}


def _parse_windows_netstat_row(parts: list[str], row: str) -> dict[str, Any]:
    if len(parts) < 4:
        return {}
    protocol = "tcp" if parts[0].lower().startswith("tcp") else "udp"
    local_address = parts[1]
    state = parts[3] if protocol == "tcp" and len(parts) >= 4 else "UNKNOWN"
    pid_text = parts[-1]
    return _port_result(
        protocol=protocol,
        state=state,
        local_address=local_address,
        pid=_safe_int(pid_text),
        process_name="",
        row=row,
    )


def _parse_linux_ss_row(parts: list[str], row: str) -> dict[str, Any]:
    if len(parts) < 5:
        return {}
    protocol = "tcp" if parts[0].lower().startswith("tcp") else "udp"
    local_address = parts[4]
    pid_match = re.search(r"pid=(\d+)", row)
    process_match = re.search(r'users:\(\("([^"]+)"', row)
    return _port_result(
        protocol=protocol,
        state=parts[1].upper(),
        local_address=local_address,
        pid=_safe_int(pid_match.group(1)) if pid_match else None,
        process_name=process_match.group(1) if process_match else "",
        row=row,
    )


def _port_result(
    *,
    protocol: str,
    state: str,
    local_address: str,
    pid: int | None,
    process_name: str,
    row: str,
) -> dict[str, Any]:
    return {
        "protocol": protocol,
        "state": state.upper(),
        "local_address": local_address,
        "local_port": _extract_port(local_address),
        "pid": pid,
        "process_name": process_name,
        "row": row,
    }


def _extract_port(address: str) -> int | None:
    cleaned = address.strip()
    if cleaned.endswith("]"):
        return None
    token = cleaned.rsplit(":", 1)[-1]
    try:
        return int(token)
    except ValueError:
        return None


def _safe_int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None
