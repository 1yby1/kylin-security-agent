from __future__ import annotations

import getpass
import os
from dataclasses import asdict, dataclass
from typing import Any

from backend.config import RuntimeSettings, get_runtime_settings


@dataclass(frozen=True)
class ExecutionIdentity:
    platform: str
    current_user: str
    current_uid: int | None
    current_gid: int | None
    target_user: str
    target_group: str
    target_uid: int | None
    target_gid: int | None
    runs_as_user: str
    least_privilege_enforced: bool
    privilege_drop_supported: bool
    warning: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def runtime_identity(settings: RuntimeSettings | None = None) -> ExecutionIdentity:
    settings = settings or get_runtime_settings()
    current_uid = _getuid()
    current_gid = _getgid()
    current_user = _current_user(current_uid)
    target_uid, target_gid, warning = _resolve_target(settings)
    supported = os.name != "nt"

    enforced = False
    runs_as_user = current_user
    if supported:
        if current_uid == target_uid and target_uid is not None:
            enforced = True
            runs_as_user = settings.agent_user
        elif current_uid == 0 and target_uid is not None:
            enforced = True
            runs_as_user = settings.agent_user
        elif current_uid != 0:
            warning = warning or "process is not root; commands inherit current non-root service identity"

    return ExecutionIdentity(
        platform=os.name,
        current_user=current_user,
        current_uid=current_uid,
        current_gid=current_gid,
        target_user=settings.agent_user,
        target_group=settings.agent_group,
        target_uid=target_uid,
        target_gid=target_gid,
        runs_as_user=runs_as_user,
        least_privilege_enforced=enforced,
        privilege_drop_supported=supported,
        warning=warning,
    )


def subprocess_security_options(settings: RuntimeSettings | None = None) -> tuple[dict[str, Any], ExecutionIdentity]:
    settings = settings or get_runtime_settings()
    identity = runtime_identity(settings)
    options: dict[str, Any] = {"close_fds": True}

    if os.name == "nt":
        return options, identity

    if identity.current_uid == 0 and identity.target_uid is None and settings.strict_least_privilege:
        raise PermissionError(identity.warning or "strict least privilege mode refused root command execution")

    if identity.current_uid == 0 and identity.target_uid is not None:
        options["user"] = identity.target_uid
        if identity.target_gid is not None:
            options["group"] = identity.target_gid
            options["extra_groups"] = []

    return options, identity


def _getuid() -> int | None:
    return os.getuid() if hasattr(os, "getuid") else None


def _getgid() -> int | None:
    return os.getgid() if hasattr(os, "getgid") else None


def _current_user(uid: int | None) -> str:
    if uid is None:
        return getpass.getuser()
    try:
        import pwd

        return pwd.getpwuid(uid).pw_name
    except Exception:
        return str(uid)


def _resolve_target(settings: RuntimeSettings) -> tuple[int | None, int | None, str]:
    if os.name == "nt":
        return None, None, "least privilege identity switching is only enforced on Kylin/Linux"

    try:
        import grp
        import pwd

        user_entry = pwd.getpwnam(settings.agent_user)
        try:
            group_entry = grp.getgrnam(settings.agent_group)
            target_gid = group_entry.gr_gid
        except KeyError:
            target_gid = user_entry.pw_gid
        return user_entry.pw_uid, target_gid, ""
    except KeyError:
        message = f"least privilege user does not exist: {settings.agent_user}"
        if settings.strict_least_privilege:
            return None, None, message
        return None, None, message
