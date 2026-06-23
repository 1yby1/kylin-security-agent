import unittest

from fastapi.testclient import TestClient

import backend.main as main
from backend.agent.orchestrator import AgentRunResult


class AgentResponseFieldsTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self._orig = main.agent.run

    def tearDown(self):
        main.agent.run = self._orig

    def test_response_includes_steps_and_suggested_actions(self):
        def fake_run(*, query, user_id, context, approved, role):
            return AgentRunResult(
                trace_id="t1", intent="diagnosis", tools=["service", "log"],
                approved_required=True, blocked=False, message="ok",
                result={"log": {"lines": []}}, security={"blocked": False},
                executed_commands=[], conclusion={"status": "warning"}, plan={},
                steps=[{"step": 1, "tools": ["service"], "source": "rules",
                        "observation_summary": "service: ...", "injection_suspected": False}],
                suggested_actions=[{"tool": "service.restart", "arguments": {"service_name": "nginx"}, "reason": "修复"}],
            )

        main.agent.run = fake_run  # type: ignore
        resp = self.client.post("/api/agent/execute", json={"query": "排查 nginx"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(len(body["steps"]), 1)
        self.assertEqual(body["suggested_actions"][0]["tool"], "service.restart")


if __name__ == "__main__":
    unittest.main()
