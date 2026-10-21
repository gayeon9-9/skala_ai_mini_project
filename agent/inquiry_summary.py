"""6단계 — 저장된 접수를 관리자용 읽기 응답으로 구성한다.

관리자 요약을 LLM에 다시 요청하지 않는다. 접수 필드와 완료 요청의 근거를 그대로
선별하며, 실패한 새 요청에 이전 답변의 점검 결과가 최신 결과처럼 붙지 않게 한다.
여기의 completed는 '답변 저장 완료'이지 '장애 해결'이 아니다.
"""


BASE_FIELDS = ("inquiry_id", "thread_id", "selected_system", "status", "created_at", "updated_at")
REQUEST_FIELDS = ("request_id", "status", "message", "error", "created_at", "updated_at", "run_id")
TELEMETRY_FIELDS = (
    "code_revision", "code_dirty", "catalog_version", "fast_model", "quality_model", "embedding_model",
    "embedding_provider", "embedding_dimensions", "embedding_query_format",
    "inquiry_id", "request_id", "thread_id", "selected_system", "data_source", "validation_status",
    "retrieved_document_ids", "index_version", "processing_seconds", "processing_scope", "tracing_enabled",
)


def _pick(value, keys):
    return {key: value[key] for key in keys if key in value} if isinstance(value, dict) else {}


def _intake(value):
    if not isinstance(value, dict):
        return {}
    result = _pick(value, ("questions", "missing_fields", "asked_fields", "last_question_field", "source", "warnings"))
    fields = value.get("fields", {})
    result["fields"] = {key: _pick(fields[key], ("status", "value", "evidence"))
                        for key in ("symptom", "occurred_at", "error_message", "environment") if key in fields}
    result["usage"] = _pick(value.get("usage"), ("model", "tokens", "usd"))
    return result


def _retrieval(value):
    if not isinstance(value, dict):
        return {}
    result = _pick(value, ("status", "error", "index_version", "model", "min_score",
                           "provider", "dimensions", "query_format"))
    # 문서 본문은 관리자에게 필요한 근거이지만 모델 프롬프트나 원격 응답 전체는 아니다.
    result["documents"] = [_pick(doc, (
        "document_id", "chunk_id", "title", "text", "path", "audience", "systems", "version",
        "document_hash", "score")) for doc in value.get("documents", []) if isinstance(doc, dict)]
    return result


def _validation(value):
    return _pick(value, ("status", "ok", "errors", "citations", "attempts"))


def inquiry_list_item(inquiry: dict) -> dict:
    """짧은 목록용 정보. 구조화 전 문의는 '원문'으로 표시해 추출된 증상과 구분한다."""
    result = _pick(inquiry, BASE_FIELDS)
    requests = inquiry.get("requests", [])
    intake = _intake(inquiry.get("details", {}).get("intake", {}))
    symptom = intake.get("fields", {}).get("symptom", {})
    if symptom.get("status") == "provided":
        result["symptom"] = symptom.get("value") or "미정리"
    elif symptom.get("status") in ("none", "unknown"):
        result["symptom"] = {"none": "없음", "unknown": "모름"}[symptom["status"]]
    else:
        result["symptom"] = "원문: " + requests[0]["message"] if requests else "미정리"
    result["request_count"] = len(requests)
    return result


def inquiry_detail(inquiry: dict) -> dict:
    """기존 완료 결과의 출처 요청을 명시하고 원시 그래프 결과는 내보내지 않는다."""
    result = _pick(inquiry, BASE_FIELDS)
    requests = inquiry.get("requests", [])
    evidence = next((request for request in reversed(requests)
                     if request.get("status") == "completed" and isinstance(request.get("result"), dict)), None)
    saved = evidence["result"] if evidence else {}
    last_request = requests[-1] if requests else None
    # 최신 요청이 실패/진행 중이면 화면에서 이전 결과임을 반드시 안내할 수 있게 한다.
    result["evidence_request_id"] = evidence["request_id"] if evidence else None
    result["evidence_created_at"] = evidence["updated_at"] if evidence else None
    result["evidence_is_current"] = bool(evidence and last_request
                                         and evidence["request_id"] == last_request["request_id"])
    stored_details = inquiry.get("details", {})
    result["details"] = {}
    for key, formatter in (("intake", _intake), ("retrieval", _retrieval), ("validation", _validation)):
        if key in stored_details:
            result["details"][key] = formatter(stored_details[key])
    result["intake"] = _intake(saved.get("intake", stored_details.get("intake", {})))
    result["retrieval"] = _retrieval(saved.get("retrieval"))
    result["operator_retrieval"] = _retrieval(saved.get("operator_retrieval"))
    result["validation"] = _validation(saved.get("validation"))
    result["observations"] = [_pick(item["data"], (
        "system_name", "status", "cpu_percent", "memory_percent", "alerts", "last_checked"))
        for item in saved.get("collected", [])
        if item.get("source") == "monitoring" and isinstance(item.get("data"), dict)
        and item["data"].get("system_name") == inquiry.get("selected_system")]
    result["answer"] = saved.get("final")
    result["messages"] = [_pick(message, ("role", "content")) for message in inquiry.get("messages", [])]
    result["requests"] = [_pick(request, REQUEST_FIELDS) for request in requests]
    result["langsmith_run_id"] = (evidence.get("run_id") or saved.get("langsmith_run_id")) if evidence else None
    result["telemetry"] = _pick(saved.get("telemetry"), TELEMETRY_FIELDS)
    return result
