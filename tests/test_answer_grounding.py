"""검색 문서의 HTTP 오류 예시를 실제 접수 사실로 옮기지 않는지 검사한다.

외부 모델·검색 API를 호출하지 않는다. 보관한 실패 답변, 고정 문서와 모델 대역으로
접수→작성 문맥→검증→재작성/대체 안내를 확인하며 실제 진단 정확도를 평가하지 않는다.
"""
import os

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

import asyncio
import copy
import json
import unittest
from pathlib import Path

from agent.answer_generation import (
    build_answer_prompt,
    clean_internal_annotations,
    deterministic_rag_answer,
    evidence_fallback,
)
from agent.answer_grounding import (
    applicable_documents,
    http_error_codes,
    reported_http_error_codes,
    unsupported_http_error_codes,
)
from agent.answer_validation import validate_answer
from agent.deps import SupervisorDeps
from agent.fake_llm import QualityLLM
from agent.graph import build_graph, validate_node, write_node
from agent.intake import empty_fields
from test_rag_flow import StubRetriever
from test_selected_system import RecordingFastLLM, RecordingMCP


ROOT = Path(__file__).resolve().parents[1]
DOCUMENT = {
    "document_id": "faq:connectivity", "chunk_id": "faq:connectivity:0",
    "title": "페이지 접속 실패와 504 시간 초과 확인",
    "text": (ROOT / "data/faq/connectivity.md").read_text(encoding="utf-8"),
    "path": "data/faq/connectivity.md", "audience": "user", "systems": ["*"],
    "score": 0.8, "version": "1",
}
LOGIN_DOCUMENT = {
    "document_id": "faq:login", "chunk_id": "faq:login:0",
    "title": "로그인 후 화면이 넘어가지 않을 때",
    "text": (ROOT / "data/faq/login.md").read_text(encoding="utf-8"),
    "path": "data/faq/login.md", "audience": "user", "systems": ["auth-service"],
    "score": 0.9, "version": "1",
}


def intake_for(status="none", value=None, symptom="로그인 화면이 멈춰요"):
    fields = empty_fields()
    fields["symptom"] = {"status": "provided", "value": symptom, "evidence": symptom}
    evidence = {"none": "오류 메시지는 없어요", "unknown": "오류 메시지는 모르겠어요",
                "unmentioned": None, "provided": value}[status]
    fields["error_message"] = {"status": status, "value": value, "evidence": evidence}
    return {"fields": fields}


def collected_evidence():
    return [
        {"source": "monitoring", "data": {
            "system_name": "auth-service", "status": "degraded",
            "cpu_percent": 42.5, "memory_percent": 31.0, "alerts": ["latency"],
        }},
        {"source": "knowledge", "data": copy.deepcopy(LOGIN_DOCUMENT)},
        {"source": "knowledge", "data": copy.deepcopy(DOCUMENT)},
        {"source": "retrieval_status", "data": {"status": "ok"}},
    ]


def answer_with(advice, citation="faq:login"):
    return (
        "1) 샘플 점검: auth-service 상태=degraded, CPU 42.5%, 메모리 31.0%, 알림 1건.\n"
        f"2) 문서 안내: {advice} [{citation}]\n"
        "3) 추가 확인: 실제 원인은 확정할 수 없으며 담당자 확인이 필요합니다."
    )


class HttpCodeGroundingTests(unittest.TestCase):
    def test_http_codes_with_korean_particles_and_sentence_punctuation_are_recognized(self):
        # 문장 끝의 점을 버전 번호와 혼동하면 실제 잘못된 답변을 놓칠 수 있다.
        for text in ("HTTP 504.", "504.", "504가 표시됩니다.", "HTTP/1.1 504",
                     "오류 **504**입니다.", "오류 504 시간 초과"):
            with self.subTest(text=text):
                self.assertEqual(http_error_codes(text), {"504"})

    def test_quantities_identifiers_paths_and_urls_are_not_http_codes(self):
        # 접수 번호와 수량을 오류로 오인하여 정상 답변을 반려하지 않아야 한다.
        cases = [
            "500ms", "500 ms", "504%", "500 MB", "500GB", "500 bytes",
            "500초", "500분", "500 시간", "500건", "500개", "500명", "500토큰",
            "E403", "ERR_504", "request504", "ticket-504", "v4.500", "1504",
            "요청 ID: 504", "request_id: 504", "접수 번호 = 504", "문서ID: 504",
            "포트 500", "port: 500", "Content-Length: 500",
            "[faq:504]", "[faq:connectivity:504]", "/errors/504", "/504/logs",
            "https://example.test/errors/504?previous=403",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(http_error_codes(text), set())

    def test_only_provided_error_value_authorizes_a_specific_code(self):
        for status in ("none", "unknown", "unmentioned"):
            with self.subTest(status=status):
                intake = intake_for(status, symptom="504는 뜨지 않고 로그인 화면만 멈춰요")
                self.assertEqual(reported_http_error_codes(intake), set())
                self.assertEqual(unsupported_http_error_codes("HTTP 504 확인", intake), {"504"})
        reported = intake_for("provided", "HTTP 504 Gateway Timeout")
        self.assertEqual(reported_http_error_codes(reported), {"504"})
        self.assertEqual(unsupported_http_error_codes("HTTP 504와 HTTP 403", reported), {"403"})

    def test_missing_intake_keeps_legacy_contract_but_empty_intake_is_not_permission(self):
        self.assertEqual(unsupported_http_error_codes("HTTP 504", None), set())
        self.assertEqual(unsupported_http_error_codes("HTTP 504", {}), {"504"})

    def test_document_applicability_uses_title_and_body_without_mutating_search_results(self):
        other = {**DOCUMENT, "document_id": "faq:access", "title": "접근 확인",
                 "text": "HTTP 403이 표시되는 경우의 안내입니다."}
        documents = [copy.deepcopy(LOGIN_DOCUMENT), copy.deepcopy(DOCUMENT), other]
        before = copy.deepcopy(documents)
        for status in ("none", "unknown", "unmentioned"):
            with self.subTest(status=status):
                selected = applicable_documents(documents, intake_for(status))
                self.assertEqual([doc["document_id"] for doc in selected], ["faq:login"])
        reported = applicable_documents(documents, intake_for("provided", "HTTP 504 Gateway Timeout"))
        self.assertEqual([doc["document_id"] for doc in reported], ["faq:login", "faq:connectivity"])
        self.assertEqual(applicable_documents(documents, None), documents)
        self.assertEqual(documents, before)


class AnswerGroundingTests(unittest.TestCase):
    def setUp(self):
        self.collected = collected_evidence()
        self.retrieval = {"status": "ok", "documents": [copy.deepcopy(LOGIN_DOCUMENT), copy.deepcopy(DOCUMENT)]}

    def check(self, draft, intake, collected=None):
        return validate_answer(draft, "auth-service", collected or self.collected,
                               self.retrieval, intake=intake)

    def test_cited_document_does_not_authorize_unreported_504(self):
        for status in ("none", "unknown", "unmentioned"):
            with self.subTest(status=status):
                result = self.check(answer_with("HTTP 504가 표시되면 다시 확인하세요."), intake_for(status))
                self.assertFalse(result["ok"], result)
                self.assertTrue(any("HTTP 오류 코드" in error and "504" in error
                                    for error in result["errors"]), result)
        self.assertTrue(self.check(answer_with("페이지와 사용 환경을 담당자에게 알려주세요."),
                                   intake_for())["ok"])

    def test_reported_504_is_allowed_without_authorizing_403(self):
        intake = intake_for("provided", "HTTP 504 Gateway Timeout")
        supported = self.check(answer_with("표시된 HTTP 504 문구와 발생 시각을 기록하세요.",
                                           "faq:connectivity"), intake)
        self.assertTrue(supported["ok"], supported)
        unsupported = self.check(answer_with("HTTP 403 접근 오류도 확인하세요."), intake)
        self.assertFalse(unsupported["ok"], unsupported)
        self.assertTrue(any("403" in error for error in unsupported["errors"]), unsupported)

    def test_negative_symptom_and_sample_alert_do_not_authorize_user_error(self):
        intake = intake_for("none", symptom="504는 안 뜨고 로그인 화면이 멈춰요")
        items = copy.deepcopy(self.collected)
        items[0]["data"]["alerts"] = ["upstream returned HTTP 504 in sample monitoring"]
        result = self.check(answer_with("HTTP 504 화면을 기록하세요."), intake, items)
        self.assertFalse(result["ok"], result)
        self.assertTrue(any("HTTP 오류 코드" in error and "504" in error
                            for error in result["errors"]), result)

    def test_prompt_separates_verified_intake_from_documents_and_sample_alerts(self):
        intake = intake_for("none")
        intake["usage"] = {"prompt": "PRIVATE_EXTRACTION_PROMPT"}
        intake["warnings"] = ["INTERNAL_WARNING"]
        items = copy.deepcopy(self.collected)
        items[0]["data"]["alerts"] = ["HTTP 504 sample alert"]
        items.append({"source": "intake", "data": intake})
        before = copy.deepcopy(items)
        system, user = build_answer_prompt("로그인 문의", items)
        context = json.loads(user)
        self.assertEqual(context["verified_intake"], intake["fields"])
        self.assertEqual(context["confirmed_http_error_codes"], [])
        self.assertEqual([doc["document_id"] for doc in context["user_documents"]], ["faq:login"])
        self.assertNotIn("504", json.dumps(context["user_documents"]))
        self.assertEqual(context["withheld_document_count"], 1)
        self.assertEqual(context["applicability_status"], "ok")
        self.assertIn("504", context["observations"][0]["data"]["alerts"][0])
        self.assertIn("confirmed_http_error_codes", system)
        self.assertIn("none", system)
        self.assertNotIn("PRIVATE_EXTRACTION_PROMPT", user)
        self.assertNotIn("INTERNAL_WARNING", user)
        self.assertEqual(items, before)

        items[-1] = {"source": "intake", "data": intake_for("provided", "HTTP 504 Gateway Timeout")}
        _, provided_user = build_answer_prompt("오류를 알려드렸어요", items)
        provided = json.loads(provided_user)
        self.assertEqual(provided["confirmed_http_error_codes"], ["504"])
        self.assertEqual([doc["document_id"] for doc in provided["user_documents"]],
                         ["faq:login", "faq:connectivity"])
        self.assertEqual(provided["withheld_document_count"], 0)

    def test_withheld_document_citation_is_rejected_even_when_it_is_in_search_results(self):
        # 원본 검색에는 있어도 작성 근거에서 제외한 문서를 다시 인용할 수는 없다.
        draft = answer_with("페이지와 사용 환경을 확인해주세요.", "faq:connectivity")
        result = self.check(draft, intake_for("none"))
        self.assertFalse(result["ok"], result)
        self.assertTrue(any("인용 [faq:connectivity]" in error for error in result["errors"]), result)
        self.assertTrue(any(doc["document_id"] == "faq:connectivity" for doc in self.retrieval["documents"]))

    def test_all_documents_withheld_keeps_search_success_without_invented_advice(self):
        items = [item for item in self.collected if item.get("data", {}).get("document_id") != "faq:login"]
        before = copy.deepcopy(items)
        system, user = build_answer_prompt("로그인 문의", items + [{"source": "intake", "data": intake_for()}])
        context = json.loads(user)
        self.assertEqual(context["retrieval_status"], "ok")
        self.assertEqual(context["user_documents"], [])
        self.assertEqual(context["applicability_status"], "no_applicable_documents")
        self.assertEqual(context["withheld_document_count"], 1)
        self.assertNotIn("504", user)
        self.assertIn("검색 결과는 있으나", system)
        fallback = evidence_fallback("auth-service", items, intake=intake_for())
        self.assertIn("검색 결과는 있으나 현재 접수에 적용할 문서를 확인하지 못해", fallback)
        for phrase in ("504", "[faq:connectivity]", "VPN", "시크릿", "검색에 실패", "관련 사용자 문서 근거가 없습니다"):
            self.assertNotIn(phrase, fallback)
        self.assertTrue(self.check(fallback, intake_for(), items)["ok"])
        self.assertEqual(items, before)

    def test_fallback_preserves_only_applicable_citations_without_unreported_title_code(self):
        fallback = evidence_fallback("auth-service", self.collected, intake=intake_for("none"))
        self.assertNotIn("504", fallback)
        self.assertIn("[faq:login]", fallback)
        self.assertNotIn("[faq:connectivity]", fallback)
        self.assertIn("CPU 42.5%", fallback)
        self.assertIn("구체적인 조치 안내는 보류", fallback)
        result = self.check(fallback, intake_for("none"))
        self.assertTrue(result["ok"], result)

        supported = evidence_fallback("auth-service", self.collected,
                                      intake=intake_for("provided", "HTTP 504 Gateway Timeout"))
        self.assertIn(DOCUMENT["title"], supported)
        self.assertIn("[faq:connectivity]", supported)

    def test_fake_uses_applicable_document_without_copying_unreported_error_examples(self):
        items = self.collected + [{"source": "intake", "data": intake_for("none")}]
        reply = deterministic_rag_answer("선택한 대상: auth-service", items)
        self.assertNotIn("504", reply)
        self.assertIn("[faq:login]", reply)
        self.assertNotIn("[faq:connectivity]", reply)
        self.assertIn("시크릿 창", reply)
        result = self.check(reply, intake_for("none"))
        self.assertTrue(result["ok"], result)

    def test_archived_openai_answer_with_no_reported_error_is_now_rejected(self):
        # 실제로 보관한 실패 원문을 수정하지 않고 다시 검사한다. 수치 검증은 통과해도
        # 접수에 없는 504 안내는 별도 이유로 반려되어야 한다.
        archive = json.loads((ROOT / "doc/evidence/final_openai/admin_detail.json").read_text(encoding="utf-8"))
        intake = archive["details"]["intake"]
        retrieval = archive["details"]["retrieval"]
        self.assertEqual(intake["fields"]["error_message"]["status"], "none")
        self.assertIn("504 시간 초과", archive["answer"])
        items = [{"source": "monitoring", "data": data} for data in archive["observations"]]
        items.extend({"source": "knowledge", "data": doc} for doc in retrieval["documents"])
        items.append({"source": "retrieval_status", "data": {"status": retrieval["status"]}})
        result = validate_answer(archive["answer"], archive["selected_system"], items,
                                 retrieval, intake=intake)
        self.assertFalse(result["ok"], result)
        self.assertTrue(any("HTTP 오류 코드" in error and "504" in error
                            for error in result["errors"]), result)

    def test_archived_model_answer_cleanup_removes_only_internal_annotations(self):
        # 실모델의 실제 저장 답변을 재생한다. 표시 정리로 문장·지표·실제 출처가
        # 바뀌지 않아야 하며, 이를 위해 모델을 다시 호출할 필요는 없다.
        archive = json.loads((ROOT / "doc/evidence/post_demo_fixes/hybrid_model.json").read_text(encoding="utf-8"))
        raw = archive["result"]["unvalidated_draft"]
        self.assertIn("[confirmed_http_error_codes]", raw)
        self.assertIn("[sample_monitoring]", raw)
        cleaned = clean_internal_annotations(raw)
        expected = raw.replace(" [confirmed_http_error_codes]", "").replace(" [sample_monitoring]", "")
        self.assertEqual(cleaned, expected)
        self.assertNotIn("[confirmed_http_error_codes]", cleaned)
        self.assertNotIn("[sample_monitoring]", cleaned)
        for citation in ("[faq:login]", "[faq:browser]"):
            self.assertGreater(raw.count(citation), 0)
            self.assertEqual(cleaned.count(citation), raw.count(citation))
        self.assertIn("HTTP 4xx/5xx", cleaned)
        self.assertIn("CPU 80.4%", cleaned)
        self.assertIn("메모리 62.2%", cleaned)


class ConnectivityRetriever(StubRetriever):
    def __init__(self, documents=None):
        super().__init__()
        self.documents = copy.deepcopy(documents if documents is not None else [LOGIN_DOCUMENT, DOCUMENT])

    async def search(self, query, system, audience="user", k=3):
        result = await super().search(query, system, audience=audience, k=k)
        if audience == "user":
            result["documents"] = copy.deepcopy(self.documents)
        return result


class SequenceQuality:
    """고정 초안으로 재작성과 fallback 경로를 재현하는 네트워크 없는 모델이다."""

    def __init__(self, *drafts):
        self.drafts = drafts
        self.calls = []

    async def write(self, query, collected, attempt, feedback, on_token=None):
        self.calls.append({"collected": collected, "attempt": attempt, "feedback": feedback})
        return self.drafts[min(attempt - 1, len(self.drafts) - 1)], {"usd": 0}


class AnswerGroundingFlowTests(unittest.TestCase):
    def run_graph(self, query, quality, previous=None, documents=None):
        mcp = RecordingMCP()
        mcp.status_overrides["auth-service"] = {
            "cpu_percent": 42.5, "memory_percent": 31.0, "alerts": ["latency"],
        }
        deps = SupervisorDeps(RecordingFastLLM(), quality, mcp, None, "v2",
                              retriever=ConnectivityRetriever(documents))
        state = {"query": query, "selected_system": "auth-service", "thread_id": "grounding-test",
                 "history": [], "events": [], "cost_usd": 0, "intake_previous": previous or {}}
        return asyncio.run(build_graph(deps).ainvoke(state))

    def test_unreported_504_is_retried_and_safe_second_draft_is_returned(self):
        bad = answer_with("HTTP 504가 표시되면 기록해주세요.")
        safe = answer_with("문제가 생긴 페이지와 사용 환경을 담당자에게 알려주세요.")
        quality = SequenceQuality(bad, safe)
        result = self.run_graph("로그인 화면이 멈춰요. 오류 메시지는 없어요.", quality)
        self.assertEqual(result["intake"]["fields"]["error_message"]["status"], "none")
        self.assertEqual(result["validation_attempts"], 2)
        self.assertEqual(result["validation"]["status"], "passed")
        self.assertEqual(result["final"].split("\n\n추가 확인:")[0], safe)
        self.assertNotIn("504", result["final"])
        self.assertTrue(any("HTTP 오류 코드" in error for event in result["events"]
                            if event["type"] == "Validation" for error in event["errors"]))
        self.assertIn("504", quality.calls[1]["feedback"])

    def test_repeated_unreported_code_exhausts_retry_and_returns_safe_fallback(self):
        quality = SequenceQuality(answer_with("HTTP 504 화면을 기록하세요."))
        result = self.run_graph("로그인 화면이 멈춰요. 오류 메시지는 없어요.", quality)
        self.assertEqual(len(quality.calls), 3)
        self.assertEqual(result["validation_attempts"], 3)
        self.assertEqual(result["validation"]["status"], "fallback")
        self.assertTrue(any("HTTP 오류 코드" in error for error in result["validation"]["errors"]))
        self.assertNotIn("504", result["final"])
        self.assertIn("[faq:login]", result["final"])
        self.assertNotIn("[faq:connectivity]", result["final"])
        self.assertIn("구체적인 조치 안내는 보류", result["final"])
        # 작성 단계에만 접수 문맥을 추가해야 재시도마다 공유 근거가 늘어나지 않는다.
        self.assertFalse(any(item["source"] == "intake" for item in result["collected"]))
        for call in quality.calls:
            self.assertEqual(sum(item["source"] == "intake" for item in call["collected"]), 1)
        rechecked = validate_answer(result["final"], "auth-service", result["collected"],
                                    result["retrieval"], intake=result["intake"])
        self.assertTrue(rechecked["ok"], rechecked)

    def test_reported_504_from_current_or_followup_intake_is_allowed(self):
        draft = answer_with("표시된 HTTP 504 문구와 발생 시각을 기록하세요.", "faq:connectivity")
        for previous in (None, intake_for("none")):
            with self.subTest(has_previous=previous is not None):
                quality = SequenceQuality(draft)
                result = self.run_graph(
                    "로그인 화면이 멈춰요. 오류 메시지: HTTP 504 Gateway Timeout", quality, previous)
                field = result["intake"]["fields"]["error_message"]
                self.assertEqual(field["status"], "provided")
                self.assertIn("504", field["value"])
                self.assertEqual(result["validation"]["status"], "passed")
                self.assertEqual(result["validation_attempts"], 1)
                self.assertIn("HTTP 504", result["final"])

    def test_fake_rag_flow_does_not_copy_unreported_document_error(self):
        result = self.run_graph("로그인 화면이 멈춰요. 오류 메시지는 없어요.", QualityLLM("fake", 0))
        self.assertEqual(result["validation"]["status"], "passed")
        self.assertNotIn("504", result["final"])
        self.assertIn("[faq:login]", result["final"])
        self.assertNotIn("[faq:connectivity]", result["final"])

    def test_all_documents_withheld_skips_model_and_returns_validated_limited_notice(self):
        # 검색 성공과 적용 근거 부족을 구분하고, 품질 모델에 조치를 추측시키지 않는다.
        quality = SequenceQuality(answer_with("HTTP 504가 표시되면 확인해주세요."))
        result = self.run_graph("로그인 화면이 멈춰요. 오류 메시지는 없어요.", quality,
                                documents=[DOCUMENT])
        self.assertEqual(quality.calls, [])
        self.assertEqual(result["validation_attempts"], 1)
        self.assertEqual(result["validation"]["status"], "passed")
        self.assertEqual(result["retrieval"]["status"], "ok")
        self.assertEqual(result["retrieval"]["documents"][0]["document_id"], "faq:connectivity")
        original_docs = [item["data"] for item in result["collected"] if item["source"] == "knowledge"]
        self.assertEqual(original_docs, [DOCUMENT])
        self.assertIn("504", original_docs[0]["text"])
        self.assertIn("검색 결과는 있으나 현재 접수에 적용할 문서를 확인하지 못해", result["final"])
        for phrase in ("504", "[faq:connectivity]", "VPN", "시크릿", "검색에 실패"):
            self.assertNotIn(phrase, result["final"])
        rechecked = validate_answer(result["final"], "auth-service", result["collected"],
                                    result["retrieval"], intake=result["intake"])
        self.assertTrue(rechecked["ok"], rechecked)

    def test_write_context_is_copied_without_mutating_shared_collected(self):
        # write 노드를 연속 호출해도 원래 검색 근거와 리스트의 항목 수는 유지한다.
        original = collected_evidence()
        before = copy.deepcopy(original)
        intake = intake_for("none")
        quality = SequenceQuality(answer_with("사용 환경을 담당자에게 알려주세요."))
        deps = SupervisorDeps(RecordingFastLLM(), quality, RecordingMCP(), None, "v2")
        state = {"query": "로그인이 안 돼요", "selected_system": "auth-service", "intake": intake,
                 "collected": original, "events": [], "cost_usd": 0}
        asyncio.run(write_node(state, deps))
        state["validation_attempts"] = 1
        asyncio.run(write_node(state, deps))
        self.assertIs(state["collected"], original)
        self.assertEqual(original, before)
        self.assertEqual(len(quality.calls), 2)
        for call in quality.calls:
            self.assertIsNot(call["collected"], original)
            contexts = [item["data"] for item in call["collected"] if item["source"] == "intake"]
            self.assertEqual(contexts, [intake])

    def test_write_cleanup_does_not_hide_unreported_code_from_validation(self):
        # 내부 태그를 지우는 처리가 실제 오류 문장까지 없애 검증을 우회하면 안 된다.
        raw = answer_with("HTTP 504가 표시되면 기록하세요. [confirmed_http_error_codes]")
        quality = SequenceQuality(raw)
        deps = SupervisorDeps(RecordingFastLLM(), quality, RecordingMCP(), None, "v2")
        state = {"query": "로그인이 안 돼요. 오류 메시지는 없어요.",
                 "selected_system": "auth-service", "intake": intake_for("none"),
                 "collected": collected_evidence(), "events": [], "cost_usd": 0,
                 "retrieval": {"status": "ok", "documents": [LOGIN_DOCUMENT, DOCUMENT]}}
        written = asyncio.run(write_node(state, deps))
        self.assertEqual(len(quality.calls), 1)
        self.assertNotIn("[confirmed_http_error_codes]", written["draft"])
        self.assertIn("HTTP 504가 표시되면 기록하세요.", written["draft"])
        self.assertIn("[faq:login]", written["draft"])
        checked = asyncio.run(validate_node({**state, **written}, deps))
        self.assertEqual(checked["validation"]["status"], "retry")
        self.assertTrue(any("HTTP 오류 코드" in error and "504" in error
                            for error in checked["validation"]["errors"]), checked)


if __name__ == "__main__":
    unittest.main()
