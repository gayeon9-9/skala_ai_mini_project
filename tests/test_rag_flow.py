"""검색 대역을 주입해 RAG와 답변의 연결을 검증한다. 의미 검색 정확도 검사는 별도다."""
import os
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

import asyncio
import unittest
from agent.deps import SupervisorDeps
from agent.fake_llm import QualityLLM
from agent.graph import build_graph
from test_selected_system import RecordingFastLLM, RecordingMCP


DOC = {"document_id": "faq:login", "chunk_id": "faq:login:0", "title": "로그인 확인",
       "text": "브라우저와 오류 발생 시각을 확인하세요.", "path": "data/faq/login.md",
       "score": 0.8, "audience": "user", "systems": ["auth-service"], "version": "1"}


class StubRetriever:
    def __init__(self, status="ok", fail=False):
        self.status, self.fail, self.calls = status, fail, []

    async def search(self, query, system, audience="user", k=3):
        self.calls.append((query, system, audience))
        if self.fail:
            raise RuntimeError("simulated retrieval outage")
        docs = ([dict(DOC)] if audience == "user" else [
            {**DOC, "document_id": "runbook:internal", "audience": "operator", "text": "비공개 운영 절차"}])
        return {"status": self.status, "documents": docs if self.status == "ok" else []}


class CapturingQuality(QualityLLM):
    def __init__(self):
        super().__init__("test", 0)
        self.inputs = []

    async def write(self, query, collected, attempt, feedback, on_token=None):
        self.inputs.append(collected)
        return await super().write(query, collected, attempt, feedback, on_token)


class RagFlowTests(unittest.TestCase):
    def run_flow(self, retriever, quality=None):
        quality = quality or CapturingQuality()
        deps = SupervisorDeps(RecordingFastLLM(), quality, RecordingMCP(), None, "v2", retriever=retriever)
        state = {"query": "로그인이 안 돼요", "selected_system": "auth-service",
                 "thread_id": "rag", "history": [], "events": [], "cost_usd": 0}
        return asyncio.run(build_graph(deps).ainvoke(state)), quality

    def test_documents_reach_generator_but_operator_text_does_not(self):
        retriever = StubRetriever()
        result, quality = self.run_flow(retriever)
        self.assertIn("[faq:login]", result["final"])
        self.assertEqual({a for _, _, a in retriever.calls}, {"user", "operator"})
        self.assertTrue(all(s == "auth-service" for _, s, _ in retriever.calls))
        self.assertTrue(result["operator_retrieval"]["documents"])
        self.assertNotIn("비공개 운영 절차", str(quality.inputs))

    def test_no_match_and_search_error_stay_distinct(self):
        for retriever, expected in ((StubRetriever("no_match"), "no_match"), (StubRetriever(fail=True), "error")):
            result, _ = self.run_flow(retriever)
            self.assertEqual(result["retrieval"]["status"], expected)
            self.assertEqual(result["retrieval"]["documents"], [])
            self.assertNotIn("runbook://", result["final"])

    def test_three_bad_drafts_return_fallback_instead_of_forced_pass(self):
        class BadQuality:
            async def write(self, *args, **kwargs):
                return "위험한 초안", {"usd": 0}
        result, _ = self.run_flow(StubRetriever(), BadQuality())
        self.assertEqual(result["validation_attempts"], 3)
        self.assertEqual(result["validation"]["status"], "fallback")
        self.assertNotIn("위험한 초안", result["final"])

    def test_unvalidated_tokens_never_reach_stream(self):
        async def observe():
            deps = SupervisorDeps(RecordingFastLLM(), CapturingQuality(), RecordingMCP(), None, "v2",
                                  retriever=StubRetriever())
            events = [item async for item in build_graph(deps).astream(
                {"query": "로그인이 안 돼요", "selected_system": "auth-service", "events": [], "history": []},
                stream_mode=["updates", "custom"])]
            self.assertFalse(any(mode == "custom" and payload.get("type") == "AnswerToken"
                                 for mode, payload in events))
        asyncio.run(observe())

    def test_long_formatted_but_ungrounded_draft_is_rejected(self):
        class MisleadingQuality:
            async def write(self, *args, **kwargs):
                return ("1) auth-service 샘플 CPU 999%, 메모리 888%입니다. "
                        "2) 복구했습니다. [faq:invented] "
                        "3) web-01도 장애 원인으로 확정됐습니다.", {"usd": 0})
        result, _ = self.run_flow(StubRetriever(), MisleadingQuality())
        self.assertEqual(result["validation"]["status"], "fallback")
        self.assertEqual(result["validation_attempts"], 3)
        self.assertTrue(result["validation"]["errors"])
        self.assertNotIn("999%", result["final"])
        self.assertNotIn("faq:invented", result["final"])
        self.assertNotIn("복구했습니다", result["final"])
