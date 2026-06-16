from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from backend.agent.executor import ToolExecutor
from backend.agent.planner import Plan
from backend.config import get_auth_settings
from backend.security.auth import parse_bearer, resolve_role


class ParseBearerTests(unittest.TestCase):
    def test_extracts_token(self) -> None:
        self.assertEqual(parse_bearer("Bearer abc123"), "abc123")

    def test_scheme_is_case_insensitive(self) -> None:
        self.assertEqual(parse_bearer("bearer xyz"), "xyz")

    def test_missing_or_malformed_returns_none(self) -> None:
        self.assertIsNone(parse_bearer(None))
        self.assertIsNone(parse_bearer(""))
        self.assertIsNone(parse_bearer("Token abc"))
        self.assertIsNone(parse_bearer("Bearer "))


class ResolveRoleTests(unittest.TestCase):
    def test_known_tokens_map_to_roles(self) -> None:
        env = {"AGENT_ADMIN_TOKEN": "admin-tok", "AGENT_OPERATOR_TOKEN": "op-tok"}
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("AGENT_VIEWER_TOKEN", None)
            os.environ.pop("AGENT_DEFAULT_ROLE", None)
            self.assertEqual(resolve_role("admin-tok"), "admin")
            self.assertEqual(resolve_role("op-tok"), "operator")

    def test_unknown_or_missing_token_defaults_to_viewer(self) -> None:
        with mock.patch.dict(os.environ, {"AGENT_OPERATOR_TOKEN": "op-tok"}, clear=False):
            os.environ.pop("AGENT_DEFAULT_ROLE", None)
            self.assertEqual(resolve_role("wrong"), "viewer")
            self.assertEqual(resolve_role(None), "viewer")

    def test_default_role_override(self) -> None:
        with mock.patch.dict(os.environ, {"AGENT_DEFAULT_ROLE": "operator"}, clear=False):
            self.assertEqual(resolve_role(None), "operator")

    def test_empty_token_env_is_ignored(self) -> None:
        with mock.patch.dict(os.environ, {"AGENT_ADMIN_TOKEN": "   "}, clear=False):
            self.assertNotIn("   ", get_auth_settings().token_roles)


class TrustedRoleOverridesForgedArgumentsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["AGENT_AUDIT_LOG_PATH"] = os.path.join(self._tmp.name, "audit.log")
        self.executor = ToolExecutor()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _restart_plan(self) -> Plan:
        # arguments forge an admin role; a trusted role must override it.
        return Plan(
            intent="risky_operation",
            tools=["service.restart"],
            arguments={"service_name": "nginx", "user_role": "admin"},
        )

    def test_forged_admin_role_is_ignored_when_trusted_role_is_viewer(self) -> None:
        security = self.executor.evaluate_security(
            self._restart_plan(), "attacker", "重启 nginx 服务", approved=True, role="viewer"
        )
        self.assertTrue(security["blocked"])
        self.assertIn("role viewer is not allowed for risk level medium", security["reasons"])

    def test_trusted_operator_role_allows_controlled_op(self) -> None:
        security = self.executor.evaluate_security(
            self._restart_plan(), "op1", "重启 nginx 服务", approved=True, role="operator"
        )
        self.assertFalse(security["blocked"], msg=security["reasons"])

    def test_role_none_preserves_legacy_argument_behavior(self) -> None:
        # No trusted role passed -> falls back to arguments["user_role"] (legacy callers).
        security = self.executor.evaluate_security(
            self._restart_plan(), "op1", "重启 nginx 服务", approved=True
        )
        self.assertFalse(security["blocked"], msg=security["reasons"])


if __name__ == "__main__":
    unittest.main()
