from __future__ import annotations

from backend.mcp_tools import (
    auth_tool,
    disk_tool,
    firewall_tool,
    process_kill_tool,
    log_tool,
    network_tool,
    process_tool,
    service_restart_tool,
    service_tool,
    system_tool,
    temp_clean_tool,
)
from backend.mcp_tools.registry import ToolDefinition, ToolRegistry


def build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    register_builtin_tools(registry)
    return registry


def register_builtin_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolDefinition(
            name="system",
            title="系统概览工具",
            description="采集主机、内核、运行时间、CPU、内存和磁盘概览。",
            category="perception",
            handler=system_tool.run,
            command_templates=[
                "system.uname",
                "system.hostnamectl",
                "system.uptime",
                "system.cpu",
                "system.memory",
                "system.disk",
            ],
            input_schema={"type": "object", "properties": {}},
        )
    )
    registry.register(
        ToolDefinition(
            name="process",
            title="进程分析工具",
            description="采集进程列表，并分析 CPU、内存占用较高的进程。",
            category="perception",
            handler=process_tool.run,
            command_templates=["process.list", "process.tree"],
            input_schema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                    "include_tree": {"type": "boolean"},
                },
            },
        )
    )
    registry.register(
        ToolDefinition(
            name="process.kill",
            title="进程终止工具",
            description="向指定非系统进程发送 TERM 信号，执行前校验 PID、进程归属和受保护进程。",
            category="operation",
            handler=process_kill_tool.run,
            command_templates=["process.by_pid", "process.kill"],
            input_schema={
                "type": "object",
                "required": ["pid"],
                "properties": {
                    "pid": {"type": "integer", "minimum": 101},
                    "expected_name": {"type": "string"},
                    "dry_run": {"type": "boolean"},
                },
            },
            risk_level="medium",
            read_only=False,
        )
    )
    registry.register(
        ToolDefinition(
            name="network",
            title="网络端口工具",
            description="采集监听端口、连接状态和可选 lsof 网络上下文。",
            category="perception",
            handler=network_tool.run,
            command_templates=["network.ports", "network.lsof"],
            input_schema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                    "include_lsof": {"type": "boolean"},
                },
            },
        )
    )
    registry.register(
        ToolDefinition(
            name="log",
            title="日志分析工具",
            description="读取 journalctl 或指定日志文件，并统计错误、告警、权限相关线索。",
            category="perception",
            handler=log_tool.run,
            command_templates=["log.journal", "log.journal_priority", "log.journal_unit"],
            input_schema={
                "type": "object",
                "properties": {
                    "source": {"type": "string", "enum": ["journal", "file"]},
                    "log_path": {"type": "string"},
                    "lines": {"type": "integer", "minimum": 1, "maximum": 500},
                    "priority": {"type": "string"},
                    "unit": {"type": "string"},
                },
            },
        )
    )
    registry.register(
        ToolDefinition(
            name="service",
            title="服务管理工具",
            description="只读查询 systemd 服务列表或单个服务状态。",
            category="perception",
            handler=service_tool.run,
            command_templates=["service.list", "service.status"],
            input_schema={
                "type": "object",
                "properties": {
                    "service_name": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                },
            },
        )
    )
    registry.register(
        ToolDefinition(
            name="service.restart",
            title="服务重启工具",
            description="重启指定白名单 systemd 服务，并在重启后查询服务状态。",
            category="operation",
            handler=service_restart_tool.run,
            command_templates=["service.restart", "service.status"],
            input_schema={
                "type": "object",
                "required": ["service_name"],
                "properties": {
                    "service_name": {"type": "string"},
                },
            },
            risk_level="medium",
            read_only=False,
        )
    )
    registry.register(
        ToolDefinition(
            name="temp.clean",
            title="临时文件清理工具",
            description="清理指定安全临时目录下超过指定时间的文件或子目录，支持 dry_run 预览。",
            category="operation",
            handler=temp_clean_tool.run,
            command_templates=[],
            input_schema={
                "type": "object",
                "required": ["path"],
                "properties": {
                    "path": {"type": "string"},
                    "max_age_hours": {"type": "integer", "minimum": 1, "maximum": 720},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 2000},
                    "dry_run": {"type": "boolean"},
                },
            },
            risk_level="medium",
            read_only=False,
        )
    )
    registry.register(
        ToolDefinition(
            name="disk",
            title="磁盘工具",
            description="查询指定路径所在文件系统的磁盘容量和使用率。",
            category="perception",
            handler=disk_tool.run,
            command_templates=[],
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                },
            },
        )
    )
    registry.register(
        ToolDefinition(
            name="auth",
            title="登录认证审计工具",
            description="采集近期成功登录、失败登录和当前会话，分析暴力破解与异常登录迹象。",
            category="security",
            handler=auth_tool.run,
            command_templates=["auth.last", "auth.lastb", "auth.who"],
            input_schema={
                "type": "object",
                "properties": {"lines": {"type": "integer", "minimum": 1, "maximum": 200}},
            },
        )
    )
    registry.register(
        ToolDefinition(
            name="firewall",
            title="防火墙暴露面工具",
            description="只读查看 firewalld 运行状态、默认区域开放端口与服务，识别高危暴露面。",
            category="security",
            handler=firewall_tool.run,
            command_templates=["firewall.state", "firewall.list_all"],
            input_schema={"type": "object", "properties": {}},
        )
    )
