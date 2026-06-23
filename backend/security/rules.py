from __future__ import annotations

import re
from dataclasses import dataclass


LOW_RISK_TOOLS = {
    "system",
    "process",
    "network",
    "network.diagnostics",
    "log",
    "service",
    "disk",
    "disk.large_files",
}
MEDIUM_RISK_TOOLS = {"service.restart", "temp.clean", "process.kill"}
HIGH_RISK_TOOLS = {"config.modify", "permission.modify", "user.modify", "service.stop"}

APPROVAL_ROLES = {"operator", "admin"}
ADMIN_ROLES = {"admin"}

SAFE_TEMP_DIRS = (
    "/tmp",
    "/var/tmp",
    "/opt/software-cup-ops/tmp",
)

CORE_SYSTEM_PATHS = (
    "/",
    "/etc",
    "/bin",
    "/sbin",
    "/usr",
    "/var",
    "/boot",
    "/lib",
    "/lib64",
    "/root",
)

PROTECTED_SERVICES = {
    "firewalld",
    "iptables",
    "nftables",
    "sshd",
    "auditd",
    "systemd-journald",
    "dbus",
    "NetworkManager",
}

PROTECTED_PID_MAX = 100

PROTECTED_PROCESS_USERS = {
    "root",
    "daemon",
    "bin",
    "sys",
    "sync",
    "games",
    "man",
    "lp",
    "mail",
    "news",
    "uucp",
    "proxy",
    "www-data",
    "backup",
    "list",
    "irc",
    "gnats",
    "nobody",
    "systemd-network",
    "systemd-resolve",
    "dbus",
    "polkitd",
    "chrony",
}

PROTECTED_PROCESS_NAMES = {
    "systemd",
    "kthreadd",
    "kworker",
    "migration",
    "rcu_sched",
    "sshd",
    "auditd",
    "systemd-journald",
    "dbus-daemon",
    "NetworkManager",
    "firewalld",
    "iptables",
    "nftables",
    "cron",
    "crond",
    "rsyslogd",
}

SERVICE_RESTART_ALLOWLIST = {
    "nginx",
    "httpd",
    "mysqld",
    "postgresql",
    "redis",
    "software-cup-ops",
}

PROHIBITED_PATTERNS = [
    r"\brm\s+-rf\s+/(?:\s|$)",
    r"\bchmod\s+777\s+/etc/passwd\b",
    r"\bmkfs(?:\.[a-z0-9]+)?\b",
    r"\bdd\b.*\bof=/dev/",
    r"\bsystemctl\s+(?:stop|disable|mask)\s+(?:firewalld|iptables|nftables)\b",
    r"\b(?:firewall-cmd\b.*--panic-on|ufw\s+disable)\b",
    r"(?:关闭|停止|禁用)防火墙",
    r"删除\s+/(?:etc|bin|sbin|usr|var|boot|lib|lib64)(?:\s|/|$)",
    r"\b(?:rm|rmdir)\b.*\s/(?:etc|bin|sbin|usr|var|boot|lib|lib64)(?:\s|/|$)",
    # fork 炸弹：:(){ :|:& };: 及其常见空白变体
    r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",
    # 远程脚本管道执行：curl/wget ... | (sudo) sh/bash/zsh/dash/ksh
    r"\b(?:curl|wget)\b.*\|\s*(?:sudo\s+)?\b(?:ba|z|da|k)?sh\b",
    # 向块设备写盘重定向：> /dev/sda、/dev/nvme0n1 等
    r">\s*/dev/(?:sd|hd|vd|nvme|mmcblk|xvd)",
]

DANGEROUS_COMMAND_PATTERNS = [
    r"\brm\s+-rf\b",
    r"\bchmod\s+(?:777|[0-7]*7[0-7]*)\b",
    r"\bchown\b",
    r"\bmkfs(?:\.[a-z0-9]+)?\b",
    r"\bdd\b",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bkill\s+-9\b",
    r"\buseradd\b",
    r"\busermod\b",
    r"\buserdel\b",
    r"\bpasswd\b",
    r"\bsystemctl\s+(?:stop|disable|mask)\b",
]

SAFE_STRING_PATTERN = re.compile(r"^[\w\s./:@+=,%\-\u4e00-\u9fff]{0,512}$", re.UNICODE)


@dataclass(frozen=True)
class RiskPolicy:
    allowed_roles: set[str]
    confirmation_required: bool
    blocked_by_default: bool


RISK_POLICIES = {
    "low": RiskPolicy(allowed_roles=set(), confirmation_required=False, blocked_by_default=False),
    "medium": RiskPolicy(allowed_roles=APPROVAL_ROLES, confirmation_required=True, blocked_by_default=False),
    "high": RiskPolicy(allowed_roles=ADMIN_ROLES, confirmation_required=True, blocked_by_default=True),
    "prohibited": RiskPolicy(allowed_roles=set(), confirmation_required=False, blocked_by_default=True),
}
