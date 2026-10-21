"""관리자 읽기 API를 임시 DB로 검증한다. 실제 대화·네트워크·외부 LLM은 사용하지 않는다."""

import os
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

import server
from agent.inquiry_store import SqliteInquiryStore
from agent.session_store import SqliteSessionStore


class AdminApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.sessions = SqliteSessionStore(Path(self.temp.name) / "admin.db")
        self.addCleanup(self.sessions.close)
        self.store = SqliteInquiryStore(self.sessions)
        server.app.state.deps = SimpleNamespace(inquiry_store=self.store, session_store=self.sessions)
        self.client = TestClient(server.app)
        self.addCleanup(self.client.close)

    def begin(self, suffix="1", thread=None):
        return self.store.begin(thread or "thread-" + suffix, "auth-service", "로그인 화면이 멈춰요", "request-" + suffix)

    def saved_result(self):
        return {
            "final": "auth-service 샘플 점검 결과를 안내했어요. [faq:login]",
            "intake": {"fields": {"symptom": {"status": "provided", "value": "로그인 화면이 멈춰요",
                                                "evidence": "로그인 화면이 멈춰요"}},
                       "missing_fields": ["environment"], "questions": ["사용 환경을 알려주세요"],
                       "source": "rules", "usage": {"tokens": 20, "usd": 0}},
            "collected": [
                {"source": "monitoring", "data": {"system_name": "auth-service", "status": "degraded",
                 "cpu_percent": 42.5, "memory_percent": 31, "alerts": ["지연"]}},
                {"source": "monitoring", "data": {"system_name": "other-service", "status": "down"}},
            ],
            "retrieval": {"status": "ok", "documents": [{"document_id": "faq:login", "audience": "user",
                          "title": "로그인 안내", "text": "사용 환경을 확인해주세요"}]},
            "operator_retrieval": {"status": "ok", "documents": [{"document_id": "runbook:latency",
                                   "audience": "operator", "title": "지연 점검", "text": "운영자 확인 절차"}]},
            "validation": {"status": "passed", "errors": [], "citations": ["faq:login"]},
            "telemetry": {"code_revision": "abc123", "processing_seconds": 0.5,
                          "processing_scope": "graph_until_persistence", "tracing_enabled": False},
        }

    def test_detail_uses_saved_fields_and_selected_observations(self):
        started = self.begin()
        self.store.complete("request-1", self.saved_result(), run_id="synthetic-run")
        response = self.client.get("/admin/inquiries/" + started["inquiry_id"])
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["intake"]["fields"]["symptom"]["value"], "로그인 화면이 멈춰요")
        self.assertEqual([item["system_name"] for item in body["observations"]], ["auth-service"])
        self.assertEqual(body["retrieval"]["documents"][0]["document_id"], "faq:login")
        self.assertEqual(body["operator_retrieval"]["documents"][0]["document_id"], "runbook:latency")
        self.assertTrue(body["evidence_is_current"])
        self.assertEqual(body["evidence_request_id"], "request-1")
        self.assertEqual(body["evidence_created_at"], body["requests"][0]["updated_at"])
        self.assertEqual(body["langsmith_run_id"], "synthetic-run")
        self.assertEqual(body["telemetry"]["processing_scope"], "graph_until_persistence")
        self.assertEqual(body["status"], "completed")  # 답변 저장 상태이며 장애 해결 여부가 아니다.
        self.assertEqual(len(body["messages"]), 2)

    def test_new_failure_marks_previous_completed_evidence_as_stale(self):
        started = self.begin()
        self.store.complete("request-1", self.saved_result())
        self.begin("2", thread="thread-1")
        self.store.fail("request-2", "外部処理失敗")
        body = self.client.get("/admin/inquiries/" + started["inquiry_id"]).json()
        self.assertEqual(body["status"], "failed")
        self.assertFalse(body["evidence_is_current"])
        self.assertEqual(body["evidence_request_id"], "request-1")
        self.assertEqual(body["answer"], self.saved_result()["final"])
        self.assertEqual([r["request_id"] for r in body["requests"]], ["request-1", "request-2"])
        self.assertEqual(body["requests"][-1]["error"], "外部処理失敗")

    def test_pending_request_has_no_completed_evidence(self):
        started = self.begin()
        body = self.client.get("/admin/inquiries/" + started["inquiry_id"]).json()
        self.assertFalse(body["evidence_is_current"])
        self.assertIsNone(body["evidence_request_id"])
        self.assertIsNone(body["answer"])
        self.assertEqual(body["observations"], [])
        self.assertEqual(body["requests"][0]["status"], "processing")

    def test_prompt_and_raw_graph_results_are_not_exposed(self):
        started = self.begin()
        result = self.saved_result()
        result["last_prompt"] = "PRIVATE_PROMPT_MARKER"
        result["draft"] = "PRIVATE_PROMPT_MARKER"
        result["history"] = [{"content": "PRIVATE_PROMPT_MARKER"}]
        result["intake"]["usage"]["prompt"] = "PRIVATE_PROMPT_MARKER"
        result["retrieval"]["documents"][0]["internal_prompt"] = "PRIVATE_PROMPT_MARKER"
        result["telemetry"]["api_key"] = "PRIVATE_PROMPT_MARKER"
        self.store.complete("request-1", result)
        response = self.client.get("/admin/inquiries/" + started["inquiry_id"])
        self.assertNotIn("PRIVATE_PROMPT_MARKER", response.text)
        self.assertNotIn("result", response.json()["requests"][0])
        self.assertNotIn("last_prompt", response.json())

    def test_pagination_has_more_and_status_filter(self):
        for number in range(3):
            self.begin(str(number))
            if number < 2:
                self.store.complete("request-" + str(number), self.saved_result())
        page1 = self.client.get("/admin/inquiries", params={"limit": 2}).json()
        page2 = self.client.get("/admin/inquiries", params={"limit": 2, "offset": 2}).json()
        self.assertEqual(len(page1["items"]), 2)
        self.assertTrue(page1["has_more"])
        self.assertEqual(len(page2["items"]), 1)
        self.assertFalse(page2["has_more"])
        ids = {item["inquiry_id"] for item in page1["items"] + page2["items"]}
        self.assertEqual(len(ids), 3)
        completed = self.client.get("/admin/inquiries", params={"status": "completed"}).json()
        self.assertEqual(len(completed["items"]), 2)
        self.assertTrue(all(item["request_count"] == 1 for item in completed["items"]))
        self.assertTrue(all(item["symptom"] == "로그인 화면이 멈춰요" for item in completed["items"]))

    def test_query_bounds_and_status_are_validated(self):
        for params in ({"limit": 0}, {"limit": 101}, {"offset": -1}, {"status": "resolved"}):
            with self.subTest(params=params):
                self.assertEqual(self.client.get("/admin/inquiries", params=params).status_code, 422)
        self.assertEqual(self.client.get("/admin/inquiries", params={"limit": 100}).status_code, 200)

    def test_unknown_inquiry_returns_404_without_creating_rows(self):
        self.assertEqual(self.client.get("/admin/inquiries/missing").status_code, 404)
        self.assertEqual(self.client.get("/admin/inquiries").json()["items"], [])

    def test_list_unstructured_input_is_labeled_as_original(self):
        self.begin()
        item = self.client.get("/admin/inquiries").json()["items"][0]
        self.assertEqual(item["symptom"], "원문: 로그인 화면이 멈춰요")
        self.assertNotIn("requests", item)
        self.assertNotIn("messages", item)


if __name__ == "__main__":
    unittest.main()
