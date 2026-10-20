"""조회 가능한 시스템 목록과 선택값 검증을 한 곳에서 관리한다.

현재 목록의 원본은 MCP 모니터링 도구도 읽는 data/monitoring.json이다.
화면용 목록과 서버의 허용 목록을 따로 작성하면 서로 어긋날 수 있으므로,
두 곳 모두 이 모듈을 사용한다. 실제 서비스 연동 전까지는 샘플 시스템 목록이다.
"""
import json
from pathlib import Path

MONITORING_PATH = Path(__file__).resolve().parent.parent / "data" / "monitoring.json"


class SystemCatalogError(RuntimeError):
    """목록 파일을 읽을 수 없는 경우. 잘못된 사용자 입력과 구분한다."""


def get_system_names() -> list[str]:
    """등록된 시스템 이름을 파일 순서대로 반환한다.

    MCP 도구가 요청마다 같은 파일을 읽으므로 여기서도 목록을 캐시하지 않는다.
    파일이 없거나 손상되면 임의의 기본 시스템을 만들지 않고 요청을 중단한다.
    """
    try:
        data = json.loads(MONITORING_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemCatalogError("시스템 목록을 읽을 수 없습니다. 관리자에게 확인해주세요.") from exc
    if not isinstance(data, dict) or not data or any(
        not name.strip() or name != name.strip() or not isinstance(info, dict)
        for name, info in data.items()
    ):
        raise SystemCatalogError("시스템 목록의 형식이 올바르지 않습니다. 관리자에게 확인해주세요.")
    return list(data)


def validate_selected_system(value: str) -> str:
    """공백을 정리하고 등록된 시스템인지 확인한다. 추측이나 자동 대체는 하지 않는다."""
    name = value.strip()
    if not name:
        raise ValueError("조회할 시스템을 선택해주세요.")
    if name not in get_system_names():
        raise ValueError("등록되지 않은 시스템입니다. 시스템 목록에서 다시 선택해주세요.")
    return name
