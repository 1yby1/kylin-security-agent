from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.config import get_audit_settings


class AuditSettingsTests(unittest.TestCase):
    def test_defaults(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENT_AUDIT_DB_PATH", None)
            os.environ.pop("AGENT_AUDIT_FAIL_CLOSED", None)
            settings = get_audit_settings()
            self.assertTrue(str(settings.db_path).endswith("audit.db"))
            self.assertFalse(settings.fail_closed)

    def test_overrides(self):
        env = {"AGENT_AUDIT_DB_PATH": "/tmp/x/audit.db", "AGENT_AUDIT_FAIL_CLOSED": "true"}
        with mock.patch.dict(os.environ, env, clear=False):
            settings = get_audit_settings()
            self.assertEqual(Path(settings.db_path), Path("/tmp/x/audit.db"))
            self.assertTrue(settings.fail_closed)


if __name__ == "__main__":
    unittest.main()
