from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LLMSettings:
    provider: str
    api_key: str
    base_url: str
    model: str
    timeout_seconds: int = 20

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.provider in {"deepseek", "qwen"})


@dataclass(frozen=True)
class RuntimeSettings:
    agent_user: str
    agent_group: str
    strict_least_privilege: bool
    safe_workdir: str


def get_llm_settings() -> LLMSettings:
    provider = os.getenv("LLM_PROVIDER", "disabled").strip().lower()
    defaults = {
        "deepseek": {
            "base_url": "https://api.deepseek.com/chat/completions",
            "model": "deepseek-chat",
            "key_env": "DEEPSEEK_API_KEY",
        },
        "qwen": {
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            "model": "qwen-max",
            "key_env": "QWEN_API_KEY",
        },
    }
    selected = defaults.get(provider, {})
    api_key = os.getenv("LLM_API_KEY") or os.getenv(selected.get("key_env", ""), "")
    base_url = os.getenv("LLM_BASE_URL") or selected.get("base_url", "")
    model = os.getenv("LLM_MODEL") or selected.get("model", "")
    timeout = int(os.getenv("LLM_TIMEOUT_SECONDS", "20"))
    return LLMSettings(provider=provider, api_key=api_key, base_url=base_url, model=model, timeout_seconds=timeout)


def get_runtime_settings() -> RuntimeSettings:
    agent_user = os.getenv("AGENT_RUN_USER", "software-cup-agent")
    agent_group = os.getenv("AGENT_RUN_GROUP", agent_user)
    strict = os.getenv("AGENT_STRICT_LEAST_PRIVILEGE", "true").strip().lower() not in {"0", "false", "no"}
    safe_workdir = os.getenv("AGENT_SAFE_WORKDIR", "/")
    return RuntimeSettings(
        agent_user=agent_user,
        agent_group=agent_group,
        strict_least_privilege=strict,
        safe_workdir=safe_workdir,
    )


@dataclass(frozen=True)
class MCPSettings:
    client_user_id: str
    client_role: str


def get_mcp_settings() -> MCPSettings:
    return MCPSettings(
        client_user_id=os.getenv("AGENT_MCP_CLIENT_USER", "mcp-client"),
        client_role=os.getenv("AGENT_MCP_CLIENT_ROLE", "viewer"),
    )


@dataclass(frozen=True)
class AuthSettings:
    token_roles: dict[str, str]
    default_role: str = "viewer"


def get_auth_settings() -> AuthSettings:
    token_roles: dict[str, str] = {}
    for env_name, role in (
        ("AGENT_ADMIN_TOKEN", "admin"),
        ("AGENT_OPERATOR_TOKEN", "operator"),
        ("AGENT_VIEWER_TOKEN", "viewer"),
    ):
        token = os.getenv(env_name, "").strip()
        if token:
            token_roles[token] = role
    default_role = os.getenv("AGENT_DEFAULT_ROLE", "viewer").strip().lower() or "viewer"
    return AuthSettings(token_roles=token_roles, default_role=default_role)


@dataclass(frozen=True)
class AuditSettings:
    db_path: Path
    fail_closed: bool


def get_audit_settings() -> AuditSettings:
    configured = os.getenv("AGENT_AUDIT_DB_PATH")
    db_path = (
        Path(configured)
        if configured
        else Path(__file__).resolve().parent / "audit" / "logs" / "audit.db"
    )
    fail_closed = os.getenv("AGENT_AUDIT_FAIL_CLOSED", "false").strip().lower() in {"1", "true", "yes"}
    return AuditSettings(db_path=db_path, fail_closed=fail_closed)

