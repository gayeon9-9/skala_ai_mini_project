"""평가 공식과 분모를 검증한다. 실제 평가셋이나 외부 임베딩을 실행하지 않는다."""

import unittest

from evals.evaluate import evaluate_cases, legacy_keyword_baseline, score_result, summarise


class FakeRetriever:
    def __init__(self):
        self.queries = []
        self.built = False

    async def build_index(self):
        self.built = True
        return {"index_version": "test", "model": "test-only"}

    async def search(self, query, system, audience, k):
        if not self.built:
            raise AssertionError("검색 시간을 재기 전에 색인을 준비해야 합니다.")
        self.queries.append(query)
        if query == "unrelated":
            return {"status": "no_match", "documents": []}
        return {"status": "ok", "documents": [{"document_id": "runbook:cpu-spike"}]}


class EvaluationTests(unittest.IsolatedAsyncioTestCase):
    def test_hit_and_recall_differ_for_multiple_gold_documents(self):
        scored = score_result(["a", "b"], {"status": "ok", "documents": [{"document_id": "a"}]})
        self.assertTrue(scored["hit"])
        self.assertEqual(scored["recall"], 0.5)
        self.assertIsNone(scored["no_match_correct"])

    def test_error_is_not_a_correct_no_match_and_not_a_hit(self):
        self.assertFalse(score_result([], {"status": "error", "documents": []})["no_match_correct"])
        self.assertEqual(score_result(["a"], {"status": "error", "documents": []})["recall"], 0)

    def test_only_top_three_and_unique_gold_are_counted(self):
        result = {"status": "ok", "documents": [{"document_id": name} for name in ("x", "y", "z", "a")]}
        self.assertFalse(score_result(["a", "a"], result)["hit"])

    def test_baseline_is_single_result_and_falls_back_to_db_down(self):
        self.assertEqual(legacy_keyword_baseline("cpu 99%", "operator")["documents"],
                         [{"document_id": "runbook:cpu-spike"}])
        self.assertEqual(legacy_keyword_baseline("unrelated", "operator")["documents"],
                         [{"document_id": "runbook:db-down"}])
        self.assertEqual(legacy_keyword_baseline("login", "user")["status"], "not_applicable")

    def test_empty_denominators_report_null_not_false_perfection(self):
        result = summarise([])
        self.assertIsNone(result["hit_at_3"])
        self.assertIsNone(result["no_match_correct_rate"])
        self.assertEqual(result["latency"]["count"], 0)

    async def test_split_repetitions_na_and_denominators(self):
        # 본 평가셋을 읽지 않고 작은 별도 fixture로 split 격리와 집계만 확인한다.
        dataset = {"cases": [
            {"id": "fixture-dev", "split": "dev", "selected_system": "payments-service",
             "retrieval_checks": [
                 {"audience": "operator", "query": "cpu", "relevant_document_ids": ["runbook:cpu-spike", "runbook:high-latency"]},
                 {"audience": "operator", "query": "unrelated", "relevant_document_ids": []},
                 {"audience": "user", "query": "cpu", "relevant_document_ids": ["runbook:cpu-spike"]},
             ]},
            {"id": "fixture-other", "split": "test", "selected_system": "auth-service",
             "retrieval_checks": [{"audience": "user", "query": "must-not-run", "relevant_document_ids": []}]},
        ]}
        retriever = FakeRetriever()
        report = await evaluate_cases(dataset, "dev", 2, retriever)
        self.assertNotIn("must-not-run", retriever.queries)
        self.assertEqual(len(retriever.queries), 6)
        summary = report["summaries"]["semantic_operator_runbook"]
        self.assertEqual(summary["executions"], 4)
        self.assertEqual(summary["unique_checks"], 2)
        self.assertEqual(summary["positive_denominator"], 2)
        self.assertEqual(summary["hit_at_3"], 1)
        self.assertEqual(summary["recall_at_3"], 0.5)
        self.assertEqual(summary["no_match_denominator"], 2)
        self.assertEqual(summary["no_match_correct_count"], 2)
        self.assertEqual(summary["latency"]["count"], 4)
        baseline = report["summaries"]["legacy_operator_runbook"]
        self.assertEqual(baseline["no_match_correct_count"], 0)
        na = report["summaries"]["legacy_user_faq_not_applicable"]
        self.assertEqual(na["not_applicable_executions"], 2)
        self.assertEqual(na["executions"], 0)
        self.assertIsNone(na["hit_at_3"])


if __name__ == "__main__":
    unittest.main()
