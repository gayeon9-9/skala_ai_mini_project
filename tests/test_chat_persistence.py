"""화면/POST가 같은 접수를 저장하고 실패·재전송에서 원문을 지키는지 확인한다."""
import os
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

import tempfile
import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from fastapi.testclient import TestClient

import server
from agent.deps import SupervisorDeps
from agent.fake_llm import QualityLLM
from agent.graph import build_graph
from agent.session_store import SqliteSessionStore
from agent.chat_service import ChatService, inquiry_store
from test_selected_system import RecordingFastLLM, RecordingMCP, sse_events


class ChatPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = SqliteSessionStore(Path(self.temp.name) / "sessions.db")
        self.addCleanup(self.store.close)
        self.mcp = RecordingMCP()
        self.deps = SupervisorDeps(RecordingFastLLM(), QualityLLM("test", 0),
                                   self.mcp, self.store, "v2")
        server.app.state.deps = self.deps
        server.app.state.graph = build_graph(self.deps)
        server.app.state.llm_backend = "fake"
        server.app.state.instance_id = id(self.deps)
        self.client = TestClient(server.app)
        self.addCleanup(self.client.close)
        self.payload = {"message": "로그인이 안 돼요", "thread_id": "persist",
                        "selected_system": "auth-service", "request_id": "request-1"}

    def test_completed_post_replayed_by_sse_without_second_tool_call(self):
        first = self.client.post("/chat", json=self.payload)
        self.assertEqual(first.status_code, 200, first.text)
        count = len(self.mcp.operations)
        response = self.client.get("/chat/stream", params=self.payload)
        answer = next(e for e in sse_events(response) if e["type"] == "FinalAnswer")
        self.assertEqual(answer["reply"], first.json()["reply"])
        self.assertEqual(len(self.mcp.operations), count)
        restored = self.client.get("/threads/persist").json()
        self.assertEqual(restored["selected_system"], "auth-service")
        self.assertEqual(len(restored["messages"]), 2)
        self.assertEqual(restored["inquiry_id"], answer["inquiry_id"])

    def test_failure_preserves_input_and_same_request_retry_does_not_duplicate(self):
        server.app.state.graph = Mock(ainvoke=AsyncMock(side_effect=RuntimeError("test failure")))
        response = self.client.post("/chat", json=self.payload)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.store.load("persist"), [{"role": "user", "content": self.payload["message"]}])
        server.app.state.graph = build_graph(self.deps)
        self.assertEqual(self.client.post("/chat", json=self.payload).status_code, 200)
        self.assertEqual(len(self.store.load("persist")), 2)

    def test_target_change_and_request_id_reuse_are_rejected_before_tools(self):
        self.client.post("/chat", json=self.payload)
        count = len(self.mcp.operations)
        for changes in ({"selected_system": "web-01", "request_id": "new"},
                        {"message": "다른 질문"}):
            for method, url in ((self.client.post, "/chat"), (self.client.get, "/chat/stream")):
                args = {"json" if url == "/chat" else "params": {**self.payload, **changes}}
                response = method(url, **args)
                self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(len(self.mcp.operations), count)

    def test_sse_failure_has_error_without_final_answer(self):
        async def broken(*args, **kwargs):
            raise RuntimeError("test failure")
            yield
        server.app.state.graph = Mock(astream=broken)
        response = self.client.get("/chat/stream", params=self.payload)
        events = sse_events(response)
        self.assertTrue(any(e["type"] == "Error" for e in events))
        self.assertFalse(any(e["type"] == "FinalAnswer" for e in events))
        self.assertEqual(len(self.store.load("persist")), 1)

    def test_final_answer_only_after_database_completion(self):
        async def observe():
            service = ChatService(self.deps, server.app.state.graph, server.ChatRequest(**self.payload))
            found = False
            async for event in service.stream():
                if event["type"] == "FinalAnswer":
                    found = True
                    # 이벤트를 받은 순간 검사하여 yield 뒤의 저장으로는 통과하지 못하게 한다.
                    stored = inquiry_store(self.deps).get_thread("persist")
                    self.assertEqual(stored["requests"][-1]["status"], "completed")
                    self.assertEqual(len(stored["messages"]), 2)
            self.assertTrue(found)
        asyncio.run(observe())

    def test_unknown_thread_does_not_create_an_inquiry(self):
        self.assertEqual(self.client.get("/threads/unknown").status_code, 404)
        self.assertEqual(self.store.load("unknown"), [])

    def test_natural_intake_merges_followup_and_restores_fields(self):
        first = self.client.post("/chat", json={**self.payload,
            "message": "로그인 후 화면이 넘어가지 않아요. 오류 메시지는 없어요."})
        self.assertEqual(first.status_code, 200, first.text)
        intake = first.json()["metadata"]["intake"]
        self.assertEqual(intake["fields"]["error_message"]["status"], "none")
        self.assertEqual(intake["last_question_field"], "occurred_at")
        second = self.client.get("/chat/stream", params={**self.payload,
            "message": "오전 9시요", "request_id": "request-2"})
        answer = next(e for e in sse_events(second) if e["type"] == "FinalAnswer")
        fields = answer["intake"]["fields"]
        self.assertIn("로그인", fields["symptom"]["value"])
        self.assertEqual(fields["error_message"]["status"], "none")
        self.assertIn("오전 9시", fields["occurred_at"]["value"])
        restored = self.client.get("/threads/persist").json()
        self.assertEqual(restored["inquiry"]["details"]["intake"]["fields"], fields)
        self.assertEqual(len(restored["messages"]), 4)
