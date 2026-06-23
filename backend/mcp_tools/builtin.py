from __future__ import annotations

from backend.mcp_tools import (
    auth_tool,
    disk_top_dirs_tool,
    disk_tool,
    firewall_tool,
    large_file_tool,
    package_repo_tool,
    privilege_tool,
    process_kill_tool,
    log_tool,
    network_config_tool,
    network_diagnostics_tool,
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
                    "pid": {"type": "integer", "minimum": 1},
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
            name="network.diagnostics",
            title="网络连通性诊断工具",
            description="对内置白名单目标执行 DNS 解析和 ping 连通性诊断。只读探测，不进行端口扫描。",
            category="perception",
            handler=network_diagnostics_tool.run,
            command_templates=[],
            input_schema={
                "type": "object",
                "required": ["target"],
                "properties": {
                    "target": {"type": "string", "enum": sorted(network_diagnostics_tool.ALLOWED_TARGETS)},
                    "count": {"type": "integer", "minimum": 1, "maximum": 5},
                    "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 10},
                    "dns": {"type": "boolean"},
                    "ping": {"type": "boolean"},
                },
            },
        )
    )
    registry.register(
        ToolDefinition(
            name="network.config",
            title="网络配置诊断工具",
            description="只读采集本机 IP 地址、路由、默认网关和 DNS 配置。",
            category="perception",
            handler=network_config_tool.run,
            command_templates=["network.addr", "network.route"],
            input_schema={
                "type": "object",
                "properties": {
                    "include_addr": {"type": "boolean"},
                    "include_route": {"type": "boolean"},
                    "include_dns": {"type": "boolean"},
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
            name="package.repo",
            title="软件源诊断工具",
            description="只读检查 yum/dnf 软件源配置、启用仓库和包管理器可用性。",
            category="perception",
            handler=package_repo_tool.run,
            command_templates=[],
            input_schema={
                "type": "object",
                "properties": {
                    "repo_dir": {"type": "string"},
                    "check_repolist": {"type": "boolean"},
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
            name="disk.large_files",
            title="大文件定位工具",
            description="只读扫描指定目录，列出占用空间最大的文件，帮助定位磁盘空间来源。",
            category="perception",
            handler=large_file_tool.run,
            command_templates=[],
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    "min_size_mb": {"type": "integer", "minimum": 0, "maximum": 1048576},
                    "max_depth": {"type": "integer", "minimum": 0, "maximum": 20},
                },
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
    registry.register(
        ToolDefinition(
            name="disk.top_dirs",
            title="目录空间定位工具",
            description="只读统计指定目录下子目录占用，定位磁盘空间主要来源。",
            category="perception",
            handler=disk_top_dirs_tool.run,
            command_templates=[],
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    "max_depth": {"type": "integer", "minimum": 0, "maximum": 20},
                    "include_files": {"type": "boolean"},
                },
            },
        )
    )
    registry.register(
        ToolDefinition(
            name="privilege",
            title="提权风险扫描工具",
            description="扫描特权目录下的 SUID/SGID 文件、UID 0 账户和空密码账户，识别提权风险。",
            category="security",
            handler=privilege_tool.run,
            command_templates=[
                "privilege.suid",
                "privilege.sgid",
                "privilege.uid0",
                "privilege.empty_password",
            ],
            input_schema={"type": "object", "properties": {}},
        )
    )
