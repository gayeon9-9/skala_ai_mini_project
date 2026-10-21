"""web_demo_server.py — all_total_vue 에이전트와 브라우저 채팅창으로 대화해보는 데모.

web_chat.html은 Vue 3(CDN 글로벌 빌드, 별도 빌드 단계 없음)로 작성되어 있다.
이 스크립트는
- web_chat.html 한 장을 같은 출처(origin)로 띄우고
- 시스템 목록과 채팅 요청을 8100으로 중계(프록시)해서

브라우저의 CORS 제약 없이 실제 에이전트와 대화할 수 있게 해주는 아주 얇은 레이어다.
server.py는 평소처럼(fake/ollama/openai 아무 백엔드로나) 8100에 미리 띄워 놓아야 한다.

실행:
    python3 web_demo_server.py        # http://localhost:8600 에서 채팅 화면 오픈
"""
import json
from pathlib import Path
from urllib.parse import quote

import anyio
import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

BACKEND = "http://127.0.0.1:8100"
HERE = Path(__file__).parent

app = FastAPI()


def _backend_unavailable() -> JSONResponse:
    return JSONResponse(
        {"detail": "에이전트 서버에 연결할 수 없습니다. 8100 포트의 서버 실행 상태를 확인해주세요."},
        status_code=503,
    )


def _forward_response(response: httpx.Response) -> Response:
    # 백엔드의 422/409 검증 응답을 200으로 바꾸지 않고 본문과 함께 전달한다.
    return Response(
        content=response.content,
        status_code=response.status_code,
        headers={"content-type": response.headers.get("content-type", "application/json")},
    )


async def _proxy_get(path: str) -> Response:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{BACKEND}{path}")
        return _forward_response(response)
    except httpx.RequestError:
        return _backend_unavailable()


@app.get("/")
async def index():
    return FileResponse(HERE / "web_chat.html")


@app.get("/health")
async def proxy_health():
    return await _proxy_get("/health")


@app.get("/systems")
async def proxy_systems():
    # 화면의 선택지는 백엔드가 관리하는 목록을 사용해 실제 조회 대상과 일치시킨다.
    return await _proxy_get("/systems")


@app.get("/threads/{thread_id}")
async def proxy_thread(thread_id: str):
    # 식별자를 URL 경로로만 전달한다. 문의 ID 자체가 인증 수단은 아니므로
    # 실제 사내 배포에서는 백엔드에 사용자별 조회 권한 검사를 추가해야 한다.
    return await _proxy_get(f"/threads/{quote(thread_id, safe='')}")


@app.post("/chat")
async def proxy_chat(req: Request):
    body = await req.body()
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(f"{BACKEND}/chat", content=body,
                                         headers={"content-type": "application/json"})
        return _forward_response(response)
    except httpx.RequestError:
        return _backend_unavailable()


@app.get("/chat/stream")
async def proxy_stream(message: str, thread_id: str = "web-demo",
                       selected_system: str | None = None,
                       request_id: str | None = None):
    params = {"message": message, "thread_id": thread_id}
    # 생략과 빈 문자열은 다르다. 빈 선택도 그대로 넘겨 백엔드가 422로 검증한다.
    if selected_system is not None:
        params["selected_system"] = selected_system
    # 끊긴 요청을 같은 ID로 다시 보내야 저장된 답변을 재사용하고 중복 접수를 피한다.
    # 빈 값도 백엔드가 검증하도록 그대로 넘긴다.
    if request_id is not None:
        params["request_id"] = request_id

    # 응답 헤더를 먼저 받아야 백엔드의 검증 실패를 정상 SSE(200)로 감추지 않는다.
    # 로컬 모델의 생성 시간은 제한하지 않고, 서버 연결에만 10초 제한을 둔다.
    client = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10.0))
    try:
        request = client.build_request("GET", f"{BACKEND}/chat/stream", params=params)
        upstream = await client.send(request, stream=True)
    except httpx.RequestError:
        await client.aclose()
        return _backend_unavailable()
    except BaseException:
        # 응답 헤더를 기다리던 요청 자체가 취소된 경우에도 클라이언트를 정리한 뒤 전파한다.
        with anyio.CancelScope(shield=True):
            await client.aclose()
        raise

    async def close_upstream():
        # 브라우저가 연결을 끊어도 취소 신호 때문에 소켓 정리가 생략되지 않게 한다.
        with anyio.CancelScope(shield=True):
            try:
                await upstream.aclose()
            finally:
                await client.aclose()

    if not upstream.is_success:
        try:
            await upstream.aread()
            return _forward_response(upstream)
        except httpx.RequestError:
            return _backend_unavailable()
        finally:
            await close_upstream()

    async def relay():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        except httpx.RequestError:
            # 스트리밍 시작 후에는 HTTP 상태를 바꿀 수 없으므로 화면용 오류 이벤트를 보낸다.
            error = {"type": "Error", "message": "에이전트 서버와의 연결이 끊겼습니다. 잠시 후 다시 요청해주세요."}
            yield f"data: {json.dumps(error, ensure_ascii=False)}\n\n"
        finally:
            await close_upstream()

    return StreamingResponse(relay(), media_type="text/event-stream",
                             background=BackgroundTask(close_upstream))


if __name__ == "__main__":
    print("브라우저에서 http://localhost:8600 을 열어보세요 (server.py가 8100에 떠 있어야 합니다)")
    # 접수 이력 조회는 인증 없는 로컬 시연용이므로 외부 인터페이스에 열지 않는다.
    uvicorn.run(app, host="127.0.0.1", port=8600)
