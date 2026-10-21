"""현재 기본 Ollama 모델 한 건으로 실제 처리 흐름의 호환성을 확인한다.

LangSmith 전송과 dotenv 로딩을 끄고 합성 문의·임시 DB만 사용한다.
실패·검증 후 대체 답변도 결과로 보존한다. 한 건의 작동 확인을 일반적인 답변
정확도나 장애 진단 성능으로 설명하지 않는다. 모델 옵션·프롬프트는 수정하지 않는다.
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
QUERY = "오전 9시부터 로그인 후 화면이 넘어가지 않아요. 오류 메시지는 없어요. 윈도우 크롬을 사용해요."


def source_summary(retrieval: dict | None) -> dict:
    retrieval = retrieval or {}
    return {"status": retrieval.get("status"), "index_version": retrieval.get("index_version"),
            "documents": [{key: doc[key] for key in ("document_id", "chunk_id", "score", "audience")
                           if key in doc} for doc in retrieval.get("documents", [])]}


async def verify(args) -> dict:
    # 앱 서버를 import하지 않으므로 기존 서버 수명·실제 DB·외부 API 설정과 분리된다.
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    os.environ["LLM_BACKEND"] = "ollama"
    from langsmith import tracing_context
    from agent.chat_service import ChatService
    from agent.deps import SupervisorDeps
    from agent.graph import build_graph
    from agent.inquiry_store import SqliteInquiryStore
    from agent.mcp_client import MCPClient
    from agent.observability import runtime_context
    from agent.ollama_llm import OllamaFastLLM, OllamaQualityLLM
    from agent.retrieval import KnowledgeRetriever
    from agent.session_store import SqliteSessionStore

    report = {
        "created_at": datetime.now(timezone.utc).isoformat(), "synthetic": True,
        "scope": "실제 Ollama 기본 생성 모델 1건 + 실제 MCP 샘플 조회 + 실제 임베딩의 호환성 확인. 일반 답변 정확도·운영 장애 진단 성능 평가가 아닙니다.",
        "query": QUERY, "selected_system": "auth-service", "status": "failed",
        "models": {"fast": args.fast_model, "quality": args.quality_model,
                   "embedding": "qwen3-embedding:0.6b"},
        "langsmith_enabled": False, "timed_out": False,
        "timeout_seconds": args.timeout_seconds,
        "errors": [], "cleanup_errors": [],
    }
    with tempfile.TemporaryDirectory(prefix="helpdesk-local-model-") as temp:
        store = SqliteSessionStore(Path(temp) / "session.db")
        retriever = KnowledgeRetriever()
        # 교육용 첫 호출 실패는 꺼서 모델 호환성 확인과 의도적 재시도를 섞지 않는다.
        mcp = MCPClient(flaky_first_tool_call=False)
        deps = SupervisorDeps(
            fast_llm=OllamaFastLLM(args.fast_model, 0.00015),
            quality_llm=OllamaQualityLLM(args.quality_model, 0.005),
            mcp=mcp, session_store=store, version="v2",
            inquiry_store=SqliteInquiryStore(store), retriever=retriever,
        )
        deps.trace_context = runtime_context(deps)
        report["runtime_context"] = deps.trace_context
        request = SimpleNamespace(
            thread_id=f"synthetic-local-{uuid4()}", request_id=str(uuid4()),
            selected_system="auth-service", message=QUERY,
        )
        service = None
        started = time.perf_counter()
        try:
            # stdio 취소 범위는 start와 stop에서 같은 태스크를 사용해야 한다.
            async with asyncio.timeout(25):
                await mcp.start()
            health = await retriever.health()
            report["retrieval_health"] = health
            if health["status"] != "ok":
                report["errors"].append("EmbeddingUnavailable")
                return report
            service = ChatService(deps, build_graph(deps), request)
            graph_started = time.perf_counter()
            with tracing_context(enabled=False):
                async with asyncio.timeout(args.timeout_seconds):
                    result = await service.invoke()
            report["graph_wall_seconds"] = round(time.perf_counter() - graph_started, 6)
            report["result"] = {
                "final": result.get("final"), "intake": result.get("intake"),
                "validation": result.get("validation"),
                "validation_attempts": result.get("validation_attempts"),
                "retrieval": source_summary(result.get("retrieval")),
                "operator_retrieval": source_summary(result.get("operator_retrieval")),
                "telemetry": result.get("telemetry"),
                "inquiry_id": result.get("inquiry_id"), "request_id": result.get("request_id"),
            }
            validation = result.get("validation", {})
            report["status"] = "completed" if validation.get("status") == "passed" else "completed_with_fallback"
            report["errors"] = list(validation.get("errors", []))
        except TimeoutError:
            report["timed_out"] = True
            report["status"] = "timed_out"
            report["errors"].append("TimeoutError")
            report["timeout_note"] = "그래프 취소를 요청했습니다. 이미 실행 중인 동기식 모델 HTTP 호출은 즉시 중단되지 않아 프로세스 종료가 늦어질 수 있습니다."
        except Exception as exc:
            # 원격 응답·경로·설정값을 포함할 수 있는 원문 대신 오류 종류만 기록한다.
            report["errors"].append(type(exc).__name__)
        finally:
            report["processing_seconds"] = round(time.perf_counter() - started, 6)
            if service is not None:
                service.interrupted()
            try:
                async with asyncio.timeout(10):
                    await mcp.stop()
            except Exception as exc:
                report["cleanup_errors"].append(type(exc).__name__)
            try:
                await retriever.aclose()
            except Exception as exc:
                report["cleanup_errors"].append(type(exc).__name__)
            store.close()
    return report


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="현재 기본 로컬 생성 모델의 합성 문의 1건 실행 확인")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=180)
    parser.add_argument("--fast-model", default="qwen3:0.6b")
    parser.add_argument("--quality-model", default="qwen3:8b")
    args = parser.parse_args(argv)
    if not 0 < args.timeout_seconds <= 180:
        parser.error("--timeout-seconds는 0보다 크고 180 이하이어야 합니다.")
    return args


async def main(args) -> int:
    try:
        report = await verify(args)
    except Exception as exc:
        report = {"status": "failed", "synthetic": True, "query": QUERY,
                  "error_type": type(exc).__name__, "timed_out": isinstance(exc, TimeoutError)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(parse_args())))
