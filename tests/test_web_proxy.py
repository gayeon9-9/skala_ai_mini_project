"""화면용 프록시가 선택한 시스템과 백엔드 오류를 그대로 전달하는지 검증한다.

실제 8100 서버 대신 httpx.MockTransport를 사용하므로 네트워크, API 키,
사용자의 대화 DB 없이 프록시의 HTTP/SSE 처리만 확인할 수 있다.
"""
import json
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

import web_demo_server


# 패치 이후에도 실제 httpx 클라이언트를 만들 수 있도록 원래 생성자를 보관한다.
REAL_ASYNC_CLIENT = httpx.AsyncClient


class TrackingStream(httpx.AsyncByteStream):
    """응답 조각과 읽기 실패를 재현하고 응답 자원 정리 여부를 기록한다."""

    def __init__(self, chunks, error=None):
        self.chunks = chunks
        self.error = error
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
        if self.error is not None:
            raise self.error

    async def aclose(self):
        self.closed = True


class WebProxyTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.clients = []
        self.streams = []
        self.handler = None

        def handle_request(request):
            self.requests.append(request)
            return self.handler(request)

        def create_client(*args, **kwargs):
            # 전송 계층만 가짜로 바꾸고 실제 httpx의 읽기/닫기 동작은 유지한다.
            kwargs["transport"] = httpx.MockTransport(handle_request)
            client = REAL_ASYNC_CLIENT(*args, **kwargs)
            self.clients.append(client)
            return client

        patcher = patch("web_demo_server.httpx.AsyncClient", side_effect=create_client)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.browser = TestClient(web_demo_server.app)
        self.addCleanup(self.browser.close)

    def response(self, status=200, *, payload=None, chunks=None, error=None):
        if chunks is None:
            chunks = [json.dumps(payload, ensure_ascii=False).encode("utf-8")]
            content_type = "application/json"
        else:
            content_type = "text/event-stream"
        stream = TrackingStream(chunks, error)
        self.streams.append(stream)
        return httpx.Response(
            status,
            headers={"content-type": content_type},
            stream=stream,
        )

    def assert_resources_closed(self):
        self.assertTrue(self.clients, "백엔드 요청용 클라이언트가 생성되어야 한다.")
        self.assertTrue(all(client.is_closed for client in self.clients))
        self.assertTrue(all(stream.closed for stream in self.streams))

    def test_systems_forwards_catalog_and_sample_source(self):
        payload = {
            "systems": ["auth-service", "web-01"],
            "data_source": "sample",
        }
        self.handler = lambda request: self.response(payload=payload)

        result = self.browser.get("/systems")

        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json(), payload)
        self.assertEqual(self.requests[0].url.path, "/systems")
        self.assert_resources_closed()

    def test_stream_forwards_selection_message_and_thread(self):
        chunks = [
            b'data: {"type":"FinalAnswer","reply":"ok"}\n\n',
            b"data: [DONE]\n\n",
        ]
        self.handler = lambda request: self.response(chunks=chunks)

        result = self.browser.get(
            "/chat/stream",
            params={
                "message": "로그인 후 화면이 넘어가지 않음",
                "thread_id": "ticket-1",
                "selected_system": "auth-service",
            },
        )

        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.headers["content-type"].startswith("text/event-stream"))
        self.assertEqual(result.content, b"".join(chunks))
        self.assertEqual(
            dict(self.requests[0].url.params),
            {
                "message": "로그인 후 화면이 넘어가지 않음",
                "thread_id": "ticket-1",
                "selected_system": "auth-service",
            },
        )
        self.assert_resources_closed()

    def test_stream_forwards_blank_values_for_backend_validation(self):
        # 빈 입력을 프록시에서 지우면 선택 생략으로 해석되어 검증을 우회할 수 있다.
        payload = {"detail": "메시지와 시스템을 입력해주세요."}
        self.handler = lambda request: self.response(422, payload=payload)

        result = self.browser.get(
            "/chat/stream",
            params={"message": "", "selected_system": ""},
        )

        self.assertEqual(result.status_code, 422)
        self.assertEqual(result.json(), payload)
        self.assertEqual(self.requests[0].url.params["message"], "")
        self.assertIn("selected_system", self.requests[0].url.params)
        self.assertEqual(self.requests[0].url.params["selected_system"], "")
        self.assert_resources_closed()

    def test_stream_keeps_selection_omitted_for_legacy_requests(self):
        self.handler = lambda request: self.response(chunks=[b"data: [DONE]\n\n"])

        result = self.browser.get("/chat/stream", params={"message": "안녕"})

        self.assertEqual(result.status_code, 200)
        self.assertNotIn("selected_system", self.requests[0].url.params)
        self.assertEqual(self.requests[0].url.params["thread_id"], "web-demo")
        self.assert_resources_closed()

    def test_stream_preserves_validation_status_and_body_before_sse(self):
        # SSE를 열기 전에 발생한 422/409는 일반 오류 응답으로 전달되어야 한다.
        for status in (422, 409):
            with self.subTest(status=status):
                payload = {"detail": f"요청 처리 불가 ({status})"}
                self.handler = lambda request: self.response(status, payload=payload)

                result = self.browser.get(
                    "/chat/stream",
                    params={"message": "로그인 지연", "selected_system": "unknown"},
                )

                self.assertEqual(result.status_code, status)
                self.assertEqual(result.json(), payload)
                self.assertTrue(result.headers["content-type"].startswith("application/json"))
                self.assert_resources_closed()

    def test_backend_connection_failure_returns_503_and_closes_client(self):
        def unavailable(request):
            raise httpx.ConnectError("test backend unavailable", request=request)

        self.handler = unavailable
        for method, path, kwargs in (
            ("GET", "/systems", {}),
            ("GET", "/chat/stream", {"params": {"message": "로그인 지연"}}),
            ("POST", "/chat", {"json": {"message": "로그인 지연"}}),
        ):
            with self.subTest(path=path):
                result = self.browser.request(method, path, **kwargs)

                self.assertEqual(result.status_code, 503)
                self.assertIn("연결할 수 없습니다", result.json()["detail"])
                self.assert_resources_closed()

    def test_midstream_failure_emits_error_event_and_closes_resources(self):
        first_event = b'data: {"type":"AnswerToken","token":"hello"}\n\n'
        self.handler = lambda request: self.response(
            chunks=[first_event],
            error=httpx.ReadError("test connection lost", request=request),
        )

        result = self.browser.get("/chat/stream", params={"message": "로그인 지연"})

        # 이미 시작한 SSE의 HTTP 200은 유지하되 화면이 읽을 수 있는 오류를 덧붙인다.
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.content.startswith(first_event))
        events = [
            json.loads(line[len("data: "):])
            for line in result.text.splitlines()
            if line.startswith("data: ")
        ]
        self.assertEqual(events[-1]["type"], "Error")
        self.assertIn("연결이 끊겼습니다", events[-1]["message"])
        self.assert_resources_closed()

    def test_post_forwards_selected_system_body_and_validation_status(self):
        submitted = {
            "message": "로그인이 안 돼요",
            "thread_id": "ticket-2",
            "selected_system": "unknown-system",
        }
        payload = {"detail": "등록된 시스템을 선택해주세요."}
        self.handler = lambda request: self.response(422, payload=payload)

        result = self.browser.post("/chat", json=submitted)

        self.assertEqual(result.status_code, 422)
        self.assertEqual(result.json(), payload)
        self.assertEqual(self.requests[0].method, "POST")
        self.assertEqual(self.requests[0].url.path, "/chat")
        self.assertEqual(json.loads(self.requests[0].content), submitted)
        self.assertEqual(self.requests[0].headers["content-type"], "application/json")
        self.assert_resources_closed()


if __name__ == "__main__":
    unittest.main()
