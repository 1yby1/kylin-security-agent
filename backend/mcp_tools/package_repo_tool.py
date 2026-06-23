from __future__ import annotations

import configparser
import re
import shutil
from pathlib import Path
from typing import Any

from backend.mcp_tools.command_runner import run_optional_template


DEFAULT_REPO_DIR = "/etc/yum.repos.d"

# 匹配 URL 中内嵌的 user:password@，脱敏后避免把仓库凭据返回给调用方。
_CRED_RE = re.compile(r"://[^/@\s:]+:[^/@\s]+@")


def _mask_url(value: str) -> str:
    if not value:
        return value
    return _CRED_RE.sub("://***:***@", value)


def run(arguments: dict[str, Any]) -> dict[str, Any]:
    repo_dir = Path(str(arguments.get("repo_dir", DEFAULT_REPO_DIR))).expanduser().resolve()
    check_repolist = bool(arguments.get("check_repolist", True))
    manager = _detect_manager()
    repositories, repo_errors = _read_repo_files(repo_dir)
    repolist = _run_repolist(manager) if check_repolist and manager else {"skipped": True}

    enabled_repos = [repo for repo in repositories if repo.get("enabled", "1") != "0"]
    repos_with_baseurl = [repo for repo in repositories if repo.get("baseurl") or repo.get("metalink") or repo.get("mirrorlist")]
    return {
        "manager": manager or "unknown",
        "repo_dir": str(repo_dir),
        "repositories": repositories,
        "repo_errors": repo_errors,
        "repolist": repolist,
        "analysis": {
            "manager_found": bool(manager),
            "repo_count": len(repositories),
            "enabled_repo_count": len(enabled_repos),
            "repo_files_with_errors": len(repo_errors),
            "repos_with_remote_source": len(repos_with_baseurl),
            "read_only": True,
        },
    }


def _detect_manager() -> str:
    for candidate in ["dnf", "yum"]:
        if shutil.which(candidate):
            return candidate
    return ""


def _read_repo_files(repo_dir: Path) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    if not repo_dir.exists():
        return [], [{"path": str(repo_dir), "reason": "repo directory does not exist"}]
    if not repo_dir.is_dir():
        return [], [{"path": str(repo_dir), "reason": "repo path is not a directory"}]

    repositories: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    for repo_file in sorted(repo_dir.glob("*.repo")):
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read(repo_file, encoding="utf-8")
        except configparser.Error as exc:
            errors.append({"path": str(repo_file), "reason": str(exc)})
            continue
        for section in parser.sections():
            repositories.append(
                {
                    "id": section,
                    "name": parser.get(section, "name", fallback=""),
                    "enabled": parser.get(section, "enabled", fallback="1"),
                    "baseurl": _mask_url(parser.get(section, "baseurl", fallback="")),
                    "mirrorlist": _mask_url(parser.get(section, "mirrorlist", fallback="")),
                    "metalink": _mask_url(parser.get(section, "metalink", fallback="")),
                    "gpgcheck": parser.get(section, "gpgcheck", fallback=""),
                    "file": str(repo_file),
                }
            )
    return repositories, errors


def _run_repolist(manager: str) -> dict[str, Any]:
    # 通过命令模板执行（白名单 + 最小权限），不直接调用 subprocess。
    return run_optional_template(f"package.repolist.{manager}", timeout=10)
