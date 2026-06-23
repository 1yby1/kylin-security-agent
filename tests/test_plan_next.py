import unittest

from backend.agent.llm_client import LLMClient, LLMDecision
from backend.agent.planner import Planner
from backend.config import LLMSettings


def _disabled_planner() -> Planner:
    # provider=disabled -> LLM 不可用，强制走规则链
    return Planner(LLMClient(LLMSettings(provider="disabled", api_key="", base_url="", model="")))


class _StubLLMClient:
    """最小化的 LLM 客户端替身，避免在测试中触发真实网络请求。"""

    def __init__(self, decision: LLMDecision | None):
        self.enabled = True
        self._decision = decision

    def analyze(self, query, context, manifest):
        return self._decision


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


class PlanNextLLMChainTest(unittest.TestCase):
    def test_llm_decision_with_new_tool_is_used(self):
        decision = LLMDecision(
            intent="diagnosis",
            tools=["log"],
            arguments={"unit": "nginx"},
            summary="LLM 建议拉取日志",
            risk_hint="low",
            need_confirmation=False,
            reasoning=["service 异常，建议查看日志"],
        )
        planner = Planner(_StubLLMClient(decision))
        prior = {"service": {"analysis": {"failed_count": 1}}}
        plan = planner.plan_next("服务为什么起不来", {}, prior, {"service"})
        self.assertIsNotNone(plan)
        self.assertEqual(plan.tools, ["log"])
        self.assertEqual(plan.source, "llm")

    def test_llm_decision_steps_are_preserved(self):
        decision = LLMDecision(
            intent="diagnosis",
            tools=["log", "disk"],
            arguments={},
            summary="LLM 建议继续编排诊断",
            risk_hint="low",
            need_confirmation=False,
            reasoning=[],
            steps=[
                {"id": "s1", "tool": "log", "arguments": {"unit": "nginx"}},
                {"id": "s2", "tool": "disk", "arguments": {"path": "/var/log"}},
            ],
        )
        planner = Planner(_StubLLMClient(decision))
        prior = {"service": {"analysis": {"failed_count": 1}}}
        plan = planner.plan_next("服务为什么起不来", {"service_name": "nginx"}, prior, {"service"})
        self.assertIsNotNone(plan)
        self.assertEqual([step.tool for step in plan.steps], ["log", "disk"])
        self.assertEqual(plan.steps[0].arguments["unit"], "nginx")
        self.assertEqual(plan.steps[1].arguments["path"], "/var/log")

    def test_llm_decision_with_only_executed_tools_returns_none(self):
        decision = LLMDecision(
            intent="diagnosis",
            tools=["service"],
            arguments={},
            summary="LLM 重复建议已执行的工具",
            risk_hint="low",
            need_confirmation=False,
            reasoning=[],
        )
        planner = Planner(_StubLLMClient(decision))
        prior = {"service": {"analysis": {"failed_count": 1}}}
        plan = planner.plan_next("服务为什么起不来", {}, prior, {"service"})
        self.assertIsNone(plan)

    def test_llm_returns_none_falls_back_to_rules(self):
        planner = Planner(_StubLLMClient(None))
        prior = {"service": {"analysis": {"failed_count": 1, "inactive_count": 0}}}
        plan = planner.plan_next("服务为什么起不来", {"service_name": "nginx"}, prior, {"service"})
        self.assertIsNotNone(plan)
        self.assertEqual(plan.tools, ["log"])
        self.assertEqual(plan.source, "rules")


if __name__ == "__main__":
    unittest.main()
