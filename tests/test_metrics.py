import unittest

from backend.observability.metrics import MetricsCollector, get_metrics


class MetricsCollectorTest(unittest.TestCase):
    def test_counts(self):
        m = MetricsCollector()
        m.record_request("/api/agent/execute")
        m.record_request("/api/agent/execute")
        m.record_request("/api/agent/plan")
        m.record_blocked()
        m.record_rate_limited()
        m.record_concurrency_rejected()
        snap = m.snapshot()
        self.assertEqual(snap["requests"], {"/api/agent/execute": 2, "/api/agent/plan": 1})
        self.assertEqual(snap["blocked"], 1)
        self.assertEqual(snap["rate_limited"], 1)
        self.assertEqual(snap["concurrency_rejected"], 1)

    def test_tool_percentiles_nearest_rank(self):
        m = MetricsCollector()
        for duration in [10, 20, 30, 40, 100]:
            m.record_tool("system", duration)
        tool = m.snapshot()["tools"]["system"]
        self.assertEqual(tool["count"], 5)
        self.assertEqual(tool["p50_ms"], 30)
        self.assertEqual(tool["p95_ms"], 100)

    def test_llm_success_rate(self):
        m = MetricsCollector()
        m.record_llm(True)
        m.record_llm(True)
        m.record_llm(False)
        llm = m.snapshot()["llm"]
        self.assertEqual(llm["success"], 2)
        self.assertEqual(llm["failure"], 1)
        self.assertEqual(llm["success_rate"], 0.667)

    def test_empty_llm_rate_is_none(self):
        self.assertIsNone(MetricsCollector().snapshot()["llm"]["success_rate"])

    def test_reset(self):
        m = MetricsCollector()
        m.record_request("/x")
        m.reset()
        self.assertEqual(m.snapshot()["requests"], {})

    def test_get_metrics_is_singleton(self):
        self.assertIs(get_metrics(), get_metrics())


if __name__ == "__main__":
    unittest.main()
