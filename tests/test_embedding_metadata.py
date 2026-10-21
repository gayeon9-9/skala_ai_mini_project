"""임베딩 공급자 변경이 저장된 요청의 실행 정보와 관리자 표시에 연결되는지 확인한다.

실제 API/키/사용자 DB를 읽지 않는다. 공개 모델 식별 필드만 기록하고 이전 완료
요청의 설정은 이후 서버 설정 변경이나 재시도로 덮어쓰지 않는 계약을 검사한다.
"""
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

from agent.chat_service import ChatService, inquiry_store
from agent.deps import SupervisorDeps
from agent.inquiry_summary import inquiry_detail
from agent.observability import completion_metadata, runtime_context
from agent.session_store import SqliteSessionStore


SECRET = "SYNTHETIC_PRIVATE_CONFIGURATION"


def configured_deps(monkeypatch, sessions=None):
    # Git이나 실제 환경 설정을 조회하지 않고 고정 코드 버전으로 검사한다.
    monkeypatch.setenv("APP_REVISION", "embedding-metadata-test")
    embedder = SimpleNamespace(provider="openai", model="text-embedding-3-small",
                               dimensions=1536, query_format="raw",
                               api_key=SECRET, client={"authorization": SECRET}, base_url=SECRET)
    return SupervisorDeps(
        SimpleNamespace(name="qwen3:0.6b"), SimpleNamespace(name="qwen3:8b"),
        None, sessions, "v2", retriever=SimpleNamespace(embedder=embedder),
    )


def test_runtime_copies_only_public_embedding_identity(monkeypatch):
    deps = configured_deps(monkeypatch)
    context = runtime_context(deps)
    assert context["embedding_provider"] == "openai"
    assert context["embedding_model"] == "text-embedding-3-small"
    assert context["embedding_dimensions"] == 1536
    assert context["embedding_query_format"] == "raw"
    assert context["quality_model"] == "qwen3:8b"
    assert SECRET not in json.dumps(context)
    assert not {"api_key", "client", "base_url"}.intersection(context)


def test_old_embedder_missing_identity_stays_unknown_and_structured_values_are_not_copied(monkeypatch):
    deps = configured_deps(monkeypatch)
    deps.retriever.embedder = SimpleNamespace(model="legacy-model")
    context = runtime_context(deps)
    assert context["embedding_model"] == "legacy-model"
    assert context["embedding_provider"] is None
    assert context["embedding_dimensions"] is None
    assert context["embedding_query_format"] is None
    deps.retriever.embedder = SimpleNamespace(provider={"api_key": SECRET}, model={"api_key": SECRET},
                                              dimensions=True, query_format={"api_key": SECRET})
    malformed = runtime_context(deps)
    assert all(malformed[key] is None for key in (
        "embedding_provider", "embedding_model", "embedding_dimensions", "embedding_query_format"))
    assert SECRET not in json.dumps(malformed)


def test_completion_uses_actual_index_identity_without_erasing_unknown_values():
    result = {"retrieval": {"provider": "ollama", "model": "local-vector-model", "dimensions": 1024,
                            "query_format": "query-instruction-v1", "index_version": "index-1",
                            "documents": [], "api_key": SECRET}}
    metadata = completion_metadata(result, 0.125)
    assert metadata["embedding_provider"] == "ollama"
    assert metadata["embedding_dimensions"] == 1024
    assert metadata["embedding_query_format"] == "query-instruction-v1"
    assert metadata["index_version"] == "index-1"
    assert metadata["processing_seconds"] == 0.125
    assert SECRET not in json.dumps(metadata)
    error_metadata = completion_metadata({"retrieval": {"status": "error", "documents": []}}, 1)
    assert not any(key.startswith("embedding_") for key in error_metadata)


def test_saved_identity_and_admin_evidence_survive_replay_and_later_failure(tmp_path, monkeypatch):
    sessions = SqliteSessionStore(tmp_path / "metadata.db")
    try:
        deps = configured_deps(monkeypatch, sessions)
        deps.trace_context = runtime_context(deps)
        request = SimpleNamespace(thread_id="thread-meta", request_id="request-1",
                                  selected_system="auth-service", message="로그인 화면이 멈춰요")
        result = {"final": "저장된 최종 안내", "retrieval": {
            "status": "ok", "provider": "openai", "model": "text-embedding-3-small",
            "dimensions": 1536, "query_format": "raw", "index_version": "openai-index", "documents": []},
            "validation": {"status": "passed", "errors": []}}
        graph = Mock(ainvoke=AsyncMock(return_value=result))
        completed = asyncio.run(ChatService(deps, graph, request).invoke())
        assert completed["telemetry"]["embedding_provider"] == "openai"
        assert completed["telemetry"]["embedding_dimensions"] == 1536
        # 나중에 서버를 로컬 임베딩으로 바꾸어도 같은 요청의 기존 결과는 불변이다.
        deps.trace_context = {"embedding_provider": "ollama", "embedding_model": "new-local-model",
                              "quality_model": "different-answer-model"}
        replay = asyncio.run(ChatService(deps, graph, request).invoke())
        assert replay["telemetry"] == completed["telemetry"]
        assert graph.ainvoke.await_count == 1
        store = inquiry_store(deps)
        store.begin("thread-meta", "auth-service", "다음 요청", "request-2")
        store.fail("request-2", "synthetic failure")
        public = inquiry_detail(store.get_thread("thread-meta"))
        assert public["evidence_is_current"] is False
        assert public["evidence_request_id"] == "request-1"
        assert public["telemetry"]["embedding_provider"] == "openai"
        assert public["telemetry"]["quality_model"] == "qwen3:8b"
        assert public["retrieval"]["provider"] == "openai"
        assert public["retrieval"]["dimensions"] == 1536
        assert public["retrieval"]["query_format"] == "raw"
    finally:
        sessions.close()


def test_admin_filters_embedding_client_secrets_from_every_public_container():
    retrieval = {"status": "ok", "provider": "openai", "model": "text-embedding-3-small",
                 "dimensions": 1536, "query_format": "raw", "api_key": SECRET,
                 "client": {"headers": SECRET}, "documents": []}
    result = {"final": "합성 답변", "retrieval": retrieval, "operator_retrieval": retrieval,
              "telemetry": {"embedding_provider": "openai", "embedding_model": "text-embedding-3-small",
                            "embedding_dimensions": 1536, "embedding_query_format": "raw",
                            "api_key": SECRET, "client": {"headers": SECRET}, "base_url": SECRET}}
    public = inquiry_detail({"inquiry_id": "inq-meta", "selected_system": "auth-service",
                             "details": {"retrieval": retrieval}, "messages": [],
                             "requests": [{"request_id": "request-meta", "status": "completed",
                                           "updated_at": "2026-10-22T01:00:00Z", "result": result}]})
    assert SECRET not in json.dumps(public)
    for item in (public["retrieval"], public["operator_retrieval"], public["details"]["retrieval"]):
        assert item["provider"] == "openai"
        assert item["dimensions"] == 1536
        assert item["query_format"] == "raw"
    assert public["telemetry"]["embedding_provider"] == "openai"


@pytest.mark.skipif(not shutil.which("node"), reason="관리자 상태 검증은 Node.js가 필요하다.")
def test_admin_model_labels_use_saved_values_and_do_not_guess_old_provider():
    from test_admin_ui import ADMIN_HARNESS
    scenario = r"""
const payload = { inquiry_id: 'with-models', evidence_request_id: 'old-request', evidence_is_current: false,
  telemetry: { quality_model: 'qwen3:8b', fast_model: 'qwen3:0.6b', embedding_provider: 'openai',
               embedding_model: 'text-embedding-3-small', embedding_dimensions: 1536, embedding_query_format: 'raw' } };
const { app } = await boot(url => response(url.includes('?') ? emptyList
  : url.endsWith('/with-models') ? payload : { inquiry_id: 'old', retrieval: { model: 'qwen3-embedding:0.6b' } }));
await app.selectInquiry('with-models');
assert.equal(app.previousEvidence.value, true);
assert.equal(app.executionModels.value.answer, 'qwen3:8b');
assert.equal(app.executionModels.value.search, 'text-embedding-3-small');
assert.equal(app.executionModels.value.provider, 'OpenAI API');
assert.equal(app.executionModels.value.dimensions, 1536);
await app.selectInquiry('old');
assert.equal(app.executionModels.value.search, 'qwen3-embedding:0.6b');
assert.equal(app.executionModels.value.provider, '공급자 기록 없음');
assert.equal(app.executionModels.value.answer, '기록 없음');
assert.equal(app.executionModels.value.dimensions, '기록 없음');
"""
    html = Path(__file__).resolve().parents[1] / "web_admin.html"
    completed = subprocess.run(
        ["node", "-e", ADMIN_HARNESS + "\n(async () => {\n" + scenario +
         "\n})().catch(error => { console.error(error); process.exitCode = 1; });", str(html)],
        capture_output=True, text=True, timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
