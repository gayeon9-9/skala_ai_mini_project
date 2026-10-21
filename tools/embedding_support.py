"""임베딩 CLI에 필요한 설정만 읽는다. 추적·생성 모델 설정은 실험에 섞지 않는다."""

import os
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
EMBEDDING_KEYS = (
    "OPENAI_API_KEY", "RAG_EMBEDDING_PROVIDER", "RAG_EMBEDDING_MODEL",
    "OPENAI_EMBEDDING_DIMENSIONS", "OLLAMA_BASE_URL", "RAG_MIN_SCORE",
)


def load_embedding_environment(keys=EMBEDDING_KEYS):
    # 셸 환경값을 우선하고 파일은 필요한 키만 가져온다. 실제 값은 출력하지 않는다.
    values = dotenv_values(ROOT / ".env", interpolate=False)
    for key in keys:
        if key not in os.environ and values.get(key):
            os.environ[key] = values[key]
