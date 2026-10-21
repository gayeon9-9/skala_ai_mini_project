"""all_total 통합 실습 — FastAPI 백엔드 (Wave 1·3·12·14 축 + 전체 조립).

- Wave 1  : lifespan에서 Agent 번들(MCP 연결·LLM·그래프)을 프로세스당 정확히 1회 생성,
            app.state로 모든 요청이 공유 (creation_count/instance_id로 증명)
- Wave 2  : mcp_server.py를 subprocess(stdio)로 기동해 연결 유지
- Wave 3  : GET /chat/stream — 그래프 실행 과정을 SSE 이벤트로 스트리밍
- Wave 12 : 대화 이력을 SQLite(session.db)에 저장 — 재시작해도 유지
- Wave 14 : SUPERVISOR_VERSION(v1/v2) 환경변수로 legacy/신형 파이프라인 즉시 전환
- LangSmith: 요청당 chat_request 루트 run 아래로 노드·LLM·MCP 호출이 계층 기록됨
"""
import json
import os
from contextlib import asynccontextmanager
from uuid import uuid4
from starlette.background import BackgroundTask

from dotenv import load_dotenv

load_dotenv()   # LANGCHAIN_TRACING_V2 / API_KEY / PROJECT — import보다 먼저

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError, field_validator

from agent.chat_service import ChatService, inquiry_store
from agent.inquiry_store import InquiryConflict
from fastapi import Query
from typing import Literal
from agent.inquiry_summary import inquiry_detail, inquiry_list_item

from agent.deps import SupervisorDeps
from agent.fake_llm import FastLLM, QualityLLM
from agent.graph import build_graph
from agent.mcp_client import MCPClient
from agent.session_store import SqliteSessionStore
from agent.system_catalog import SystemCatalogError, get_system_names, validate_selected_system

CREATION_COUNT = 0   # Wave 1 — 프로세스 내 Agent 번들 생성 횟수 (항상 1이어야 정상)


class ChatRequest(BaseModel):   # Wave 1 — Pydantic 요청 검증
    message: str = Field(min_length=1, max_length=8000)
    thread_id: str = Field(default="default", min_length=1, max_length=128)
    request_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=128)
    # 기존 실습용 API와의 호환성을 위해 생략은 허용한다.
    # 새 웹 화면에서는 반드시 선택해서 보낸다. 빈 문자열은 '생략'이 아니라 입력 오류다.
    selected_system: str | None = None

    @field_validator("message", "thread_id", "request_id")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("공백만 입력할 수 없습니다.")
        return value

    @field_validator("selected_system")
    @classmethod
    def validate_system(cls, value: str | None) -> str | None:
        return validate_selected_system(value) if value is not None else None


def _stream_request(
    message: str, thread_id: str = "default", selected_system: str | None = None,
    request_id: str | None = None,
) -> ChatRequest:
    """GET의 쿼리도 POST의 JSON 본문과 동일한 규칙으로 검증한다.

    스트리밍 시작 후에는 HTTP 상태를 422로 바꿀 수 없다. 따라서 이 의존성에서
    먼저 검증하고, 오류가 있으면 SSE 연결을 열기 전에 일반 오류 응답을 반환한다.
    """
    try:
        return ChatRequest(message=message, thread_id=thread_id, selected_system=selected_system,
                           **({"request_id": request_id} if request_id is not None else {}))
    except ValidationError as exc:
        errors = [{**error, "loc": ("query", *error["loc"])} for error in exc.errors()]
        raise RequestValidationError(errors) from exc


def _build_llms():
    """LLM_BACKEND=fake(기본, 결정론) | ollama(로컬 실모델) | openai(실제 OpenAI API)."""
    backend = os.environ.get("LLM_BACKEND", "fake")
    if backend == "ollama":
        from agent.ollama_llm import OllamaFastLLM, OllamaQualityLLM
        fast = OllamaFastLLM(os.environ.get("OLLAMA_FAST_MODEL", "qwen3:0.6b"),
                             usd_per_1k_tokens=0.00015)
        quality = OllamaQualityLLM(os.environ.get("OLLAMA_QUALITY_MODEL", "qwen3:8b"),
                                   usd_per_1k_tokens=0.005)
        return backend, fast, quality
    if backend == "openai":
        from agent.openai_llm import OpenAIFastLLM, OpenAIQualityLLM
        fast = OpenAIFastLLM(os.environ.get("OPENAI_FAST_MODEL", "gpt-4o-mini"),
                             usd_per_1k_tokens=0.00015)
        quality = OpenAIQualityLLM(os.environ.get("OPENAI_QUALITY_MODEL", "gpt-4o"),
                                   usd_per_1k_tokens=0.005)
        return backend, fast, quality
    fast = FastLLM("fast-model-stub", usd_per_1k_tokens=0.00015)
    quality = QualityLLM("quality-model-stub", usd_per_1k_tokens=0.005)
    return backend, fast, quality


@asynccontextmanager
async def lifespan(app: FastAPI):
    global CREATION_COUNT
    CREATION_COUNT += 1
    mcp = MCPClient()
    await mcp.start()   # Wave 2 — MCP subprocess는 서버 수명 동안 딱 1개
    backend, fast_llm, quality_llm = _build_llms()
    from agent.retrieval import KnowledgeRetriever, OllamaEmbedder
    retriever = KnowledgeRetriever(embedder=OllamaEmbedder(
        model=os.environ.get("RAG_EMBEDDING_MODEL", "qwen3-embedding:0.6b"),
        base_url=os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")),
        min_score=float(os.environ.get("RAG_MIN_SCORE", "0.50")))
    app.state.llm_backend = backend
    deps = SupervisorDeps(
        fast_llm=fast_llm,
        quality_llm=quality_llm,
        mcp=mcp,
        session_store=SqliteSessionStore(),
        version=os.environ.get("SUPERVISOR_VERSION", "v2"),
        retriever=retriever,
    )
    inquiry_store(deps).recover_interrupted()  # 이전 프로세스에서 중단된 접수를 재시도 가능하게 한다.
    from agent.observability import runtime_context
    deps.trace_context = runtime_context(deps)
    app.state.deps = deps
    app.state.graph = build_graph(deps) if deps.version == "v2" else None
    app.state.instance_id = id(deps)
    try:
        yield
    finally:
        # 애플리케이션 종료가 예외로 시작되어도 직접 연 연결을 정리한다.
        await mcp.stop()
        await retriever.aclose()
        deps.session_store.close()
        try:
            from langchain_core.tracers.langchain import wait_for_all_tracers
            wait_for_all_tracers()
        except Exception:
            pass


app = FastAPI(lifespan=lifespan)


@app.exception_handler(InquiryConflict)
async def inquiry_conflict(_request: Request, exc: InquiryConflict):
    return JSONResponse({"detail": str(exc)}, status_code=409)


@app.get("/admin/inquiries")
async def admin_inquiries(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status: Literal["processing", "completed", "failed", "interrupted"] | None = None,
):
    """로컬 시연용 읽기 목록. 사내 배포 전에는 인증·관리자 권한 검사가 필요하다."""
    store = inquiry_store(app.state.deps)
    rows = store.list_inquiries(limit=limit + 1, offset=offset, status=status)
    # 하나를 더 조회해 다음 페이지 존재를 표시한다. 소규모 로컬 데모에서만 개별
    # 상세를 조회하며, 운영 규모가 커지면 count/요약 컬럼을 SQL 집계로 옮길 수 있다.
    items = [inquiry_list_item(store.get_inquiry(row["inquiry_id"])) for row in rows[:limit]]
    return {"items": items, "limit": limit, "offset": offset, "has_more": len(rows) > limit}


@app.get("/admin/inquiries/{inquiry_id}")
async def admin_inquiry_detail(inquiry_id: str):
    """저장된 근거와 문의를 읽기만 한다. 조회 중 AI를 다시 호출하거나 조치하지 않는다."""
    inquiry = inquiry_store(app.state.deps).get_inquiry(inquiry_id)
    if inquiry is None:
        raise HTTPException(status_code=404, detail="저장된 접수가 없습니다.")
    return inquiry_detail(inquiry)


@app.get("/threads/{thread_id}")
async def thread_detail(thread_id: str):
    """로컬 시연용 대화 복원. thread ID 자체를 사용자 인증으로 간주하지 않는다."""
    inquiry = inquiry_store(app.state.deps).get_thread(thread_id)
    if inquiry is None:
        raise HTTPException(status_code=404, detail="저장된 문의가 없습니다.")
    # 화면 복원에는 모델 프롬프트·전체 그래프 상태가 필요하지 않다.
    public = {key: inquiry[key] for key in (
        "inquiry_id", "thread_id", "selected_system", "status", "details", "created_at", "updated_at")}
    public["requests"] = [{key: item[key] for key in (
        "request_id", "status", "message", "error")} for item in inquiry["requests"]]
    return {"thread_id": thread_id, "selected_system": inquiry["selected_system"],
            "inquiry_id": inquiry["inquiry_id"], "messages": inquiry["messages"],
            "inquiry": public}



@app.exception_handler(SystemCatalogError)
async def system_catalog_error(_request: Request, exc: SystemCatalogError):
    # 서버의 목록 파일 문제는 사용자 입력 오류(422)가 아닌 일시적인 서비스 오류다.
    return JSONResponse({"detail": str(exc)}, status_code=503)


@app.get("/systems")
async def systems():
    """화면의 선택 목록. 실시간 운영 시스템이 아닌 샘플 목록임을 함께 전달한다."""
    return {"systems": get_system_names(), "data_source": "sample"}


@app.get("/health")
async def health():
    return {"status": "ok", "instance_id": app.state.instance_id,
            "creation_count": CREATION_COUNT,
            "supervisor_version": app.state.deps.version,
            "llm_backend": app.state.llm_backend,
            "fast_llm": app.state.deps.fast_llm.name,
            "quality_llm": app.state.deps.quality_llm.name,
            "code_revision": (app.state.deps.trace_context or {}).get("code_revision")}


@app.get("/version")
async def version():
    return {"supervisor_version": app.state.deps.version}


def _metadata(result: dict, run_id: str | None) -> dict:
    return {
        "route": result.get("route"),
        "task_count": len(result.get("tasks", [])),
        "gate_removed": result.get("gate_removed", 0),
        "replan_rounds": result.get("replan_rounds", 0),
        "retry_count": result.get("retry_count", 0),
        "parallel_elapsed": result.get("parallel_elapsed"),
        "validation_attempts": result.get("validation_attempts", 0),
        "cost_usd": result.get("cost_usd", 0.0),
        "llm_backend": app.state.llm_backend,
        "supervisor_version": app.state.deps.version,
        "instance_id": app.state.instance_id,
        "langsmith_run_id": run_id,
        "inquiry_id": result.get("inquiry_id"),
        "request_id": result.get("request_id"),
        "intake": result.get("intake"),
        "retrieval": result.get("retrieval"),
        "validation": result.get("validation"),
        "telemetry": result.get("telemetry"),
        "selected_system": result.get("selected_system"),
        "data_source": "sample" if result.get("selected_system") else None,
    }


def _initial_state(req: ChatRequest) -> dict:
    """일반 응답과 SSE 응답에 같은 대상·질문·이력을 전달한다."""
    deps = app.state.deps
    if req.selected_system is not None and deps.version != "v2":
        # v1은 선택값을 이해하지 못하므로 조용히 무시해서 다른 시스템을 조회하지 않는다.
        raise HTTPException(
            status_code=409,
            detail="시스템 선택 기능은 SUPERVISOR_VERSION=v2에서 사용할 수 있습니다.",
        )
    return {
        "query": req.message, "thread_id": req.thread_id,
        "selected_system": req.selected_system,
        "history": deps.session_store.load(req.thread_id),
        "events": [], "cost_usd": 0.0,
    }


def _service(req: ChatRequest) -> ChatService:
    # 버전·대상 검증과 접수 충돌 검사는 SSE 헤더를 보내기 전에 끝낸다.
    _initial_state(req)
    return ChatService(app.state.deps, app.state.graph, req)


async def _handle_chat(
    message: str, thread_id: str, selected_system: str | None = None,
    request_id: str | None = None,
) -> tuple[dict, str | None]:
    req = ChatRequest(message=message, thread_id=thread_id, selected_system=selected_system,
                      **({"request_id": request_id} if request_id is not None else {}))
    service = _service(req)
    try:
        result = await service.invoke()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="처리를 완료하지 못했습니다. 같은 요청으로 재시도해 주세요.") from exc
    return result, result.get("langsmith_run_id")


@app.post("/chat")
async def chat(req: ChatRequest):
    result, run_id = await _handle_chat(req.message, req.thread_id, req.selected_system, req.request_id)
    return {"reply": result["final"], "events": result.get("events", []),
            "metadata": _metadata(result, run_id)}


@app.get("/chat/stream")
async def chat_stream(req: ChatRequest = Depends(_stream_request)):
    service = _service(req)

    async def gen():
        async for event in service.stream():
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             background=BackgroundTask(service.interrupted))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8100, log_level="warning")
