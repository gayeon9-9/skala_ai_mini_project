"""검색기의 임베딩 공급자만 교체한다. 선택한 공급자가 실패해도 다른 곳으로 전송하지 않는다.

Ollama의 모델 식별자·질의 지시문은 기존 캐시와 같은 형식을 유지한다.
OpenAI는 공식 API만 사용하고 키는 호출 시 환경변수에서 읽는다. HTTP 오류 원문과
API 키는 저장하거나 반환하지 않으며, 자동 재시도로 호출량을 숨기지 않는다.
"""

import asyncio
import math
import os

import httpx

QUERY_INSTRUCTION = "Retrieve relevant IT helpdesk guidance or incident response procedures for the query."
OPENAI_BASE_URL = "https://api.openai.com/v1"
OPENAI_DIMENSIONS = {"text-embedding-3-small": 1536, "text-embedding-3-large": 3072}
MAX_BATCH_INPUTS = 64


class RetrievalError(RuntimeError):
    """문서/모델/색인 문제. 관련 문서가 없는 정상 검색과 구분한다."""


class _Usage:
    def _init_usage(self):
        self._usage = {"successful_api_calls": 0, "input_tokens": 0,
                       "failed_api_calls": 0, "unknown_usage_api_calls": 0}

    def usage_snapshot(self) -> dict:
        """누적값의 복사본. 호출 전후 차이로 색인·질의 비용을 따로 집계할 수 있다.

        API 호출 수는 임베딩 POST만 센다. 설정 검사·로컬 캐시·모델 목록 GET은 제외한다.
        성공은 이 공급자 모듈의 응답 처리 완료를 뜻한다. 기존 Ollama 벡터 검사는
        검색기가 이어서 수행한다. HTTP 2xx 뒤 공급자의 응답 검증이 실패해도
        응답에 유효한 사용량이 있었다면 입력 토큰은 누적해 비용을 숨기지 않는다.
        """
        return dict(self._usage)

    def _record_tokens(self, tokens):
        if type(tokens) is int and tokens >= 0:
            self._usage["input_tokens"] += tokens
        else:
            # 사용량 없는 응답을 토큰 0개로 단정하지 않도록 별도로 표시한다.
            self._usage["unknown_usage_api_calls"] += 1


class OllamaEmbedder(_Usage):
    """기존 Ollama 동작을 보존한다. 서버 실행 중 모델을 자동 다운로드하지 않는다."""

    provider = "ollama"
    query_format = QUERY_INSTRUCTION

    def __init__(self, model="qwen3-embedding:0.6b", base_url="http://127.0.0.1:11434",
                 timeout=60):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout, trust_env=False)
        self._init_usage()

    def format_query(self, query: str) -> str:
        return f"Instruct: {QUERY_INSTRUCTION}\nQuery:{query.strip()}"

    async def identity(self) -> dict:
        response = await self.client.get("/api/tags")
        response.raise_for_status()
        models = response.json().get("models", [])
        item = next((m for m in models if m.get("name") == self.model), None)
        if not item or not item.get("digest"):
            raise RetrievalError(f"임베딩 모델 {self.model}이 설치되지 않았습니다.")
        capabilities = item.get("capabilities")
        if capabilities is not None and "embedding" not in capabilities:
            raise RetrievalError("선택한 모델은 임베딩 전용 모델이 아닙니다.")
        # provider 필드를 여기 추가하면 기존 색인 서명이 달라진다. 정확히 보존한다.
        return {"model": self.model, "digest": item["digest"],
                "dimensions": item.get("details", {}).get("embedding_length")}

    async def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            response = await self.client.post("/api/embed", json={
                "model": self.model, "input": texts, "truncate": False,
            })
            response.raise_for_status()
            body = response.json()
            self._record_tokens(body.get("prompt_eval_count"))
            self._usage["successful_api_calls"] += 1
            return body.get("embeddings")
        except (Exception, asyncio.CancelledError):
            self._usage["failed_api_calls"] += 1
            raise

    async def aclose(self):
        await self.client.aclose()


class OpenAIEmbedder(_Usage):
    """text-embedding-3 공식 API. 지시문을 붙이지 않고 질의 원문을 임베딩한다."""

    provider = "openai"
    query_format = "raw"

    def __init__(self, model="text-embedding-3-small", dimensions=None, timeout=60):
        if model not in OPENAI_DIMENSIONS:
            raise RetrievalError("OpenAI 임베딩 모델은 text-embedding-3-small/large만 지원합니다.")
        dimensions = OPENAI_DIMENSIONS[model] if dimensions is None else dimensions
        if type(dimensions) is not int or not 1 <= dimensions <= OPENAI_DIMENSIONS[model]:
            raise RetrievalError("선택한 OpenAI 모델의 임베딩 차원 설정이 올바르지 않습니다.")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 60:
            raise RetrievalError("OpenAI 임베딩 요청 제한 시간은 0초 초과 60초 이하여야 합니다.")
        self.model, self.dimensions = model, dimensions
        self.base_url = OPENAI_BASE_URL
        self.client = httpx.AsyncClient(base_url=OPENAI_BASE_URL,
                                       timeout=httpx.Timeout(timeout, connect=min(10, timeout)),
                                       trust_env=False, follow_redirects=False)
        self._init_usage()

    @staticmethod
    def _api_key() -> str:
        key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not key or key.startswith("sk-...") or any(ch.isspace() for ch in key):
            raise RetrievalError("OpenAI 임베딩 API 키가 설정되지 않았습니다.")
        return key

    def format_query(self, query: str) -> str:
        return query.strip()

    async def identity(self) -> dict:
        # 키 누락은 검색 단계의 명확한 오류로 처리한다. Ollama로 자동 우회하지 않는다.
        self._api_key()
        # 원격 모델의 실제 가중치 digest는 API가 제공하지 않는다. 로컬 구성 버전임을
        # 명시하여 모델을 내려받아 해시를 확인한 것처럼 기록하지 않는다.
        return {"provider": self.provider, "model": self.model, "dimensions": self.dimensions,
                "query_format": self.query_format, "cache_revision": "openai-v1"}

    def _vectors(self, body, count: int) -> list[list[float]]:
        if not isinstance(body, dict) or body.get("model") != self.model:
            raise RetrievalError("OpenAI 임베딩 응답 모델이 요청과 일치하지 않습니다.")
        rows = body.get("data")
        if not isinstance(rows, list) or len(rows) != count:
            raise RetrievalError("OpenAI 임베딩 응답 개수가 입력과 다릅니다.")
        ordered = [None] * count
        for row in rows:
            if not isinstance(row, dict):
                raise RetrievalError("OpenAI 임베딩 응답 형식이 올바르지 않습니다.")
            index = row.get("index")
            if type(index) is not int or not 0 <= index < count or ordered[index] is not None:
                raise RetrievalError("OpenAI 임베딩 응답 순서가 중복되거나 올바르지 않습니다.")
            vector = row.get("embedding")
            if not isinstance(vector, list) or len(vector) != self.dimensions or any(
                type(value) not in (int, float) or not math.isfinite(value) for value in vector
            ):
                raise RetrievalError("OpenAI 임베딩 벡터의 차원 또는 숫자가 올바르지 않습니다.")
            norm = math.sqrt(sum(value * value for value in vector))
            if not math.isfinite(norm) or norm <= 0:
                raise RetrievalError("OpenAI 임베딩 벡터의 크기가 올바르지 않습니다.")
            ordered[index] = vector
        # 응답 data 순서가 바뀌어도 원래 입력 문서 순서에 정확히 연결한다.
        return ordered

    async def _batch(self, texts: list[str], api_key: str) -> list[list[float]]:
        try:
            response = await self.client.post("/embeddings", headers={"Authorization": f"Bearer {api_key}"},
                                              json={"model": self.model, "input": texts,
                                                    "encoding_format": "float", "dimensions": self.dimensions})
            response.raise_for_status()
            body = response.json()
            usage = body.get("usage", {}) if isinstance(body, dict) else {}
            self._record_tokens(usage.get("prompt_tokens") if isinstance(usage, dict) else None)
            vectors = self._vectors(body, len(texts))
            self._usage["successful_api_calls"] += 1
            return vectors
        except asyncio.CancelledError:
            self._usage["failed_api_calls"] += 1
            raise
        except httpx.TimeoutException:
            self._usage["failed_api_calls"] += 1
            raise RetrievalError("OpenAI 임베딩 요청 제한 시간이 지났습니다.") from None
        except httpx.HTTPStatusError as exc:
            self._usage["failed_api_calls"] += 1
            code = exc.response.status_code
            message = ("OpenAI 임베딩 인증 또는 접근 권한을 확인해주세요." if code in (401, 403)
                       else "OpenAI 임베딩 요청 한도 또는 사용 한도를 확인해주세요." if code == 429
                       else f"OpenAI 임베딩 서비스 요청이 실패했습니다 (HTTP {code}).")
            raise RetrievalError(message) from None
        except httpx.RequestError:
            self._usage["failed_api_calls"] += 1
            raise RetrievalError("OpenAI 임베딩 서비스에 연결하지 못했습니다.") from None
        except RetrievalError:
            self._usage["failed_api_calls"] += 1
            raise
        except Exception:
            self._usage["failed_api_calls"] += 1
            raise RetrievalError("OpenAI 임베딩 응답을 확인하지 못했습니다.") from None

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # 빈 입력이나 다른 자료형은 과금 요청 전에 차단한다.
        if not isinstance(texts, list) or not texts or any(
            not isinstance(text, str) or not text.strip() for text in texts
        ):
            raise RetrievalError("임베딩 입력은 비어 있지 않은 문자열 목록이어야 합니다.")
        key = self._api_key()
        vectors = []
        for offset in range(0, len(texts), MAX_BATCH_INPUTS):
            vectors.extend(await self._batch(texts[offset:offset + MAX_BATCH_INPUTS], key))
        return vectors

    async def aclose(self):
        await self.client.aclose()


def create_embedder(provider=None, model=None, base_url=None, dimensions=None):
    """명시 인자 → 환경변수 → 공급자 기본값 순서. 잘못 섞인 설정은 조용히 바꾸지 않는다."""
    provider = provider if provider is not None else os.environ.get("RAG_EMBEDDING_PROVIDER", "ollama")
    if provider not in {"ollama", "openai"}:
        raise RetrievalError("RAG_EMBEDDING_PROVIDER는 ollama 또는 openai여야 합니다.")
    model = model if model is not None else os.environ.get("RAG_EMBEDDING_MODEL")
    if provider == "ollama":
        model = model or "qwen3-embedding:0.6b"
        if model.startswith("text-embedding-"):
            raise RetrievalError("Ollama 공급자에 OpenAI 모델이 지정되었습니다. 공급자와 모델 설정을 확인해주세요.")
        if dimensions is not None:
            raise RetrievalError("Ollama 임베딩의 차원은 설치된 모델에서 확인합니다.")
        return OllamaEmbedder(model=model, base_url=base_url or os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"))
    model = model or "text-embedding-3-small"
    if base_url is not None and base_url.rstrip("/") != OPENAI_BASE_URL:
        raise RetrievalError("OpenAI 임베딩은 공식 API 주소만 지원합니다.")
    if dimensions is None and os.environ.get("OPENAI_EMBEDDING_DIMENSIONS"):
        try:
            dimensions = int(os.environ["OPENAI_EMBEDDING_DIMENSIONS"])
        except ValueError:
            raise RetrievalError("OPENAI_EMBEDDING_DIMENSIONS는 정수여야 합니다.") from None
    return OpenAIEmbedder(model=model, dimensions=dimensions)
