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
from backend.mcp_tools import (
    disk_top_dirs_tool,
    large_file_tool,
    network_config_tool,
    network_diagnostics_tool,
    package_repo_tool,
)


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

        for tool_name in ["network.config", "package.repo", "disk.top_dirs"]:
            with self.subTest(tool=tool_name):
                metadata = registry.describe(tool_name)
                self.assertIsNotNone(metadata)
                assert metadata is not None
                self.assertTrue(metadata["read_only"])
                self.assertEqual(metadata["risk_level"], "low")
                self.assertEqual(metadata["input_schema"]["type"], "object")


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


class DiskTopDirsToolTests(unittest.TestCase):
    def test_returns_top_directory_usage_without_mutating_filesystem(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            alpha = root / "alpha"
            beta = root / "beta"
            alpha.mkdir()
            beta.mkdir()
            (alpha / "a.log").write_bytes(b"a" * 100)
            (beta / "b.log").write_bytes(b"b" * 300)
            (root / "root.cache").write_bytes(b"c" * 50)

            result = disk_top_dirs_tool.run(
                {"path": str(root), "limit": 2, "max_depth": 2, "include_files": True}
            )

            self.assertNotIn("error", result)
            self.assertEqual([Path(item["path"]).name for item in result["top_entries"]], ["beta", "alpha"])
            self.assertEqual(result["analysis"]["entry_count"], 2)
            self.assertTrue((beta / "b.log").exists())


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


class NetworkConfigToolTests(unittest.TestCase):
    @mock.patch("backend.mcp_tools.network_config_tool._read_resolv_conf")
    @mock.patch("backend.mcp_tools.network_config_tool.run_optional_template")
    def test_parses_route_and_dns_configuration(self, run_optional_template, read_resolv_conf) -> None:
        def fake_template(name, timeout=5):
            if name == "network.addr":
                return {
                    "command": "ip addr show",
                    "exit_code": 0,
                    "stdout": ["2: eth0: <UP>", "    inet 192.168.153.149/24 brd 192.168.153.255 scope global eth0"],
                    "stderr": [],
                }
            if name == "network.route":
                return {
                    "command": "ip route show",
                    "exit_code": 0,
                    "stdout": ["default via 192.168.153.2 dev eth0", "192.168.153.0/24 dev eth0 proto kernel"],
                    "stderr": [],
                }
            raise AssertionError(name)

        run_optional_template.side_effect = fake_template
        read_resolv_conf.return_value = {"path": "/etc/resolv.conf", "nameservers": ["8.8.8.8"], "search": []}

        result = network_config_tool.run({})

        self.assertEqual(result["analysis"]["default_gateway"], "192.168.153.2")
        self.assertEqual(result["analysis"]["dns_servers"], ["8.8.8.8"])
        self.assertTrue(result["analysis"]["has_ipv4"])


class PackageRepoToolTests(unittest.TestCase):
    def test_reads_repo_files_and_reports_enabled_repos(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_dir = Path(tmp)
            (repo_dir / "kylin.repo").write_text(
                "[base]\nname=Kylin Base\nbaseurl=https://updates.kylinos.cn/base\nenabled=1\ngpgcheck=1\n"
                "[disabled]\nname=Disabled\nenabled=0\n",
                encoding="utf-8",
            )

            with mock.patch("backend.mcp_tools.package_repo_tool.shutil.which", return_value=None):
                result = package_repo_tool.run({"repo_dir": str(repo_dir), "check_repolist": False})

            self.assertNotIn("error", result)
            self.assertEqual(result["analysis"]["repo_count"], 2)
            self.assertEqual(result["analysis"]["enabled_repo_count"], 1)
            self.assertFalse(result["analysis"]["manager_found"])
            self.assertEqual(result["repositories"][0]["id"], "base")


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

    def test_network_config_request_selects_network_config_tool(self) -> None:
        plan = _disabled_planner().plan("检查本机 IP 网关 路由 和 DNS 配置")

        self.assertIn("network.config", plan.tools)
        self.assertNotIn("network.diagnostics", plan.tools)

    def test_package_repo_request_selects_package_repo_tool(self) -> None:
        plan = _disabled_planner().plan("检查麒麟 yum 软件源和 repo 配置是否可用")

        self.assertIn("package.repo", plan.tools)

    def test_directory_space_request_selects_top_dirs_tool(self) -> None:
        plan = _disabled_planner().plan("看看 /var 下哪个目录最占空间")

        self.assertIn("disk.top_dirs", plan.tools)
        self.assertEqual(plan.arguments["path"], "/var")


if __name__ == "__main__":
    unittest.main()
