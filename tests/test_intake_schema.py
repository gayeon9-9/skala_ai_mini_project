"""생성 스키마와 실제 접수 검증을 함께 확인한다. 실제 모델/API는 호출하지 않는다.

jsonschema는 기존 MCP SDK의 필수 의존성이다. 직접 정의한 스키마 검사기를 만들어
같은 오류를 반복하지 않고 JSON Schema 표준 검사기로 모순 조합을 검증한다.
"""

import os

os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

import copy
import json
import unittest
from unittest.mock import Mock, patch

from jsonschema import Draft202012Validator

from agent.intake import analyze_intake, empty_fields
from agent.intake_llm import INTAKE_SCHEMA
from agent.ollama_llm import OllamaFastLLM


QUERY = "오전 9시부터 로그인 후 화면이 넘어가지 않아요. 오류 메시지는 없어요. 윈도우 크롬을 사용해요."
VALID_OUTPUT = {"fields": {
    "symptom": {"status": "provided", "value": "로그인 후 화면이 넘어가지 않아요", "evidence": "로그인 후 화면이 넘어가지 않아요"},
    "occurred_at": {"status": "provided", "value": "오전 9시", "evidence": "오전 9시"},
    "error_message": {"status": "none", "value": None, "evidence": "오류 메시지는 없어요"},
    "environment": {"status": "provided", "value": "윈도우 크롬", "evidence": "윈도우 크롬"},
}}


class FixedLLM:
    def __init__(self, output):
        self.output = output

    def extract_intake(self, query, previous):
        return self.output, {"tokens": 10, "usd": 0}


class IntakeSchemaTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        Draft202012Validator.check_schema(INTAKE_SCHEMA)
        self.validator = Draft202012Validator(INTAKE_SCHEMA)

    def test_schema_accepts_provided_none_unknown_and_unmentioned(self):
        self.assertTrue(self.validator.is_valid(VALID_OUTPUT))
        self.assertTrue(self.validator.is_valid({"fields": empty_fields()}))
        unknown = copy.deepcopy(VALID_OUTPUT)
        unknown["fields"]["occurred_at"] = {"status": "unknown", "value": None, "evidence": "발생 시각은 모르겠어요"}
        self.assertTrue(self.validator.is_valid(unknown))

    def test_schema_rejects_status_value_evidence_contradictions(self):
        contradictory = [
            {"status": "provided", "value": None, "evidence": "원문"},
            {"status": "provided", "value": "원문", "evidence": None},
            {"status": "provided", "value": "", "evidence": "원문"},
            {"status": "provided", "value": "원문", "evidence": ""},
            {"status": "none", "value": "오류 없음", "evidence": "오류 없음"},
            {"status": "none", "value": None, "evidence": None},
            {"status": "unknown", "value": "모름", "evidence": "모름"},
            {"status": "unknown", "value": None, "evidence": ""},
            {"status": "unmentioned", "value": "임의 값", "evidence": None},
            {"status": "unmentioned", "value": None, "evidence": "임의 근거"},
        ]
        for invalid in contradictory:
            with self.subTest(invalid=invalid):
                output = copy.deepcopy(VALID_OUTPUT)
                output["fields"]["symptom"] = invalid
                self.assertFalse(self.validator.is_valid(output))

    def test_schema_rejects_flat_values_missing_fields_and_extra_properties(self):
        invalid = [
            {"fields": {"symptom": "로그인 실패"}},
            {"fields": {}},
            {**copy.deepcopy(VALID_OUTPUT), "selected_system": "web-01"},
        ]
        extra = copy.deepcopy(VALID_OUTPUT)
        extra["fields"]["symptom"]["confidence"] = 1
        invalid.append(extra)
        for output in invalid:
            with self.subTest(output=output):
                self.assertFalse(self.validator.is_valid(output))

    async def test_valid_model_output_flows_through_real_adapter_as_model_source(self):
        response = Mock()
        response.json.return_value = {"message": {"content": json.dumps(VALID_OUTPUT)},
                                      "prompt_eval_count": 20, "eval_count": 30}
        # HTTP 경계만 대체해 schema 전달→JSON 파싱→접수 근거 검증까지 실행한다.
        with patch("agent.ollama_llm.httpx.post", return_value=response) as post:
            result = await analyze_intake(QUERY, {}, OllamaFastLLM("qwen3:0.6b", 0))
        payload = post.call_args.kwargs["json"]
        self.assertTrue(Draft202012Validator(payload["format"]).is_valid(VALID_OUTPUT))
        bad = copy.deepcopy(VALID_OUTPUT)
        bad["fields"]["symptom"]["value"] = None
        self.assertFalse(Draft202012Validator(payload["format"]).is_valid(bad))
        self.assertIn("예시 출력", payload["messages"][0]["content"])
        self.assertEqual(json.loads(payload["messages"][1]["content"])["current_message"], QUERY)
        self.assertIs(payload["think"], False)
        self.assertEqual(result["source"], "model")
        self.assertEqual(result["fields"], VALID_OUTPUT["fields"])
        self.assertEqual(result["missing_fields"], [])
        self.assertEqual(result["warnings"], [])
        self.assertEqual(result["usage"]["tokens"], 50)

    async def test_invalid_model_combinations_still_use_labelled_rules_fallback(self):
        output = {"fields": {name: {"status": "provided", "value": None, "evidence": QUERY}
                             for name in empty_fields()}}
        self.assertFalse(self.validator.is_valid(output))
        result = await analyze_intake(QUERY, {}, FixedLLM(output))
        self.assertEqual(result["source"], "rules_fallback")
        self.assertEqual(len(result["warnings"]), 4)
        self.assertEqual(result["fields"]["error_message"]["status"], "none")
        self.assertEqual(result["missing_fields"], [])

    async def test_schema_valid_hallucinated_example_is_rejected_by_evidence_check(self):
        output = {"fields": empty_fields()}
        output["fields"]["occurred_at"] = {"status": "provided", "value": "오후 2시", "evidence": "오후 2시"}
        # JSON 형식이 맞아도 프롬프트 예시를 현재 문의의 사실로 받아들이면 안 된다.
        self.assertTrue(self.validator.is_valid(output))
        result = await analyze_intake(QUERY, {}, FixedLLM(output))
        self.assertEqual(result["source"], "rules_fallback")
        self.assertEqual(result["fields"]["occurred_at"]["value"], "오전 9시")
        self.assertTrue(any("원문" in warning for warning in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
