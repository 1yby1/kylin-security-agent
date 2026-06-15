from __future__ import annotations

import os
import tempfile
import unittest

from backend.agent.orchestrator import AgentOrchestrator
from backend.agent.planner import Plan
from backend.mcp_tools.log_tool import run as run_log
from backend.mcp_tools.log_search_tool import run as run_log_search
from backend.mcp_tools.network_port_lookup_tool import _find_port_matches
from backend.mcp_tools.process_detail_tool import _analyze_process_detail
from backend.mcp_tools.process_top_tool import _parse_process_rows


class GranularToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._old_allowed_log_dirs = os.environ.get("AGENT_ALLOWED_LOG_DIRS")
        os.environ["AGENT_AUDIT_LOG_PATH"] = os.path.join(self._tmp.name, "audit.log")
        os.environ["AGENT_ALLOWED_LOG_DIRS"] = self._tmp.name
        self.agent = AgentOrchestrator()

    def tearDown(self) -> None:
        if self._old_allowed_log_dirs is None:
            os.environ.pop("AGENT_ALLOWED_LOG_DIRS", None)
        else:
            os.environ["AGENT_ALLOWED_LOG_DIRS"] = self._old_allowed_log_dirs
        self._tmp.cleanup()

    def test_process_top_parser_orders_cpu_and_memory_inputs(self) -> None:
        rows = [
            "PID PPID COMMAND %CPU %MEM",
            "1001 1 python 88.5 4.2",
            "1002 1 nginx 1.0 0.5",
            "1003 1 java 30.0 22.7",
        ]
        parsed = _parse_process_rows(rows)

        self.assertEqual(parsed[0]["pid"], 1001)
        self.assertEqual(parsed[0]["command"], "python")
        self.assertEqual(parsed[0]["cpu_percent"], 88.5)
        self.assertEqual(parsed[2]["memory_percent"], 22.7)

    def test_process_detail_parses_linux_ps_output(self) -> None:
        analysis = _analyze_process_detail(
            1234,
            0,
            ["1234 1 appuser S python python app.py --port 8000"],
            [],
        )

        self.assertTrue(analysis["exists"])
        self.assertEqual(analysis["platform"], "linux")
        self.assertEqual(analysis["process"]["pid"], 1234)
        self.assertEqual(analysis["process"]["user"], "appuser")
        self.assertEqual(analysis["process"]["command"], "python")
        self.assertEqual(analysis["process"]["args"], "python app.py --port 8000")

    def test_process_detail_parses_windows_tasklist_output(self) -> None:
        analysis = _analyze_process_detail(
            4321,
            0,
            [
                "Image Name:   python.exe",
                "PID:          4321",
                "Session Name: Console",
                "Session#:     1",
                "Mem Usage:    20,000 K",
            ],
            [],
        )

        self.assertTrue(analysis["exists"])
        self.assertEqual(analysis["platform"], "windows")
        self.assertEqual(analysis["process"]["pid"], 4321)
        self.assertEqual(analysis["process"]["image_name"], "python.exe")

    def test_network_port_lookup_parses_windows_netstat(self) -> None:
        rows = [
            "  TCP    0.0.0.0:8080       0.0.0.0:0       LISTENING       4321",
            "  TCP    127.0.0.1:9000     127.0.0.1:50000 ESTABLISHED     9999",
        ]
        matches = _find_port_matches(rows, 8080, "tcp")

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["pid"], 4321)
        self.assertEqual(matches[0]["local_port"], 8080)
        self.assertEqual(matches[0]["state"], "LISTENING")

    def test_network_port_lookup_parses_linux_ss(self) -> None:
        rows = [
            'tcp LISTEN 0 4096 0.0.0.0:8080 0.0.0.0:* users:(("python",pid=2345,fd=3))',
            'udp UNCONN 0 0 0.0.0.0:68 0.0.0.0:* users:(("dhclient",pid=721,fd=6))',
        ]
        matches = _find_port_matches(rows, 8080, "tcp")

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["pid"], 2345)
        self.assertEqual(matches[0]["process_name"], "python")
        self.assertEqual(matches[0]["state"], "LISTEN")

    def test_log_search_finds_keyword_in_file(self) -> None:
        log_file = os.path.join(self._tmp.name, "app.log")
        with open(log_file, "w", encoding="utf-8") as handle:
            handle.write("service started\n")
            handle.write("ERROR failed to connect database\n")
            handle.write("permission denied for appuser\n")

        result = run_log_search(
            {
                "source": "file",
                "log_path": log_file,
                "keyword": "failed",
                "lines": 20,
                "limit": 5,
            }
        )

        self.assertEqual(result["matched_count"], 1)
        self.assertEqual(result["matches"][0]["line_number"], 2)
        self.assertIn("failed to connect", result["matches"][0]["line"])
        self.assertTrue(result["analysis"]["matched"])

    def test_log_file_tools_reject_paths_outside_allowed_dirs(self) -> None:
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        log_file = os.path.join(outside.name, "secret.log")
        with open(log_file, "w", encoding="utf-8") as handle:
            handle.write("secret token should not be readable\n")

        log_result = run_log({"source": "file", "log_path": log_file})
        search_result = run_log_search(
            {
                "source": "file",
                "log_path": log_file,
                "keyword": "secret",
            }
        )

        self.assertIn("outside allowed log directories", log_result["error"])
        self.assertIn("outside allowed log directories", search_result["error"])

    def test_granular_tools_are_registered_as_low_risk(self) -> None:
        tools = self.agent.executor.available_tools()
        self.assertIn("process.top", tools)
        self.assertIn("process.detail", tools)
        self.assertIn("network.port_lookup", tools)
        self.assertIn("log.search", tools)

        for tool_name in ["process.top", "process.detail", "network.port_lookup", "log.search"]:
            metadata = self.agent.executor.tool_metadata(tool_name)
            self.assertIsNotNone(metadata)
            self.assertEqual(metadata["risk_level"], "low")
            self.assertTrue(metadata["read_only"])

    def test_planner_selects_process_top_for_high_cpu_query(self) -> None:
        plan = self.agent.planner.plan("找出高 CPU 占用的进程", {"user_role": "operator"})

        self.assertEqual(plan.tools, ["process.top"])
        self.assertEqual(plan.arguments["metric"], "cpu")

    def test_planner_selects_process_detail_for_pid_query(self) -> None:
        plan = self.agent.planner.plan("查看 PID 1234 的进程详情", {"user_role": "viewer"})

        self.assertEqual(plan.tools, ["process.detail"])
        self.assertEqual(plan.arguments["pid"], 1234)

    def test_planner_selects_network_port_lookup_for_port_pid_query(self) -> None:
        plan = self.agent.planner.plan("查询 8080 端口对应的进程 PID", {"user_role": "operator"})

        self.assertEqual(plan.tools, ["network.port_lookup"])
        self.assertEqual(plan.arguments["port"], 8080)

    def test_planner_selects_log_search_for_keyword_query(self) -> None:
        plan = self.agent.planner.plan("搜索日志中的 error", {"user_role": "viewer"})

        self.assertEqual(plan.tools, ["log.search"])
        self.assertEqual(plan.arguments["keyword"], "error")

    def test_low_risk_granular_tools_pass_security(self) -> None:
        process_result = self.agent.evaluate_security(
            "找出高 CPU 占用的进程",
            "viewer1",
            {"user_role": "viewer", "metric": "cpu", "limit": 5},
            approved=False,
        )
        self.assertFalse(process_result["security"]["blocked"])

        detail_plan = Plan(
            intent="inspection",
            tools=["process.detail"],
            arguments={"user_role": "viewer", "pid": 1234},
        )
        detail_security = self.agent.executor.evaluate_security(
            detail_plan,
            "viewer1",
            "查看 PID 1234 的进程详情",
            approved=False,
        )
        self.assertFalse(detail_security["blocked"])

        port_result = self.agent.evaluate_security(
            "查询 8080 端口对应的进程 PID",
            "viewer1",
            {"user_role": "viewer", "port": 8080},
            approved=False,
        )
        self.assertFalse(port_result["security"]["blocked"])

        log_search_plan = Plan(
            intent="inspection",
            tools=["log.search"],
            arguments={"user_role": "viewer", "keyword": "error", "lines": 100},
        )
        log_search_security = self.agent.executor.evaluate_security(
            log_search_plan,
            "viewer1",
            "搜索日志中的 error",
            approved=False,
        )
        self.assertFalse(log_search_security["blocked"])

    def test_port_lookup_requires_port(self) -> None:
        result = self.agent.evaluate_security(
            "查询端口对应的进程 PID",
            "viewer1",
            {"user_role": "viewer"},
            approved=False,
        )

        self.assertTrue(result["security"]["blocked"])
        self.assertIn("network.port_lookup: port is required", result["security"]["reasons"])

    def test_process_detail_requires_pid(self) -> None:
        plan = Plan(
            intent="inspection",
            tools=["process.detail"],
            arguments={"user_role": "viewer"},
        )
        security = self.agent.executor.evaluate_security(
            plan,
            "viewer1",
            "查看进程详情",
            approved=False,
        )

        self.assertTrue(security["blocked"])
        self.assertIn("process.detail: pid is required", security["reasons"])

    def test_log_search_requires_keyword(self) -> None:
        result = self.agent.evaluate_security(
            "搜索日志",
            "viewer1",
            {"user_role": "viewer"},
            approved=False,
        )

        self.assertTrue(result["security"]["blocked"])
        self.assertIn("log.search: keyword is required", result["security"]["reasons"])


if __name__ == "__main__":
    unittest.main()
