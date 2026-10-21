"""같은 문서·질문의 Ollama/OpenAI 검색 비교. 실제 호출은 CLI 실행 시에만 한다.

Ollama의 기존 문턱값 0.50은 고정한다. --calibrate --split dev로 OpenAI 문턱값을
추천하고, test 실행에서는 그 보고서를 반드시 읽는다. 기존 test는 이미 확인한
회귀 평가셋이며 새로운 독립 held-out이라고 표현하지 않는다.
"""
import argparse
import asyncio
import copy
import json
import math
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.evaluate import code_version, file_sha, score_result

PROVIDERS = ("ollama", "openai")
GRID = [round(0.20 + step * 0.05, 2) for step in range(13)]
WARMUP = "로그인 화면이 멈출 때 사용자가 확인할 내용"


def configuration(args):
    return {"ollama": {"model": args.ollama_model, "min_score": 0.50},
            "openai": {"model": args.openai_model, "dimensions": args.openai_dimensions,
                       "min_score": args.openai_min_score}}


def input_fingerprints(cases):
    catalog_path = ROOT / "data/knowledge_catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    return {"dataset_sha256": file_sha(cases), "catalog_sha256": file_sha(catalog_path),
            "document_sha256": {doc["path"]: file_sha(ROOT / doc["path"]) for doc in catalog["documents"]},
            "retrieval_source_sha256": {name: file_sha(ROOT / name) for name in
                ("agent/retrieval.py", "agent/embeddings.py", "evals/evaluate.py", "evals/compare_embeddings.py")}}


def validate_dev_report(report, config, fingerprints):
    """test 결과를 본 뒤 다른 문턱값/문서/모델로 바꿔 같은 평가라고 부르지 않는다."""
    if report.get("split") != "dev" or not report.get("calibration") or report.get("status") != "completed":
        raise ValueError("성공한 dev calibration 보고서가 필요합니다.")
    if report.get("fingerprints") != fingerprints:
        raise ValueError("개발 평가 이후 문서·사례·검색 코드가 달라졌습니다.")
    previous = report.get("configuration", {})
    for provider in PROVIDERS:
        current_model = {key: value for key, value in config[provider].items() if key != "min_score"}
        old_model = {key: value for key, value in previous.get(provider, {}).items() if key != "min_score"}
        if current_model != old_model:
            raise ValueError("개발 평가와 모델·차원이 다릅니다.")
    recommended = report["calibration"].get("recommended_openai_min_score")
    if recommended is None or config["openai"]["min_score"] != recommended or config["ollama"]["min_score"] != 0.50:
        raise ValueError("Ollama 0.50과 dev에서 선택한 OpenAI 문턱값을 그대로 전달해야 합니다.")


def threshold_result(row, cutoff):
    if row["status"] == "error":
        return {"status": "error", "documents": []}
    # 표시용 반올림 값이 cutoff를 넘는 것처럼 보일 수 있어 실제 검색과 같은
    # 원래 cosine 값으로 판정한다. 예전 fixture에는 표시 점수만 있을 수 있다.
    docs = [doc for doc in row.get("documents", [])[:3]
            if doc.get("raw_score", doc.get("score", -1)) >= cutoff]
    return {"status": "ok" if docs else "no_match", "documents": docs}


def score_row(row, cutoff):
    result = threshold_result(row, cutoff)
    expected = set(row["expected_document_ids"])
    ids = [doc["document_id"] for doc in result["documents"]]
    first = next((rank for rank, doc in enumerate(ids, 1) if doc in expected), None)
    cross_system = sum(not isinstance(doc.get("systems"), list) or not (
        "*" in doc["systems"] or row["system"] in doc["systems"]) for doc in result["documents"])
    cross_audience = sum(doc.get("audience") != row["audience"] for doc in result["documents"])
    return {**row, "effective_status": result["status"], "min_score": cutoff,
            "returned_document_ids": ids, **score_result(list(expected), result),
            "hit_at_1": bool(ids and ids[0] in expected) if expected else None,
            "reciprocal_rank_at_3": 1 / first if first else 0.0,
            "precision_at_3": len(set(ids) & expected) / 3,
            "irrelevant_documents": len(set(ids) - expected),
            "cross_system_documents": cross_system, "cross_audience_documents": cross_audience}


def latency(values):
    ordered = sorted(values)
    return {"count": len(values), "mean_seconds": statistics.mean(values) if values else None,
            "median_seconds": statistics.median(values) if values else None,
            "p95_seconds": ordered[math.ceil(len(ordered) * .95) - 1] if ordered else None}


def summarise(rows):
    positive = [row for row in rows if row["expected_document_ids"]]
    negative = [row for row in rows if not row["expected_document_ids"]]
    avg = lambda items, key: sum(float(item[key]) for item in items) / len(items) if items else None
    return {"executions": len(rows), "unique_checks": len({(r["case_id"], r["check_index"]) for r in rows}),
            "positive_denominator": len(positive), "no_match_denominator": len(negative),
            "hit_at_1": avg(positive, "hit_at_1"), "hit_at_3": avg(positive, "hit"),
            "recall_at_3": avg(positive, "recall"), "precision_at_3": avg(positive, "precision_at_3"),
            "mean_reciprocal_rank_at_3": avg(positive, "reciprocal_rank_at_3"),
            "no_match_correct_rate": avg(negative, "no_match_correct"),
            "error_count": sum(r["status"] == "error" for r in rows),
            "not_attempted_count": sum(r["elapsed_seconds"] is None for r in rows),
            "irrelevant_document_count": sum(r["irrelevant_documents"] for r in rows),
            "cross_system_document_count": sum(r["cross_system_documents"] for r in rows),
            "cross_audience_document_count": sum(r["cross_audience_documents"] for r in rows),
            "latency": latency([r["elapsed_seconds"] for r in rows if r["elapsed_seconds"] is not None]),
            "successful_latency": latency([r["elapsed_seconds"] for r in rows
                                            if r["status"] != "error" and r["elapsed_seconds"] is not None])}


def calibrate_openai(rows):
    curve = []
    for cutoff in GRID:
        summary = summarise([score_row(row, cutoff) for row in rows])
        parts = [summary[key] for key in ("hit_at_3", "no_match_correct_rate") if summary[key] is not None]
        curve.append({"min_score": cutoff, "balanced_score": statistics.mean(parts) if parts else None,
                      "summary": summary})
    eligible = [point for point in curve if point["balanced_score"] is not None]
    best = max(eligible, key=lambda point: (
        point["balanced_score"], -point["summary"]["irrelevant_document_count"],
        point["summary"]["precision_at_3"] or 0, -point["min_score"])) if eligible else None
    return {"recommended_openai_min_score": best["min_score"] if best else None,
            "ollama_min_score": 0.50, "curve": curve,
            "selection_rule": "dev Hit@3와 no-match 처리율의 평균 최대화; 동점은 오답 문서 수 최소, Precision@3 최대, 낮은 문턱값 순. 설정 파일은 수정하지 않음."}


def usage_snapshot(retriever):
    method = getattr(getattr(retriever, "embedder", None), "usage_snapshot", None)
    snapshot = method() if method else {}
    return {key: value for key, value in snapshot.items() if isinstance(value, (int, float)) and not isinstance(value, bool)}


def usage_delta(before, after):
    return {key: after.get(key, 0) - before.get(key, 0) for key in set(before) | set(after)}


async def safe_search(retriever, query, system, audience, expected_index=None):
    started = time.perf_counter()
    try:
        raw = await retriever.search(query, system, audience=audience, k=3)
        # 예외/HTTP 본문에는 키가 섞일 수 있으므로 오류 분류만 보존한다.
        result = {"status": raw["status"], "documents": [{key: doc[key] for key in
                  ("document_id", "chunk_id", "score", "raw_score", "audience", "systems") if key in doc}
                  for doc in raw.get("documents", [])[:3]], "index_version": raw.get("index_version")}
        if raw["status"] == "error":
            result["error_type"] = "RetrievalError"
        elif expected_index is not None and raw.get("index_version") != expected_index:
            # 검색기는 호출마다 문서/모델 변경을 확인한다. 평가 도중 바뀐 벡터 공간은
            # 준비한 색인과 다른 실험이므로 좋은 점수가 나와도 성공으로 집계하지 않는다.
            result.update(status="error", documents=[], error_type="IndexChangedDuringEvaluation")
    except Exception as exc:
        result = {"status": "error", "documents": [], "error_type": type(exc).__name__}
    return result, time.perf_counter() - started


async def compare_cases(dataset, split, repetitions, retrievers, cutoffs, *, calibrate=False,
                        expected_preparation=None):
    """DI 가능한 평가 실행. build/warmup을 점수·지연 표본에서 제외한다."""
    if split not in {"dev", "test"} or repetitions < 1 or (calibrate and split != "dev"):
        raise ValueError("calibrate는 dev에서만 실행하고 반복은 1 이상이어야 합니다.")
    selected = [case for case in dataset["cases"] if case["split"] == split]
    checks = [(case, index, check) for case in selected for index, check in enumerate(case.get("retrieval_checks", []))]
    if not checks:
        raise ValueError("검색 평가 사례가 없습니다.")
    preparation, warmups, usage, rows, failed = {}, {}, {}, [], set()
    for provider in PROVIDERS:
        retriever = retrievers[provider]
        before = usage_snapshot(retriever)
        started = time.perf_counter()
        try:
            index = await retriever.build_index()
            preparation[provider] = {"status": "ok", "index": copy.deepcopy(index)}
            cached = getattr(retriever, "_index", None) or {}
            preparation[provider].update({"manifest": copy.deepcopy(cached.get("manifest", {})),
                "query_format": getattr(getattr(retriever, "embedder", None), "query_format", None),
                "actual_dimensions": len(cached["vectors"][0]) if cached.get("vectors") else None})
            if expected_preparation and any(preparation[provider].get(key) != expected_preparation[provider].get(key)
                    for key in ("manifest", "query_format", "actual_dimensions")):
                raise ValueError("CalibrationEnvironmentChanged")
        except Exception as exc:
            failed.add(provider)
            preparation.setdefault(provider, {}).update(status="error", error_type=type(exc).__name__)
        preparation[provider]["elapsed_seconds"] = time.perf_counter() - started
        after_build = usage_snapshot(retriever)
        if provider not in failed:
            warm, elapsed = await safe_search(retriever, WARMUP, "auth-service", "user", index["index_version"])
            warmups[provider] = {**warm, "elapsed_seconds": elapsed, "scored": False}
        else:
            warmups[provider] = {"status": "skipped", "reason": "index_build_failed", "scored": False}
        after_warmup = usage_snapshot(retriever)
        usage[provider] = {"index_build": usage_delta(before, after_build),
                           "warmup": usage_delta(after_build, after_warmup), "timed_start": after_warmup}
    # 짝수/홀수 순서를 번갈아 네트워크·로컬 모델 실행 순서 편향을 줄인다.
    pair = 0
    for repetition in range(1, repetitions + 1):
        for check_position, (case, index, check) in enumerate(checks):
            # 질문 수가 짝수여도 같은 질문의 선행 provider를 다음 반복에서 바꾼다.
            order = PROVIDERS if (check_position + repetition - 1) % 2 == 0 else tuple(reversed(PROVIDERS))
            pair += 1
            for position, provider in enumerate(order):
                if provider in failed:
                    # 실제 요청하지 않은 행을 0초 성능으로 오해하지 않도록 null로 보존한다.
                    # 품질 점수의 실패 분모에서는 제외하지 않는다.
                    raw, elapsed = {"status": "error", "documents": [], "error_type": "IndexBuildFailed"}, None
                else:
                    raw, elapsed = await safe_search(retrievers[provider], check["query"], case["selected_system"], check["audience"],
                                                    preparation[provider]["index"]["index_version"])
                row = {"provider": provider, "case_id": case["id"], "check_index": index,
                       "repetition": repetition, "pair_index": pair, "provider_position": position,
                       "query": check["query"], "system": case["selected_system"], "audience": check["audience"],
                       "expected_document_ids": check["relevant_document_ids"], "elapsed_seconds": elapsed, **raw}
                rows.append(score_row(row, cutoffs[provider]))
    for provider in PROVIDERS:
        usage[provider]["timed_queries"] = usage_delta(usage[provider].pop("timed_start"), usage_snapshot(retrievers[provider]))
    summaries = {provider: {"all": summarise([row for row in rows if row["provider"] == provider]),
                  **{audience: summarise([row for row in rows if row["provider"] == provider and row["audience"] == audience])
                     for audience in ("user", "operator")}} for provider in PROVIDERS}
    return {"split": split, "selected_case_count": len(selected), "checks_per_provider": len(checks),
            "repetitions": repetitions, "preparation": preparation, "warmup": warmups,
            "planned_embedding_requests": {provider: {"document_build_batches": 1, "warmup_queries": 1,
                "timed_queries": len(checks) * repetitions} for provider in PROVIDERS},
            "usage": usage, "results": rows, "summaries": summaries,
            "calibration": calibrate_openai([row for row in rows if row["provider"] == "openai"]) if calibrate else None,
            "status": "completed" if not any(row["status"] == "error" for row in rows) else "completed_with_errors"}


def selective_openai_key():
    if not os.environ.get("OPENAI_API_KEY"):
        from dotenv import dotenv_values
        key = dotenv_values(ROOT / ".env", interpolate=False).get("OPENAI_API_KEY")
        if key:
            os.environ["OPENAI_API_KEY"] = key


async def run(args):
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "schema_version": 1,
              "split": args.split, "configuration": configuration(args)}
    retrievers = {}
    try:
        dataset = json.loads(args.cases.read_text(encoding="utf-8"))
        fingerprints = input_fingerprints(args.cases)
        report.update({"fingerprints": fingerprints, "dataset_version": dataset.get("dataset_version"),
                       "code": code_version()})
        if args.split == "test":
            dev = json.loads(args.dev_report.read_text(encoding="utf-8"))
            validate_dev_report(dev, report["configuration"], fingerprints)
            report["calibration_provenance"] = {"dev_report_sha256": file_sha(args.dev_report),
                "dev_created_at": dev.get("created_at"), "openai_min_score": args.openai_min_score}
        selective_openai_key()
        from agent.embeddings import create_embedder
        from agent.retrieval import KnowledgeRetriever
        # 매 실행마다 별도 빈 색인 폴더를 사용한다. 기존 서비스 캐시/모델을 삭제하지 않는다.
        with tempfile.TemporaryDirectory(prefix="helpdesk-embedding-comparison-") as cache:
            for provider in PROVIDERS:
                embedder = create_embedder(provider=provider,
                    model=args.ollama_model if provider == "ollama" else args.openai_model,
                    base_url=args.ollama_base_url if provider == "ollama" else None,
                    dimensions=args.openai_dimensions if provider == "openai" else None)
                cutoff = report["configuration"][provider]["min_score"]
                retrievers[provider] = KnowledgeRetriever(embedder=embedder,
                    min_score=0 if args.calibrate else cutoff, cache_dir=Path(cache) / provider,
                    include_raw_scores=True)
            repetitions = 1 if args.calibrate else args.repetitions
            report.update(await compare_cases(dataset, args.split, repetitions, retrievers,
                          {key: value["min_score"] for key, value in report["configuration"].items()}, calibrate=args.calibrate,
                          expected_preparation=dev["preparation"] if args.split == "test" else None))
            report["scope"] = {
                "dataset": "기존에 확인한 test의 회귀 비교이며 새로운 독립 held-out 평가가 아님.",
                "fairness": "동일 문서/질문/system/audience/top3. Ollama cutoff0.50고정, OpenAI dev추천고정. provider 권장 query formatting 차이는 manifest로 공개.",
                "precision": "정답 있는 질문만 평균, Precision@3 분모는 반환 개수와 무관하게 3. MRR은 상위3개 안에서만 계산. 오답 문서 수는 제공 정답 집합 밖의 문서 수이며 미표기 문서의 실제 무관함까지 확정하지 않음.",
                "errors": "오류를 품질 분모에 포함. latency는 실제 요청의 오류 포함 전체와 성공 표본을 별도 제시. 색인 실패로 요청하지 못한 행은 null이며 지연 표본에서 제외. p95는 nearest-rank.",
                "latency": "색인 생성과 warmup 제외, 쿼리 동시성1·provider교대. 모델 unload/OS캐시통제 없이 실행하므로 cold start 비교 아님.",
                "usage": "API가 보고한 토큰/호출만 집계. Ollama identity 조회·HTTP 재시도와 임베딩 요청 수는 구분. 요금 계산은 포함하지 않음.",
                "limits": "검색 단계 비교이며 LLM 답변 품질·실제 장애 진단 정확도·사용자 전체 대기시간 개선을 뜻하지 않음."}
    except Exception as exc:
        report.update({"status": "failed", "error_type": type(exc).__name__})
    finally:
        for provider, retriever in retrievers.items():
            try:
                await retriever.aclose()
            except Exception as exc:
                report.setdefault("cleanup_errors", {})[provider] = type(exc).__name__
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report.get("status"), "output": str(args.output),
                      "suggested_openai_min_score": (report.get("calibration") or {}).get("recommended_openai_min_score")}, ensure_ascii=False))
    return 0 if report.get("status") == "completed" else 1


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("dev", "test"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cases", type=Path, default=ROOT / "evals/cases.json")
    parser.add_argument("--calibrate", action="store_true")
    parser.add_argument("--dev-report", type=Path)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--ollama-model", default="qwen3-embedding:0.6b")
    parser.add_argument("--ollama-base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--ollama-min-score", type=float, default=.50)
    parser.add_argument("--openai-model", default="text-embedding-3-small")
    parser.add_argument("--openai-dimensions", type=int, default=1536)
    parser.add_argument("--openai-min-score", type=float, required=True)
    args = parser.parse_args(argv)
    if args.ollama_min_score != .50 or not 0 <= args.openai_min_score <= 1 or args.repetitions not in range(1, 4):
        parser.error("Ollama 문턱값은 0.50 고정, OpenAI는 0~1, 반복은 1~3입니다.")
    if args.openai_dimensions <= 0 or (args.calibrate and args.split != "dev") or (args.split == "test" and not args.dev_report):
        parser.error("양수 차원이 필요하며 calibration은 dev 전용, test는 --dev-report가 필요합니다.")
    return args


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
