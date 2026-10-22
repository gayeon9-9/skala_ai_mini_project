"""작성자가 나눈 Markdown 절의 적용 조건을 유지한다.

문장을 임의로 지워 조치의 전제 조건이 사라지지 않게, H2(##) 절 전체를 최소
적용 단위로 삼는다. H3 이하의 하위 제목은 부모 절에 남긴다. 문서 작성자는
일반 안내와 특정 오류 안내를 별도 H2 절로 작성해야 한다.
"""
import re


def markdown_sections(text: str) -> list[tuple[str, str]]:
    """(제목, 원문 본문)을 반환한다. 코드 블록 안의 ##는 제목이 아니다."""
    sections, lines, heading, fence = [], [], "", None
    for line in text.splitlines():
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
        match = re.match(r"^##\s+(.+?)\s*#*\s*$", line) if fence is None and not marker else None
        if match:
            body = "\n".join(lines).strip()
            if body:
                sections.append((heading, body))
            heading, lines = match.group(1), [line]
        else:
            lines.append(line)
    body = "\n".join(lines).strip()
    if body:
        sections.append((heading, body))
    return sections
