"""고정된 정상/오류 답변으로 차단과 오차단을 함께 검사한다. 의미 품질 평가는 별도다."""

import unittest
from unittest.mock import patch

from agent.answer_generation import deterministic_rag_answer, evidence_fallback
from agent.answer_validation import validate_answer


class AnswerValidationTests(unittest.TestCase):
    def setUp(self):
        self.doc = {"document_id": "faq:login", "title": "로그인 확인", "audience": "user",
                    "text": "5분 후에도 화면이 멈추면 오류 504를 기록해주세요. 정상 진행률 100% 표시는 완료를 뜻합니다."}
        self.retrieval = {"status": "ok", "documents": [self.doc]}
        self.collected = [
            {"source": "monitoring", "data": {"system_name": "auth-service", "status": "degraded",
             "cpu_percent": 42.5, "memory_percent": 31.0, "alerts": ["latency"]}},
            {"source": "knowledge", "data": self.doc},
            {"source": "retrieval_status", "data": {"status": "ok"}},
        ]
        self.good = "1) 샘플 점검: auth-service 상태=degraded, CPU 42.5%, 메모리 31.0%, 알림 1건.\n2) 문서 안내: 5분 후에도 증상이 있으면 오류 504를 기록해주세요. [faq:login]\n3) 실제 원인은 추가 확인이 필요합니다."

    def check(self, draft, collected=None, retrieval=None):
        return validate_answer(draft, "auth-service", collected if collected is not None else self.collected,
                               retrieval if retrieval is not None else self.retrieval)

    def assert_rejected(self, draft, reason):
        result = self.check(draft)
        self.assertFalse(result["ok"], result)
        self.assertTrue(any(reason in error for error in result["errors"]), result)

    def test_supported_answer_and_citations_pass(self):
        result = self.check(self.good)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["citations"], ["faq:login"])

    def test_fake_generated_and_limited_fallback_answers_pass(self):
        for reply in (deterministic_rag_answer("선택한 대상: auth-service", self.collected),
                      evidence_fallback("auth-service", self.collected)):
            with self.subTest(reply=reply):
                self.assertTrue(self.check(reply)["ok"], self.check(reply))

    def test_unknown_and_other_registered_target_are_rejected(self):
        for system in ("payments-service", "imaginary-service", "web-01"):
            with self.subTest(system=system):
                self.assert_rejected(self.good.replace("auth-service", system), "선택하지 않은")

    def test_missing_target_and_sample_label_are_rejected(self):
        self.assert_rejected(self.good.replace("auth-service", "대상 시스템"), "시스템 이름")
        self.assert_rejected(self.good.replace("샘플", "실시간"), "샘플")

    def test_catalog_target_without_service_suffix_is_also_checked(self):
        with patch("agent.answer_validation.get_system_names", return_value=["auth-service", "billing"]):
            self.assert_rejected(self.good + "\nbilling도 장애입니다.", "선택하지 않은")

    def test_wrong_missing_and_operator_citations_are_rejected(self):
        for citation in ("[faq:invented]", "[runbook:database]"):
            self.assert_rejected(self.good.replace("[faq:login]", citation), "검색 결과에 없습니다")
        self.assert_rejected(self.good.replace("[faq:login]", ""), "문서 ID 인용")

    def test_citation_must_also_be_in_actual_search_result(self):
        result = self.check(self.good, retrieval={"status": "ok", "documents": []})
        self.assertFalse(result["ok"])
        self.assertTrue(any("검색 결과에 없습니다" in error for error in result["errors"]))

    def test_wrong_metric_and_cpu_memory_swap_are_rejected(self):
        self.assert_rejected(self.good.replace("CPU 42.5%", "CPU 99%"), "CPU 99%")
        self.assert_rejected(self.good.replace("CPU 42.5%, 메모리 31.0%", "CPU 31.0%, 메모리 42.5%"), "관측 수치")
        self.assert_rejected(self.good.replace("CPU 42.5%", "31.0%의 CPU 사용률"), "관측 수치")

    def test_invented_unlabeled_percent_and_alarm_count_are_rejected(self):
        self.assert_rejected(self.good + "\n현재 사용률은 77%입니다.", "없는 비율")
        self.assert_rejected(self.good.replace("알림 1건", "알림 3건"), "알림 3건")
        self.assert_rejected(self.good.replace("알림 1건", "2건의 알림"), "알림 2건")

    def test_markdown_emphasis_does_not_hide_wrong_or_swapped_metric(self):
        for metric in ("CPU **999%**", "**CPU** 999%", "CPU **31.0%**", "`CPU` `31.0%`"):
            with self.subTest(metric=metric):
                self.assert_rejected(self.good.replace("CPU 42.5%", metric), "관측 수치")
        normal = self.good.replace("CPU 42.5%", "**CPU** **42.5%**")
        self.assertTrue(self.check(normal)["ok"], self.check(normal))

    def test_sample_status_mismatch_is_rejected(self):
        self.assert_rejected(self.good.replace("상태=degraded", "상태=healthy"), "상태=healthy")

    def test_numbers_from_cited_faq_are_not_misclassified_as_monitoring(self):
        result = self.check(self.good + "\n문서의 정상 진행률 100% 표시를 확인해주세요. [faq:login]")
        self.assertTrue(result["ok"], result)
        # 같은 100%라도 CPU라고 주장하면 문서의 일반 수치로 허용하지 않는다.
        self.assert_rejected(self.good + "\nCPU 100%입니다. [faq:login]", "CPU 100%")

    def test_cause_certainty_and_claimed_completed_action_are_rejected(self):
        self.assert_rejected(self.good + "\n원인은 DB 장애입니다.", "원인을 확정")
        self.assert_rejected(self.good + "\n서버를 재시작했습니다.", "조치·복구")
        self.assert_rejected(self.good + "\n복구를 완료했습니다.", "조치·복구")

    def test_negated_cause_and_unknown_recovery_do_not_trigger_certainty_rule(self):
        reply = self.good + "\nDB 장애라고 단정할 수 없습니다. 복구 완료 여부는 담당자의 확인이 필요합니다."
        self.assertTrue(self.check(reply)["ok"], self.check(reply))

    def test_explicit_uncertain_cause_is_allowed_but_other_sentence_is_still_checked(self):
        for sentence in ("원인이 아직 확정되지 않았습니다.", "원인은 아직 확인되지 않았습니다.",
                         "원인은 DB 장애라고 확정할 수 없습니다.", "원인은 불명확합니다."):
            with self.subTest(sentence=sentence):
                result = self.check(self.good + "\n" + sentence)
                self.assertTrue(result["ok"], result)
        self.assert_rejected(self.good + "\n원인은 DB 장애입니다. 원인이 확정되지 않았습니다.", "원인을 확정")

    def test_operator_commands_and_server_action_advice_are_rejected(self):
        for command in ("sudo systemctl restart database", "kubectl rollout restart deployment/api",
                        "docker restart database", "DROP TABLE messages", "서버를 재시작하세요"):
            with self.subTest(command=command):
                self.assert_rejected(self.good + "\n" + command, "운영 명령")

    def test_retrieval_no_match_and_failure_must_be_reported_differently(self):
        observations = [self.collected[0]]
        for status, notice in (("no_match", "관련 사용자 문서가 없습니다."),
                               ("error", "문서 검색에 실패했습니다.")):
            items = observations + [{"source": "retrieval_status", "data": {"status": status}}]
            retrieval = {"status": status, "documents": []}
            reply = f"1) 샘플 점검: auth-service 상태=degraded.\n2) {notice}\n3) 실제 원인은 확인이 필요합니다."
            self.assertTrue(self.check(reply, items, retrieval)["ok"], self.check(reply, items, retrieval))
            other_status = "error" if status == "no_match" else "no_match"
            wrong = self.check(reply, items, {"status": other_status, "documents": []})
            self.assertFalse(wrong["ok"], wrong)

    def test_fallback_with_missing_documents_or_search_failure_passes(self):
        for status in ("no_match", "error"):
            items = [self.collected[0], {"source": "retrieval_status", "data": {"status": status}}]
            reply = evidence_fallback("auth-service", items)
            result = self.check(reply, items, {"status": status, "documents": []})
            self.assertTrue(result["ok"], result)

    def test_missing_monitoring_does_not_allow_invented_metrics(self):
        result = self.check(self.good, collected=self.collected[1:])
        self.assertFalse(result["ok"])
        self.assertTrue(any("CPU" in error for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
