from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from backend.agent.llm_client import LLMClient
from backend.security.sanitizer import build_observation_block


@dataclass(frozen=True)
class PlanStep:
    """A single ordered step in a tool-orchestration chain.

    Each step carries its own ``tool`` and ``arguments`` so a plan can chain
    several tools, each with distinct parameters. Argument values may contain
    ``${stepId.path}`` references that are resolved against earlier steps'
    outputs just before the step is security-checked and executed.
    """

    id: str
    tool: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Plan:
    intent: str
    tools: list[str]
    arguments: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    source: str = "rules"
    reasoning: list[str] = field(default_factory=list)
    steps: list[PlanStep] = field(default_factory=list)

    def execution_steps(self) -> list[PlanStep]:
        """Return the ordered steps to execute.

        When an explicit ``steps`` orchestration is present it is used as-is.
        Otherwise one step per tool is derived, each sharing the plan-level
        ``arguments``. This preserves the original single-arguments behaviour
        for legacy plans that only carry ``tools`` + ``arguments``.
        """
        if self.steps:
            return self.steps
        return [
            PlanStep(id=f"s{index + 1}", tool=tool, arguments=dict(self.arguments))
            for index, tool in enumerate(self.tools)
        ]


class Planner:
    """Lightweight planner placeholder.

    This keeps the architecture usable before wiring in an LLM. Later, replace
    the keyword rules with a model call that returns the same Plan shape.
    """

    NETWORK_DIAGNOSTIC_TARGETS = {
        "localhost",
        "127.0.0.1",
        "::1",
        "updates.kylinos.cn",
        "mirrors.aliyun.com",
        "repo.huaweicloud.com",
        "mirrors.tuna.tsinghua.edu.cn",
        "www.baidu.com",
        "114.114.114.114",
        "223.5.5.5",
        "8.8.8.8",
    }

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self._llm_client = llm_client or LLMClient()

    def plan(self, query: str, context: dict[str, Any] | None = None, tool_manifest: dict[str, Any] | None = None) -> Plan:
        text = query.lower()
        context = context or {}
        llm_decision = self._llm_client.analyze(query, context, tool_manifest)
        if llm_decision is not None:
            base_arguments = {"query": query, **context}
            if llm_decision.steps:
                steps = [
                    PlanStep(
                        id=step["id"],
                        tool=step["tool"],
                        arguments=self._enrich_arguments(
                            text, step["tool"], {**base_arguments, **step.get("arguments", {})}
                        ),
                    )
                    for step in llm_decision.steps
                ]
                return Plan(
                    intent=llm_decision.intent,
                    tools=list(dict.fromkeys(step.tool for step in steps)),
                    arguments=base_arguments,
                    summary=llm_decision.summary,
                    source="llm",
                    reasoning=llm_decision.reasoning or [],
                    steps=steps,
                )
            arguments = {**base_arguments, **llm_decision.arguments}
            for tool in llm_decision.tools:
                arguments = self._enrich_arguments(text, tool, arguments)
            return Plan(
                intent=llm_decision.intent,
                tools=list(dict.fromkeys(llm_decision.tools)),
                arguments=arguments,
                summary=llm_decision.summary,
                source="llm",
                reasoning=llm_decision.reasoning or [],
            )

        tools: list[str] = []

        if self._contains_any(text, ["kill", "terminate", "结束", "终止", "杀死"]) and self._contains_any(text, ["process", "pid", "进程"]):
            tools.append("process.kill")
        if self._contains_any(text, ["clean", "清理"]) and self._contains_any(text, ["temp", "tmp", "临时"]):
            tools.append("temp.clean")
        if self._contains_any(text, ["restart", "重启"]) and self._contains_any(text, ["service", "服务", "systemctl"]):
            tools.append("service.restart")
        if self._contains_any(text, ["overview", "system", "hostname", "uptime", "系统", "概览", "主机", "状态"]):
            tools.append("system")
        if "process.kill" not in tools and self._contains_any(text, ["process", "pid", "cpu", "memory", "进程", "内存"]):
            tools.append("process")
        if self._is_network_config_request(text):
            tools.append("network.config")
        if self._is_network_diagnostic_request(text) and "network.config" not in tools:
            tools.append("network.diagnostics")
        elif "network.config" not in tools and self._contains_any(text, ["port", "network", "tcp", "udp", "端口", "网络"]):
            tools.append("network")
        if self._contains_any(text, ["log", "error", "exception", "日志", "报错", "异常"]):
            tools.append("log")
        if "service.restart" not in tools and self._contains_any(text, ["service", "systemctl", "status", "服务"]):
            tools.append("service")
        if self._is_package_repo_request(text):
            tools.append("package.repo")
        if self._is_top_dirs_request(text):
            tools.append("disk.top_dirs")
        if self._is_large_file_request(text):
            tools.append("disk.large_files")
        if self._contains_any(text, ["disk", "space", "df", "磁盘", "空间"]):
            tools.append("disk")

        if not tools:
            tools = ["system", "process"]

        arguments = {"query": query, **context}
        if "service.restart" in tools and "service_name" not in arguments:
            service_name = self._extract_service_name(text)
            if service_name:
                arguments["service_name"] = service_name
        if "process.kill" in tools and "pid" not in arguments:
            pid = self._extract_pid(text)
            if pid:
                arguments["pid"] = pid
        if "temp.clean" in tools and "path" not in arguments:
            temp_path = self._extract_temp_path(text)
            if temp_path:
                arguments["path"] = temp_path
        if "temp.clean" in tools:
            arguments = self._enrich_arguments(text, "temp.clean", arguments)
        if "disk.large_files" in tools:
            arguments = self._enrich_arguments(text, "disk.large_files", arguments)
        if "network.diagnostics" in tools:
            arguments = self._enrich_arguments(text, "network.diagnostics", arguments)
        if "disk.top_dirs" in tools:
            arguments = self._enrich_arguments(text, "disk.top_dirs", arguments)
        if "package.repo" in tools:
            arguments = self._enrich_arguments(text, "package.repo", arguments)

        return Plan(
            intent=self._infer_intent(text),
            tools=list(dict.fromkeys(tools)),
            arguments=arguments,
            summary="本地规则识别的运维请求",
            source="rules",
            reasoning=["LLM 未启用或返回不可用，使用本地关键词规则选择工具。"],
        )

    def plan_next(
        self,
        query: str,
        context: dict[str, Any],
        prior_results: dict[str, Any],
        executed_tools: set[str],
        tool_manifest: dict[str, Any] | None = None,
    ) -> Plan | None:
        if self._llm_client.enabled:
            observation = build_observation_block(prior_results)
            enriched = {**context, "observations": observation, "already_executed": sorted(executed_tools)}
            decision = self._llm_client.analyze(query, enriched, tool_manifest)
            if decision is None:
                return self._rule_next(query, context, prior_results, executed_tools)
            base_arguments = {"query": query, **context}
            if decision.steps:
                steps = [
                    PlanStep(
                        id=step["id"],
                        tool=step["tool"],
                        arguments={**base_arguments, **step.get("arguments", {})},
                    )
                    for step in decision.steps
                    if step["tool"] not in executed_tools
                ]
                if not steps:
                    return None
                return Plan(
                    intent=decision.intent,
                    tools=list(dict.fromkeys(step.tool for step in steps)),
                    arguments=base_arguments,
                    summary=decision.summary or "闭环下一步",
                    source="llm",
                    reasoning=decision.reasoning or [],
                    steps=steps,
                )
            new_tools = [tool for tool in decision.tools if tool not in executed_tools]
            if not new_tools:
                return None
            return Plan(
                intent=decision.intent,
                tools=list(dict.fromkeys(new_tools)),
                arguments={**base_arguments, **decision.arguments},
                summary=decision.summary or "闭环下一步",
                source="llm",
                reasoning=decision.reasoning or [],
            )
        return self._rule_next(query, context, prior_results, executed_tools)

    @staticmethod
    def _rule_next(
        query: str,
        context: dict[str, Any],
        prior_results: dict[str, Any],
        executed_tools: set[str],
    ) -> Plan | None:
        service_output = prior_results.get("service")
        if not isinstance(service_output, dict) or "log" in executed_tools:
            return None
        analysis = service_output.get("analysis", {})
        if not isinstance(analysis, dict):
            return None
        if analysis.get("failed_count", 0) <= 0 and analysis.get("inactive_count", 0) <= 0:
            return None
        arguments: dict[str, Any] = {"query": query, **context}
        service_name = context.get("service_name")
        if service_name:
            arguments["unit"] = service_name
        return Plan(
            intent="diagnosis",
            tools=["log"],
            arguments=arguments,
            summary="检测到服务异常，自动拉取日志",
            source="rules",
            reasoning=["service 工具发现 failed/inactive 服务，升级到 log 工具。"],
        )

    @property
    def llm_client(self) -> LLMClient:
        return self._llm_client

    @staticmethod
    def _contains_any(text: str, words: list[str]) -> bool:
        return any(word in text for word in words)

    @classmethod
    def _is_large_file_request(cls, text: str) -> bool:
        return cls._contains_any(
            text,
            [
                "large file",
                "largest",
                "big file",
                "du",
                "大文件",
                "最大文件",
                "谁占",
                "占空间",
                "占用空间",
                "空间占用",
                "磁盘满",
                "空间不足",
            ],
        )

    @classmethod
    def _is_top_dirs_request(cls, text: str) -> bool:
        return cls._contains_any(
            text,
            [
                "top dirs",
                "top directories",
                "目录占用",
                "目录空间",
                "哪个目录",
                "哪些目录",
                "子目录",
                "文件夹占用",
                "du",
                "谁占",
                "磁盘满",
                "空间不足",
            ],
        )

    @classmethod
    def _is_network_config_request(cls, text: str) -> bool:
        return cls._contains_any(
            text,
            [
                "network config",
                "ip addr",
                "ip address",
                "ip route",
                "gateway",
                "route",
                "resolv.conf",
                "网络配置",
                "网卡",
                "网关",
                "路由",
                "dns 配置",
                "dns配置",
                "本机 ip",
            ],
        )

    @classmethod
    def _is_network_diagnostic_request(cls, text: str) -> bool:
        return cls._contains_any(
            text,
            [
                "ping",
                "dns",
                "resolve",
                "resolution",
                "reachable",
                "connectivity",
                "连通",
                "连通性",
                "解析",
                "网络诊断",
                "能不能访问",
            ],
        )

    @classmethod
    def _is_package_repo_request(cls, text: str) -> bool:
        return cls._contains_any(
            text,
            [
                "yum",
                "dnf",
                "repo",
                "repository",
                "软件源",
                "仓库",
                "安装源",
                "基础依赖",
                "依赖安装",
                "包管理",
            ],
        )

    @staticmethod
    def _infer_intent(text: str) -> str:
        if any(word in text for word in ["restart", "stop", "kill", "terminate", "clean", "删除", "停止", "重启", "清理", "杀死", "结束", "终止"]):
            return "risky_operation"
        if any(word in text for word in ["why", "原因", "排查", "诊断"]):
            return "diagnosis"
        return "inspection"

    @staticmethod
    def _extract_service_name(text: str) -> str:
        allowlist = ["nginx", "httpd", "mysqld", "postgresql", "redis", "software-cup-ops"]
        for service_name in allowlist:
            if service_name in text:
                return service_name
        return ""

    @staticmethod
    def _extract_pid(text: str) -> int:
        patterns = [
            r"\bpid\s*(?:[:=]|为|是)?\s*(\d{1,10})\b",
            r"\bprocess\s+(\d{1,10})\b",
            r"进程\s*(\d{1,10})",
            r"(\d{1,10})\s*号?进程",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return int(match.group(1))
        return 0

    @staticmethod
    def _extract_temp_path(text: str) -> str:
        for path in ["/opt/software-cup-ops/tmp", "/var/tmp", "/tmp"]:
            if path in text:
                return path
        return ""

    @classmethod
    def _enrich_arguments(cls, text: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Fill tool arguments the planner can read from explicit text.

        This is fill-if-missing only: it never overrides a value the LLM already
        produced. For controlled tools it only fills values the user literally
        wrote (and the safe ``dry_run=true`` direction), so enrichment preserves
        user intent instead of rewriting the model's decision.
        """
        enriched = dict(arguments)
        if tool == "temp.clean":
            if "path" not in enriched:
                temp_path = cls._extract_temp_path(text)
                if temp_path:
                    enriched["path"] = temp_path
            if "max_age_hours" not in enriched:
                hours = cls._extract_max_age_hours(text)
                if hours:
                    enriched["max_age_hours"] = hours
            if "dry_run" not in enriched and cls._is_temp_clean_preview(text):
                enriched["dry_run"] = True
            return enriched
        if tool == "disk.large_files":
            if "path" not in enriched:
                path = cls._extract_path(text)
                if path:
                    enriched["path"] = path
            if "limit" not in enriched:
                limit = cls._extract_limit(text)
                if limit:
                    enriched["limit"] = limit
            if "min_size_mb" not in enriched:
                min_size_mb = cls._extract_min_size_mb(text)
                if min_size_mb is not None:
                    enriched["min_size_mb"] = min_size_mb
            if "max_depth" not in enriched:
                max_depth = cls._extract_max_depth(text)
                if max_depth is not None:
                    enriched["max_depth"] = max_depth
            return enriched
        if tool == "network.diagnostics":
            if "target" not in enriched:
                enriched["target"] = cls._extract_network_target(text)
            if "count" not in enriched:
                enriched["count"] = 3
            if "timeout_seconds" not in enriched:
                enriched["timeout_seconds"] = 3
            return enriched
        if tool == "disk.top_dirs":
            if "path" not in enriched:
                path = cls._extract_path(text)
                if path:
                    enriched["path"] = path
            if "limit" not in enriched:
                limit = cls._extract_limit(text)
                if limit:
                    enriched["limit"] = limit
            if "max_depth" not in enriched:
                max_depth = cls._extract_max_depth(text)
                if max_depth is not None:
                    enriched["max_depth"] = max_depth
            return enriched
        if tool == "package.repo":
            if "repo_dir" not in enriched:
                path = cls._extract_path(text)
                if path and path.endswith(".repos.d"):
                    enriched["repo_dir"] = path
            return enriched
        return enriched

    @staticmethod
    def _extract_path(text: str) -> str:
        paths = re.findall(r"(?<![\w.-])/(?:[\w.+-]+/?)+", text)
        return paths[0].rstrip("/") if paths else ""

    @staticmethod
    def _extract_limit(text: str) -> int:
        patterns = [
            r"(?:top|limit)\s*(\d{1,3})",
            r"(?:前|最多|列出)\s*(\d{1,3})\s*(?:个|条)?",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return max(1, min(int(match.group(1)), 100))
        return 0

    @staticmethod
    def _extract_min_size_mb(text: str) -> int | None:
        pattern = r"(?:超过|大于|over|>)\s*(\d{1,6})\s*(gb|g|mb|m)\b"
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is None:
            return None
        value = int(match.group(1))
        unit = match.group(2).lower()
        if unit in {"gb", "g"}:
            value *= 1024
        return max(0, min(value, 1024 * 1024))

    @staticmethod
    def _extract_max_depth(text: str) -> int | None:
        patterns = [
            r"max_depth\s*[:=]?\s*(\d{1,2})",
            r"(?:深度|层级)\s*(\d{1,2})",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return max(0, min(int(match.group(1)), 20))
        return None

    @classmethod
    def _extract_network_target(cls, text: str) -> str:
        for target in sorted(cls.NETWORK_DIAGNOSTIC_TARGETS, key=len, reverse=True):
            if target in text:
                return target
        domain = re.search(r"\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b", text, flags=re.IGNORECASE)
        if domain:
            return domain.group(0).lower().rstrip(".")
        ipv4 = re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text)
        if ipv4:
            return ipv4.group(0)
        if cls._contains_any(text, ["kylin", "麒麟", "yum", "dnf", "repo", "软件源", "更新源", "updates"]):
            return "updates.kylinos.cn"
        return "localhost"

    @staticmethod
    def _is_temp_clean_preview(text: str) -> bool:
        preview_markers = [
            "dry_run",
            "dry-run",
            "dry run",
            "preview",
            "预览",
            "预演",
            "模拟",
            "试运行",
            "只查看",
            "仅查看",
            "不要真正删除",
            "不真正删除",
            "不要删除",
            "不删除",
            "不实际删除",
        ]
        return any(marker in text for marker in preview_markers)

    @staticmethod
    def _extract_max_age_hours(text: str) -> int:
        hour_patterns = [
            r"(?:超过|大于|older\s+than|over|超过了)\s*(\d{1,4})\s*(?:小时|hour|hours|h)",
            r"(\d{1,4})\s*(?:小时|hour|hours|h)\s*(?:以上|前|以前|old)?",
            r"max_age_hours\s*[:=]?\s*(\d{1,4})",
        ]
        for pattern in hour_patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return max(1, min(int(match.group(1)), 720))
        day_patterns = [
            r"(?:超过|大于|older\s+than|over)\s*(\d{1,3})\s*(?:天|day|days|d)",
            r"(\d{1,3})\s*(?:天|day|days|d)\s*(?:以上|前|以前|old)?",
        ]
        for pattern in day_patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return max(1, min(int(match.group(1)) * 24, 720))
        return 0
