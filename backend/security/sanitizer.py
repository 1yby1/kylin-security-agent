from __future__ import annotations

import json
import re
import secrets
from typing import Any

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_INJECTION_PATTERNS = {
    "ignore_previous": re.compile(r"ignore\s+(?:all\s+)?previous", re.IGNORECASE),
    "disregard_above": re.compile(r"disregard\s+(?:the\s+)?above", re.IGNORECASE),
    "role_override": re.compile(
        r"you\s+are\s+now|system\s+prompt|你现在是|忽略(?:以上|之前|上面)",
        re.IGNORECASE,
    ),
    "destructive_cmd": re.compile(r"rm\s+-rf|mkfs(?:\.[a-z0-9]+)?|>\s*/dev/sd", re.IGNORECASE),
}

_TRUNCATION_SUFFIX = "…[truncated]"


def sanitize_output(text: str, max_len: int = 2000) -> str:
    cleaned = _ANSI.sub("", str(text))
    cleaned = _CONTROL_CHARS.sub("", cleaned)
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len] + _TRUNCATION_SUFFIX
    return cleaned


def scan_injection(text: str) -> list[str]:
    haystack = str(text)
    return [name for name, pattern in _INJECTION_PATTERNS.items() if pattern.search(haystack)]


def wrap_untrusted(text: str, source: str) -> str:
    nonce = secrets.token_hex(3)
    return (
        f'<OBSERVED_DATA source="{source}" trust="untrusted" nonce={nonce}>\n'
        f"{text}\n"
        f"</OBSERVED_DATA nonce={nonce}>"
    )


def build_observation_block(tool_result: dict[str, Any], max_len: int = 2000) -> str:
    serialized = json.dumps(tool_result, ensure_ascii=False)
    return wrap_untrusted(sanitize_output(serialized, max_len), source="tool_result")
