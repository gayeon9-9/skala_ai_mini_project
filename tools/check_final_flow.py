"""원본과 최종 커밋을 격리 실행한 뒤 실제 HTTP 응답을 파일로 보존한다.

compare_versions.py serve가 만든 포트/임시 DB만 사용한다. fake 생성기와 실제 MCP,
최종 버전의 실제 Ollama 검색기를 사용하므로 생성 품질 평가로 해석하지 않는다.
"""
import argparse
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compare_versions import observe, save_json


def check(manifest):
    observations, checks = {}, {}
    query = "로그인 후 화면이 넘어가지 않아요. 오류 메시지는 없어요."
    stem = "final-" + uuid4().hex[:10]
    with httpx.Client(timeout=120, trust_env=False) as client:
        for label, selected in (("before", False), ("after", True)):
            base = f"http://127.0.0.1:{manifest['ports'][label]['proxy']}"
            payload = {"message": query, "thread_id": f"{stem}-{label}", "request_id": f"{stem}-{label}-1"}
            if selected:
                payload["selected_system"] = "auth-service"
            start = time.perf_counter()
            first = observe(client, base, "POST", payload)
            first["http_elapsed_seconds"] = round(time.perf_counter() - start, 6)
            observations[label] = {"initial": first}
            checks[f"{label}_initial_200"] = first["status"] == 200
            if not selected:
                checks["original_natural_question_skips_monitoring"] = first["monitoring_targets"] == []
                continue

            metadata = first["body"]["metadata"]
            checks["selected_only_auth"] = first["monitoring_targets"] == ["auth-service"]
            checks["faq_login_retrieved"] = "faq:login" in [d["document_id"] for d in metadata["retrieval"]["documents"]]
            checks["explicit_no_error_preserved"] = metadata["intake"]["fields"]["error_message"]["status"] == "none"
            checks["validation_terminal"] = metadata["validation"]["status"] in {"passed", "fallback"}
            retry = observe(client, base, "GET", payload)
            observations[label]["replay"] = retry
            checks["replay_same_answer"] = retry["reply"] == first["reply"]
            checks["replay_no_tools"] = not retry["monitoring_targets"]
            followup = observe(client, base, "GET", {**payload, "request_id": f"{stem}-2",
                                                     "message": "오전 9시부터요. Chrome을 사용하고 있어요."})
            observations[label]["followup"] = followup
            final = next(e for e in followup["events"] if e["type"] == "FinalAnswer")
            checks["no_draft_tokens"] = not any(e["type"] == "AnswerToken" for e in followup["events"])
            fields = final["intake"]["fields"]
            checks["followup_merges_fields"] = ("로그인" in fields["symptom"]["value"]
                and "9시" in fields["occurred_at"]["value"] and "Chrome" in fields["environment"]["value"]
                and fields["error_message"]["status"] == "none")
            restored = client.get(base + "/threads/" + payload["thread_id"]).json()
            observations[label]["restored"] = restored
            checks["restored_four_messages_no_duplicates"] = len(restored["messages"]) == 4
            detail = client.get(base + "/admin/inquiries/" + final["inquiry_id"]).json()
            observations[label]["admin_detail"] = detail
            checks["admin_matches_latest_request"] = detail["evidence_is_current"] and detail["evidence_request_id"] == f"{stem}-2"
            checks["admin_operator_documents"] = bool(detail["operator_retrieval"]["documents"])
            checks["admin_no_raw_graph_prompt"] = "last_prompt" not in detail and "collected" not in detail
            none = observe(client, base, "POST", {"message": "토성의 고리와 고양이 털의 색깔을 알려줘",
                "thread_id": f"{stem}-none", "request_id": f"{stem}-none", "selected_system": "web-01"})
            observations[label]["no_match"] = none
            checks["unrelated_no_faq"] = none["body"]["metadata"]["retrieval"]["status"] == "no_match"
            invalid = client.post(base + "/chat", json={**payload, "message": "   ", "request_id": f"{stem}-invalid"})
            observations[label]["invalid"] = {"status": invalid.status_code, "body": invalid.json()}
            checks["blank_422"] = invalid.status_code == 422
    return {"manifest": manifest, "checks": checks, "passed": sum(checks.values()), "total": len(checks),
            "observations": observations,
            "conditions": "생성=fake, MCP=실제 subprocess의 샘플 데이터, 최종 검색=실제 로컬 임베딩. HTTP 시간은 1회 관찰이며 모델·실제 장애 진단 성능 비교가 아님."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = check(json.loads(args.manifest.read_text()))
    save_json(args.output, report)
    print(json.dumps({"passed": report["passed"], "total": report["total"], "checks": report["checks"]}, ensure_ascii=False))
    raise SystemExit(0 if all(report["checks"].values()) else 1)
