from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from backend.agent.llm_client import LLMClient
from backend.agent.planner import Planner
from backend.config import LLMSettings
from backend.mcp_tools import build_registry
from backend.mcp_tools import large_file_tool, network_diagnostics_tool


def _disabled_planner() -> Planner:
    return Planner(LLMClient(LLMSettings(provider="disabled", api_key="", base_url="", model="")))


class DiagnosticToolRegistryTests(unittest.TestCase):
    def test_read_only_diagnostic_tools_are_registered(self) -> None:
        registry = build_registry()

        large_files = registry.describe("disk.large_files")
        self.assertIsNotNone(large_files)
        assert large_files is not None
        self.assertTrue(large_files["read_only"])
        self.assertEqual(large_files["risk_level"], "low")
        self.assertEqual(large_files["input_schema"]["type"], "object")

        network_diag = registry.describe("network.diagnostics")
        self.assertIsNotNone(network_diag)
        assert network_diag is not None
        self.assertTrue(network_diag["read_only"])
        self.assertEqual(network_diag["risk_level"], "low")
        self.assertEqual(network_diag["input_schema"]["type"], "object")


class LargeFileToolTests(unittest.TestCase):
    def test_returns_largest_files_without_mutating_filesystem(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "small.log").write_bytes(b"a" * 10)
            (root / "large.bin").write_bytes(b"b" * 300)
            nested = root / "nested"
            nested.mkdir()
            (nested / "medium.cache").write_bytes(b"c" * 120)

            result = large_file_tool.run(
                {"path": str(root), "limit": 2, "min_size_mb": 0, "max_depth": 3}
            )

            self.assertNotIn("error", result)
            self.assertEqual([Path(item["path"]).name for item in result["largest_files"]], ["large.bin", "medium.cache"])
            self.assertEqual(result["analysis"]["file_count"], 2)
            self.assertTrue((root / "large.bin").exists())
            self.assertTrue((nested / "medium.cache").exists())

    def test_respects_max_depth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            deep = root / "a" / "b"
            deep.mkdir(parents=True)
            (deep / "deep.bin").write_bytes(b"x" * 200)
            (root / "root.bin").write_bytes(b"y" * 100)

            result = large_file_tool.run(
                {"path": str(root), "limit": 5, "min_size_mb": 0, "max_depth": 0}
            )

            self.assertEqual([Path(item["path"]).name for item in result["largest_files"]], ["root.bin"])


class NetworkDiagnosticsToolTests(unittest.TestCase):
    @mock.patch("backend.mcp_tools.network_diagnostics_tool.subprocess.run")
    @mock.patch("backend.mcp_tools.network_diagnostics_tool.socket.getaddrinfo")
    def test_runs_dns_and_ping_for_allowlisted_target(self, getaddrinfo, subprocess_run) -> None:
        getaddrinfo.return_value = [
            (None, None, None, "", ("127.0.0.1", 0)),
            (None, None, None, "", ("127.0.0.1", 0)),
        ]
        subprocess_run.return_value = SimpleNamespace(
            returncode=0,
            stdout="PING localhost\n1 packets transmitted, 1 received",
            stderr="",
        )

        result = network_diagnostics_tool.run(
            {"target": "localhost", "count": 1, "timeout_seconds": 1}
        )

        self.assertNotIn("error", result)
        self.assertEqual(result["target"], "localhost")
        self.assertEqual(result["dns"]["addresses"], ["127.0.0.1"])
        self.assertTrue(result["analysis"]["dns_resolved"])
        self.assertTrue(result["analysis"]["ping_reachable"])
        self.assertTrue(result["analysis"]["diagnostic_completed"])
        self.assertIn("ping", result["ping"]["command"])
        subprocess_run.assert_called_once()

    def test_rejects_targets_outside_allowlist(self) -> None:
        result = network_diagnostics_tool.run({"target": "example.com"})

        self.assertIn("error", result)
        self.assertIn("not in allowlist", result["error"])


class DiagnosticPlannerTests(unittest.TestCase):
    def test_large_file_request_selects_large_file_tool(self) -> None:
        plan = _disabled_planner().plan("找出 /var/log 下谁最占空间，列出大文件")

        self.assertIn("disk.large_files", plan.tools)
        self.assertEqual(plan.arguments["path"], "/var/log")
        self.assertEqual(plan.intent, "inspection")

    def test_network_connectivity_request_selects_diagnostics_tool(self) -> None:
        plan = _disabled_planner().plan("诊断 updates.kylinos.cn 的 DNS 解析和 ping 连通性")

        self.assertIn("network.diagnostics", plan.tools)
        self.assertEqual(plan.arguments["target"], "updates.kylinos.cn")
        self.assertEqual(plan.intent, "diagnosis")


if __name__ == "__main__":
    unittest.main()
