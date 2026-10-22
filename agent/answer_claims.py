"""실제 시연에서 발견한 해석 오류를 제한된 규칙으로 재검사한다.

범용 의미 판정기나 두 번째 LLM이 아니다. 샘플 지표를 원인으로 연결하거나,
검색된 문서를 무관하다고 단정하거나, 근거 없이 삭제를 권하는 명시적 표현을
잡는다. 다른 표현의 오류를 놓치거나 정상 문장을 오차단할 수 있어 회귀 평가를
함께 유지한다. 규칙 통과는 답변 전체의 정확성을 보증하지 않는다.
"""
import re


def claim_errors(draft: str, documents: dict) -> list[str]:
    text = re.sub(r"[*`]", "", draft)
    errors = []
    for line in text.splitlines():
        # 샘플만 있는 서비스는 가능성 표현을 붙여도 실제 원인의 근거가 생기지 않는다.
        # '원인을 확인해야 합니다'처럼 확인을 요청하는 표현은 이 규칙에 걸리지 않는다.
        if re.search(r"원인(?:일\s*수|으로\s*추정|으로\s*보|일\s*가능)|때문일\s*수", line):
            errors.append("샘플 지표를 실제 원인의 가능성으로 연결하지 말고, 관측 사실과 필요한 확인을 구분하세요.")
        if re.search(r"고부하\s*(?:상태)?(?:입니다|이다|로\s*(?:판단|확인))", line):
            errors.append("제공된 수치에 없는 고부하 판정을 추가하지 말고 샘플 수치와 알림을 그대로 설명하세요.")
        citations = re.findall(r"\[([A-Za-z][A-Za-z0-9_-]*:[A-Za-z0-9:_-]+)\]", line)
        if any(c in documents for c in citations) and re.search(
                r"(?:관련|연관)(?:성)?[^.!?\n]{0,15}없|무관(?:합|하|한)", line):
            errors.append("검색된 문서와 증상이 무관하다고 단정하지 말고 적용 여부의 추가 확인이 필요하다고 설명하세요.")
        # 문서에 없는 캐시/쿠키 삭제를 기존 시연에서 권했다. 부정 안내는 허용하고
        # 명시적인 삭제 권고만 검사한다. 현재는 알려진 행동 범위만 검사한다.
        action = re.search(r"(?:캐시|쿠키)[^.!?\n]{0,25}(?:삭제|초기화)(?:\s*후|해|하세|합시|를\s*(?:권|진행))", line)
        if action:
            source = "\n".join(doc.get("text", "") for doc in documents.values())
            if not re.search(r"(?:캐시|쿠키)[^.!?\n]{0,25}(?:삭제|초기화)", source):
                errors.append("작성에 사용하는 문서에 없는 캐시·쿠키 삭제 절차를 추가하지 마세요.")
    return list(dict.fromkeys(errors))
