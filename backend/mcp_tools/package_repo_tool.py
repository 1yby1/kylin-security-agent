from __future__ import annotations

import configparser
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from backend.config import get_runtime_settings
from backend.security.least_privilege import subprocess_security_options


DEFAULT_REPO_DIR = "/etc/yum.repos.d"


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
                    "baseurl": parser.get(section, "baseurl", fallback=""),
                    "mirrorlist": parser.get(section, "mirrorlist", fallback=""),
                    "metalink": parser.get(section, "metalink", fallback=""),
                    "gpgcheck": parser.get(section, "gpgcheck", fallback=""),
                    "file": str(repo_file),
                }
            )
    return repositories, errors


def _run_repolist(manager: str) -> dict[str, Any]:
    command = [manager, "repolist", "--enabled"]
    try:
        runtime_settings = get_runtime_settings()
        security_options, identity = subprocess_security_options(runtime_settings)
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            cwd=runtime_settings.safe_workdir if os.name != "nt" else None,
            **security_options,
        )
        return {
            "command": " ".join(command),
            "exit_code": completed.returncode,
            "stdout": completed.stdout.splitlines()[:80],
            "stderr": completed.stderr.splitlines()[:80],
            "execution_identity": identity.to_dict(),
        }
    except Exception as exc:  # pragma: no cover - depends on target OS utilities
        return {"command": " ".join(command), "exit_code": None, "stdout": [], "stderr": [str(exc)]}
