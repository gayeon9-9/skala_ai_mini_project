"""별도 소형 fixture로 평가 공식·짝비교·개발/회귀 분리를 검사한다. 외부 호출 없음."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from evals.compare_embeddings import (calibrate_openai, compare_cases, latency, parse_args,
                                      run, score_row, selective_openai_key, summarise, validate_dev_report)


def document(name="right", score=.7, system="auth-service", audience="user"):
    return {"document_id": name, "score": score, "systems": [system], "audience": audience}


def row(expected=None, docs=None, status="ok", **extra):
    return {"case_id": "fixture", "check_index": 0, "provider": "openai", "status": status,
            "documents": docs or [], "expected_document_ids": ["right"] if expected is None else expected,
            "elapsed_seconds": .1, "system": "auth-service", "audience": "user", **extra}


class FakeRetriever:
    def __init__(self, provider, order, fail_build=False):
        self.provider, self.order, self.fail_build = provider, order, fail_build
        self.calls, self.tokens = 0, 0
        self.embedder = SimpleNamespace(usage_snapshot=self.snapshot, query_format="raw")
        self._index = {"manifest": {"embedding": {"model": provider, "digest": "fixed"}}, "vectors": [[1, 0]]}

    def snapshot(self):
        return {"successful_api_calls": self.calls, "input_tokens": self.tokens}

    async def build_index(self):
        if self.fail_build:
            raise RuntimeError("SECRET never printed")
        self.calls += 1
        self.tokens += 100
        return {"index_version": self.provider, "dimensions": 2}

    async def search(self, query, system, audience, k):
        self.calls += 1
        self.tokens += 10
        self.order.append((self.provider, query))
        if query == "raise":
            raise RuntimeError("SECRET never printed")
        return {"status": "ok", "documents": [document(score=.7 if query == "positive" else .3)], "index_version": self.provider}


class EmbeddingComparisonTests(unittest.IsolatedAsyncioTestCase):
    def dataset(self):
        return {"cases": [
            {"id": "fixture-dev", "split": "dev", "selected_system": "auth-service", "retrieval_checks": [
                {"query": "positive", "audience": "user", "relevant_document_ids": ["right"]},
                {"query": "negative", "audience": "user", "relevant_document_ids": []}]},
            {"id": "fixture-test", "split": "test", "selected_system": "auth-service", "retrieval_checks": [
                {"query": "DO_NOT_RUN_TEST_IN_DEV", "audience": "user", "relevant_document_ids": ["right"]}]}]}

    def test_ranking_precision_fixed_denominator_and_recall(self):
        scored = score_row(row(expected=["right", "second"], docs=[document("wrong"), document("right")]), .5)
        self.assertFalse(scored["hit_at_1"])
        self.assertTrue(scored["hit"])
        self.assertEqual(scored["recall"], .5)
        self.assertEqual(scored["precision_at_3"], 1 / 3)
        self.assertEqual(scored["reciprocal_rank_at_3"], .5)
        self.assertEqual(scored["irrelevant_documents"], 1)

    def test_cutoff_uses_raw_cosine_at_rounding_boundary(self):
        borderline = {**document(score=.5), "raw_score": .4999996}
        excluded = score_row(row(docs=[borderline]), .5)
        included = score_row(row(docs=[borderline]), .45)
        self.assertFalse(excluded["hit"])
        self.assertEqual(excluded["effective_status"], "no_match")
        self.assertTrue(included["hit"])
        self.assertEqual(included["returned_document_ids"], ["right"])

    def test_errors_stay_in_positive_and_negative_denominators(self):
        rows = [score_row(row(docs=[document()]), .5), score_row(row(status="error"), .5),
                score_row(row(expected=[], status="error"), .5), score_row(row(expected=[], docs=[]), .5)]
        summary = summarise(rows)
        self.assertEqual(summary["positive_denominator"], 2)
        self.assertEqual(summary["hit_at_3"], .5)
        self.assertEqual(summary["no_match_correct_rate"], .5)
        self.assertEqual(summary["error_count"], 2)
        self.assertEqual(summary["latency"]["count"], 4)
        self.assertEqual(summary["successful_latency"]["count"], 2)

    def test_cross_scope_violations_are_reported(self):
        bad = score_row(row(docs=[document(system="other-service", audience="operator")]), .5)
        self.assertEqual(bad["cross_system_documents"], 1)
        self.assertEqual(bad["cross_audience_documents"], 1)

    def test_nearest_rank_p95_and_empty_summary(self):
        self.assertEqual(latency(list(range(1, 21)))["p95_seconds"], 19)
        self.assertIsNone(summarise([])["hit_at_3"])
        self.assertIsNone(summarise([])["latency"]["p95_seconds"])

    def test_calibration_balances_no_match_and_hits_and_keeps_ollama(self):
        rows = [row(docs=[document(score=.65), document("wrong", score=.45)]),
                row(expected=[], docs=[document("unrelated", score=.4)])]
        calibration = calibrate_openai(rows)
        self.assertEqual(calibration["recommended_openai_min_score"], .50)
        self.assertEqual(calibration["ollama_min_score"], .50)
        self.assertEqual(len(calibration["curve"]), 13)

    async def test_pair_order_warmup_usage_and_dev_split(self):
        order = []
        retrievers = {p: FakeRetriever(p, order) for p in ("ollama", "openai")}
        report = await compare_cases(self.dataset(), "dev", 2, retrievers, {"ollama": .5, "openai": .5})
        timed = [item for item in order if item[1] in ("positive", "negative")]
        self.assertEqual([item[0] for item in timed], ["ollama", "openai", "openai", "ollama",
                                                     "openai", "ollama", "ollama", "openai"])
        self.assertNotIn("DO_NOT_RUN_TEST_IN_DEV", [query for _, query in order])
        for provider in retrievers:
            self.assertEqual(report["usage"][provider]["index_build"]["input_tokens"], 100)
            self.assertEqual(report["usage"][provider]["warmup"]["input_tokens"], 10)
            self.assertEqual(report["usage"][provider]["timed_queries"]["input_tokens"], 40)
            self.assertEqual(report["summaries"][provider]["all"]["executions"], 4)
        self.assertEqual(report["status"], "completed")

    async def test_build_failure_preserves_failed_provider_denominators_and_hides_exception_text(self):
        retrievers = {p: FakeRetriever(p, [], fail_build=p == "openai") for p in ("ollama", "openai")}
        report = await compare_cases(self.dataset(), "dev", 1, retrievers, {"ollama": .5, "openai": .5})
        self.assertEqual(report["summaries"]["openai"]["all"]["error_count"], 2)
        self.assertEqual(report["summaries"]["openai"]["all"]["positive_denominator"], 1)
        self.assertEqual(report["summaries"]["openai"]["all"]["not_attempted_count"], 2)
        self.assertEqual(report["summaries"]["openai"]["all"]["latency"]["count"], 0)
        self.assertIsNone(report["summaries"]["openai"]["all"]["latency"]["mean_seconds"])
        self.assertNotIn("SECRET", json.dumps(report))
        self.assertEqual(report["status"], "completed_with_errors")

    async def test_changed_model_digest_is_not_silently_accepted(self):
        retrievers = {p: FakeRetriever(p, []) for p in ("ollama", "openai")}
        first = await compare_cases(self.dataset(), "dev", 1, retrievers, {"ollama": .5, "openai": .5})
        retrievers["openai"]._index["manifest"]["embedding"]["digest"] = "changed"
        # 보고서는 당시 manifest의 복사본을 보관하므로 외부 객체 변경에 따라 바뀌지 않는다.
        expected = copy.deepcopy(first["preparation"])
        self.assertEqual(expected["openai"]["manifest"]["embedding"]["digest"], "fixed")
        second = await compare_cases(self.dataset(), "test", 1, retrievers, {"ollama": .5, "openai": .5}, expected_preparation=expected)
        self.assertEqual(second["status"], "completed_with_errors")
        self.assertEqual(second["summaries"]["openai"]["all"]["error_count"], 1)
        self.assertEqual(second["preparation"]["openai"]["manifest"]["embedding"]["digest"], "changed")

    async def test_index_changed_after_preparation_is_an_error_not_a_hit(self):
        class DriftingRetriever(FakeRetriever):
            async def search(self, query, system, audience, k):
                result = await super().search(query, system, audience, k)
                if query == "positive":
                    result["index_version"] = "different-documents-or-model"
                return result
        retrievers = {"ollama": FakeRetriever("ollama", []), "openai": DriftingRetriever("openai", [])}
        report = await compare_cases(self.dataset(), "dev", 1, retrievers, {"ollama": .5, "openai": .5})
        summary = report["summaries"]["openai"]["all"]
        self.assertEqual(summary["hit_at_3"], 0)
        self.assertEqual(summary["error_count"], 1)
        self.assertEqual(summary["not_attempted_count"], 0)
        self.assertEqual(summary["latency"]["count"], 2)
        self.assertEqual(report["status"], "completed_with_errors")
        failed = next(row for row in report["results"] if row["status"] == "error")
        self.assertEqual(failed["error_type"], "IndexChangedDuringEvaluation")

    def test_frozen_cutoff_model_and_fingerprints_are_required(self):
        config = {"ollama": {"model": "local", "min_score": .5}, "openai": {"model": "remote", "dimensions": 1536, "min_score": .45}}
        fingerprints = {"dataset_sha256": "fixed"}
        report = {"split": "dev", "status": "completed", "configuration": config,
                  "fingerprints": fingerprints, "calibration": {"recommended_openai_min_score": .45}}
        validate_dev_report(report, config, fingerprints)
        for changed in ({**fingerprints, "dataset_sha256": "changed"}, {}):
            with self.assertRaises(ValueError):
                validate_dev_report(report, config, changed)
        wrong = copy.deepcopy(config)
        wrong["openai"]["min_score"] = .5
        with self.assertRaises(ValueError):
            validate_dev_report(report, wrong, fingerprints)

    def test_cli_rejects_test_calibration_or_missing_provenance(self):
        with patch("sys.stderr"):
            for flags in (["--split", "test"], ["--split", "test", "--calibrate"],
                          ["--split", "dev", "--ollama-min-score", ".6"]):
                with self.assertRaises(SystemExit):
                    parse_args([*flags, "--output", "/tmp/not-written.json", "--openai-min-score", ".5"])

    def test_only_api_key_is_loaded_without_environment_interpolation(self):
        with patch.dict(os.environ, {}, clear=True), patch("dotenv.dotenv_values", return_value={
                "OPENAI_API_KEY": "synthetic-key", "LANGSMITH_TRACING": "true"}) as read:
            selective_openai_key()
            self.assertFalse(read.call_args.kwargs["interpolate"])
            self.assertEqual(os.environ["OPENAI_API_KEY"], "synthetic-key")
            self.assertNotIn("LANGSMITH_TRACING", os.environ)

    def test_existing_api_key_does_not_read_dotenv(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-existing-key"}, clear=True), \
                patch("dotenv.dotenv_values") as read:
            selective_openai_key()
            read.assert_not_called()

    async def test_runtime_failure_is_saved_without_secret_exception_text(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "failure.json"
            args = parse_args(["--split", "dev", "--output", str(output), "--openai-min-score", ".5"])
            with patch("evals.compare_embeddings.input_fingerprints", side_effect=RuntimeError("SECRET")), patch("builtins.print"):
                self.assertEqual(await run(args), 1)
            report = json.loads(output.read_text())
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["error_type"], "RuntimeError")
            self.assertNotIn("SECRET", output.read_text())


if __name__ == "__main__":
    unittest.main()
