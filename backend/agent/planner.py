from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from backend.agent.llm_client import LLMClient


@dataclass(frozen=True)
class Plan:
    intent: str
    tools: list[str]
    arguments: dict[str, Any] = field(default_factory=dict)
    arguments_by_tool: dict[str, dict[str, Any]] = field(default_factory=dict)
    summary: str = ""
    source: str = "rules"
    reasoning: list[str] = field(default_factory=list)

    def args_for(self, tool_name: str) -> dict[str, Any]:
        overrides = self.arguments_by_tool.get(tool_name, {})
        if not overrides:
            return self.arguments
        return {**self.arguments, **overrides}


class Planner:
    """Lightweight planner placeholder.

    This keeps the architecture usable before wiring in an LLM. Later, replace
    the keyword rules with a model call that returns the same Plan shape.
    """

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self._llm_client = llm_client or LLMClient()

    def plan(self, query: str, context: dict[str, Any] | None = None, tool_manifest: dict[str, Any] | None = None) -> Plan:
        text = query.lower()
        context = context or {}
        llm_decision = self._llm_client.analyze(query, context, tool_manifest)
        if llm_decision is not None:
            tools = list(dict.fromkeys(llm_decision.tools))
            arguments = {"query": query, **context, **llm_decision.arguments}
            self._apply_query_argument_overrides(text, tools, arguments)
            return Plan(
                intent=llm_decision.intent,
                tools=tools,
                arguments=arguments,
                arguments_by_tool=dict(llm_decision.arguments_by_tool),
                summary=llm_decision.summary,
                source="llm",
                reasoning=llm_decision.reasoning or [],
            )

        tools: list[str] = []

        if self._contains_any(text, ["kill", "terminate", "结束", "终止", "杀死"]) and self._contains_any(text, ["process", "pid", "进程"]):
            tools.append("process.kill")
        if "process.kill" not in tools and self._extract_pid(text) and self._contains_any(text, ["process", "pid", "进程"]):
            tools.append("process.detail")
        if "process.detail" not in tools and self._contains_any(text, ["top", "highest", "high", "cpu", "memory", "占用", "最高", "高"]) and self._contains_any(text, ["process", "pid", "cpu", "memory", "进程", "内存"]):
            tools.append("process.top")
        if self._contains_any(text, ["port", "端口"]) and self._contains_any(text, ["pid", "process", "进程", "占用", "监听", "对应"]):
            tools.append("network.port_lookup")
        if self._contains_any(text, ["search", "find", "grep", "搜索", "查找", "检索"]) and self._contains_any(text, ["log", "journal", "日志", "报错", "错误", "异常"]):
            tools.append("log.search")
        if self._contains_any(text, ["clean", "清理"]) and self._contains_any(text, ["temp", "tmp", "临时"]):
            tools.append("temp.clean")
        if self._contains_any(text, ["restart", "重启"]) and self._contains_any(text, ["service", "服务", "systemctl"]):
            tools.append("service.restart")
        if self._contains_any(text, ["overview", "system", "hostname", "uptime", "系统", "概览", "主机", "状态"]):
            tools.append("system")
        if not any(tool in tools for tool in ["process.kill", "process.detail", "process.top", "network.port_lookup"]) and self._contains_any(text, ["process", "pid", "cpu", "memory", "进程", "内存"]):
            tools.append("process")
        if "network.port_lookup" not in tools and self._contains_any(text, ["port", "network", "tcp", "udp", "ping", "端口", "网络"]):
            tools.append("network")
        if "log.search" not in tools and self._contains_any(text, ["log", "error", "exception", "日志", "报错", "异常"]):
            tools.append("log")
        if "service.restart" not in tools and self._contains_any(text, ["service", "systemctl", "status", "服务"]):
            tools.append("service")
        if self._contains_any(text, ["disk", "space", "df", "磁盘", "空间", "使用率", "盘"]):
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
        if "process.detail" in tools and "pid" not in arguments:
            pid = self._extract_pid(text)
            if pid:
                arguments["pid"] = pid
        if "process.top" in tools:
            arguments.setdefault("metric", self._extract_process_metric(text))
        if "network.port_lookup" in tools and "port" not in arguments:
            port = self._extract_port(text)
            if port:
                arguments["port"] = port
        if "log.search" in tools and "keyword" not in arguments:
            keyword = self._extract_log_keyword(text)
            if keyword:
                arguments["keyword"] = keyword
        if "temp.clean" in tools and "path" not in arguments:
            temp_path = self._extract_temp_path(text)
            if temp_path:
                arguments["path"] = temp_path
        self._apply_query_argument_overrides(text, tools, arguments)

        return Plan(
            intent=self._infer_intent(text),
            tools=list(dict.fromkeys(tools)),
            arguments=arguments,
            summary="本地规则识别的运维请求",
            source="rules",
            reasoning=["LLM 未启用或返回不可用，使用本地关键词规则选择工具。"],
        )

    @property
    def llm_client(self) -> LLMClient:
        return self._llm_client

    @staticmethod
    def _contains_any(text: str, words: list[str]) -> bool:
        return any(word in text for word in words)

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
    def _extract_process_metric(text: str) -> str:
        if any(word in text for word in ["memory", "mem", "内存"]):
            return "memory"
        return "cpu"

    @staticmethod
    def _extract_port(text: str) -> int:
        patterns = [
            r"\bport\s*(?:[:=]|为|是)?\s*(\d{1,5})\b",
            r"端口\s*(\d{1,5})",
            r"(\d{1,5})\s*端口",
            r":(\d{1,5})\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if not match:
                continue
            port = int(match.group(1))
            if 1 <= port <= 65535:
                return port
        return 0

    @staticmethod
    def _extract_log_keyword(text: str) -> str:
        patterns = [
            r"(?:关键词|关键字)\s*(?:[:=]|为|是)?\s*([^\s，。；,;]{1,64})",
            r"(?:search|find|grep)\s+(?:logs?\s+(?:for|with)\s+)?([^\s，。；,;]{1,64})",
            r"(?:搜索|查找|检索)\s+(?:日志中?的?)?\s*([^\s，。；,;]{1,64})",
            r"(?:日志中?的?)\s*(?:搜索|查找|检索)\s*([^\s，。；,;]{1,64})",
        ]
        stopwords = {"log", "logs", "journal", "日志", "中", "的"}
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if not match:
                continue
            keyword = match.group(1).strip("'\"“”‘’")
            if keyword and keyword not in stopwords:
                return keyword

        for keyword in ["exception", "failed", "failure", "denied", "timeout", "warning", "error", "异常", "失败", "拒绝", "超时", "警告", "错误"]:
            if keyword in text:
                return keyword
        return ""

    @staticmethod
    def _apply_query_argument_overrides(text: str, tools: list[str], arguments: dict[str, Any]) -> None:
        if "disk" in tools:
            disk_path = Planner._extract_disk_path(text)
            if disk_path:
                arguments["path"] = disk_path

    @staticmethod
    def _extract_disk_path(text: str) -> str:
        windows_patterns = [
            r"(?<![a-z0-9])([a-z])\s*(?:盘|drive)",
            r"(?<![a-z0-9])([a-z])\s*:\s*(?:\\|/)?",
        ]
        for pattern in windows_patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return f"{match.group(1).upper()}:/"

        linux_match = re.search(r"(?<![\w.-])/(?:[\w.+-]+/?)*", text)
        if linux_match:
            path = linux_match.group(0)
            return path if path else "/"
        return ""

    @staticmethod
    def _extract_temp_path(text: str) -> str:
        for path in ["/opt/software-cup-ops/tmp", "/var/tmp", "/tmp"]:
            if path in text:
                return path
        return ""
