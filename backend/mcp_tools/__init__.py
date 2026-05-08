"""Local MCP-like tools exposed to the agent executor."""

from backend.mcp_tools.builtin import build_registry, register_builtin_tools
from backend.mcp_tools.registry import ToolDefinition, ToolRegistry

__all__ = ["ToolDefinition", "ToolRegistry", "build_registry", "register_builtin_tools"]
