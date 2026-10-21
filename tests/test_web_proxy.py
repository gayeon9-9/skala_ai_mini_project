"""화면용 프록시가 선택한 시스템과 백엔드 오류를 그대로 전달하는지 검증한다.

실제 8100 서버 대신 httpx.MockTransport를 사용하므로 네트워크, API 키,
사용자의 대화 DB 없이 프록시의 HTTP/SSE 처리만 확인할 수 있다.
"""
import json
from pathlib import Path
import shutil
import subprocess
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

    def test_thread_restore_preserves_payload_and_not_found_status(self):
        payload = {"thread_id": "t-web-123", "selected_system": "auth-service",
                   "inquiry_id": "inq-123", "messages": [{"role": "user", "content": "로그인 실패"}]}
        for status, body in ((200, payload), (404, {"detail": "문의가 없습니다."})):
            with self.subTest(status=status):
                self.handler = lambda request: self.response(status, payload=body)

                result = self.browser.get("/threads/t-web-123")

                self.assertEqual(result.status_code, status)
                self.assertEqual(result.json(), body)
                self.assertEqual(self.requests[-1].url.path, "/threads/t-web-123")
                self.assert_resources_closed()

    def test_admin_page_serves_local_html(self):
        result = self.browser.get("/admin")
        self.assertEqual(result.status_code, 200)
        self.assertIn("문의 접수 관리", result.text)
        self.assertIn("/admin/inquiries", result.text)
        self.assertEqual(self.requests, [])

    def test_admin_list_forwards_pagination_filter_and_validation_errors(self):
        for status in (200, 422):
            with self.subTest(status=status):
                payload = {"items": [], "limit": 20, "offset": 40, "has_more": False} if status == 200 else {"detail": "잘못된 상태"}
                self.handler = lambda request: self.response(status, payload=payload)
                params = {"limit": "20", "offset": "40", "status": "failed" if status == 200 else "invalid"}

                result = self.browser.get("/admin/inquiries", params=params)

                self.assertEqual(result.status_code, status)
                self.assertEqual(result.json(), payload)
                self.assertEqual(self.requests[-1].url.path, "/admin/inquiries")
                self.assertEqual(dict(self.requests[-1].url.params), params)
                self.assert_resources_closed()

    def test_admin_detail_preserves_stale_evidence_and_404_status(self):
        for status in (200, 404):
            with self.subTest(status=status):
                payload = {"inquiry_id": "inq-1", "status": "failed", "evidence_request_id": "previous",
                           "evidence_is_current": False} if status == 200 else {"detail": "문의 없음"}
                self.handler = lambda request: self.response(status, payload=payload)

                result = self.browser.get("/admin/inquiries/inq-1")

                self.assertEqual(result.status_code, status)
                self.assertEqual(result.json(), payload)
                self.assertEqual(self.requests[-1].url.path, "/admin/inquiries/inq-1")
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
                "request_id": "retryable-request-1",
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
                "request_id": "retryable-request-1",
            },
        )
        self.assert_resources_closed()

    def test_stream_forwards_blank_values_for_backend_validation(self):
        # 빈 입력을 프록시에서 지우면 선택 생략으로 해석되어 검증을 우회할 수 있다.
        payload = {"detail": "메시지와 시스템을 입력해주세요."}
        self.handler = lambda request: self.response(422, payload=payload)

        result = self.browser.get(
            "/chat/stream",
            params={"message": "", "selected_system": "", "request_id": ""},
        )

        self.assertEqual(result.status_code, 422)
        self.assertEqual(result.json(), payload)
        self.assertEqual(self.requests[0].url.params["message"], "")
        self.assertIn("selected_system", self.requests[0].url.params)
        self.assertEqual(self.requests[0].url.params["selected_system"], "")
        self.assertEqual(self.requests[0].url.params["request_id"], "")
        self.assert_resources_closed()

    def test_stream_keeps_selection_omitted_for_legacy_requests(self):
        self.handler = lambda request: self.response(chunks=[b"data: [DONE]\n\n"])

        result = self.browser.get("/chat/stream", params={"message": "안녕"})

        self.assertEqual(result.status_code, 200)
        self.assertNotIn("selected_system", self.requests[0].url.params)
        self.assertNotIn("request_id", self.requests[0].url.params)
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
            ("GET", "/threads/t-web-unavailable", {}),
            ("GET", "/admin/inquiries", {}),
            ("GET", "/admin/inquiries/inq-unavailable", {}),
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


# DOM 렌더링 대신 실제 HTML의 setup 코드를 실행해 저장/복원/재시도 상태를 검증한다.
# Vue 반응성·fetch·EventSource 경계만 대체하므로 CDN이나 브라우저 설치가 필요 없다.
BROWSER_HARNESS = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const { webcrypto } = require('node:crypto');
const script = fs.readFileSync(process.argv[1], 'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
const key = 'ai-helpdesk.session.v1';
async function boot({ cache = null, status = 404, payload = null } = {}) {
  let app;
  const storage = new Map(cache ? [[key, JSON.stringify(cache)]] : []);
  const streams = [];
  const context = {
    crypto: webcrypto, URLSearchParams, TypeError,
    localStorage: { getItem: name => storage.get(name) || null,
                    setItem: (name, value) => storage.set(name, value) },
    Vue: {
      ref: value => ({ value }), reactive: value => value,
      computed: getter => ({ get value() { return getter(); } }),
      nextTick: () => Promise.resolve(),
      createApp: config => ({ mount() { app = config.setup(); } }),
    },
    fetch: async url => {
      if (url === '/systems') return { ok: true, json: async () => ({
        systems: ['auth-service', 'web-01'], data_source: 'sample' }) };
      if (url === '/health') return { ok: true, json: async () => ({ llm_backend: 'fake' }) };
      return { status, ok: status < 400, json: async () => payload };
    },
    EventSource: class {
      constructor(url) { this.url = url; streams.push(this); }
      close() { this.closed = true; }
      emit(event) { this.onmessage({ data: JSON.stringify(event) }); }
    },
  };
  vm.runInNewContext(script, context);
  await new Promise(resolve => setImmediate(resolve));
  return { app, streams, saved: () => JSON.parse(storage.get(key)) };
}
"""


@unittest.skipUnless(shutil.which("node"), "화면 상태 검증은 Node.js가 있을 때 실행한다.")
class BrowserSessionTests(unittest.TestCase):
    def run_browser(self, scenario):
        html = Path(__file__).resolve().parents[1] / "web_chat.html"
        result = subprocess.run(
            ["node", "-e", BROWSER_HARNESS + "\n(async () => {\n" + scenario +
             "\n})().catch(error => { console.error(error); process.exitCode = 1; });", str(html)],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_answer_tokens_and_internal_prompt_never_become_visible_messages(self):
        self.run_browser(r"""
const { app, streams, saved } = await boot();
app.selectedSystem.value = 'auth-service';
app.onSelectedSystemChange();
app.input.value = '로그인 실패';
app.onSubmit();
streams[0].emit({ type: 'AnswerToken', attempt: 1, delta: '서버를 즉시 재시작하세요' });
streams[0].emit({ type: 'Validation', verdict: '불합격' });
streams[0].emit({ type: 'AnswerToken', attempt: 2, delta: '다시 작성한 초안' });
assert.equal(app.log.value.some(item => ['agent', 'draft'].includes(item.kind)), false);
assert.equal(saved().messages.length, 1);
streams[0].emit({ type: 'FinalAnswer', reply: '확인된 최종 답변', prompt: '내부용 원문' });
assert.equal(app.log.value.filter(item => item.kind === 'agent').length, 1);
assert.equal(app.log.value.find(item => item.kind === 'agent').text, '확인된 최종 답변');
assert.equal(app.log.value.some(item => item.kind === 'prompt'), false);
assert.equal(JSON.stringify(saved()).includes('내부용 원문'), false);
""")

    def test_retrieval_only_keeps_user_sources_and_resets_on_new_inquiry(self):
        self.run_browser(r"""
const { app, streams, saved } = await boot();
app.selectedSystem.value = 'auth-service';
app.onSelectedSystemChange();
app.input.value = '로그인 실패';
app.onSubmit();
streams[0].emit({ type: 'FinalAnswer', reply: '확인된 안내', retrieval: { status: 'ok', documents: [
  { document_id: 'faq-login', chunk_id: '1', title: '로그인 FAQ', audience: 'user', text: '문서 전체 원문' },
  { document_id: 'faq-login', chunk_id: '2', title: '로그인 FAQ', audience: 'user' },
  { document_id: 'runbook-private', title: '운영자 조치', audience: 'operator', text: '운영 절차' },
] }, validation: { status: 'fallback', errors: ['내부 검증 상세'] } });
assert.equal(app.retrieval.value.status, 'ok');
assert.equal(app.retrieval.value.documents.length, 1);
assert.equal(app.retrieval.value.documents[0].document_id, 'faq-login');
assert.equal(app.validation.value.status, 'fallback');
assert.equal(JSON.stringify(saved()).includes('runbook-private'), false);
assert.equal(JSON.stringify(saved()).includes('문서 전체 원문'), false);
assert.equal(JSON.stringify(saved()).includes('내부 검증 상세'), false);
app.newInquiry();
assert.equal(app.retrieval.value, null);
assert.equal(app.validation.value, null);
assert.equal(saved().retrieval, null);
""")

    def test_retrieval_restore_distinguishes_no_match_and_search_error(self):
        self.run_browser(r"""
for (const status of ['no_match', 'error']) {
  const { app, saved } = await boot({
    cache: { thread_id: 't-sources', selected_system: 'auth-service', messages: [] },
    status: 200, payload: { thread_id: 't-sources', selected_system: 'auth-service', messages: [],
      inquiry: { details: { retrieval: { status, error: '내부 오류 경로', documents: [] },
                            validation: { status: 'passed' } } } },
  });
  assert.equal(app.retrieval.value.status, status);
  assert.ok(app.retrievalStatusText.value.includes(status === 'error' ? '실패' : '찾지 못'));
  assert.equal(saved().retrieval.status, status);
  assert.equal(JSON.stringify(saved()).includes('내부 오류 경로'), false);
}
""")

    def test_intake_card_keeps_none_unknown_unmentioned_distinct(self):
        self.run_browser(r"""
const { app, streams, saved } = await boot();
app.selectedSystem.value = 'auth-service';
app.onSelectedSystemChange();
app.input.value = '로그인이 안 되고 오류 메시지는 없어요.';
app.onSubmit();
streams[0].emit({ type: 'FinalAnswer', reply: '언제부터 발생했나요?', intake: { fields: {
  symptom: { status: 'provided', value: '<script>입력은 HTML이 아님</script>', evidence: '로그인이 안 되고' },
  occurred_at: { status: 'unmentioned', value: null },
  error_message: { status: 'none', value: null, evidence: '오류 메시지는 없어요' },
  environment: { status: 'unknown', value: null, evidence: '모르겠어요' },
} } });
assert.deepEqual(Array.from(app.intakeRows.value, field => field.statusLabel), ['값 있음', '미언급', '없음', '모름']);
assert.deepEqual(Array.from(app.missingIntakeLabels.value), ['발생 시각']);
assert.equal(app.intakeRows.value[0].text, '<script>입력은 HTML이 아님</script>');
assert.equal(saved().intake.fields.error_message.status, 'none');
app.newInquiry();
assert.equal(app.intake.value, null);
assert.equal(saved().intake, null);
""")

    def test_intake_card_restores_server_values_and_keeps_cache_on_error(self):
        self.run_browser(r"""
const intake = { fields: {
  symptom: { status: 'provided', value: '화면 멈춤' },
  occurred_at: { status: 'provided', value: '오전 9시', evidence: '오전 9시부터' },
  error_message: { status: 'none' }, environment: { status: 'unmentioned' },
} };
const cache = { thread_id: 't-intake', selected_system: 'auth-service', messages: [], intake };
const restored = await boot({ cache, status: 200, payload: {
  thread_id: 't-intake', selected_system: 'auth-service', inquiry_id: 'inq-1',
  messages: [], inquiry: { details: { intake }, requests: [] },
} });
assert.equal(restored.app.intake.value.fields.occurred_at.value, '오전 9시');
assert.equal(restored.saved().intake.fields.environment.status, 'unmentioned');
const failed = await boot({ cache, status: 503 });
assert.ok(failed.app.restoreError.value);
assert.equal(failed.app.intake.value.fields.occurred_at.value, '오전 9시');
assert.equal(failed.saved().intake.fields.symptom.value, '화면 멈춤');
""")

    def test_failed_request_reuses_id_and_success_caches_only_final(self):
        self.run_browser(r"""
const { app, streams, saved } = await boot();
app.selectedSystem.value = 'auth-service';
app.onSelectedSystemChange();
app.input.value = '로그인 실패';
assert.equal(app.canSend.value, true);
app.onSubmit();
const first = new URLSearchParams(streams[0].url.split('?')[1]);
assert.ok(first.get('request_id'));
streams[0].emit({ type: 'AnswerToken', attempt: 1, delta: '검증 전 초안' });
streams[0].onerror();
assert.equal(app.pendingRequest.value.request_id, first.get('request_id'));
assert.equal(saved().messages.length, 1);
app.retryRequest();
const retry = new URLSearchParams(streams[1].url.split('?')[1]);
assert.equal(retry.get('request_id'), first.get('request_id'));
assert.equal(retry.get('thread_id'), first.get('thread_id'));
assert.equal(app.log.value.filter(item => item.kind === 'user').length, 1);
streams[1].emit({ type: 'FinalAnswer', reply: '저장된 최종 답변', inquiry_id: 'inq-1' });
assert.equal(app.pendingRequest.value, null);
assert.equal(app.sending.value, false);
assert.equal(saved().messages.length, 2);
assert.equal(saved().messages[1].content, '저장된 최종 답변');
assert.equal(saved().inquiry_id, 'inq-1');
streams[1].emit({ type: 'FinalAnswer', reply: '중복 이벤트' });
assert.equal(app.log.value.filter(item => item.kind === 'agent').length, 1);
""")

    def test_restore_completed_request_does_not_add_duplicate_reply(self):
        self.run_browser(r"""
const pending = { request_id: 'r-1', message: '로그인 실패', target: 'auth-service' };
const messages = [{ role: 'user', content: '로그인 실패' },
                  { role: 'assistant', content: '최종 답변' }];
const { app, streams, saved } = await boot({
  cache: { thread_id: 't-1', selected_system: 'auth-service',
           messages: messages.slice(0, 1), pending_request: pending },
  status: 200, payload: { thread_id: 't-1', selected_system: 'auth-service',
    inquiry_id: 'inq-1', messages,
    inquiry: { requests: [{ request_id: 'r-1', status: 'completed' }] } },
});
assert.equal(app.pendingRequest.value, null);
assert.equal(app.log.value.length, 2);
assert.equal(app.selectedSystem.value, 'auth-service');
assert.equal(saved().thread_id, 't-1');
assert.equal(streams.length, 0);
""")

    def test_restore_failure_keeps_existing_history_and_disables_send(self):
        self.run_browser(r"""
const cache = { thread_id: 't-preserve', selected_system: 'auth-service', inquiry_id: 'inq-1',
                messages: [{ role: 'user', content: '보존할 질문' }] };
for (const status of [503, 404]) {
  const { app, saved } = await boot({ cache, status });
  assert.ok(app.restoreError.value);
  assert.equal(app.log.value[0].text, '보존할 질문');
  assert.equal(saved().thread_id, 't-preserve');
  app.input.value = '다음 질문';
  assert.equal(app.canSend.value, false);
}
""")

    def test_system_change_starts_new_thread_and_preserves_target(self):
        self.run_browser(r"""
const { app, saved } = await boot({
  cache: { thread_id: 't-old', selected_system: 'auth-service', messages: [] },
  status: 200, payload: { thread_id: 't-old', selected_system: 'auth-service',
    inquiry_id: 'inq-old', messages: [{ role: 'user', content: '이전 질문' }], inquiry: {} },
});
app.selectedSystem.value = 'web-01';
app.onSelectedSystemChange();
assert.notEqual(saved().thread_id, 't-old');
assert.equal(saved().selected_system, 'web-01');
assert.equal(app.inquiryId.value, '');
assert.equal(app.log.value.length, 0);
assert.equal(app.pendingRequest.value, null);
""")

    def test_first_request_not_reached_server_remains_retryable(self):
        self.run_browser(r"""
const { app, streams } = await boot({ cache: {
  thread_id: 't-not-sent', selected_system: 'auth-service', inquiry_id: '',
  messages: [{ role: 'user', content: '전송 전 끊김' }],
  pending_request: { request_id: 'r-unsent', message: '전송 전 끊김', target: 'auth-service' },
} });
assert.equal(app.restoreError.value, '');
app.retryRequest();
assert.equal(streams.length, 1);
streams[0].onmessage({ data: '[DONE]' });
assert.equal(app.sending.value, false);
assert.equal(app.pendingRequest.value.request_id, 'r-unsent');
assert.equal(app.log.value.filter(item => item.kind === 'agent').length, 0);
""")


if __name__ == "__main__":
    unittest.main()
