"""3단계 — 자연어 원문을 근거로 접수 정보를 추출하고 이전 답변과 합친다.

LLM의 그럴듯한 추측을 접수 사실로 저장하지 않도록, 값과 근거가 현재 사용자
문장 안에 있는지 확인한다. 시스템 선택은 이 모듈의 추출 대상이 아니다.
규칙 기반 추출은 fake 모델 시연/외부 모델 실패 시의 보수적인 대체 경로다.
"""

import asyncio
import inspect
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator


FIELD_NAMES = ("symptom", "occurred_at", "error_message", "environment")
QUESTIONS = {
    "symptom": "어떤 동작에서 어떤 증상이 나타나는지 알려주세요.",
    "occurred_at": "언제부터 발생했나요? 정확하지 않으면 모른다고 말씀해도 돼요.",
    "error_message": "표시된 오류 메시지가 있나요? 없으면 '없어요'라고 알려주세요.",
    "environment": "사용한 기기와 브라우저를 알려주세요. 모르시면 모른다고 말씀해도 돼요.",
}


class IntakeField(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["unmentioned", "none", "unknown", "provided"] = "unmentioned"
    value: str | None = None
    evidence: str | None = None

    @model_validator(mode="after")
    def consistent_value(self):
        if self.status == "provided":
            if not self.value or not self.value.strip():
                raise ValueError("provided 상태에는 원문의 값이 필요합니다.")
        elif self.value is not None:
            raise ValueError("값이 없는 상태에는 value=null을 사용합니다.")
        if self.status != "unmentioned" and (not self.evidence or not self.evidence.strip()):
            raise ValueError("사용자가 답한 필드에는 원문 근거가 필요합니다.")
        if self.status == "unmentioned" and self.evidence is not None:
            raise ValueError("언급하지 않은 필드에는 근거를 만들지 않습니다.")
        return self


def empty_fields() -> dict:
    return {name: IntakeField().model_dump() for name in FIELD_NAMES}


def _previous_fields(previous: dict | None) -> dict:
    fields = empty_fields()
    if not isinstance(previous, dict):
        return fields
    candidate = previous.get("fields", previous)
    if not isinstance(candidate, dict):
        return fields
    for name in FIELD_NAMES:
        if name in candidate:
            try:
                fields[name] = IntakeField.model_validate(candidate[name]).model_dump()
            except (ValidationError, TypeError):
                # 오래된/잘못된 접수 형식이 전체 요청을 중단시키지 않게 한다.
                pass
    return fields


def _field(value: str | None, evidence: str, status: str = "provided") -> dict:
    return {"status": status, "value": value, "evidence": evidence}


_UNKNOWN = r"(?:모르(?:겠|는|나|죠|고|겠어|겠습)?|몰라|기억[^,.!?\n]{0,8}안|알\s*수\s*없)"
_NONE = r"(?:없(?:어|습|었|다|음|는|고|어요)?|표시되지\s*않|뜨지\s*않)"
_LABELS = {
    "occurred_at": r"(?:발생\s*(?:시각|시간)|시각|시간|언제)",
    "error_message": r"(?:오류|에러|error)(?:\s*(?:메시지|메세지|코드|문구))?",
    "environment": r"(?:사용\s*환경|환경|브라우저|기기|운영체제|OS)",
}


def extract_rules(query: str, previous: dict | None = None) -> dict:
    """명시된 한국어 표현만 추출한다. 의미를 확신할 수 없으면 unmentioned로 둔다.

    짧은 '없어요'는 직전에 질문한 한 항목에만 연결한다. 임의의 누락 필드에
    끼워 넣으면 오류 메시지 없음이 증상 없음으로 바뀌는 문제가 생기기 때문이다.
    """
    fields = empty_fields()
    text = query.strip()
    if not text:
        return fields
    for name, label in _LABELS.items():
        unknown = re.search(label + r"[^,.!?\n]{0,18}?" + _UNKNOWN, text, re.I)
        none = re.search(label + r"[^,.!?\n]{0,18}?" + _NONE, text, re.I)
        match, status = (unknown, "unknown") if unknown else (none, "none")
        if match:
            label_end = re.match(label, match.group(0), re.I).end()
            other_labels = "|".join(pattern for key, pattern in _LABELS.items() if key != name)
            # '브라우저는 크롬이고 오류 메시지는 없어요'의 없음은 브라우저 값이 아니다.
            if not re.search(other_labels, match.group(0)[label_end:], re.I):
                fields[name] = _field(None, match.group(0), status)

    # 발생 시각은 원문을 그대로 보존한다. 날짜/시간대를 현재 시각으로 보충하지 않는다.
    time = re.search(
        r"(?:(?:오늘|어제|그제|월요일|화요일|수요일|목요일|금요일|토요일|일요일)\s*)?"
        r"(?:(?:오전|오후|새벽|아침|저녁|밤)\s*)?\d{1,2}\s*시(?:\s*\d{1,2}\s*분)?"
        r"|(?:오늘|어제|그제)\s*(?:새벽|아침|오전|오후|저녁|밤)"
        r"|\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:\s+\d{1,2}:\d{2})?",
        text,
    )
    if time and fields["occurred_at"]["status"] == "unmentioned":
        fields["occurred_at"] = _field(time.group(0), time.group(0))

    # 브라우저/기기의 여러 단어가 있으면 그 사이의 원문도 함께 보존한다.
    env = list(re.finditer(
        r"윈도우(?:\s*\d+)?|Windows(?:\s*\d+)?|macOS|맥북|아이폰|아이패드|안드로이드|Android|iOS"
        r"|크롬(?:\s*[\d.]+)?|Chrome(?:\s*[\d.]+)?|사파리|Safari|엣지|Edge|파이어폭스|Firefox"
        r"|사내\s*PC|회사\s*PC|모바일|데스크톱", text, re.I))
    if env and fields["environment"]["status"] == "unmentioned":
        value = text[env[0].start():env[-1].end()]
        fields["environment"] = _field(value, value)

    if fields["error_message"]["status"] == "unmentioned":
        error = re.search(r"(?:HTTP\s*)?[45]\d{2}\b|\b[A-Z]{1,8}[-_]?\d{3,}\b", text)
        quoted = re.search(r"(?:오류|에러)(?:\s*(?:메시지|메세지|코드|문구))?[^\n,.!?]{0,8}?[\"'“‘]([^\"'”’]+)[\"'”’]", text)
        labeled = re.search(r"(?:오류|에러)\s*(?:메시지|메세지|코드|문구)\s*[:：]\s*([^\n,.!?]+)", text)
        if quoted:
            fields["error_message"] = _field(quoted.group(1), quoted.group(0))
        elif error:
            fields["error_message"] = _field(error.group(0), error.group(0))
        elif labeled:
            fields["error_message"] = _field(labeled.group(1).strip(), labeled.group(0))

    # 단순 시간/환경 답변을 새 증상으로 덮어쓰지 않는다. 명백한 문제 표현만 사용한다.
    for segment in re.split(r"[\n.!?;]+", text):
        segment = segment.strip()
        if not segment:
            continue
        problem = re.search(r"멈|먹통|실패|지연|느리|느려|끊|불가|안\s*(?:되|돼|됨|넘어|열|떠|뜨)|넘어가지\s*않|되지\s*않|열리지\s*않", segment)
        context = re.search(r"로그인|접속|화면|결제|페이지|서비스|응답|화면|저장|업로드|다운로드|접근|연결", segment)
        if problem and context:
            fields["symptom"] = _field(segment, segment)
            break

    last_question = previous.get("last_question_field") if isinstance(previous, dict) else None
    # 범주가 명시되지 않은 짧은 응답은 직전 질문과만 연결한다. 긴 자유문은 추측하지 않는다.
    if last_question in FIELD_NAMES and len(text) <= 35 and all(
            f["status"] == "unmentioned" for f in fields.values()):
        if re.fullmatch(r"(?:잘\s*)?(?:모르겠어요|모르겠습니다|몰라요|모름|기억이\s*안\s*나요)[.!?]?", text):
            fields[last_question] = _field(None, text, "unknown")
        elif re.fullmatch(r"(?:네[, ]*)?(?:없어요|없습니다|없음)[.!?]?", text) or (
                last_question == "error_message" and re.fullmatch(r"아니요[.!?]?", text)):
            fields[last_question] = _field(None, text, "none")
    return fields


def _validate_patch(raw, query: str) -> tuple[dict, list[str]]:
    """필드별로 검증한다. 한 필드의 환각 때문에 다른 정상 정보까지 버리지 않는다."""
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        raise ValueError("접수 추출 결과는 JSON 객체여야 합니다.")
    raw_fields = raw.get("fields", raw)
    if not isinstance(raw_fields, dict):
        raise ValueError("fields는 JSON 객체여야 합니다.")
    result, warnings = {}, []
    for name in FIELD_NAMES:
        if name not in raw_fields:
            continue
        try:
            field = IntakeField.model_validate(raw_fields[name])
        except (ValidationError, TypeError):
            warnings.append(f"{name}: 형식이 잘못되어 추출 값을 제외했습니다.")
            continue
        if field.status == "unmentioned":
            continue  # 언급 없음은 이전에 사용자가 제공한 값을 지우는 명령이 아니다.
        if field.evidence not in query or (field.value is not None and field.value not in field.evidence):
            warnings.append(f"{name}: 사용자 원문에서 확인할 수 없어 추출 값을 제외했습니다.")
            continue
        if ((field.status == "none" and not re.search(_NONE + r"|아니요", field.evidence))
                or (field.status == "unknown" and not re.search(_UNKNOWN + r"|모름", field.evidence))):
            warnings.append(f"{name}: 없음/모름의 명시적 근거가 없어 추출 값을 제외했습니다.")
            continue
        result[name] = field.model_dump()
    return result, warnings


async def analyze_intake(query: str, previous: dict | None, llm) -> dict:
    """추출→근거 검증→누적 병합→다음 질문을 수행한다. 외부 LLM 실패도 접수는 유지한다."""
    fields = _previous_fields(previous)
    warnings, usage, source = [], {}, "model"
    try:
        method = llm.extract_intake
        if inspect.iscoroutinefunction(method):
            raw, usage = await method(query, previous or {})
        else:
            # 동기 HTTP 모델 호출이 FastAPI 이벤트 루프를 막지 않도록 분리한다.
            returned = await asyncio.to_thread(method, query, previous or {})
            if inspect.isawaitable(returned):
                returned = await returned
            raw, usage = returned
        patch, validation_warnings = _validate_patch(raw, query)
        warnings.extend(validation_warnings)
        if isinstance(raw, dict) and raw.get("source") == "rules":
            source = "rules"
        # 유효한 정보가 하나도 없고 형식/근거 오류가 있으면 명시적 표현만 재확인한다.
        if not patch and validation_warnings:
            source = "rules_fallback"
            patch, _ = _validate_patch(extract_rules(query, previous), query)
    except Exception as exc:
        source = "rules_fallback"
        warnings.append(f"추출 모델 처리 실패({type(exc).__name__}); 명시적 표현만 규칙으로 확인했습니다.")
        patch, _ = _validate_patch(extract_rules(query, previous), query)
    if not isinstance(usage, dict):
        usage = {}
    fields.update(patch)
    missing = [name for name in FIELD_NAMES if fields[name]["status"] == "unmentioned"]
    already_asked = previous.get("asked_fields", []) if isinstance(previous, dict) else []
    asked = [name for name in already_asked if name in FIELD_NAMES] if isinstance(already_asked, list) else []
    next_field = next((name for name in missing if name not in asked), None)
    questions = [QUESTIONS[next_field]] if next_field else []
    if next_field:
        asked.append(next_field)
    return {"fields": fields, "questions": questions, "missing_fields": missing,
            "asked_fields": asked, "last_question_field": next_field,
            "source": source, "warnings": warnings, "usage": usage}
