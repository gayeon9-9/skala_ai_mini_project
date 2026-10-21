"""5단계 — 답변의 대상·출처·샘플 수치와 명백한 과장을 규칙으로 검사한다.

정규식은 의미를 이해하는 완전한 환각 탐지기가 아니다. 출처가 존재한다는 사실도
모든 문장의 의미가 그 출처와 일치함을 보장하지 않는다. 이 검사는 검출 가능한
불일치를 차단하는 방어선이며, 실제 품질은 별도의 고정 질문·사람 평가로 확인한다.
"""

import math
import re

from .system_catalog import SystemCatalogError, get_system_names


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _same(value: float, candidates: set[float]) -> bool:
    return any(abs(value - candidate) <= 0.000001 for candidate in candidates)


def _documents(collected, retrieval):
    collected_docs = {item["data"]["document_id"]: item["data"] for item in collected
                      if item.get("source") == "knowledge" and isinstance(item.get("data"), dict)
                      and item["data"].get("audience") == "user"
                      and isinstance(item["data"].get("document_id"), str)}
    if isinstance(retrieval, dict) and "documents" in retrieval:
        # 실제 사용자 검색 결과와 작성 문맥 양쪽에 존재하는 문서만 인용할 수 있다.
        searched_ids = {doc.get("document_id") for doc in retrieval.get("documents", [])
                        if isinstance(doc, dict) and doc.get("audience") == "user"}
        return {key: doc for key, doc in collected_docs.items() if key in searched_ids}
    return collected_docs


def _metric_errors(draft, selected_system, collected, docs):
    # 흔한 Markdown 강조로 CPU와 수치의 연결이 끊어지지 않게 한다.
    # 예: CPU **31%**는 메모리 관측값 31%를 CPU에 바꿔 쓰면 여전히 차단해야 한다.
    draft = re.sub(r"[*`]", "", draft)
    errors = []
    observations = [item["data"] for item in collected
                    if item.get("source") == "monitoring" and isinstance(item.get("data"), dict)
                    and (selected_system is None or item["data"].get("system_name") == selected_system)]
    expected = {"cpu": set(), "memory": set(), "alerts": set()}
    alert_percentages = set()
    for data in observations:
        for key, field in (("cpu", "cpu_percent"), ("memory", "memory_percent")):
            value = _number(data.get(field))
            if value is not None:
                expected[key].add(value)
        if isinstance(data.get("alerts"), list):
            expected["alerts"].add(float(len(data["alerts"])))
            # 연결 풀 사용률처럼 CPU/메모리 외의 비율도 실제 알림에 포함될 수 있다.
            # 선택 대상의 관측 알림 문자열에서만 읽고, 수치가 있다는 이유로 다른
            # 지표(CPU 등)에 바꿔 붙이는 것은 아래의 지표별 검사에서 계속 차단한다.
            for alert in data["alerts"]:
                if isinstance(alert, str):
                    alert_percentages.update(float(value) for value in re.findall(
                        r"(?<![\w.])(-?\d+(?:\.\d+)?)\s*%", alert))

    metric_names = r"CPU|메모리|memory|RAM"
    between = r"(?:\s|사용률|사용량|점유율|사용|는|은|이|가|:|=|약|현재|율|률)*"
    labeled = list(re.finditer(rf"(?P<label>{metric_names}){between}(?P<value>\d+(?:\.\d+)?)\s*%", draft, re.I))
    labeled += list(re.finditer(rf"(?P<value>\d+(?:\.\d+)?)\s*%\s*(?:의\s*)?(?P<label>{metric_names})", draft, re.I))
    matched_percent_spans = []
    for match in labeled:
        key = "cpu" if match.group("label").lower() == "cpu" else "memory"
        value = float(match.group("value"))
        matched_percent_spans.append(match.span("value"))
        if not _same(value, expected[key]):
            errors.append(f"{match.group('label')} {match.group('value')}%가 선택 대상의 관측 수치와 다릅니다.")

    # FAQ의 '5분 후', HTTP 504 같은 숫자는 점검 수치가 아니다. '%'만 별도 검사하되
    # 실제 인용 문서 안에 있는 일반 비율은 허용한다. CPU/메모리로 명시했으면 위 검사가 우선한다.
    cited_ids = set(re.findall(r"\[([A-Za-z][A-Za-z0-9_-]*:[A-Za-z0-9:_-]+)\]", draft))
    documented_percentages = set()
    for key, doc in docs.items():
        if key in cited_ids:
            documented_percentages.update(float(value) for value in re.findall(r"(\d+(?:\.\d+)?)\s*%", doc.get("text", "")))
    allowed = expected["cpu"] | expected["memory"] | alert_percentages | documented_percentages
    for match in re.finditer(r"(?<![\w.])(?P<value>-?\d+(?:\.\d+)?)\s*%", draft):
        if match.span("value") not in matched_percent_spans and not _same(float(match.group("value")), allowed):
            errors.append(f"제공된 점검·인용 문서에 없는 비율 {match.group('value')}%를 사용했습니다.")
    alerts = list(re.finditer(r"(?:알림|경고)(?:\s|수|개수|건수|는|은|이|가|총|:|=)*(\d+)\s*건", draft))
    alerts += list(re.finditer(r"(\d+)\s*건(?:의)?\s*(?:알림|경고)", draft))
    for match in alerts:
        if not _same(float(match.group(1)), expected["alerts"]):
            errors.append(f"알림 {match.group(1)}건이 선택 대상의 관측 결과와 다릅니다.")
    expected_status = {str(data.get("status", "")).lower() for data in observations}
    for match in re.finditer(r"상태\s*[:=]\s*(healthy|degraded|down|unknown)\b", draft, re.I):
        if match.group(1).lower() not in expected_status:
            errors.append(f"상태={match.group(1)}이 선택 대상의 관측 결과와 다릅니다.")
    return errors


def validate_answer(draft: str, selected_system: str | None,
                    collected: list[dict], retrieval: dict | None = None) -> dict:
    """실패 이유를 한국어로 반환해 제한된 재작성과 관리자 검토에 함께 사용한다."""
    errors = []
    if not isinstance(draft, str):
        return {"ok": False, "errors": ["답변이 문자열이 아닙니다."], "citations": []}
    if len(draft.strip()) < 30 or "1)" not in draft:
        errors.append("답변은 30자 이상이며 1) 번호를 포함해야 합니다.")

    citations = list(dict.fromkeys(re.findall(r"\[([A-Za-z][A-Za-z0-9_-]*:[A-Za-z0-9:_-]+)\]", draft)))
    docs = _documents(collected, retrieval)
    if selected_system:
        target_text = re.sub(r"\[[A-Za-z][A-Za-z0-9_-]*:[A-Za-z0-9:_-]+\]", "", draft)
        mentioned = set(re.findall(r"(?<![A-Za-z0-9_-])([A-Za-z0-9][A-Za-z0-9_-]*-(?:service|\d+))(?![A-Za-z0-9_-])", target_text, re.I))
        try:
            known_systems = get_system_names()
        except SystemCatalogError:
            # 기본 이름 패턴 검사는 유지하지만 목록을 확인할 수 없는 상태를 합격시키지 않는다.
            known_systems = []
            errors.append("시스템 목록을 확인하지 못해 대상 검증을 완료할 수 없습니다.")
        for name in known_systems:
            if re.search(r"(?<![A-Za-z0-9_-])" + re.escape(name) + r"(?![A-Za-z0-9_-])", target_text, re.I):
                mentioned.add(name)
        wrong = sorted(name for name in mentioned if name.lower() != selected_system.lower())
        if wrong:
            errors.append("선택하지 않은 시스템이 답변에 포함되었습니다: " + ", ".join(wrong))
        if not re.search(r"(?<![A-Za-z0-9_-])" + re.escape(selected_system) + r"(?![A-Za-z0-9_-])", target_text, re.I):
            errors.append("선택한 시스템 이름을 답변에 명시해야 합니다.")
        if "샘플" not in draft and "sample" not in draft.lower():
            errors.append("실습용 샘플 데이터라는 표시가 필요합니다.")

    for citation in citations:
        if citation not in docs:
            errors.append(f"인용 [{citation}]은 이번 사용자 검색 결과에 없습니다.")
    if docs and not any(citation in docs for citation in citations):
        errors.append("검색 문서를 사용한 답변에는 실제 문서 ID 인용이 필요합니다.")

    errors.extend(_metric_errors(draft, selected_system, collected, docs))
    for sentence in re.split(r"[.!?\n,;]+", draft):
        uncertain = re.search(
            r"(?:확정|확인|단정)(?:되지|하지)\s*(?:않|못)|(?:확정|확인|단정)할\s*수\s*없"
            r"|원인(?:은|이)[^.!?\n]{0,20}(?:불명확|미확인|알\s*수\s*없|모르)", sentence)
        if uncertain:
            continue  # '원인이 아직 확정되지 않았습니다'는 필요한 불확실성 안내다.
        if re.search(r"원인(?:은|이)\s*[^.!?\n]{1,60}(?:입니다|이다|확실|확정|맞습니다)", sentence):
            errors.append("샘플 점검만으로 장애 원인을 확정하는 표현을 사용할 수 없습니다.")
        elif re.search(r"(?:DB|데이터베이스|서버|네트워크)\s*(?:장애|고장)(?:가|이)?(?:입니다|이다|났습니다)", sentence, re.I):
            errors.append("확인되지 않은 실제 장애를 단정했습니다.")
    action_pattern = r"(?:재시작|복구|재배포|롤백|설정\s*변경|해결)(?:을|를)?\s*(?:완료\s*)?(?:했|하였|되었|됐)"
    for sentence in re.split(r"[.!?\n;]+", draft):
        for action in re.finditer(action_pattern, sentence):
            # FAQ의 '해결되었다는 뜻은 아닙니다'는 완료 주장이 아니라 경고다.
            # 문장 전체에 '아니다'가 있다는 이유로 통과시키지 않고, 해당 동사의
            # 바로 뒤에 붙은 부정/미확인 표현만 확인한다. 독립된 복구 주장은 남는다.
            suffix = sentence[action.end():]
            negated = re.match(
                r"^(?:다는|다고|음을|음)\s*(?:(?:뜻|의미|것|사실|상태|결과)(?:은|는|이|가|을|를)?\s*)?"
                r"(?:아니|아닙|단정할\s*수\s*없|확정할\s*수\s*없|볼\s*수\s*없|보장하지\s*않)"
                r"|^는지(?:는)?\s*(?:아직\s*)?(?:확인|모르)", suffix)
            if not negated:
                errors.append("실행하지 않은 조치·복구를 완료했다고 표현했습니다.")

    forbidden = (r"\b(?:sudo|kubectl|systemctl|chmod|chown)\b"
                 r"|\brm\s+-[A-Za-z]*r[A-Za-z]*f|\bkill\s+-9"
                 r"|\b(?:DROP|TRUNCATE)\s+(?:TABLE|DATABASE)\b"
                 r"|\bdocker\s+(?:restart|stop|rm)\b")
    operation_advice = r"(?:서버|서비스|데이터베이스|DB)[^.!?\n]{0,25}(?:재시작|삭제|재배포|롤백)(?:하|해|을\s*하)"
    if re.search(forbidden, draft, re.I) or re.search(operation_advice, draft, re.I):
        errors.append("사용자 안내에 운영 명령·관리자 조치 절차가 포함되었습니다.")

    status = retrieval.get("status") if isinstance(retrieval, dict) else None
    if status is None:
        status = next((item.get("data", {}).get("status") for item in reversed(collected)
                       if item.get("source") == "retrieval_status"), None)
    no_match_notice = re.search(r"(?:문서|근거)[^.!?\n]{0,35}(?:없|부족|찾지\s*못)", draft)
    search_error_notice = re.search(r"검색[^.!?\n]{0,20}(?:실패|오류|못|불가)", draft)
    if status == "no_match" and (not no_match_notice or search_error_notice):
        errors.append("검색은 성공했지만 관련 문서가 없다는 상태를 정확히 안내해야 합니다.")
    if status == "error" and (not search_error_notice or re.search(r"관련\s*(?:사용자\s*)?문서(?:가|는|\s)*없", draft)):
        errors.append("관련 문서 없음으로 단정하지 말고 문서 검색 실패를 안내해야 합니다.")
    return {"ok": not errors, "errors": list(dict.fromkeys(errors))[:16], "citations": citations}
