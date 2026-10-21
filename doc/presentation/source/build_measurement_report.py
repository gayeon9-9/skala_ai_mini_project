"""저장된 검색 평가 JSON을 읽어 발표용 정적 보고서를 만든다.

외부 API나 실험을 다시 실행하지 않는다. 원시 결과 행으로 분모·회수율·지연을
다시 계산하고 저장된 요약과 일치하는지 확인하여, 서로 다른 두 비교가 섞이지
않도록 독립된 HTML section으로 출력한다.
"""
from __future__ import annotations

import html
import json
import math
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[3]
BASELINE_PATH = Path("doc/evidence/final/rag_test.json")
EMBEDDING_PATH = Path("doc/evidence/embedding_comparison/test.json")
OUTPUT_PATH = Path("doc/evidence/final_openai/measurement_report.html")


def load(relative: Path) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def metric(rows: list[dict]) -> dict:
    """저장된 판정 수치 대신 실제 정답·검색 결과 ID로 Hit@k를 재계산한다."""
    applicable = [row for row in rows if row["status"] != "not_applicable"]
    positives = [row for row in applicable if row["expected_document_ids"]]
    negatives = [row for row in applicable if not row["expected_document_ids"]]

    def found(row: dict, k: int) -> bool:
        expected = set(row["expected_document_ids"])
        returned = {item["document_id"] for item in row["documents"][:k]}
        return bool(expected & returned)

    hits = sum(found(row, 3) for row in positives)
    first_hits = sum(found(row, 1) for row in positives)
    no_matches = sum(row["status"] == "no_match" and not row["documents"] for row in negatives)
    elapsed = [row["elapsed_seconds"] for row in applicable if row["elapsed_seconds"] is not None]
    ordered = sorted(elapsed)
    unique = {(row["case_id"], row["check_index"]) for row in applicable}
    positive_unique = {(row["case_id"], row["check_index"]) for row in positives}
    negative_unique = {(row["case_id"], row["check_index"]) for row in negatives}
    return {
        "executions": len(applicable), "unique": len(unique),
        "positive": len(positives), "positive_unique": len(positive_unique),
        "negative": len(negatives), "negative_unique": len(negative_unique),
        "hits": hits, "first_hits": first_hits,
        "rate": hits / len(positives) if positives else None,
        "no_matches": no_matches,
        "no_match_rate": no_matches / len(negatives) if negatives else None,
        "errors": sum(row["status"] == "error" for row in applicable),
        "mean": mean(elapsed) if elapsed else None,
        "median": median(elapsed) if elapsed else None,
        # 평가 원본과 같은 nearest-rank 정의를 적용한다.
        "p95": ordered[math.ceil(len(ordered) * .95) - 1] if ordered else None,
    }


def verify(calculated: dict, summary: dict) -> None:
    pairs = {
        "executions": "executions", "positive": "positive_denominator",
        "negative": "no_match_denominator", "rate": "hit_at_3",
        "no_match_rate": "no_match_correct_rate", "errors": "error_count",
    }
    for left, right in pairs.items():
        assert calculated[left] == summary[right], (left, calculated[left], summary[right])
    for name, key in [("mean", "mean_seconds"), ("median", "median_seconds"), ("p95", "p95_seconds")]:
        if key in summary["latency"]:
            assert math.isclose(calculated[name], summary["latency"][key], abs_tol=1e-12)


def ratio(count: int, denominator: int) -> str:
    return f"{count}/{denominator}"


def hit(value: dict) -> str:
    return f'<strong>{value["rate"]:.0%}</strong><span class="fraction">{ratio(value["hits"], value["positive"])}회</span>'


def row(label: str, left: str, right: str, emphasis: bool = False) -> str:
    return f'<tr{chr(32) + "class=highlight" if emphasis else ""}><th scope="row">{label}</th><td>{left}</td><td>{right}</td></tr>'


def measured_at(report: dict) -> str:
    return datetime.fromisoformat(report["created_at"]).astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M KST")


def source_footer(report: dict, path: Path) -> str:
    return (
        '<footer><span>원시 자료</span> '
        f'<code>{html.escape(str(path))}</code><br>'
        f'실측 시점 {measured_at(report)} · 코드 {report["code"]["git_sha"][:7]}'
        '</footer>'
    )


def main() -> None:
    baseline = load(BASELINE_PATH)
    embedding = load(EMBEDDING_PATH)
    operator = [row for row in baseline["results"] if row["audience"] == "operator"]
    old = metric([row for row in operator if row["method"] == "legacy_keyword_adapter"])
    rag = metric([row for row in operator if row["method"] == "semantic"])
    verify(old, baseline["summaries"]["legacy_operator_runbook"])
    verify(rag, baseline["summaries"]["semantic_operator_runbook"])
    providers = {}
    for provider in ["ollama", "openai"]:
        values = metric([row for row in embedding["results"] if row["provider"] == provider])
        verify(values, embedding["summaries"][provider]["all"])
        providers[provider] = values
    ollama, openai = providers["ollama"], providers["openai"]

    baseline_rows = "\n".join([
        row("정답 문서 포함 비율", hit(old), hit(rag), True),
        row("검색 후보 수", "최대 1개", f'상위 {baseline["k"]}개'),
        row("근거 없는 질의 구분", f'{old["no_matches"]}/{old["negative"]} · 기본 문서 반환',
            f'{rag["no_matches"]}/{rag["negative"]} · no_match'),
    ])
    embedding_rows = "\n".join([
        row("Hit@3 · 상위 3개 정답 포함", hit(ollama), hit(openai), True),
        row("Hit@1 · 첫 결과 정답 포함",
            f'{ollama["first_hits"] / ollama["positive"]:.0%} · {ratio(ollama["first_hits"], ollama["positive"])}회',
            f'{openai["first_hits"] / openai["positive"]:.0%} · {ratio(openai["first_hits"], openai["positive"])}회'),
        row("근거 없는 질의 · no_match",
            f'{ratio(ollama["no_matches"], ollama["negative"])}회',
            f'{ratio(openai["no_matches"], openai["negative"])}회'),
        row("검색 시간 · 평균", f'{ollama["mean"] * 1000:.1f} ms', f'{openai["mean"] * 1000:.1f} ms', True),
        row("검색 시간 · 중앙값", f'{ollama["median"] * 1000:.1f} ms', f'{openai["median"] * 1000:.1f} ms'),
        row("검색 시간 · p95", f'{ollama["p95"] * 1000:.1f} ms', f'{openai["p95"] * 1000:.1f} ms'),
        row("API·검색 오류", f'{ollama["errors"]}건', f'{openai["errors"]}건'),
    ])
    configuration = embedding["configuration"]
    report = f'''<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>실측 결과 보고서 · 저장된 JSON 기반</title>
<style>
* {{ box-sizing:border-box; }}
body {{ margin:0; color:#142138; background:#f2f5f9; font-family:"Apple SD Gothic Neo","Noto Sans KR",sans-serif; font-size:24px; line-height:1.4; }}
main {{ max-width:1200px; margin:0 auto; }}
section {{ background:white; padding:34px 42px 28px; margin:0 0 30px; scroll-margin-top:0; }}
.report-label {{ margin:0 0 10px; font-size:20px; font-weight:700; color:#255db8; letter-spacing:.01em; }}
h1,h2 {{ font-size:38px; line-height:1.23; letter-spacing:-.035em; margin:0 0 15px; }}
.intro {{ margin:0 0 21px; font-size:24px; }}
table {{ width:100%; border-collapse:collapse; margin:12px 0 18px; table-layout:fixed; }}
thead th {{ font-size:23px; color:#1c4f9d; background:#f0f5fd; border-top:2px solid #2b65bc; padding:15px 10px; }}
thead th:first-child {{ width:42%; text-align:left; }}
td,tbody th {{ padding:12px 10px; border-bottom:1px solid #dae1ec; text-align:center; vertical-align:middle; }}
tbody th {{ text-align:left; font-size:23px; font-weight:600; }}
td {{ font-size:25px; font-variant-numeric:tabular-nums; }}
.highlight td {{ color:#194f9e; font-weight:700; }}
td strong {{ font-size:40px; font-weight:750; }}
.fraction {{ display:block; font-size:22px; line-height:1.2; color:#34465e; font-weight:500; }}
.conditions {{ font-size:22px; color:#34465e; margin:14px 0; }}
.conditions p {{ margin:7px 0; }}
.caution {{ border-left:4px solid #2d63b8; padding:0 0 0 16px; margin:19px 0 17px; font-size:22px; }}
.caution p {{ margin:6px 0; }}
.finding {{ font-size:24px; font-weight:700; color:#194f9e; margin:17px 0; }}
footer {{ font-size:18px; line-height:1.55; color:#53627a; padding-top:13px; border-top:1px solid #dbe3ee; }}
footer span {{ font-weight:700; }}
code {{ font-family:ui-monospace,SFMono-Regular,Consolas,monospace; font-size:17px; overflow-wrap:anywhere; }}
.scope {{ font-size:21px; margin:10px 0 15px; color:#34465e; }}
@media(max-width:760px) {{ body {{font-size:20px;}} section {{padding:25px 20px;}} h1,h2 {{font-size:30px;}} thead th,tbody th,td {{font-size:19px;}} }}
@media print {{ body {{background:white;}} section {{break-after:page; margin:0;}} }}
</style>
</head>
<body><main>
<section id="baseline-rag" aria-labelledby="baseline-title">
<p class="report-label">실측 결과 보고서 · 저장된 JSON 기반</p>
<h1 id="baseline-title">기존 규칙과 RAG의 문서 검색 비교</h1>
<p class="intro">같은 운영 Runbook에서 정답 문서가 있는 질의 <b>{rag["positive_unique"]}개</b>를 비교했다.</p>
<table aria-label="기존 규칙과 의미 검색 결과">
<thead><tr><th>지표</th><th>기존 규칙 어댑터</th><th>Ollama 의미 검색</th></tr></thead>
<tbody>{baseline_rows}</tbody></table>
<div class="conditions">
<p><b>조건</b> 기존 Runbook {len(baseline["operator_document_ids"])}개, 정답 질의 {rag["positive_unique"]}개 + 근거 없는 질의 {rag["negative_unique"]}개, 각 {baseline["repetitions"]}회.</p>
<p><b>검색</b> 실제 {html.escape(baseline["model"])} 임베딩, {baseline["embedding"]["dimensions"]:,}차원, 문턱값 {baseline["min_score"]:.2f}.</p>
</div>
<div class="caution">
<p><b>기존 규칙 어댑터:</b> 같은 검색문을 합성 모니터링 알림으로 넣어 규칙을 적용했다. 원본 서비스 전체의 응답·진단 성능 비교가 아니다.</p>
<p>FAQ는 새 기능이므로 이 전후 비율에 합산하지 않았다. 소수 합성 질의의 문서 회수 결과이며, 모든 검색 후보나 실제 답변이 정확하다는 뜻은 아니다.</p>
</div>
<p class="scope">이 표는 실제 임베딩의 <b>검색 단계</b>만 측정했다. fake 생성 모델의 화면 시연 및 실제 Qwen 답변 생성 확인과 별도다.</p>
{source_footer(baseline, BASELINE_PATH)}
</section>

<section id="embedding-comparison" aria-labelledby="embedding-title">
<p class="report-label">실측 결과 보고서 · 저장된 JSON 기반</p>
<h2 id="embedding-title">Ollama와 OpenAI 임베딩 비교</h2>
<p class="intro">동일 문서·고유 검색 질의 <b>{ollama["unique"]}개 × {embedding["repetitions"]}회</b>를 공급자별로 실행했다.</p>
<table aria-label="임베딩 공급자별 검색 품질과 지연">
<thead><tr><th>지표</th><th>Ollama Qwen</th><th>OpenAI small</th></tr></thead>
<tbody>{embedding_rows}</tbody></table>
<div class="conditions">
<p><b>분모</b> 정답 질의 {ollama["positive_unique"]}개 × {embedding["repetitions"]}회 = {ollama["positive"]}회. 근거 없는 질의 {ollama["negative_unique"]}개 × {embedding["repetitions"]}회 = {ollama["negative"]}회.</p>
<p><b>모델·기준</b> {html.escape(configuration["ollama"]["model"])} ({configuration["ollama"]["min_score"]:.2f}) / {html.escape(configuration["openai"]["model"])} ({configuration["openai"]["min_score"]:.2f}).</p>
<p><b>시간</b> 검색 {ollama["executions"]}회씩, 동시성 1·공급자 순서 교대. 색인 생성·준비 질의 제외. p95는 nearest-rank.</p>
</div>
<p class="finding">이 표본에서는 OpenAI의 정답 문서 발견률·검색 속도 우위를 확인하지 못했다.</p>
<div class="caution">
<p>기존에 확인한 합성 질의의 회귀 비교다. 최초 실행·OS 캐시를 통제한 실험이 아니며, 검색 시간은 전체 사용자 대기시간이 아니다.</p>
<p>두 공급자 모두 실제 임베딩을 호출했다. 최종 답변 생성은 이 비교에서 실행하지 않았다. fake 시연 결과나 실제 장애 진단 정확도와 구분한다.</p>
</div>
{source_footer(embedding, EMBEDDING_PATH)}
</section>
</main></body></html>'''
    target = ROOT / OUTPUT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(report, encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT_PATH), "source_summaries_match": True,
                      "baseline": {"legacy": old["hits"], "rag": rag["hits"], "denominator": rag["positive"]},
                      "providers": {key: {"hit": value["hits"], "denominator": value["positive"],
                                           "mean_ms": round(value["mean"] * 1000, 1)}
                                    for key, value in providers.items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
