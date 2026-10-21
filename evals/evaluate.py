"""고정 사례로 실제 검색과 기존 Runbook 규칙을 비교하는 재현 가능한 평가 도구.

예: .venv/bin/python evals/evaluate.py --split test --output /tmp/rag-test.json
최종 확인용 질문으로 문턱값을 맞추지 않는다. 이 도구는 검색기를 평가하며,
LLM 답변 품질·실제 장애 원인 판정·전체 API 응답 시간을 측정하지 않는다.
"""

import argparse
import asyncio
import hashlib
import json
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.graph import _classify_incident_type
from agent.retrieval import KnowledgeRetriever, OllamaEmbedder


def legacy_keyword_baseline(query: str, audience: str) -> dict:
    """원본의 알림 분류 규칙에 동일 검색문을 합성 알림으로 전달하는 비교용 어댑터.

원본은 실측 알림을 입력받고 문서 한 개만 선택했다. 여기서는 검색 방식 차이를
비교하려고 양쪽에 같은 문장을 주므로, 이 결과를 원본 서비스 전체 성능으로 해석하면 안 된다.
"""
    if audience != "operator":
        return {"status": "not_applicable", "documents": [],
                "reason": "원본에는 사용자 FAQ 검색이 없으므로 직접 비교하지 않습니다."}
    incident = _classify_incident_type([
        {"source": "monitoring", "data": {"status": "", "alerts": [query]}}
    ]) or "db-down"
    return {"status": "ok", "documents": [{"document_id": f"runbook:{incident}"}]}


def score_result(expected: list[str], result: dict) -> dict:
    """검색 오류를 분모에서 빼서 점수가 부풀지 않도록 실패로 포함한다."""
    if result["status"] == "not_applicable":
        return {"hit": None, "recall": None, "no_match_correct": None}
    actual = {d["document_id"] for d in result.get("documents", [])[:3]}
    expected = set(expected)
    if expected:
        matched = actual & expected if result["status"] == "ok" else set()
        return {"hit": bool(matched), "recall": len(matched) / len(expected),
                "no_match_correct": None}
    return {"hit": None, "recall": None,
            "no_match_correct": result["status"] == "no_match" and not actual}


def summarise(rows: list[dict]) -> dict:
    applicable = [r for r in rows if r["status"] != "not_applicable"]
    positive = [r for r in applicable if r["expected_document_ids"]]
    negative = [r for r in applicable if not r["expected_document_ids"]]
    durations = [r["elapsed_seconds"] for r in applicable]
    return {
        "executions": len(applicable),
        "unique_checks": len({(r["case_id"], r["check_index"]) for r in applicable}),
        "not_applicable_executions": len(rows) - len(applicable),
        "positive_denominator": len(positive),
        "hit_count": sum(bool(r["hit"]) for r in positive),
        "hit_at_3": sum(bool(r["hit"]) for r in positive) / len(positive) if positive else None,
        "recall_at_3": sum(r["recall"] for r in positive) / len(positive) if positive else None,
        "no_match_denominator": len(negative),
        "no_match_correct_count": sum(bool(r["no_match_correct"]) for r in negative),
        "no_match_correct_rate": (sum(bool(r["no_match_correct"]) for r in negative) / len(negative)
                                  if negative else None),
        "error_count": sum(r["status"] == "error" for r in applicable),
        "latency": {"count": len(durations),
                    "mean_seconds": statistics.mean(durations) if durations else None,
                    "median_seconds": statistics.median(durations) if durations else None},
    }


async def evaluate_cases(dataset: dict, split: str, repetitions: int, retriever) -> dict:
    """네트워크 DI가 가능하도록 실행·집계를 분리한다. 테스트는 고정 검색 대역을 넣는다."""
    if split not in {"dev", "test"} or repetitions < 1:
        raise ValueError("split은 dev/test, repetitions는 1 이상이어야 합니다.")
    selected = [case for case in dataset["cases"] if case["split"] == split]
    if not selected:
        raise ValueError("선택한 split에 평가 사례가 없습니다.")
    started = time.perf_counter()
    index = await retriever.build_index()
    preparation_seconds = time.perf_counter() - started
    rows = []
    for repetition in range(1, repetitions + 1):
        for case in selected:
            for check_index, check in enumerate(case.get("retrieval_checks", [])):
                for method in ("semantic", "legacy_keyword_adapter"):
                    start = time.perf_counter()
                    if method == "semantic":
                        result = await retriever.search(check["query"], case["selected_system"],
                                                        audience=check["audience"], k=3)
                    else:
                        result = legacy_keyword_baseline(check["query"], check["audience"])
                    elapsed = time.perf_counter() - start
                    row = {
                        "case_id": case["id"], "check_index": check_index,
                        "repetition": repetition, "method": method,
                        "system": case["selected_system"], "audience": check["audience"],
                        "query": check["query"],
                        "expected_document_ids": check["relevant_document_ids"],
                        "status": result["status"],
                        "documents": [{key: doc[key] for key in ("document_id", "chunk_id", "score")
                                       if key in doc} for doc in result.get("documents", [])],
                        "elapsed_seconds": elapsed,
                        **score_result(check["relevant_document_ids"], result),
                    }
                    if result.get("error"):
                        row["error"] = result["error"]
                    if result.get("reason"):
                        row["reason"] = result["reason"]
                    rows.append(row)
    return {
        "split": split, "selected_case_count": len(selected), "repetitions": repetitions,
        "index": index, "index_preparation_seconds": preparation_seconds,
        "summaries": {
            "semantic_user_faq": summarise([r for r in rows if r["method"] == "semantic"
                                              and r["audience"] == "user"]),
            "semantic_operator_runbook": summarise([r for r in rows if r["method"] == "semantic"
                                                     and r["audience"] == "operator"]),
            "legacy_operator_runbook": summarise([r for r in rows if r["method"] == "legacy_keyword_adapter"
                                                   and r["audience"] == "operator"]),
            "legacy_user_faq_not_applicable": summarise([r for r in rows if r["method"] == "legacy_keyword_adapter"
                                                          and r["audience"] == "user"]),
        },
        "results": rows,
    }


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def code_version() -> dict:
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, timeout=5).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT,
                                            text=True, timeout=5).strip())
    except (OSError, subprocess.SubprocessError):
        sha, dirty = None, None
    return {"git_sha": sha, "git_dirty": dirty,
            "source_file_sha256": {name: file_sha(ROOT / name) for name in
                                   ("agent/retrieval.py", "agent/graph.py", "evals/evaluate.py")}}


async def run(args) -> int:
    dataset = json.loads(args.cases.read_text(encoding="utf-8"))
    retriever = KnowledgeRetriever(
        embedder=OllamaEmbedder(model=args.model, base_url=args.base_url),
        cache_dir=args.cache_dir, min_score=args.min_score,
    )
    try:
        health = await retriever.health()
        if health["status"] != "ok":
            print(json.dumps(health, ensure_ascii=False))
            return 1
        report = await evaluate_cases(dataset, args.split, args.repetitions, retriever)
        catalog = json.loads((ROOT / "data/knowledge_catalog.json").read_text(encoding="utf-8"))
        report.update({
            "created_at": datetime.now(timezone.utc).isoformat(),
            "schema_version": 1, "dataset_version": dataset.get("dataset_version"),
            "dataset_file_sha256": file_sha(args.cases), "code": code_version(),
            "model": args.model, "embedding": health["embedding"], "min_score": args.min_score,
            "index_manifest_sha256": report["index"]["index_version"], "k": 3,
            "operator_document_ids": sorted(d["document_id"] for d in catalog["documents"]
                                            if d["audience"] == "operator"),
            "scope": {
                "measurement": "검색 단계만 측정. 실제 LLM 답변 정확도·실제 장애 진단 성능·전체 API 지연이 아님.",
                "baseline": "동일 검색문을 합성 monitoring alert로 넣는 기존 키워드 분류 어댑터. 미매칭 시 db-down. 결과는 최대 1개. 원본 end-to-end 재현이 아님.",
                "fair_comparison": "운영자 Runbook은 같은 기존 6개 문서 집합에서 비교. 사용자 FAQ는 신규 문서이므로 원본 대비 정확도 증가로 합산하지 않음.",
                "latency": "순차 요청, 동시성 1. build_index를 먼저 호출한 뒤 검색별 시간을 측정. 색인 준비 시간 별도. 모델을 내리지 않으므로 cold start 통제 실험이 아니며 첫 질의는 추가 로드 비용을 포함할 수 있음.",
                "repetitions": "지표 분모는 반복 실행 수를 포함하고 unique_checks는 고유 질문 수. 작은 합성 표본 결과이며 일반 운영 성능으로 확대 해석하지 않음.",
            },
        })
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"output": str(args.output), "split": args.split,
                          "summaries": report["summaries"]}, ensure_ascii=False, indent=2))
        return 0 if not any(r["status"] == "error" for r in report["results"]) else 1
    finally:
        await retriever.aclose()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="합성 고정 질문의 실제 검색 품질 평가")
    parser.add_argument("--split", choices=("dev", "test"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--cases", type=Path, default=ROOT / "evals/cases.json")
    parser.add_argument("--model", default="qwen3-embedding:0.6b")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--min-score", type=float, default=0.50)
    parser.add_argument("--cache-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.repetitions < 1:
        parser.error("--repetitions는 1 이상이어야 합니다.")
    return args


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
