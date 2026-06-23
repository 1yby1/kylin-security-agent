import unittest

from backend.agent.executor import ToolExecutor
from backend.agent.llm_client import LLMClient
from backend.agent.planner import Plan
from backend.config import LLMSettings
from backend.observability.metrics import get_metrics


class ExecutorInstrumentationTest(unittest.TestCase):
    def test_records_tool_latency(self):
        executor = ToolExecutor()
        executor._registry.call = lambda tool, arguments: {"source": tool, "analysis": {}}  # type: ignore[assignment]
        get_metrics().reset()
        executor.execute(plan=Plan(intent="inspection", tools=["system"], arguments={}), user_id="u", raw_query="q", role="viewer")
        tools = get_metrics().snapshot()["tools"]
        self.assertIn("system", tools)
        self.assertEqual(tools["system"]["count"], 1)

    def test_records_blocked_step(self):
        # disk.large_files with path="/" is medium for viewer -> guard blocks it.
        executor = ToolExecutor()
        get_metrics().reset()
        execution = executor.execute(
            plan=Plan(intent="inspection", tools=["disk.large_files"], arguments={"path": "/"}),
            user_id="u", raw_query="scan root", role="viewer",
        )
        self.assertTrue(execution.blocked)
        self.assertGreaterEqual(get_metrics().snapshot()["blocked"], 1)


class LLMInstrumentationTest(unittest.TestCase):
    def _client(self) -> LLMClient:
        return LLMClient(LLMSettings(provider="deepseek", api_key="x", base_url="http://x", model="m"))

    def test_records_llm_success(self):
        client = self._client()
        client._post_chat = lambda payload: {"choices": [{"message": {"content": "{}"}}]}  # type: ignore[assignment]
        get_metrics().reset()
        client._chat_json(system_prompt="s", user_payload={})
        self.assertEqual(get_metrics().snapshot()["llm"]["success"], 1)

    def test_records_llm_failure(self):
        client = self._client()
        client._post_chat = lambda payload: None  # type: ignore[assignment]
        get_metrics().reset()
        client._chat_json(system_prompt="s", user_payload={})
        self.assertEqual(get_metrics().snapshot()["llm"]["failure"], 1)


if __name__ == "__main__":
    unittest.main()
