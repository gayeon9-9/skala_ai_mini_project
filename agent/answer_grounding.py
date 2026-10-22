"""문서의 오류 예시를 사용자에게 실제로 표시된 오류와 구분한다.

HTTP 4xx/5xx의 명백한 코드 불일치를 검사하는 작은 규칙이다. 자연어 전체의
의미나 원인을 판정하지 않으며, 검색 점수나 문서에 등장한다는 이유만으로
사용자가 그 오류를 경험했다고 간주하지 않는다.
"""

import re


def http_error_codes(text: str) -> set[str]:
    # 문서 ID·URL·버전·긴 숫자를 HTTP 코드로 잘못 읽지 않는다. 한국어 조사가
    # 붙은 '504가'는 인식하고, '500ms', '500건', 포트 500 같은 수량은 제외한다.
    text = re.sub(r"\[[A-Za-z][A-Za-z0-9_-]*:[A-Za-z0-9:_-]+\]|https?://\S+", "", text)
    text = re.sub(r"[*`]", "", text)
    pattern = r"(?<![A-Za-z0-9_.:/_-])(?:HTTP(?:/\d\.\d)?[ _-]*)?([45]\d{2})(?![A-Za-z0-9_:/_-]|\.\d)"
    codes = set()
    for match in re.finditer(pattern, text, re.I):
        prefix, suffix = text[:match.start()], text[match.end():]
        if re.search(r"(?:포트|port|Content-Length|(?:request|요청|접수|문서)[ _-]*(?:id|번호))\s*[:=]?\s*$", prefix, re.I):
            continue
        if re.match(r"\s*(?:%|ms\b|s\b|MB\b|GB\b|bytes?\b|초|분|시간(?!\s*초과)|건|개|명|자|밀리초|토큰)", suffix, re.I):
            continue
        codes.add(match.group(1))
    return codes


def reported_http_error_codes(intake: dict | None) -> set[str]:
    """원문 검증을 통과한 오류 메시지 값만 허용 근거로 사용한다.

    증상·문서·샘플 알림에는 부정된 오류나 일반적인 예시도 있을 수 있다.
    따라서 해당 문자열을 합쳐 허용 범위를 넓히지 않고, 명시적으로 접수한
    오류 메시지가 없거나 불명확하면 코드 대신 추가 확인을 안내한다.
    예: 사용자가 '오류 메시지는 없어요'라고 했으면 문서에 504가 있어도
    허용 코드는 빈 집합이다. 'HTTP 504가 떠요'를 접수한 경우에만 504를 허용한다.
    """
    field = (intake or {}).get("fields", {}).get("error_message", {})
    if field.get("status") == "provided" and isinstance(field.get("value"), str):
        return http_error_codes(field["value"])
    return set()


def unsupported_http_error_codes(text: str, intake: dict | None) -> set[str]:
    # 접수 단계가 없던 레거시 교육용 API는 기존 계약을 유지한다.
    if intake is None:
        return set()
    return http_error_codes(text) - reported_http_error_codes(intake)


def applicable_documents(documents: list[dict], intake: dict | None) -> list[dict]:
    """문턱값을 통과한 검색 절 중 접수에 적용 가능한 부분만 작성에 사용한다.

    예: 같은 접속 안내의 '일반 확인'은 남기고 '504 오류' 절은 제외할 수 있다.
    절 제목도 검사하므로 뒤쪽 청크에 코드가 없어도 적용 조건을 우회하지 않는다.
    절 정보가 없는 과거 결과는 문서 전체 검사로 유지한다. 검색 원본은 변경하지
    않고 복사본만 만든다. 문서 ID 인용은 유지하되 실제 본문은 통과한 절로 한정한다.
    """
    result = []
    for doc in documents:
        if unsupported_http_error_codes(doc.get("title", ""), intake):
            continue  # 문서 제목 자체가 특정 오류 조건이면 모든 절에 적용된다.
        if "sections" not in doc:
            if not unsupported_http_error_codes(doc.get("text", ""), intake):
                result.append(doc)
            continue
        sections = doc["sections"]
        if not isinstance(sections, list):
            continue  # 손상된 절 정보를 원본 전체 본문으로 되돌려 우회하지 않는다.
        allowed, excluded = [], []
        for section in sections:
            if not isinstance(section, dict) or not isinstance(section.get("text"), str):
                continue
            condition = str(section.get("section_title", ""))
            if unsupported_http_error_codes(condition + "\n" + section["text"], intake):
                excluded.append(section.get("chunk_id"))
            elif section["text"].strip():
                allowed.append(section)
        if allowed:
            result.append({**doc, "text": "\n\n".join(
                f"{part.get('section_title', '')}\n{part['text']}".strip() for part in allowed),
                "sections": allowed, "applied_section_ids": [part.get("chunk_id") for part in allowed],
                "excluded_section_ids": excluded})
    return result


def collected_intake(collected: list[dict]) -> dict | None:
    for item in reversed(collected):
        if item.get("source") == "intake" and isinstance(item.get("data"), dict):
            return item["data"]
    return None
