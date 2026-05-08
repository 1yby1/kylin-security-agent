from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable


ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    title: str
    description: str
    category: str
    handler: ToolHandler
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    command_templates: list[str] = field(default_factory=list)
    platforms: list[str] = field(default_factory=lambda: ["kylin-v11", "linux"])
    risk_level: str = "low"
    read_only: bool = True
    enabled: bool = True

    def metadata(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("handler")
        return data


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> ToolDefinition:
        if definition.name in self._tools:
            raise ValueError(f"Tool already registered: {definition.name}")
        self._tools[definition.name] = definition
        return definition

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def names(self, enabled_only: bool = True) -> list[str]:
        return sorted(
            name for name, definition in self._tools.items() if definition.enabled or not enabled_only
        )

    def get(self, name: str) -> ToolDefinition | None:
        definition = self._tools.get(name)
        if definition is None or not definition.enabled:
            return None
        return definition

    def describe(self, name: str) -> dict[str, Any] | None:
        definition = self.get(name)
        return definition.metadata() if definition else None

    def manifest(self) -> dict[str, Any]:
        return {
            "protocol": "mcp-like",
            "version": "0.1.0",
            "tools": [self._tools[name].metadata() for name in self.names()],
        }

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        definition = self.get(name)
        if definition is None:
            return {"error": f"unknown tool: {name}"}
        return definition.handler(arguments)

