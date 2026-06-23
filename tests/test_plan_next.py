import unittest

from backend.agent.llm_client import LLMClient
from backend.agent.planner import Planner
from backend.config import LLMSettings


def _disabled_planner() -> Planner:
    # provider=disabled -> LLM 不可用，强制走规则链
    return Planner(LLMClient(LLMSettings(provider="disabled", api_key="", base_url="", model="")))


class PlanNextRuleChainTest(unittest.TestCase):
    def test_service_failure_escalates_to_log(self):
        planner = _disabled_planner()
        prior = {"service": {"analysis": {"failed_count": 1, "inactive_count": 0}}}
        plan = planner.plan_next("服务为什么起不来", {"service_name": "nginx"}, prior, {"service"})
        self.assertIsNotNone(plan)
        self.assertEqual(plan.tools, ["log"])
        self.assertEqual(plan.arguments.get("unit"), "nginx")
        self.assertEqual(plan.source, "rules")

    def test_no_failure_returns_none(self):
        planner = _disabled_planner()
        prior = {"service": {"analysis": {"failed_count": 0, "inactive_count": 0}}}
        self.assertIsNone(planner.plan_next("查看服务", {}, prior, {"service"}))

    def test_log_already_executed_returns_none(self):
        planner = _disabled_planner()
        prior = {"service": {"analysis": {"failed_count": 2}}}
        self.assertIsNone(planner.plan_next("排查", {}, prior, {"service", "log"}))


if __name__ == "__main__":
    unittest.main()
