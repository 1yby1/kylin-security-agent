from __future__ import annotations

from typing import Any

# 安全态势工具：返回侦察级敏感明细（来源 IP、开放端口清单、SUID 文件、UID0/空密码
# 账户名）。这些工具在 LOW_RISK_TOOLS 内，无令牌 viewer 也能调用，因此对 viewer 只
# 返回计数与风险标志，明细需 operator/admin。脱敏只作用于返回给调用方/LLM 的结果，
# 审计与步骤引用仍保留全量。
RECON_TOOLS = {"auth", "firewall", "privilege"}
_PRIVILEGED_ROLES = {"operator", "admin"}


def redact_security_tool_output(tool_name: str, result: Any, role: str | None) -> Any:
    """对低权限（viewer）调用方剥离安全态势工具的侦察级明细，只保留计数与风险标志。

    operator/admin 返回全量；非侦察工具原样返回。"""
    if tool_name not in RECON_TOOLS:
        return result
    if (role or "viewer").lower() in _PRIVILEGED_ROLES:
        return result
    if not isinstance(result, dict):
        return result

    analysis = result.get("analysis")
    redacted_analysis = _redact_analysis(tool_name, analysis) if isinstance(analysis, dict) else {}
    redacted: dict[str, Any] = {
        "source": result.get("source", tool_name),
        "analysis": redacted_analysis,
        "detail_redacted": True,
    }
    # 保留 Windows 降级提示等无敏感信息的元字段。
    for key in ("platform", "message"):
        if key in result:
            redacted[key] = result[key]
    return redacted


def _redact_analysis(tool_name: str, analysis: dict[str, Any]) -> dict[str, Any]:
    summary = dict(analysis)
    if tool_name == "auth":
        ips = summary.pop("top_source_ips", {})
        summary["top_source_ip_count"] = len(ips) if isinstance(ips, (dict, list)) else 0
    elif tool_name == "firewall":
        summary.pop("open_ports", None)
        summary.pop("open_services", None)
    elif tool_name == "privilege":
        summary.pop("suid_files", None)
        uid0 = summary.pop("extra_uid0_accounts", [])
        summary["extra_uid0_count"] = len(uid0) if isinstance(uid0, list) else 0
        empty = summary.pop("empty_password_accounts", [])
        summary["empty_password_count"] = len(empty) if isinstance(empty, list) else 0
    return summary
