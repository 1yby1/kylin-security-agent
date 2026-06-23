from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.agent.llm_client import LLMClient
from backend.agent.planner import Planner
from backend.config import LLMSettings
from backend.mcp_tools import disk_top_dirs_tool, large_file_tool, package_repo_tool
from backend.mcp_tools.builtin import build_registry
from backend.security.guard import SecurityGuard


def _decision(tools, arguments, role, approved=False):
    return SecurityGuard().check(
        raw_query="",
        tools=tools,
        arguments=arguments,
        user_id="u",
        registry=build_registry(),
        approved=approved,
        role=role,
    )


def _disabled_planner() -> Planner:
    return Planner(LLMClient(LLMSettings(provider="disabled", api_key="", base_url="", model="")))


class ScanPathBoundaryTest(unittest.TestCase):
    """P1: read-only scan path is allowlist-gated in the guard."""

    def test_allowlisted_path_is_low_and_viewer_allowed(self):
        decision = _decision(["disk.large_files"], {"path": "/var/log"}, "viewer")
        self.assertEqual(decision.risk_level, "low")
        self.assertFalse(decision.blocked)

    def test_nonallowlist_path_escalates_to_medium_and_blocks_viewer(self):
        decision = _decision(["disk.large_files"], {"path": "/"}, "viewer")
        self.assertEqual(decision.risk_level, "medium")
        self.assertTrue(decision.blocked)

    def test_nonallowlist_path_allowed_for_operator_with_approval(self):
        decision = _decision(["disk.top_dirs"], {"path": "/etc"}, "operator", approved=True)
        self.assertEqual(decision.risk_level, "medium")
        self.assertFalse(decision.blocked)

    def test_missing_path_defaults_to_medium(self):
        decision = _decision(["disk.top_dirs"], {}, "viewer")
        self.assertEqual(decision.risk_level, "medium")
        self.assertTrue(decision.blocked)

    def test_package_repo_default_dir_is_low(self):
        decision = _decision(["package.repo"], {}, "viewer")
        self.assertEqual(decision.risk_level, "low")
        self.assertFalse(decision.blocked)

    def test_package_repo_custom_dir_escalates_to_medium(self):
        decision = _decision(["package.repo"], {"repo_dir": "/etc"}, "viewer")
        self.assertEqual(decision.risk_level, "medium")
        self.assertTrue(decision.blocked)

    def test_is_safe_scan_path_rejects_traversal(self):
        self.assertFalse(SecurityGuard.is_safe_scan_path("/var/log/../../etc"))
        self.assertTrue(SecurityGuard.is_safe_scan_path("/var/log/messages"))


class ScanBudgetTest(unittest.TestCase):
    """P1: read-only scans stop at a max-entries budget."""

    def test_large_file_budget_exceeded(self):
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(5):
                (Path(tmp) / f"f{i}.bin").write_bytes(b"x")
            original = large_file_tool._MAX_SCAN_ENTRIES
            large_file_tool._MAX_SCAN_ENTRIES = 2
            try:
                result = large_file_tool.run({"path": tmp, "min_size_mb": 0})
            finally:
                large_file_tool._MAX_SCAN_ENTRIES = original
            self.assertTrue(result["budget_exceeded"])

    def test_large_file_no_budget_for_small_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a.bin").write_bytes(b"x")
            result = large_file_tool.run({"path": tmp, "min_size_mb": 0})
            self.assertFalse(result["budget_exceeded"])

    def test_top_dirs_budget_exceeded(self):
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(5):
                sub = Path(tmp) / f"d{i}"
                sub.mkdir()
                (sub / "f.bin").write_bytes(b"x")
            original = disk_top_dirs_tool._MAX_SCAN_ENTRIES
            disk_top_dirs_tool._MAX_SCAN_ENTRIES = 2
            try:
                result = disk_top_dirs_tool.run({"path": tmp})
            finally:
                disk_top_dirs_tool._MAX_SCAN_ENTRIES = original
            self.assertTrue(result["budget_exceeded"])


class RepoCredentialMaskTest(unittest.TestCase):
    """P2: repo URLs with embedded credentials are masked."""

    def test_mask_credentials_in_url(self):
        self.assertEqual(
            package_repo_tool._mask_url("https://user:pass@repo.internal/base"),
            "https://***:***@repo.internal/base",
        )

    def test_mask_is_noop_without_credentials(self):
        self.assertEqual(
            package_repo_tool._mask_url("https://repo.internal/base"),
            "https://repo.internal/base",
        )

    def test_repo_file_baseurl_is_masked(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_dir = Path(tmp)
            (repo_dir / "secret.repo").write_text(
                "[secret]\nname=Secret\nbaseurl=https://admin:s3cret@repo.internal/x\nenabled=1\n",
                encoding="utf-8",
            )
            from unittest import mock

            with mock.patch("backend.mcp_tools.package_repo_tool.shutil.which", return_value=None):
                result = package_repo_tool.run({"repo_dir": str(repo_dir), "check_repolist": False})
            baseurl = result["repositories"][0]["baseurl"]
            self.assertNotIn("s3cret", baseurl)
            self.assertIn("***", baseurl)


class PlannerDuWordBoundaryTest(unittest.TestCase):
    """P3: 'du' triggers scans only as a whole word, not as a substring."""

    def setUp(self):
        self.planner = _disabled_planner()

    def test_du_substring_does_not_trigger_scan(self):
        plan = self.planner.plan("please schedule a module rollout", {}, None)
        self.assertNotIn("disk.large_files", plan.tools)
        self.assertNotIn("disk.top_dirs", plan.tools)

    def test_du_word_triggers_scan(self):
        plan = self.planner.plan("run du now", {}, None)
        self.assertTrue("disk.large_files" in plan.tools or "disk.top_dirs" in plan.tools)


if __name__ == "__main__":
    unittest.main()
