from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

import backend.main as main
from backend.agent.orchestrator import AgentOrchestrator
from backend.agent.planner import Plan
from backend.agent.session_context import ConversationSessionStore
from backend.security.auth import session_principal


def _plan() -> Plan:
    return Plan(intent="inspection", tools=["service"], arguments={"service_name": "nginx"}, source="rules")


def _seed(store: ConversationSessionStore, session_id: str, owner: str) -> None:
    store.update(session_id, owner=owner, query="看服务", plan=_plan(), result={}, conclusion={"conclusion": "ok"})


class SessionPrincipalTest(unittest.TestCase):
    def test_no_token_is_anon(self):
        self.assertEqual(session_principal(None), "anon")
        self.assertEqual(session_principal(""), "anon")

    def test_token_is_stable_hashed_and_distinct(self):
        principal = session_principal("secret-token")
        self.assertTrue(principal.startswith("tok:"))
        self.assertEqual(principal, session_principal("secret-token"))  # stable
        self.assertNotIn("secret-token", principal)  # no cleartext
        self.assertNotEqual(principal, session_principal("other-token"))


class SessionOwnerBindingTest(unittest.TestCase):
    def test_context_requires_matching_owner(self):
        store = ConversationSessionStore()
        _seed(store, "s1", "tok:abc")
        self.assertEqual(store.context("s1", "tok:abc")["last_entities"].get("service_name"), "nginx")
        self.assertEqual(store.context("s1", "anon"), {})
        self.assertEqual(store.context("s1", "tok:other"), {})

    def test_resolve_rejects_foreign_or_unissued_id(self):
        store = ConversationSessionStore()
        _seed(store, "s1", "tok:abc")
        # Foreign principal presenting a real id -> gets a fresh id, not s1.
        self.assertNotEqual(store.resolve_session_id("s1", "anon"), "s1")
        # Owner presenting their own id -> keeps it.
        self.assertEqual(store.resolve_session_id("s1", "tok:abc"), "s1")
        # A caller-chosen, never-issued id -> replaced by a server-issued id.
        self.assertNotEqual(store.resolve_session_id("attacker-picked", "anon"), "attacker-picked")


class SessionCapacityTest(unittest.TestCase):
    def test_evicts_least_recently_updated_over_capacity(self):
        clock = [100.0]
        store = ConversationSessionStore(max_sessions=3, clock=lambda: clock[0])
        for index in range(5):
            clock[0] = 100.0 + index
            store.update(f"s{index}", owner="anon", query="q", plan=_plan(), result={}, conclusion={})
        self.assertEqual(len(store._sessions), 3)
        self.assertNotIn("s0", store._sessions)
        self.assertNotIn("s1", store._sessions)
        self.assertIn("s4", store._sessions)


class OrchestratorContextAccessorTest(unittest.TestCase):
    def test_conversation_context_is_owner_scoped(self):
        orchestrator = AgentOrchestrator()
        _seed(orchestrator._session_store, "s1", "anon")
        self.assertIn("last_entities", orchestrator.conversation_context("s1", "anon"))
        self.assertEqual(orchestrator.conversation_context("s1", "tok:x"), {})


class PlanEndpointSessionTest(unittest.TestCase):
    """P3: /api/agent/plan injects the same read-only follow-up context as execute."""

    def setUp(self):
        self.client = TestClient(main.app)

    def test_plan_uses_session_context(self):
        # Seed a session owned by the anonymous principal (no token).
        main.agent._session_store.update(
            "plan-sess", owner="anon", query="看 nginx 服务",
            plan=_plan(), result={}, conclusion={"conclusion": "ok"},
        )
        resp = self.client.post(
            "/api/agent/plan", json={"query": "继续看它的日志", "session_id": "plan-sess"}
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("log", body["tools"])
        self.assertEqual(body["arguments"].get("unit"), "nginx")


if __name__ == "__main__":
    unittest.main()
