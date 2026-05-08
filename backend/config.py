from __future__ import annotations

import os
from dataclasses import dataclass


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
            "model": "qwen-plus",
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
