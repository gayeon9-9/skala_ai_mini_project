"""FAQ와 Runbook을 실제 임베딩으로 검색하는 작은 RAG 검색기.

검색과 답변 생성은 분리한다. 이 모듈은 근거 후보만 반환하며, 찾은 문서만으로
사용자의 장애 원인을 확정하지 않는다. 테스트는 embedder를 교체해 네트워크 없이
필터·색인 무효화·실패 처리를 검증하고, 의미 검색 품질은 실제 모델로 따로 평가한다.
"""

import asyncio
import hashlib
import json
import math
import os
import tempfile
from pathlib import Path

from agent.embeddings import QUERY_INSTRUCTION, RetrievalError, OllamaEmbedder

ROOT = Path(__file__).resolve().parents[1]
CHUNK_SIZE = 900
CHUNK_OVERLAP = 100


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _normalise(vectors, count: int, dimensions: int | None = None) -> list[list[float]]:
    """손상된 벡터를 점수로 사용하지 않도록 길이·유한값·영벡터를 확인한다."""
    if not isinstance(vectors, list) or len(vectors) != count or not vectors:
        raise RetrievalError("임베딩 응답 개수가 입력과 다릅니다.")
    result = []
    size = dimensions
    for vector in vectors:
        if not isinstance(vector, list) or not vector:
            raise RetrievalError("빈 임베딩 벡터입니다.")
        size = size or len(vector)
        if len(vector) != size or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
            for v in vector
        ):
            raise RetrievalError("임베딩 차원 또는 숫자 형식이 올바르지 않습니다.")
        norm = math.sqrt(sum(v * v for v in vector))
        if not math.isfinite(norm) or norm <= 0:
            raise RetrievalError("임베딩 벡터의 크기가 올바르지 않습니다.")
        result.append([v / norm for v in vector])
    return result


class KnowledgeRetriever:
    """문서별 최고 청크를 반환한다. 사용자/운영자와 적용 대상 필터를 먼저 적용한다."""

    def __init__(self, embedder=None, root=ROOT, catalog_path=None, cache_dir=None,
                 min_score=0.50, include_raw_scores=False):
        self.root = Path(root).resolve()
        self.catalog_path = Path(catalog_path or self.root / "data/knowledge_catalog.json")
        self.cache_dir = Path(cache_dir or self.root / ".rag_cache")
        self.embedder = embedder if embedder is not None else OllamaEmbedder()
        if not 0 <= min_score <= 1:
            raise ValueError("min_score는 0~1 범위여야 합니다.")
        self.min_score = min_score
        # 평가 도구만 원래 점수로 dev 문턱값을 계산한다. 화면용 반올림 점수로
        # 재필터하면 경계에서 실제 search의 문턱값 판정과 달라질 수 있다.
        self.include_raw_scores = include_raw_scores
        self._lock = asyncio.Lock()
        self._index = None

    def _documents(self) -> tuple[str, list[dict]]:
        catalog = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        if not isinstance(catalog, dict) or not catalog.get("version"):
            raise RetrievalError("문서 목록 버전이 없습니다.")
        documents = catalog.get("documents")
        if not isinstance(documents, list) or not documents:
            raise RetrievalError("검색할 문서 목록이 비어 있습니다.")
        seen, chunks = set(), []
        for item in documents:
            if not isinstance(item, dict):
                raise RetrievalError("문서 메타데이터 형식이 올바르지 않습니다.")
            for field in ("document_id", "title", "path", "version"):
                if not isinstance(item.get(field), str) or not item[field].strip():
                    raise RetrievalError(f"문서 메타데이터 {field}가 없습니다.")
            if item["document_id"] in seen or item.get("audience") not in {"user", "operator"}:
                raise RetrievalError("문서 ID가 중복되거나 독자 범위가 잘못되었습니다.")
            systems = item.get("systems")
            if not isinstance(systems, list) or not systems or any(
                not isinstance(s, str) or not s.strip() for s in systems
            ):
                raise RetrievalError("문서 적용 시스템이 없습니다.")
            relative = Path(item["path"])
            resolved = (self.root / relative).resolve()
            # 목록 파일에 실수로 절대 경로나 상위 폴더를 넣어도 외부 파일을 색인하지 않는다.
            if relative.is_absolute() or not resolved.is_relative_to(self.root) or resolved.suffix != ".md":
                raise RetrievalError("문서 경로는 저장소 내부 Markdown 파일이어야 합니다.")
            text = resolved.read_text(encoding="utf-8").strip()
            if not text:
                raise RetrievalError("내용이 없는 문서입니다.")
            seen.add(item["document_id"])
            # 현재 매뉴얼은 작아 대개 한 청크다. 긴 문서도 유니코드 문자 단위로
            # 상한을 두고 조금 겹쳐 잘라 문단 경계의 내용 손실을 줄인다.
            offset, number = 0, 0
            while offset < len(text):
                body = text[offset:offset + CHUNK_SIZE]
                chunk = {key: item[key] for key in
                         ("document_id", "title", "audience", "systems", "version")}
                chunk.update({"path": resolved.relative_to(self.root).as_posix(),
                              "chunk_id": f"{item['document_id']}:{number}",
                              "text": body, "document_hash": _hash(text)})
                chunks.append(chunk)
                if offset + CHUNK_SIZE >= len(text):
                    break
                offset += CHUNK_SIZE - CHUNK_OVERLAP
                number += 1
        return str(catalog["version"]), chunks

    async def _ensure_index(self) -> dict:
        async with self._lock:
            # 문서 읽기·해시는 작고 저렴하다. 바뀌지 않은 본문은 다시 임베딩하지 않는다.
            version, chunks = self._documents()
            identity = await self.embedder.identity()
            dimensions = identity.get("dimensions")
            if dimensions is not None and (type(dimensions) is not int or dimensions <= 0):
                raise RetrievalError("모델의 임베딩 차원이 올바르지 않습니다.")
            # 제공자별 벡터 공간·질의 형식을 캐시 서명에 포함한다. 기존 Qwen은
            # 과거 서명을 그대로 유지하고 OpenAI는 별도 색인을 만들어 섞이지 않는다.
            query_format = getattr(self.embedder, "query_format", QUERY_INSTRUCTION)
            manifest = {"schema_version": 1, "catalog_version": version,
                        "embedding": identity, "query_instruction": query_format,
                        "chunk_size": CHUNK_SIZE, "chunk_overlap": CHUNK_OVERLAP,
                        "content_hash": _hash(chunks)}
            signature = _hash(manifest)
            if self._index and self._index["index_version"] == signature:
                return self._index
            path = self.cache_dir / f"{signature}.json"
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
                if cached.get("manifest") != manifest or cached.get("chunks") != chunks:
                    raise RetrievalError("저장된 색인의 근거 정보가 달라졌습니다.")
                cached["vectors"] = _normalise(cached["vectors"], len(chunks), dimensions)
                cached["index_version"] = signature
                self._index = cached
                return cached
            except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, RetrievalError):
                # 캐시가 없거나 손상되면 원본 문서로 재생성한다. 잘못된 캐시로 검색하지 않는다.
                pass
            vectors = _normalise(await self.embedder.embed(
                [f"{c['title']}\n{c['text']}" for c in chunks]), len(chunks), dimensions)
            result = {"index_version": signature, "manifest": manifest,
                      "chunks": chunks, "vectors": vectors}
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            tmp_name = None
            try:
                # 완성된 JSON만 보이도록 같은 폴더에서 임시 파일을 원자적으로 교체한다.
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.cache_dir,
                                                 suffix=".tmp", delete=False) as stream:
                    tmp_name = stream.name
                    json.dump(result, stream, ensure_ascii=False, allow_nan=False)
                os.replace(tmp_name, path)
            finally:
                if tmp_name and Path(tmp_name).exists():
                    Path(tmp_name).unlink()
            self._index = result
            return result

    async def build_index(self) -> dict:
        index = await self._ensure_index()
        return {"status": "ok", "index_version": index["index_version"],
                "model": index["manifest"]["embedding"]["model"],
                "provider": getattr(self.embedder, "provider", "custom"),
                "query_format": index["manifest"]["query_instruction"],
                "document_count": len({c["document_id"] for c in index["chunks"]}),
                "chunk_count": len(index["chunks"]), "dimensions": len(index["vectors"][0])}

    async def health(self) -> dict:
        try:
            identity = await self.embedder.identity()
            version, chunks = self._documents()
            return {"status": "ok", "embedding": identity, "catalog_version": version,
                    "document_count": len({c["document_id"] for c in chunks})}
        except Exception as exc:
            return {"status": "error", "error": self._error(exc)}

    @staticmethod
    def _error(exc: Exception) -> str:
        if isinstance(exc, RetrievalError):
            return str(exc)
        # 원격 응답 본문·로컬 전체 경로 등 내부 내용을 사용자 오류 메시지에 섞지 않는다.
        return f"문서 검색을 실행하지 못했습니다 ({type(exc).__name__})."

    async def search(self, query: str, system: str, audience="user", k=3) -> dict:
        if audience not in {"user", "operator"} or type(k) is not int or not 1 <= k <= 10:
            return {"status": "error", "documents": [], "error": "잘못된 검색 범위입니다."}
        if not isinstance(query, str) or not query.strip():
            return {"status": "no_match", "documents": []}
        if not isinstance(system, str) or not system.strip():
            return {"status": "error", "documents": [], "error": "검색 대상 시스템이 없습니다."}
        try:
            index = await self._ensure_index()
            candidates = [(chunk, vector) for chunk, vector in zip(index["chunks"], index["vectors"])
                          if chunk["audience"] == audience
                          and (system in chunk["systems"] or "*" in chunk["systems"])]
            metadata = {"index_version": index["index_version"],
                        "model": index["manifest"]["embedding"]["model"],
                        "provider": getattr(self.embedder, "provider", "custom"),
                        "dimensions": len(index["vectors"][0]),
                        "query_format": index["manifest"]["query_instruction"],
                        "min_score": self.min_score}
            if not candidates:
                return {"status": "no_match", "documents": [], **metadata}
            format_query = getattr(self.embedder, "format_query", None)
            # 예전 DI 테스트 대역은 format_query가 없으므로 기존 Qwen 계약을 유지한다.
            # OpenAI에는 Qwen 전용 Instruct 지시문을 덧붙이지 않고 원래 질문을 보낸다.
            prompt = (format_query(query.strip()) if format_query
                      else f"Instruct: {QUERY_INSTRUCTION}\nQuery:{query.strip()}")
            vector = _normalise(await self.embedder.embed([prompt]), 1,
                                len(index["vectors"][0]))[0]
            ranked = []
            for chunk, stored in candidates:
                score = sum(a * b for a, b in zip(vector, stored))
                if score >= self.min_score:
                    ranked.append({**chunk, "score": round(score, 6),
                                   **({"raw_score": score} if self.include_raw_scores else {})})
            ranked.sort(key=lambda c: (-c["score"], c["chunk_id"]))
            # 긴 문서 하나의 여러 청크가 상위 3개를 모두 차지하지 않도록 문서별 최고점만 남긴다.
            documents, seen = [], set()
            for chunk in ranked:
                if chunk["document_id"] not in seen:
                    seen.add(chunk["document_id"])
                    documents.append(chunk)
                if len(documents) == k:
                    break
            return {"status": "ok" if documents else "no_match", "documents": documents, **metadata}
        except Exception as exc:
            return {"status": "error", "documents": [], "error": self._error(exc)}

    async def aclose(self):
        close = getattr(self.embedder, "aclose", None)
        if close:
            await close()
