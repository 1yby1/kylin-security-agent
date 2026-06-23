from __future__ import annotations

from typing import Any

from backend.monitor.alerts import Alert


def check_disk(disk_output: dict[str, Any], threshold_percent: float) -> list[Alert]:
    if not isinstance(disk_output, dict):
        return []
    used = disk_output.get("used_percent")
    if isinstance(used, (int, float)) and not isinstance(used, bool) and used > threshold_percent:
        return [
            Alert(
                severity="critical",
                source="disk",
                metric="used_percent",
                value=used,
                threshold=threshold_percent,
                message=f"磁盘使用率 {used}% 超过阈值 {threshold_percent}%",
            )
        ]
    return []


def check_service(service_output: dict[str, Any]) -> list[Alert]:
    analysis = service_output.get("analysis") if isinstance(service_output, dict) else None
    failed = analysis.get("failed_count", 0) if isinstance(analysis, dict) else 0
    if isinstance(failed, int) and not isinstance(failed, bool) and failed > 0:
        return [
            Alert(
                severity="warning",
                source="service",
                metric="failed_count",
                value=failed,
                threshold=0,
                message=f"有 {failed} 个服务处于 failed 状态",
            )
        ]
    return []


def check_auth(auth_output: dict[str, Any], threshold: int) -> list[Alert]:
    analysis = auth_output.get("analysis") if isinstance(auth_output, dict) else None
    failed = analysis.get("failed_login_count", 0) if isinstance(analysis, dict) else 0
    if isinstance(failed, int) and not isinstance(failed, bool) and failed > threshold:
        return [
            Alert(
                severity="warning",
                source="auth",
                metric="failed_login_count",
                value=failed,
                threshold=threshold,
                message=f"失败登录 {failed} 次超过阈值 {threshold} 次，疑似暴力破解",
            )
        ]
    return []


def run_all_checks(outputs: dict[str, dict[str, Any]], settings: Any) -> list[Alert]:
    alerts: list[Alert] = []
    alerts.extend(check_disk(outputs.get("disk", {}), settings.disk_percent))
    alerts.extend(check_service(outputs.get("service", {})))
    alerts.extend(check_auth(outputs.get("auth", {}), settings.failed_login))
    return alerts
