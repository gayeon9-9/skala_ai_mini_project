"""Qwen 생성 + 선택 가능한 임베딩 실행. 기본은 OpenAI, 비교 시 Ollama를 명시한다.

기존 포트의 서버를 종료하지 않고 자기 프로세스에서만 서버를 실행한다.
설정 출력은 공개 모델 정보만 포함하며 API 키나 .env 원문은 출력하지 않는다.
"""

import argparse
import os
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def configure(args):
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    # 생성 모델의 공급자와 검색 모델의 공급자를 의도적으로 따로 선택한다.
    os.environ["LLM_BACKEND"] = "ollama"
    os.environ["SUPERVISOR_VERSION"] = "v2"
    os.environ["RAG_EMBEDDING_PROVIDER"] = args.embedding_provider
    os.environ["RAG_EMBEDDING_MODEL"] = args.embedding_model or (
        "text-embedding-3-small" if args.embedding_provider == "openai"
        else "qwen3-embedding:0.6b")
    # dev 사례에서 고정한 OpenAI .30을 사용한다. test 결과를 보고 재조정하지 않는다.
    # 공급자를 바꿀 때 서로 다른 점수 척도를 섞지 않도록 Ollama 기본값은 기존 .50이다.
    if args.min_score is None:
        args.min_score = .30 if args.embedding_provider == "openai" else .50
    os.environ["RAG_MIN_SCORE"] = str(args.min_score)


def check_generation_models():
    """유료 임베딩 전에 로컬 답변 모델 준비 여부를 확인한다. 설치·기동은 하지 않는다."""
    import httpx
    from agent.ollama_llm import OLLAMA_URL
    models = {"fast": os.environ.get("OLLAMA_FAST_MODEL", "qwen3:0.6b"),
              "quality": os.environ.get("OLLAMA_QUALITY_MODEL", "qwen3:8b")}
    try:
        response = httpx.get(OLLAMA_URL.removesuffix("/chat") + "/tags", timeout=5, trust_env=False)
        response.raise_for_status()
        installed = {item["name"] for item in response.json()["models"]}
    except Exception:
        raise ValueError("로컬 Ollama에 연결하지 못했습니다. Ollama를 먼저 실행해주세요.") from None
    if any(model not in installed for model in models.values()):
        raise ValueError("답변 생성 모델이 설치되지 않았습니다. 설정한 fast/quality 모델을 먼저 설치해주세요.")
    return models


def main(argv=None):
    parser = argparse.ArgumentParser(description="Qwen 답변 + OpenAI/Ollama 문서 검색")
    parser.add_argument("--embedding-provider", choices=("openai", "ollama"), default="openai")
    parser.add_argument("--embedding-model")
    parser.add_argument("--min-score", type=float, help="기본: OpenAI .30(dev 고정), Ollama .50")
    parser.add_argument("--port", type=int, default=8100)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535 or (args.min_score is not None and not 0 <= args.min_score <= 1):
        parser.error("port는 1~65535, min-score는 0~1 범위여야 합니다.")
    configure(args)
    # 접속 검사보다 bind 검사가 기존 서버와 미사용 포트를 정확히 구별한다.
    # 검사 이후 경쟁이 생기면 uvicorn의 바인딩 실패로 종료하며 남의 서버는 건드리지 않는다.
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", args.port))
        except OSError:
            parser.error("포트가 이미 사용 중입니다. 기존 서버를 직접 종료하거나 --port로 다른 포트를 선택하세요.")
    from agent.embeddings import OpenAIEmbedder
    if args.embedding_provider == "openai":
        # 실행 전 키 존재만 검사한다. 실제 인증·네트워크 성공은 --probe로 확인한다.
        try:
            OpenAIEmbedder._api_key()
        except Exception:
            parser.error(".env 또는 환경변수에 유효한 OPENAI_API_KEY를 설정해주세요.")
    try:
        models = check_generation_models()
    except ValueError as exc:
        parser.error(str(exc))
    print(f"답변: Ollama {models['quality']} / 문의 분석: {models['fast']} / 검색: {args.embedding_provider} "
          f"{os.environ['RAG_EMBEDDING_MODEL']} / min_score={args.min_score}")
    print("OpenAI 검색은 문서·질문을 외부 API로 전송하며 사용량에 따라 과금됩니다."
          if args.embedding_provider == "openai" else "임베딩 비교 기준: 로컬 Ollama")
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
