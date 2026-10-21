"""접수 실행과 코드·모델·검색 버전을 연결한다. 추적은 기능 동작의 필수 조건이 아니다.

LangSmith의 노드별 실행 시간이 병목 분석용이고, 여기의 processing_seconds는 요청
실행 시작부터 DB 저장 직전까지다. 네트워크 전송까지 포함한 사용자 체감 시간과 구분한다.
"""
import json
import os
from pathlib import Path
import subprocess

from langsmith.run_helpers import get_current_run_tree

ROOT = Path(__file__).resolve().parents[1]


def _embedding_metadata(provider=None, model=None, dimensions=None, query_format=None) -> dict:
    """모델 식별에 필요한 공개 값만 복사한다. 클라이언트/설정 객체 전체는 기록하지 않는다."""
    def text(value):
        return value if isinstance(value, str) and value.strip() else None
    return {"embedding_provider": text(provider), "embedding_model": text(model),
            "embedding_dimensions": dimensions if type(dimensions) is int and dimensions > 0 else None,
            "embedding_query_format": text(query_format)}


def runtime_context(deps) -> dict:
    """서버 시작 시 한 번 구한다. archive 배포는 APP_REVISION으로 버전을 지정할 수 있다."""
    revision, dirty = os.environ.get("APP_REVISION"), None
    if not revision:
        try:
            revision = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
                stderr=subprocess.DEVNULL, timeout=3).strip()
            dirty = bool(subprocess.check_output(
                ["git", "status", "--porcelain"], cwd=ROOT, text=True,
                stderr=subprocess.DEVNULL, timeout=3).strip())
        except (OSError, subprocess.SubprocessError):
            revision = "unknown"
    try:
        catalog_version = json.loads((ROOT / "data/knowledge_catalog.json").read_text())["version"]
    except (OSError, ValueError, KeyError):
        catalog_version = "unknown"
    embedder = getattr(getattr(deps, "retriever", None), "embedder", None)
    return {"code_revision": revision, "code_dirty": dirty, "catalog_version": catalog_version,
            "fast_model": getattr(deps.fast_llm, "name", type(deps.fast_llm).__name__),
            "quality_model": getattr(deps.quality_llm, "name", type(deps.quality_llm).__name__),
            **_embedding_metadata(
                getattr(embedder, "provider", None), getattr(embedder, "model", None),
                getattr(embedder, "dimensions", None), getattr(embedder, "query_format", None))}


def annotate_run(metadata: dict) -> bool:
    """추적이 꺼졌으면 저장/응답을 그대로 진행한다. 메타데이터에 원문·키를 추가하지 않는다."""
    try:
        run = get_current_run_tree()
        if run is None:
            return False
        run.add_metadata(metadata)
        run.add_tags(["helpdesk", "sample-data"])
        return True
    except Exception:
        # 추적 SDK 문제 때문에 접수를 processing에 남기거나, 저장한 답변을 오류로
        # 표시하지 않는다. 실제 원격 수집 성공은 별도 검증 도구에서 확인한다.
        return False


def completion_metadata(result: dict, elapsed: float) -> dict:
    retrieval = result.get("retrieval") or {}
    # 시작 시 차원을 알 수 없는 로컬 모델도 검색 결과에서 실제 색인 차원을 기록한다.
    # 검색 오류/옛 버전 결과에 필드가 없으면 시작 시 설정을 None으로 덮어쓰지 않는다.
    embedding = _embedding_metadata(retrieval.get("provider"), retrieval.get("model"),
                                    retrieval.get("dimensions"), retrieval.get("query_format"))
    return {"validation_status": (result.get("validation") or {}).get("status"),
            "retrieved_document_ids": [doc["document_id"] for doc in retrieval.get("documents", [])
                                       if "document_id" in doc],
            "index_version": retrieval.get("index_version"),
            "processing_seconds": round(elapsed, 6), "processing_scope": "graph_until_persistence",
            **{key: value for key, value in embedding.items() if value is not None}}
