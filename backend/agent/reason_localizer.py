"""Localize SecurityGuard reason strings to Chinese for user-facing output.

The guard emits stable English reason strings (used by audit, the API
``security`` block and tests). This module maps those strings to Chinese only at
the presentation boundary (the blocked conclusion), so the user sees readable
reasons while the raw English contract stays unchanged. Unknown strings — e.g.
already-Chinese orchestration messages — pass through untouched.
"""

from __future__ import annotations

import re
from typing import Callable, Iterable


_RISK_ZH = {"low": "低", "medium": "中", "high": "高", "prohibited": "禁止"}
_TYPE_ZH = {"integer": "整数", "boolean": "布尔值", "string": "字符串"}


def _risk(level: str) -> str:
    return _RISK_ZH.get(level, level)


_RULES: list[tuple[re.Pattern[str], Callable[[re.Match[str]], str]]] = [
    (re.compile(r"^secondary confirmation required$"), lambda m: "需要二次确认"),
    (re.compile(r"^confirmation accepted$"), lambda m: "二次确认已通过"),
    (
        re.compile(r"^role (\S+) is not allowed for risk level (\S+)$"),
        lambda m: f"角色 {m.group(1)} 无权执行{_risk(m.group(2))}风险操作",
    ),
    (re.compile(r"^prohibited operations are never allowed$"), lambda m: "禁止类操作永远不被允许"),
    (re.compile(r"^high risk operation is blocked by default policy$"), lambda m: "高风险操作被默认策略阻断"),
    (re.compile(r"^tool is not registered or enabled: (.+)$"), lambda m: f"工具未注册或未启用：{m.group(1)}"),
    (re.compile(r"^(\S+): (\S+) is required$"), lambda m: f"{m.group(1)}：缺少必填参数 {m.group(2)}"),
    (re.compile(r"^(\S+) is required$"), lambda m: f"缺少必填参数 {m.group(1)}"),
    (
        re.compile(r"^(\S+) must be (integer|boolean|string)$"),
        lambda m: f"参数 {m.group(1)} 必须是{_TYPE_ZH.get(m.group(2), m.group(2))}",
    ),
    (re.compile(r"^(\S+) must be one of (.+)$"), lambda m: f"参数 {m.group(1)} 必须是其中之一：{m.group(2)}"),
    (re.compile(r"^(\S+) is below minimum (\d+)$"), lambda m: f"参数 {m.group(1)} 小于最小值 {m.group(2)}"),
    (re.compile(r"^(\S+) exceeds maximum (\d+)$"), lambda m: f"参数 {m.group(1)} 超过最大值 {m.group(2)}"),
    (re.compile(r"^parameter contains unsafe characters: (.+)$"), lambda m: f"参数包含非法字符：{m.group(1)}"),
    (re.compile(r"^process\.kill requires integer pid$"), lambda m: "process.kill 需要整数 pid"),
    (re.compile(r"^pid is in protected system range: (\d+)$"), lambda m: f"pid 处于受保护的系统范围：{m.group(1)}"),
    (
        re.compile(r"^refuse to kill current agent process or its parent$"),
        lambda m: "拒绝终止当前 Agent 进程或其父进程",
    ),
    (re.compile(r"^refuse to kill protected process: (.+)$"), lambda m: f"拒绝终止受保护进程：{m.group(1)}"),
    (re.compile(r"^request mentions protected process: (.+)$"), lambda m: f"请求涉及受保护进程：{m.group(1)}"),
    (
        re.compile(r"^clean operation is only allowed under safe temp directories: (.+)$"),
        lambda m: f"清理操作只允许在安全临时目录下进行：{m.group(1)}",
    ),
    (
        re.compile(r"^destructive operation touches protected core path: (.+)$"),
        lambda m: f"危险操作触及受保护的核心路径：{m.group(1)}",
    ),
    (
        re.compile(r"^request matched prohibited command pattern: (.+)$"),
        lambda m: f"请求匹配到禁止命令模式：{m.group(1)}",
    ),
    (
        re.compile(r"^request matched dangerous command pattern: (.+)$"),
        lambda m: f"请求匹配到危险命令模式：{m.group(1)}",
    ),
]


def localize_reason(reason: str) -> str:
    """Translate a single guard reason to Chinese, or return it unchanged."""
    text = (reason or "").strip()
    for pattern, render in _RULES:
        match = pattern.match(text)
        if match:
            return render(match)
    return reason


def localize_reasons(reasons: Iterable[str]) -> list[str]:
    """Translate and de-duplicate a list of guard reasons."""
    localized: list[str] = []
    for reason in reasons:
        zh = localize_reason(str(reason))
        if zh not in localized:
            localized.append(zh)
    return localized
