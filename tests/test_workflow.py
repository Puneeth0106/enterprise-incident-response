"""Exercise the compiled graph and SQLite saver without provider credentials."""
import sqlite3
import tempfile
import unittest
from collections import defaultdict, deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from langchain_core.documents import Document
from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import ValidationError

from src import self_rag as rag
from src.config import Settings


class ScriptedModel:
    def __init__(self):
        self.responses = defaultdict(deque)
        self.calls = []
        self.defaults = {
            rag.RetrieveDecision: {"should_retrieve": True},
            rag.RelevanceDecision: {"is_relevant": True},
            rag.SupportDecision: {"status": "fully_supported", "evidence": ["runbook"]},
            rag.UsefulnessDecision: {"status": "useful", "reason": "Addresses incident"},
            rag.QueryRewrite: {"query": "checkout API deployment rollback"},
            None: "Check the deployment runbook.",
        }

    def with_structured_output(self, schema):
        return SimpleNamespace(invoke=lambda messages: self.reply(schema, messages))

    def invoke(self, messages):
        return self.reply(None, messages)

    def reply(self, schema, messages):
        self.calls.append((schema, messages))
        value = self.responses[schema].popleft() if self.responses[schema] else self.defaults[schema]
        if isinstance(value, Exception):
            raise value
        return schema(**value) if schema else SimpleNamespace(content=value)

    def count(self, schema):
        return sum(kind is schema for kind, _ in self.calls)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(self.temp) / "checkpoints.sqlite"
        self.settings = Settings(tavily_api_key="test-only", max_support_retries=2,
                                 max_retrieval_rewrites=2, max_web_rewrites=2)
        self.enterContext(patch.object(rag, "get_settings", return_value=self.settings))
        self.model = ScriptedModel()
        self.enterContext(patch.object(rag, "_llm", return_value=self.model))
        self.retriever = Mock()
        self.retriever.invoke.side_effect = lambda query: [Document(
            page_content="Check the deployment runbook.",
            metadata={"source": "checkout.md", "title": "Checkout", "page": 0},
        )]
        self.enterContext(patch.object(rag, "get_retriever", return_value=self.retriever))
        self.web = Mock()
        self.web.search.return_value = {"results": [{
            "title": "External guidance", "url": "https://example.com/ops",
            "content": "Check the deployment runbook.",
        }]}
        self.web_client = self.enterContext(patch.object(rag, "TavilyClient", return_value=self.web))
        self.enterContext(patch.object(rag, "_graph", self.open_graph()))

    def open_graph(self):
        connection = sqlite3.connect(self.path, check_same_thread=False)
        self.addCleanup(connection.close)
        return rag.build_graph(SqliteSaver(connection))

    def run_question(self, question="Why is checkout failing?", thread="incident-1"):
        return rag.run_self_rag(question, thread)

    def test_internal_evidence_is_supported_and_cited(self):
        result = self.run_question()
        self.assertEqual(result["route"], "Private Runbooks")
        self.assertEqual(result["support_status"], "fully_supported")
        self.assertEqual(result["memory_turns"], 1)
        self.assertEqual(result["sources"], [{"type": "internal", "title": "Checkout",
                         "source": "checkout.md", "url": None, "page": 1}])
        self.web_client.assert_not_called()

    def test_reopened_checkpoint_contextualizes_followup_retrieval(self):
        self.run_question()
        # A new saver/connection must load the persisted turn, not in-memory state.
        rag._graph = self.open_graph()
        result = self.run_question("What should I try next?")
        self.assertEqual(result["memory_turns"], 2)
        self.retriever.invoke.assert_called_with("checkout API deployment rollback")
        messages = next(messages for schema, messages in self.model.calls if schema is rag.QueryRewrite)
        self.assertIn("Why is checkout failing?", messages[-1].content)
        self.assertIn("Check the deployment runbook.", messages[-1].content)
        self.assertIn("What should I try next?", messages[-1].content)
        self.assertEqual(sum("checkpoint updated" in item for item in result["trace"]), 1)

    def test_distinct_threads_do_not_reuse_memory(self):
        self.run_question()
        result = self.run_question("Explain a load balancer", thread="incident-2")
        self.assertEqual(result["memory_turns"], 1)
        self.assertEqual(self.model.count(rag.QueryRewrite), 0)
        self.retriever.invoke.assert_called_with("Explain a load balancer")

    def test_direct_followup_resets_prior_sources_and_status(self):
        self.run_question()
        self.model.responses[rag.RetrieveDecision].append({"should_retrieve": False})
        result = self.run_question("Explain the general concept")
        self.assertEqual(result["route"], "General Knowledge")
        self.assertEqual(result["sources"], [])
        self.assertEqual(result["support_status"], "")
        self.assertFalse(result["used_web_search"])
        self.assertEqual(result["memory_turns"], 2)

    def test_unsupported_answer_stops_after_revision_budget(self):
        self.model.defaults[rag.SupportDecision] = {"status": "no_support"}
        result = self.run_question()
        self.assertEqual(result["route"], "No Reliable Evidence")
        self.assertNotEqual(result["answer"], "Check the deployment runbook.")
        self.assertEqual(result["sources"], [])
        self.assertEqual(self.model.count(rag.SupportDecision), 3)
        self.assertEqual(self.model.count(None), 3)
        self.assertEqual(self.model.count(rag.UsefulnessDecision), 0)
        self.assertEqual(result["memory_turns"], 1)

    def test_partial_support_with_zero_revisions_never_reaches_usefulness(self):
        self.settings.max_support_retries = 0
        self.model.defaults[rag.SupportDecision] = {"status": "partially_supported"}
        result = self.run_question()
        self.assertEqual(result["route"], "No Reliable Evidence")
        self.assertEqual(self.model.count(rag.SupportDecision), 1)
        self.assertEqual(self.model.count(rag.UsefulnessDecision), 0)

    def test_revision_can_recover_supported_answer(self):
        self.model.responses[rag.SupportDecision].append({"status": "partially_supported"})
        result = self.run_question()
        self.assertEqual(result["route"], "Private Runbooks")
        self.assertEqual(result["support_status"], "fully_supported")
        self.assertEqual(self.model.count(rag.SupportDecision), 2)
        self.assertEqual(self.model.count(None), 2)

    def test_empty_internal_evidence_falls_back_to_cited_web(self):
        self.retriever.invoke.side_effect = lambda query: []
        result = self.run_question()
        self.assertEqual(result["route"], "Internet Search")
        self.assertTrue(result["used_web_search"])
        self.assertEqual(self.retriever.invoke.call_count, 3)
        self.web.search.assert_called_once()
        self.assertEqual(result["sources"][0]["url"], "https://example.com/ops")

    def test_zero_web_budget_disables_search_after_irrelevant_internal_docs(self):
        self.settings.max_retrieval_rewrites = 0
        self.settings.max_web_rewrites = 0
        self.model.defaults[rag.RelevanceDecision] = {"is_relevant": False}
        result = self.run_question()
        self.assertEqual(result["route"], "No Reliable Evidence")
        self.assertFalse(result["used_web_search"])
        self.web_client.assert_not_called()
        self.assertEqual(self.retriever.invoke.call_count, 1)

    def test_useless_internal_answer_respects_disabled_web_budget(self):
        self.settings.max_retrieval_rewrites = 0
        self.settings.max_web_rewrites = 0
        self.model.defaults[rag.UsefulnessDecision] = {"status": "not_useful", "reason": "Unrelated"}
        result = self.run_question()
        self.assertEqual(result["route"], "No Reliable Evidence")
        self.assertEqual(result["sources"], [])
        self.assertEqual(result["usefulness"], "")
        self.web_client.assert_not_called()

    def test_empty_internal_and_web_evidence_exhausts_exact_search_budget(self):
        self.retriever.invoke.side_effect = lambda query: []
        self.web.search.return_value = {"results": []}
        result = self.run_question()
        self.assertEqual(result["route"], "No Reliable Evidence")
        self.assertEqual(self.retriever.invoke.call_count, 3)
        self.assertEqual(self.web.search.call_count, 2)
        self.assertEqual(self.model.count(None), 0)

    def test_relevance_grader_failure_does_not_admit_ungraded_evidence(self):
        self.settings.max_retrieval_rewrites = 0
        self.settings.max_web_rewrites = 0
        self.model.responses[rag.RelevanceDecision].append(RuntimeError("grading failed"))
        result = self.run_question()
        self.assertEqual(result["route"], "No Reliable Evidence")
        self.assertEqual(result["sources"], [])
        self.assertEqual(self.model.count(None), 0)

    def test_missing_search_key_exhausts_budget_without_client_calls(self):
        self.retriever.invoke.side_effect = lambda query: []
        self.settings.tavily_api_key = ""
        result = self.run_question()
        self.assertEqual(result["route"], "No Reliable Evidence")
        self.assertTrue(result["used_web_search"])
        self.assertEqual(self.model.count(rag.QueryRewrite), 4)
        self.web_client.assert_not_called()
        self.assertEqual(result["memory_turns"], 1)

    def test_maximum_budgets_finish_without_graph_recursion_error(self):
        self.settings.max_retrieval_rewrites = 5
        self.settings.max_web_rewrites = 5
        self.settings.max_support_retries = 5
        self.model.defaults[rag.UsefulnessDecision] = {"status": "not_useful", "reason": "Unhelpful"}
        # Every attempt consumes the full revision budget before usefulness rejects it.
        for _ in range(11):
            self.model.responses[rag.SupportDecision].extend([
                {"status": "partially_supported"} for _ in range(5)
            ] + [{"status": "fully_supported"}])
        result = self.run_question()
        self.assertEqual(result["route"], "No Reliable Evidence")
        self.assertEqual(self.retriever.invoke.call_count, 6)
        self.assertEqual(self.web.search.call_count, 5)
        self.assertEqual(self.model.count(rag.SupportDecision), 66)
        self.assertEqual(result["memory_turns"], 1)

    def test_provider_failure_does_not_commit_completed_turn(self):
        self.model.responses[None].append(RuntimeError("provider failure"))
        with self.assertRaisesRegex(RuntimeError, "provider failure"):
            self.run_question()
        state = rag._graph.get_state({"configurable": {"thread_id": "incident-1"}})
        self.assertEqual(state.values["memory"], [])
        rag._graph = self.open_graph()
        result = self.run_question("Try another question")
        self.assertEqual(result["memory_turns"], 1)
        self.assertNotIn("Why is checkout failing?", rag._graph.get_state(
            {"configurable": {"thread_id": "incident-1"}}).values["memory"][0])


class RetrySettingsTests(unittest.TestCase):
    def test_retry_limits_validate_explicit_and_environment_values(self):
        fields = {
            "max_support_retries": "MAX_SUPPORT_RETRIES",
            "max_retrieval_rewrites": "MAX_RETRIEVAL_REWRITES",
            "max_web_rewrites": "MAX_WEB_REWRITES",
        }
        for field, env in fields.items():
            for value in [-1, 6, "invalid"]:
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValidationError):
                        Settings(**{field: value})
                    with patch.dict("os.environ", {env: str(value)}):
                        with self.assertRaises((ValidationError, ValueError)):
                            Settings()
            for value in [0, 5]:
                with patch.dict("os.environ", {env: str(value)}):
                    self.assertEqual(getattr(Settings(), field), value)


if __name__ == "__main__":
    unittest.main()
