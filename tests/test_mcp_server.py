import os
import unittest
from unittest import mock

from backend.agent.executor import ToolExecutor
from backend.config import get_mcp_settings
from backend.mcp_server.server import build_tool_list


class MCPSettingsTests(unittest.TestCase):
    def test_defaults_to_lowest_privilege(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENT_MCP_CLIENT_ROLE", None)
            os.environ.pop("AGENT_MCP_CLIENT_USER", None)
            settings = get_mcp_settings()
            self.assertEqual(settings.client_role, "viewer")
            self.assertEqual(settings.client_user_id, "mcp-client")

    def test_role_override(self):
        with mock.patch.dict(os.environ, {"AGENT_MCP_CLIENT_ROLE": "operator"}, clear=False):
            self.assertEqual(get_mcp_settings().client_role, "operator")


class BuildToolListTests(unittest.TestCase):
    def test_lists_all_registry_tools_with_object_schema(self):
        executor = ToolExecutor()
        tools = build_tool_list(executor)
        names = sorted(tool.name for tool in tools)
        self.assertEqual(names, sorted(executor.available_tools()))
        for tool in tools:
            self.assertIsInstance(tool.inputSchema, dict)
            self.assertEqual(tool.inputSchema.get("type", "object"), "object")


if __name__ == "__main__":
    unittest.main()
