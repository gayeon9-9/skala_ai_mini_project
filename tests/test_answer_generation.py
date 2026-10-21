"""검색 문서가 작성 문맥에 들어가고 사용자/운영자 자료가 섞이지 않는지 검사한다."""

import os
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from agent.answer_generation import build_answer_prompt, evidence_fallback
from agent.fake_llm import QualityLLM
from agent.ollama_llm import OllamaFastLLM, OllamaQualityLLM
from agent.openai_llm import OpenAIQualityLLM


def evidence(status="ok"):
    return [
        {"source": "monitoring", "data": {"system_name": "auth-service", "status": "degraded",
         "cpu_percent": 42.5, "memory_percent": 31.0, "alerts": ["latency"]}},
        {"source": "retrieval_status", "data": {"status": status}},
        {"source": "knowledge", "data": {"document_id": "faq:login", "title": "로그인 확인",
         "text": "시크릿 창에서도 같은 현상인지 확인하세요.", "path": "data/faq/login.md", "audience": "user"}},
        {"source": "knowledge", "data": {"document_id": "runbook:private", "title": "운영 내부 자료",
         "text": "ADMIN_ONLY_RESTART_DATABASE", "path": "data/runbooks/private.md", "audience": "operator"}},
    ]


class AnswerGenerationTests(unittest.IsolatedAsyncioTestCase):
    def test_prompt_contains_actual_user_document_and_citation_contract(self):
        system, user = build_answer_prompt("로그인 화면이 멈춰요", evidence())
        data = json.loads(user)
        self.assertEqual(data["user_documents"][0]["text"], "시크릿 창에서도 같은 현상인지 확인하세요.")
        self.assertEqual(data["user_documents"][0]["document_id"], "faq:login")
        self.assertIn("[faq:login]", system)
        self.assertIn("샘플", system)
        self.assertIn("원인을 확정할 수 없다", system)
        self.assertNotIn("ADMIN_ONLY", user)
        self.assertNotIn("runbook:private", user)

    def test_rag_context_excludes_legacy_operator_runbook_even_if_mixed_in(self):
        items = evidence() + [{"source": "runbook", "data": "LEGACY_PRIVATE_COMMAND"}]
        _, user = build_answer_prompt("문의", items)
        self.assertNotIn("LEGACY_PRIVATE_COMMAND", user)

    def test_legacy_context_retains_collected_runbook(self):
        _, user = build_answer_prompt("기존 교육용 문의", [{"source": "runbook", "data": "기존 매뉴얼"}])
        self.assertIn("기존 매뉴얼", user)

    async def test_fake_second_attempt_uses_retrieved_text_and_cites_it(self):
        chunks = []
        reply, _ = await QualityLLM("fake", 0).write(
            "선택한 대상: auth-service\n로그인 문의", evidence(), 2, None, on_token=chunks.append)
        self.assertIn("시크릿 창", reply)
        self.assertIn("[faq:login]", reply)
        self.assertIn("CPU 42.5%", reply)
        self.assertNotIn("ADMIN_ONLY", reply)
        self.assertEqual(chunks, [reply])

    async def test_no_match_and_retrieval_failure_are_distinct_and_not_invented(self):
        model = QualityLLM("fake", 0)
        replies = []
        for status in ("no_match", "error"):
            items = [item for item in evidence(status) if item["source"] != "knowledge"]
            reply, _ = await model.write("선택한 대상: auth-service", items, 2, None)
            replies.append(reply)
            self.assertNotIn("시크릿", reply)
            self.assertNotIn("재시작", reply)
            self.assertNotIn("[faq:", reply)
        self.assertIn("문서가 없어", replies[0])
        self.assertIn("검색에 실패", replies[1])

    def test_fallback_only_reports_selected_observations_and_document_titles(self):
        items = evidence() + [{"source": "monitoring", "data": {
            "system_name": "payments-service", "status": "down", "cpu_percent": 99,
            "memory_percent": 98, "alerts": []}}]
        reply = evidence_fallback("auth-service", items)
        self.assertIn("auth-service", reply)
        self.assertIn("CPU 42.5%", reply)
        self.assertIn("로그인 확인 [faq:login]", reply)
        self.assertNotIn("payments-service", reply)
        self.assertNotIn("시크릿 창", reply)
        self.assertNotIn("ADMIN_ONLY", reply)
        self.assertIn("담당자가 실제 오류 로그", reply)

    async def test_real_adapters_use_same_prompt_without_network(self):
        ollama = OllamaQualityLLM("test", 0)
        ollama._chat_stream = AsyncMock(return_value=("답변", {"usd": 0}))
        openai = OpenAIQualityLLM("test", 0)
        openai._chat = Mock(return_value=("답변", {"usd": 0}))
        await ollama.write("문의", evidence(), 2, "근거를 확인하세요")
        await openai.write("문의", evidence(), 2, "근거를 확인하세요")
        self.assertEqual(ollama._chat_stream.call_args.args, openai._chat.call_args.args)
        self.assertIn("faq:login", openai._chat.call_args.args[1])

    def test_ollama_structured_output_disables_thinking_and_bounds_generation(self):
        response = Mock()
        response.json.return_value = {"message": {"content": '{"fields":{}}'}, "eval_count": 5}
        model = OllamaFastLLM("qwen3:0.6b", 0)
        with patch("agent.ollama_llm.httpx.post", return_value=response) as post:
            model.extract_intake("합성 문의", {})
        payload = post.call_args.kwargs["json"]
        self.assertIs(payload["think"], False)
        self.assertEqual(payload["options"]["num_predict"], 2048)
        self.assertIsInstance(payload["format"], dict)
        self.assertFalse(payload["stream"])

    def test_ollama_normal_text_call_keeps_existing_generation_options(self):
        response = Mock()
        response.json.return_value = {"message": {"content": "일반 답변"}}
        with patch("agent.ollama_llm.httpx.post", return_value=response) as post:
            OllamaFastLLM("test", 0)._chat("지시", "질문")
        payload = post.call_args.kwargs["json"]
        self.assertNotIn("think", payload)
        self.assertNotIn("num_predict", payload["options"])


if __name__ == "__main__":
    unittest.main()
