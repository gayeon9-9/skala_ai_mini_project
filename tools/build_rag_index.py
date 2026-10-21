"""선택한 공급자로 문서 색인 생성. --probe는 합성 한 문장의 실제 연결을 확인한다."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.embeddings import create_embedder
from agent.retrieval import KnowledgeRetriever, _normalise
from tools.embedding_support import load_embedding_environment


async def run(args):
    retriever = None
    try:
        load_embedding_environment()
        retriever = KnowledgeRetriever(
            embedder=create_embedder(provider=args.provider, model=args.model,
                                     base_url=args.base_url, dimensions=args.dimensions),
            cache_dir=args.cache_dir,
        )
        if args.probe:
            identity = await retriever.embedder.identity()
            vectors = await retriever.embedder.embed(["IT helpdesk connection check"])
            _normalise(vectors, 1, identity.get("dimensions"))
            result = {"status": "ok", "provider": retriever.embedder.provider,
                      "model": retriever.embedder.model, "dimensions": len(vectors[0]),
                      "remote_verified": True}
        elif args.health:
            result = await retriever.health()
            # OpenAI identity는 구성·키 존재 검사다. HTTP 성공으로 오해하지 않게 표시한다.
            result["remote_verified"] = False
        else:
            result = await retriever.build_index()
        result["usage"] = retriever.embedder.usage_snapshot()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "ok" else 1
    except Exception as exc:
        print(json.dumps({"status": "error", "error": KnowledgeRetriever._error(exc)}, ensure_ascii=False))
        return 1
    finally:
        if retriever is not None:
            await retriever.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ollama/OpenAI 임베딩으로 FAQ·Runbook 색인 생성")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--health", action="store_true", help="구성·문서 확인; OpenAI 실제 호출 없음")
    mode.add_argument("--probe", action="store_true", help="합성 문장 1건 실제 호출; OpenAI 과금 가능")
    parser.add_argument("--provider", choices=("ollama", "openai"))
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--dimensions", type=int)
    parser.add_argument("--cache-dir", type=Path, default=None)
    raise SystemExit(asyncio.run(run(parser.parse_args())))
