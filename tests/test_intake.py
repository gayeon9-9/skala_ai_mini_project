"""고정 접수 예제로 병합 규칙을 검사한다. 실제 LLM의 추출 정확도 평가는 별도다."""

import unittest

from agent.intake import analyze_intake, empty_fields, extract_rules


class RulesLLM:
    def extract_intake(self, query, previous):
        return {"fields": extract_rules(query, previous), "source": "rules"}, {"tokens": 0, "usd": 0}


class FixedLLM:
    def __init__(self, output):
        self.output = output

    def extract_intake(self, query, previous):
        return self.output, {"tokens": 10, "usd": 0.001}


def field(value=None, evidence=None, status="provided"):
    return {"status": status, "value": value, "evidence": evidence or value}


class IntakeTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_symptom_time_none_and_environment_are_structured(self):
        query = "오후 2시부터 결제 화면이 멈췄어요. 오류 메시지는 없어요. 윈도우 11에서 크롬을 써요."
        result = await analyze_intake(query, {}, RulesLLM())
        self.assertEqual(result["fields"]["occurred_at"]["value"], "오후 2시")
        self.assertEqual(result["fields"]["error_message"]["status"], "none")
        self.assertEqual(result["fields"]["environment"]["value"], "윈도우 11에서 크롬")
        self.assertIn("결제 화면이 멈", result["fields"]["symptom"]["value"])
        self.assertEqual(result["missing_fields"], [])
        self.assertEqual(result["questions"], [])
        self.assertEqual(result["source"], "rules")

    async def test_absent_information_is_not_invented(self):
        result = await analyze_intake("페이지가 열리지 않아요", {}, RulesLLM())
        self.assertEqual(result["fields"]["error_message"]["status"], "unmentioned")
        self.assertEqual(result["fields"]["occurred_at"]["status"], "unmentioned")
        self.assertEqual(result["last_question_field"], "occurred_at")
        self.assertEqual(len(result["questions"]), 1)

    async def test_followup_merges_time_and_preserves_original_symptom(self):
        first = await analyze_intake("결제가 안 돼요", {}, RulesLLM())
        second = await analyze_intake("어제 오후 4시부터요", first, RulesLLM())
        self.assertEqual(second["fields"]["symptom"], first["fields"]["symptom"])
        self.assertEqual(second["fields"]["occurred_at"]["value"], "어제 오후 4시")
        self.assertEqual(second["last_question_field"], "error_message")
        third = await analyze_intake("없어요", second, RulesLLM())
        self.assertEqual(third["fields"]["error_message"]["status"], "none")
        self.assertEqual(third["last_question_field"], "environment")
        final = await analyze_intake("잘 모르겠어요", third, RulesLLM())
        self.assertEqual(final["fields"]["environment"]["status"], "unknown")
        self.assertEqual(final["questions"], [])
        self.assertEqual(final["missing_fields"], [])

    async def test_none_and_unknown_are_not_asked_again(self):
        first = await analyze_intake("로그인이 안 돼요. 발생 시간은 몰라요. 오류 메시지는 없어요.", {}, RulesLLM())
        self.assertEqual(first["fields"]["occurred_at"]["status"], "unknown")
        second = await analyze_intake("크롬입니다", first, RulesLLM())
        self.assertEqual(second["fields"]["occurred_at"]["status"], "unknown")
        self.assertEqual(second["fields"]["error_message"]["status"], "none")
        self.assertEqual(second["questions"], [])

    async def test_unanswered_question_is_not_repeated_every_turn(self):
        first = await analyze_intake("페이지가 느려요", {}, RulesLLM())
        second = await analyze_intake("일단 확인해주세요", first, RulesLLM())
        self.assertEqual(first["last_question_field"], "occurred_at")
        self.assertEqual(second["last_question_field"], "error_message")
        self.assertIn("occurred_at", second["missing_fields"])
        self.assertEqual(second["fields"]["symptom"], first["fields"]["symptom"])

    async def test_unsupported_value_and_evidence_are_discarded(self):
        output = {"symptom": field("화면 멈춤", "화면이 멈춰요"),
                  "occurred_at": field("오늘 오후 2시", "오늘 오후 2시"),
                  "error_message": field("E777", "E777")}
        result = await analyze_intake("화면이 멈춰요", {}, FixedLLM(output))
        self.assertEqual(result["fields"]["occurred_at"]["status"], "unmentioned")
        self.assertEqual(result["fields"]["error_message"]["status"], "unmentioned")
        self.assertGreaterEqual(len(result["warnings"]), 3)
        self.assertEqual(result["source"], "rules_fallback")

    async def test_one_invalid_field_does_not_discard_valid_model_fields(self):
        result = await analyze_intake("사파리에서 화면이 멈춰요", {}, FixedLLM({
            "symptom": field("화면이 멈춰요"),
            "occurred_at": {"status": "provided", "value": 3, "evidence": "3"},
        }))
        self.assertEqual(result["fields"]["symptom"]["value"], "화면이 멈춰요")
        self.assertEqual(result["fields"]["occurred_at"]["status"], "unmentioned")
        self.assertEqual(result["source"], "model")
        self.assertEqual(len(result["warnings"]), 1)

    async def test_model_error_falls_back_with_label_without_exposing_error_text(self):
        class BrokenLLM:
            def extract_intake(self, query, previous):
                raise RuntimeError("SECRET token should not appear")
        result = await analyze_intake("결제 페이지가 멈춰요. 오후 5시부터예요", {}, BrokenLLM())
        self.assertEqual(result["source"], "rules_fallback")
        self.assertEqual(result["fields"]["occurred_at"]["value"], "오후 5시")
        self.assertNotIn("SECRET", str(result))

    async def test_malformed_json_uses_fallback(self):
        result = await analyze_intake("로그인이 안 돼요", {}, FixedLLM("not JSON"))
        self.assertEqual(result["source"], "rules_fallback")
        self.assertEqual(result["fields"]["symptom"]["status"], "provided")

    async def test_explicit_correction_replaces_field_without_normalizing_time(self):
        first = await analyze_intake("오후 2시부터 화면이 멈춰요", {}, RulesLLM())
        second = await analyze_intake("시각을 정정할게요. 오후 3시예요", first, RulesLLM())
        self.assertEqual(second["fields"]["occurred_at"]["value"], "오후 3시")
        self.assertEqual(second["fields"]["symptom"], first["fields"]["symptom"])

    async def test_none_not_inferred_from_a_sentence_that_merely_mentions_error(self):
        result = await analyze_intake("오류가 나요", {}, FixedLLM({
            "error_message": field(None, "오류가 나요", "none")}))
        self.assertEqual(result["fields"]["error_message"]["status"], "unmentioned")
        self.assertTrue(result["warnings"])

    async def test_adjacent_error_none_does_not_erase_explicit_environment(self):
        result = await analyze_intake("브라우저는 크롬이고 오류 메시지는 없어요", {}, RulesLLM())
        self.assertEqual(result["fields"]["environment"]["value"], "크롬")
        self.assertEqual(result["fields"]["error_message"]["status"], "none")

    async def test_short_response_without_question_context_is_not_assigned(self):
        result = await analyze_intake("없어요", {}, RulesLLM())
        self.assertEqual(result["fields"], empty_fields())

    async def test_arbitrary_other_fields_cannot_change_selected_system(self):
        result = await analyze_intake("시스템은 web-01입니다", {}, FixedLLM({"selected_system": "web-01"}))
        self.assertNotIn("selected_system", result["fields"])

    async def test_async_adapter_and_json_object_supported(self):
        class AsyncLLM:
            async def extract_intake(self, query, previous):
                return '{"error_message":{"status":"provided","value":"E403","evidence":"E403"}}', {"tokens": 4}
        result = await analyze_intake("오류 코드 E403", {}, AsyncLLM())
        self.assertEqual(result["fields"]["error_message"]["value"], "E403")
        self.assertEqual(result["usage"]["tokens"], 4)


if __name__ == "__main__":
    unittest.main()
