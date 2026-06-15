import os
import unittest
from unittest import mock

from backend.config import get_mcp_settings


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


if __name__ == "__main__":
    unittest.main()
