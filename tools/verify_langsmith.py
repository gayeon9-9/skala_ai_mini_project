"""합성 문의 한 건으로 LangSmith 실제 수집·메타데이터·규칙 평가 기록을 확인한다.

이 CLI를 실행하면 지정 프로젝트로 합성 실행 기록과 점수 2개를 전송한다.
실제 사용자 DB나 OpenAI API는 사용하지 않는다. 공개 공유 링크는 만들지 않는다.
예: .venv/bin/python tools/verify_langsmith.py --output /tmp/langsmith-check.json
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REQUIRED_METADATA = (
    "code_revision", "code_dirty", "catalog_version", "fast_model", "quality_model",
    "embedding_model", "inquiry_id", "request_id", "thread_id", "selected_system",
    "validation_status", "retrieved_document_ids", "index_version", "processing_seconds",
)


def tracing_configuration(dotenv_path: Path) -> dict:
    """필요한 추적 설정만 선택한다. 키 값이나 설정 파일 원문은 출력하지 않는다."""
    from dotenv.parser import parse_stream

    allowed = {"LANGSMITH_API_KEY", "LANGCHAIN_API_KEY", "LANGSMITH_ENDPOINT",
               "LANGCHAIN_ENDPOINT", "LANGSMITH_WORKSPACE_ID", "LANGCHAIN_WORKSPACE_ID"}
    values = {key: os.environ[key] for key in allowed if os.environ.get(key)}
    if dotenv_path.is_file():
        with dotenv_path.open(encoding="utf-8") as stream:
            for binding in parse_stream(stream):
                if binding.key in allowed and binding.value and binding.key not in values:
                    values[binding.key] = binding.value
    key = values.get("LANGSMITH_API_KEY") or values.get("LANGCHAIN_API_KEY")
    if not key or "..." in key:
        raise ValueError("LangSmith API key is not configured")
    return {"api_key": key,
            "api_url": values.get("LANGSMITH_ENDPOINT") or values.get("LANGCHAIN_ENDPOINT")
                       or "https://api.smith.langchain.com",
            "workspace_id": values.get("LANGSMITH_WORKSPACE_ID") or values.get("LANGCHAIN_WORKSPACE_ID")}


def configure_environment(config: dict, project: str):
    # 다른 실행 스크립트의 프로젝트·tracing=false 설정과 혼합되지 않게 이 프로세스만
    # 명시적으로 설정한다. .env 파일 자체는 쓰지 않고 다른 API 키를 로드하지 않는다.
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ["LLM_BACKEND"] = "fake"
    for prefix in ("LANGSMITH", "LANGCHAIN"):
        os.environ[f"{prefix}_API_KEY"] = config["api_key"]
        os.environ[f"{prefix}_ENDPOINT"] = config["api_url"]
        os.environ[f"{prefix}_PROJECT"] = project
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    # SDK 오류 원문에는 HTTP 정보가 들어갈 수 있다. 이 도구는 분류명만 보고한다.
    for name in ("langsmith", "httpx", "httpcore", "urllib3"):
        logging.getLogger(name).setLevel(logging.CRITICAL)


def descendants(run) -> list:
    result = []
    for child in getattr(run, "child_runs", None) or []:
        result.append(child)
        result.extend(descendants(child))
    return result


def remote_checks(run, result: dict, request) -> dict:
    metadata = (getattr(run, "extra", None) or {}).get("metadata", {})
    missing = [key for key in REQUIRED_METADATA if key not in metadata]
    checks = {
        "root_run_id_matches": str(run.id) == result.get("langsmith_run_id"),
        "root_name_matches": run.name == "chat_request",
        "root_completed": run.end_time is not None,
        "root_has_no_error": not bool(run.error),
        "has_child_runs": bool(descendants(run)),
        "required_metadata_present": not missing,
        "inquiry_id_matches": metadata.get("inquiry_id") == result.get("inquiry_id"),
        "request_id_matches": metadata.get("request_id") == request.request_id,
        "thread_id_matches": metadata.get("thread_id") == request.thread_id,
        "selected_system_matches": metadata.get("selected_system") == request.selected_system,
        "validation_status_matches": metadata.get("validation_status") == result.get("validation", {}).get("status"),
        "retrieved_document_ids_recorded": "faq:login" in (metadata.get("retrieved_document_ids") or []),
        "processing_seconds_recorded": isinstance(metadata.get("processing_seconds"), (int, float))
                                       and metadata["processing_seconds"] >= 0,
    }
    return {"checks": checks, "missing_metadata_fields": missing,
            "verified": all(checks.values()), "child_count": len(descendants(run)),
            "metadata": {key: metadata.get(key) for key in REQUIRED_METADATA}}


async def verify(args) -> dict:
    # server를 import하면 기존 앱 환경·DB가 만들어질 수 있으므로 서비스와 DI만 사용한다.
    config = tracing_configuration(args.dotenv)
    configure_environment(config, args.project)
    from langsmith import Client, tracing_context
    from urllib3.util.retry import Retry
    from agent.chat_service import ChatService
    from agent.deps import SupervisorDeps
    from agent.fake_llm import FastLLM, QualityLLM
    from agent.graph import build_graph
    from agent.inquiry_store import SqliteInquiryStore
    from agent.mcp_client import MCPClient
    from agent.observability import runtime_context
    from agent.retrieval import KnowledgeRetriever, OllamaEmbedder
    from agent.session_store import SqliteSessionStore

    client = Client(**config, timeout_ms=(3000, 10000), retry_config=Retry(total=0),
                    tracing_sampling_rate=1.0, hide_inputs=False, hide_outputs=False,
                    hide_metadata=False, omit_traced_runtime_info=True)
    request = SimpleNamespace(
        thread_id=f"synthetic-trace-{uuid4()}", request_id=str(uuid4()),
        selected_system="auth-service",
        message="오전 9시부터 로그인 후 화면이 넘어가지 않아요. 오류 메시지는 없어요. 윈도우 크롬을 사용해요.",
    )
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "synthetic": True,
              "project": args.project, "status": "failed", "key_configured": True,
              "scope": "fake 생성 모델 + 실제 MCP 샘플 조회 + 실제 로컬 임베딩의 추적 수집 확인. 실모델 답변 정확도나 실제 장애 진단 평가가 아닙니다.",
              "models": {"fast": "trace-fast-fake", "quality": "trace-quality-fake",
                         "embedding": args.embedding_model}}
    with tempfile.TemporaryDirectory(prefix="helpdesk-langsmith-") as temp:
        store = SqliteSessionStore(Path(temp) / "session.db")
        # 교육용 첫 호출 실패는 끈다. 정상 합성 경로 하나를 수집하는 확인 도구이기 때문이다.
        mcp = MCPClient(flaky_first_tool_call=False)
        retriever = KnowledgeRetriever(embedder=OllamaEmbedder(model=args.embedding_model,
                                                             base_url=args.ollama_url))
        deps = SupervisorDeps(
            fast_llm=FastLLM("trace-fast-fake", 0.00015),
            quality_llm=QualityLLM("trace-quality-fake", 0.002), mcp=mcp,
            session_store=store, version="v2", inquiry_store=SqliteInquiryStore(store),
            retriever=retriever,
        )
        deps.trace_context = runtime_context(deps)
        try:
            # MCP stdio의 AnyIO 취소 범위는 여는 태스크와 닫는 태스크가 같아야 한다.
            # wait_for는 별도 태스크를 만들기 때문에 여기서는 timeout 문맥을 사용한다.
            async with asyncio.timeout(25):
                await mcp.start()
            health = await retriever.health()
            if health["status"] != "ok":
                report["error_type"] = "EmbeddingUnavailable"
                return report
            service = ChatService(deps, build_graph(deps), request)
            with tracing_context(client=client, enabled=True, project_name=args.project,
                                 metadata={"synthetic": True, "evaluation_scope": "trace_smoke"},
                                 tags=["synthetic", "helpdesk", "trace-verification"]):
                result = await asyncio.wait_for(service.invoke(), timeout=55)
            run_id = result.get("langsmith_run_id")
            report.update({"run_id": run_id, "inquiry_id": result.get("inquiry_id"),
                           "request_id": request.request_id,
                           "validation": result.get("validation")})
            if not run_id:
                report["error_type"] = "MissingRunId"
                return report
            # SDK 0.14.11은 wait_for_futures 대신 flush(timeout=...)를 제공한다.
            await asyncio.wait_for(asyncio.to_thread(client.flush, timeout=15), timeout=20)
            remote = None
            for attempt in range(1, 6):
                report["collection_read_attempts"] = attempt
                try:
                    candidate = await asyncio.wait_for(
                        asyncio.to_thread(client.read_run, run_id, load_child_runs=True), timeout=20)
                    checked = remote_checks(candidate, result, request)
                    report.update(checked)
                    remote = candidate
                    if checked["verified"]:
                        break
                except Exception as exc:
                    report["collection_error_type"] = type(exc).__name__
                if attempt < 5:
                    await asyncio.sleep(1)
            if remote is None or not report.get("verified"):
                report["error_type"] = "TraceNotFullyCollected"
                return report
            report["run_url"] = await asyncio.wait_for(
                asyncio.to_thread(client.get_run_url, run=remote, project_name=args.project), timeout=20)
            user_docs = result.get("retrieval", {}).get("user", {}).get("documents", [])
            # 실제 graph 반환은 user가 아닌 documents를 직접 넣는 버전도 지원한다.
            if not user_docs:
                user_docs = result.get("retrieval", {}).get("documents", [])
            scores = {"synthetic_rules_validation_passed": result.get("validation", {}).get("status") == "passed",
                      "synthetic_faq_login_hit": any(d.get("document_id") == "faq:login" for d in user_docs)}
            report["rules"] = scores
            feedback = []
            for key, passed in scores.items():
                item = await asyncio.wait_for(asyncio.to_thread(
                    client.create_feedback, run_id=run_id, session_id=remote.session_id,
                    key=key, score=int(passed),
                    comment="합성 문의 한 건의 규칙 검사/FAQ 검색 확인. fake 생성 모델이며 실LLM 정확도 점수가 아닙니다.",
                    stop_after_attempt=1), timeout=20)
                feedback.append({"key": key, "score": int(passed), "feedback_id": str(item.id)})
            await asyncio.wait_for(asyncio.to_thread(client.flush, timeout=15), timeout=20)
            report["feedback"] = feedback
            report["status"] = "verified" if all(scores.values()) else "collected_with_failed_rules"
            return report
        finally:
            # 이 도구에서 만든 자원만 닫는다. 사용자의 실행 중인 서버·DB는 건드리지 않는다.
            try:
                async with asyncio.timeout(10):
                    await mcp.stop()
            finally:
                await retriever.aclose()
                store.close()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="합성 문의 한 건의 LangSmith 실제 수집 확인")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--project", default="skala-helpdesk-synthetic-eval")
    parser.add_argument("--dotenv", type=Path, default=ROOT / ".env")
    parser.add_argument("--embedding-model", default="qwen3-embedding:0.6b")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    return parser.parse_args(argv)


async def main(args) -> int:
    try:
        report = await verify(args)
    except Exception as exc:
        # 인증·네트워크 예외의 본문은 저장하지 않는다. 키·URL·내부 응답 노출을 막는다.
        report = {"created_at": datetime.now(timezone.utc).isoformat(), "synthetic": True,
                  "project": args.project, "status": "failed", "error_type": type(exc).__name__}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "verified" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(parse_args())))
