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

from dotenv import load_dotenv

load_dotenv()   # LANGCHAIN_TRACING_V2 / API_KEY / PROJECT — import보다 먼저

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from langsmith import traceable
from langsmith.run_helpers import get_current_run_tree
from pydantic import BaseModel, ValidationError, field_validator

from agent.deps import SupervisorDeps
from agent.fake_llm import FastLLM, QualityLLM
from agent.graph import build_graph
from agent.mcp_client import MCPClient
from agent.session_store import SqliteSessionStore
from agent.supervisor_v1 import run_v1
from agent.system_catalog import SystemCatalogError, get_system_names, validate_selected_system

CREATION_COUNT = 0   # Wave 1 — 프로세스 내 Agent 번들 생성 횟수 (항상 1이어야 정상)


class ChatRequest(BaseModel):   # Wave 1 — Pydantic 요청 검증
    message: str
    thread_id: str = "default"
    # 기존 실습용 API와의 호환성을 위해 생략은 허용한다.
    # 새 웹 화면에서는 반드시 선택해서 보낸다. 빈 문자열은 '생략'이 아니라 입력 오류다.
    selected_system: str | None = None

    @field_validator("message", "thread_id")
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
) -> ChatRequest:
    """GET의 쿼리도 POST의 JSON 본문과 동일한 규칙으로 검증한다.

    스트리밍 시작 후에는 HTTP 상태를 422로 바꿀 수 없다. 따라서 이 의존성에서
    먼저 검증하고, 오류가 있으면 SSE 연결을 열기 전에 일반 오류 응답을 반환한다.
    """
    try:
        return ChatRequest(message=message, thread_id=thread_id, selected_system=selected_system)
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
    app.state.llm_backend = backend
    deps = SupervisorDeps(
        fast_llm=fast_llm,
        quality_llm=quality_llm,
        mcp=mcp,
        session_store=SqliteSessionStore(),
        version=os.environ.get("SUPERVISOR_VERSION", "v2"),
    )
    app.state.deps = deps
    app.state.graph = build_graph(deps) if deps.version == "v2" else None
    app.state.instance_id = id(deps)
    yield
    await mcp.stop()
    deps.session_store.close()
    try:   # 종료 전 LangSmith 배치 전송 플러시 — kill 시 run이 pending으로 남는 것 방지
        from langchain_core.tracers.langchain import wait_for_all_tracers
        wait_for_all_tracers()
    except Exception:
        pass


app = FastAPI(lifespan=lifespan)


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
            "quality_llm": app.state.deps.quality_llm.name}


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


@traceable(name="chat_request")
async def _handle_chat(
    message: str, thread_id: str, selected_system: str | None = None,
) -> tuple[dict, str | None]:
    deps = app.state.deps
    req = ChatRequest(message=message, thread_id=thread_id, selected_system=selected_system)
    state = _initial_state(req)
    if deps.version == "v2":
        result = await app.state.graph.ainvoke(state)
    else:
        result = await run_v1(deps, message, thread_id, state["history"])   # Wave 14
    deps.session_store.append(thread_id, "user", message) ##이번 대화의 사용자 질문을 DB에 저장 
    deps.session_store.append(thread_id, "assistant", result["final"]) ##이번 대화의 최종 답변을 DB에 저장
    rt = get_current_run_tree()
    return result, (str(rt.id) if rt else None)


@app.post("/chat")
async def chat(req: ChatRequest):
    result, run_id = await _handle_chat(req.message, req.thread_id, req.selected_system)
    return {"reply": result["final"], "events": result.get("events", []),
            "metadata": _metadata(result, run_id)}


@app.get("/chat/stream")
async def chat_stream(req: ChatRequest = Depends(_stream_request)):
    """Wave 3 — 그래프의 노드별 진행 상황을 SSE로 스트리밍.

    이벤트마다 'data: {json}\\n\\n' 형태로 내려보내고 마지막에 data: [DONE] 센티널을
    보낸다 — 프론트엔드는 \\n\\n 경계로 이벤트를 분리해 파싱해야 한다(Wave 3b).
    """
    deps = app.state.deps
    state = _initial_state(req)   # 버전 확인과 이력 로딩도 SSE 헤더 전송 전에 수행한다.
    message, thread_id = req.message, req.thread_id

    async def gen():
        history = state["history"]
        if deps.version != "v2":
            result = await run_v1(deps, message, thread_id, history)
            for ev in result["events"]:
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'FinalAnswer', 'reply': result['final']}, ensure_ascii=False)}\n\n"
        else:
            seen = 0
            final = ""
            last_prompt = None   # finalize까지 살아남은 최종 write/chat 호출의 실제 프롬프트
            # stream_mode를 리스트로 주면 (mode, payload) 튜플이 나온다 — "updates"는
            # 노드가 끝날 때마다의 상태 델타, "custom"은 write_node/chat_answer_node가
            # get_stream_writer()로 실행 중에 즉시 흘려보내는 토큰(AnswerToken) 이벤트다.
            async for mode, payload in app.state.graph.astream(
                    state, stream_mode=["updates", "custom"]):
                if mode == "custom":
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    continue
                for node_name, delta in payload.items():
                    for ev in (delta.get("events") or [])[seen:]:
                        ev = {**ev, "node": node_name}
                        yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                    if delta.get("events"):
                        seen = len(delta["events"])
                    if delta.get("final"):
                        final = delta["final"]
                    if delta.get("last_prompt"):
                        last_prompt = delta["last_prompt"]
            # 화면 외의 API 사용자도 답변이 어느 선택 대상/데이터 출처에 대한 것인지
            # 확인할 수 있도록 최종 이벤트에 동일한 메타데이터를 넣는다.
            answer = {"type": "FinalAnswer", "reply": final, "prompt": last_prompt,
                      "selected_system": req.selected_system,
                      "data_source": "sample" if req.selected_system else None}
            yield f"data: {json.dumps(answer, ensure_ascii=False)}\n\n"
            deps.session_store.append(thread_id, "user", message)
            deps.session_store.append(thread_id, "assistant", final)
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8100, log_level="warning")
