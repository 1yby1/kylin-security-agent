from __future__ import annotations

import unittest

from backend.agent.executor import ToolExecutor
from backend.agent.planner import Plan
from backend.security.redaction import redact_security_tool_output


def _auth_full() -> dict:
    return {
        "source": "auth",
        "last": {"stdout": ["root pts/0 192.168.1.5 still logged in"]},
        "lastb": {"stdout": ["bad ssh:notty 10.0.0.9 Mon"]},
        "who": {"stdout": ["root pts/0"]},
        "analysis": {
            "success_login_count": 1,
            "failed_login_count": 1,
            "active_sessions": 1,
            "root_remote_login": True,
            "top_source_ips": {"10.0.0.9": 2},
            "failed_log_readable": True,
        },
    }


def _privilege_full() -> dict:
    return {
        "source": "privilege",
        "suid": {"stdout": ["/usr/bin/sudo"]},
        "analysis": {
            "suid_count": 1,
            "sgid_count": 0,
            "suid_files": ["/usr/bin/sudo"],
            "extra_uid0_accounts": ["backdoor"],
            "empty_password_accounts": ["ghost"],
            "shadow_readable": True,
        },
    }


def _firewall_full() -> dict:
    return {
        "source": "firewall",
        "state": {"stdout": ["running"]},
        "list_all": {"stdout": ["public", "  ports: 22/tcp 23/tcp"]},
        "analysis": {
            "running": True,
            "open_port_count": 2,
            "open_service_count": 1,
            "open_ports": ["22/tcp", "23/tcp"],
            "open_services": ["ssh"],
            "high_risk_exposed": ["23"],
            "readable": True,
        },
    }


class RedactionUnitTest(unittest.TestCase):
    def test_viewer_auth_strips_detail_keeps_counts(self):
        redacted = redact_security_tool_output("auth", _auth_full(), "viewer")
        for raw in ("last", "lastb", "who"):
            self.assertNotIn(raw, redacted)
        self.assertNotIn("top_source_ips", redacted["analysis"])
        self.assertEqual(redacted["analysis"]["top_source_ip_count"], 1)
        self.assertEqual(redacted["analysis"]["failed_login_count"], 1)
        self.assertTrue(redacted["analysis"]["root_remote_login"])
        self.assertTrue(redacted["detail_redacted"])

    def test_operator_auth_is_unchanged(self):
        full = _auth_full()
        self.assertEqual(redact_security_tool_output("auth", full, "operator"), full)

    def test_admin_auth_is_unchanged(self):
        full = _auth_full()
        self.assertEqual(redact_security_tool_output("auth", full, "admin"), full)

    def test_viewer_privilege_strips_names_keeps_counts(self):
        redacted = redact_security_tool_output("privilege", _privilege_full(), "viewer")
        self.assertNotIn("suid", redacted)
        self.assertNotIn("suid_files", redacted["analysis"])
        self.assertNotIn("extra_uid0_accounts", redacted["analysis"])
        self.assertNotIn("empty_password_accounts", redacted["analysis"])
        self.assertEqual(redacted["analysis"]["extra_uid0_count"], 1)
        self.assertEqual(redacted["analysis"]["empty_password_count"], 1)
        self.assertEqual(redacted["analysis"]["suid_count"], 1)

    def test_viewer_firewall_strips_lists_keeps_flags(self):
        redacted = redact_security_tool_output("firewall", _firewall_full(), "viewer")
        self.assertNotIn("list_all", redacted)
        self.assertNotIn("open_ports", redacted["analysis"])
        self.assertNotIn("open_services", redacted["analysis"])
        self.assertEqual(redacted["analysis"]["open_port_count"], 2)
        self.assertEqual(redacted["analysis"]["high_risk_exposed"], ["23"])

    def test_non_recon_tool_unchanged(self):
        full = {"source": "system", "analysis": {"x": 1}, "detail": "keep"}
        self.assertEqual(redact_security_tool_output("system", full, "viewer"), full)

    def test_none_role_defaults_to_viewer(self):
        redacted = redact_security_tool_output("auth", _auth_full(), None)
        self.assertTrue(redacted["detail_redacted"])


class RedactionExecutorTest(unittest.TestCase):
    """Redaction is applied to the returned result by role; references/audit stay full."""

    def _execute_auth(self, role: str) -> object:
        executor = ToolExecutor()
        executor._registry.call = lambda tool, arguments: _auth_full()  # type: ignore[assignment]
        plan = Plan(intent="inspection", tools=["auth"], arguments={})
        return executor.execute(plan=plan, user_id="u", raw_query="检查登录情况", role=role)

    def test_viewer_execute_returns_redacted(self):
        execution = self._execute_auth("viewer")
        self.assertNotIn("last", execution.result["auth"])
        self.assertNotIn("top_source_ips", execution.result["auth"]["analysis"])
        self.assertTrue(execution.result["auth"]["detail_redacted"])

    def test_operator_execute_returns_full(self):
        execution = self._execute_auth("operator")
        self.assertIn("last", execution.result["auth"])
        self.assertNotIn("detail_redacted", execution.result["auth"])


if __name__ == "__main__":
    unittest.main()
