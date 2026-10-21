"""로컬 임베딩 모델과 문서 색인을 확인하는 CLI. 모델을 자동 다운로드하지 않는다."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.retrieval import KnowledgeRetriever, OllamaEmbedder


async def run(args):
    retriever = KnowledgeRetriever(
        embedder=OllamaEmbedder(model=args.model, base_url=args.base_url),
        cache_dir=args.cache_dir,
    )
    try:
        result = await retriever.health() if args.health else await retriever.build_index()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "ok" else 1
    except Exception as exc:
        print(json.dumps({"status": "error", "error": retriever._error(exc)}, ensure_ascii=False))
        return 1
    finally:
        await retriever.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="실제 임베딩 모델로 FAQ·Runbook 색인 생성")
    parser.add_argument("--health", action="store_true", help="설치 모델·문서 확인만 수행")
    parser.add_argument("--model", default="qwen3-embedding:0.6b")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--cache-dir", type=Path, default=None)
    raise SystemExit(asyncio.run(run(parser.parse_args())))
