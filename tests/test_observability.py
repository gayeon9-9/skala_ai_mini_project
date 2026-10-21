"""추적 유무와 무관하게 저장되고, 요청별 버전·근거가 올바르게 연결되는지 검사한다."""
import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

from agent.chat_service import ChatService, inquiry_store
from agent.deps import SupervisorDeps
from agent.session_store import SqliteSessionStore


def setup_service(tmp_path):
    db = SqliteSessionStore(tmp_path / "test.db")
    deps = SupervisorDeps(None, None, None, db, "v2", trace_context={"code_revision": "fixed-test"})
    request = SimpleNamespace(message="로그인 문제", thread_id="thread", request_id="request",
                              selected_system="auth-service")
    result = {"final": "저장 가능한 답변", "validation": {"status": "passed"},
              "retrieval": {"index_version": "index-test", "documents": [{"document_id": "faq:login"}]}}
    graph = Mock(ainvoke=AsyncMock(return_value=result))
    return db, deps, request, graph


def test_tracing_disabled_persists_version_and_replay_keeps_original_measurement(tmp_path):
    db, deps, request, graph = setup_service(tmp_path)
    try:
        first = asyncio.run(ChatService(deps, graph, request).invoke())
        telemetry = first["telemetry"]
        assert first["langsmith_run_id"] is None
        assert telemetry["tracing_enabled"] is False
        assert telemetry["code_revision"] == "fixed-test"
        assert telemetry["retrieved_document_ids"] == ["faq:login"]
        assert telemetry["processing_seconds"] >= 0
        assert telemetry["processing_scope"] == "graph_until_persistence"
        second = asyncio.run(ChatService(deps, graph, request).invoke())
        assert second["telemetry"] == telemetry
        assert graph.ainvoke.await_count == 1
        assert inquiry_store(deps).get_thread("thread")["latest_result"]["telemetry"] == telemetry
    finally:
        db.close()


def test_current_run_receives_request_identity_and_saved_validation(tmp_path):
    db, deps, request, graph = setup_service(tmp_path)
    run = Mock(id="synthetic-run")
    try:
        with patch("agent.chat_service.get_current_run_tree", return_value=run), \
             patch("agent.observability.get_current_run_tree", return_value=run):
            result = asyncio.run(ChatService(deps, graph, request).invoke())
        metadata = {}
        for call in run.add_metadata.call_args_list:
            metadata.update(call.args[0])
        assert metadata["request_id"] == "request"
        assert metadata["inquiry_id"] == result["inquiry_id"]
        assert metadata["validation_status"] == "passed"
        assert metadata["request_status"] == "completed"
        assert result["langsmith_run_id"] == "synthetic-run"
        assert "message" not in metadata
    finally:
        db.close()


def test_failed_stream_is_recorded_in_trace_without_a_final_answer(tmp_path):
    db, deps, request, graph = setup_service(tmp_path)
    async def broken(*args, **kwargs):
        raise RuntimeError("private details")
        yield
    graph.astream = broken
    run = Mock(id="synthetic-run")
    async def consume():
        return [e async for e in ChatService(deps, graph, request).stream()]
    try:
        with patch("agent.observability.get_current_run_tree", return_value=run):
            events = asyncio.run(consume())
        assert [e["type"] for e in events] == ["Error"]
        assert any(c.args[0].get("request_status") == "failed" for c in run.add_metadata.call_args_list)
        assert "private details" not in str(run.add_metadata.call_args_list)
        assert inquiry_store(deps).get_thread("thread")["status"] == "failed"
    finally:
        db.close()


def test_metadata_sdk_failure_does_not_strand_or_fail_completed_inquiry(tmp_path):
    db, deps, request, graph = setup_service(tmp_path)
    run = Mock()
    run.add_metadata.side_effect = RuntimeError("synthetic trace failure")
    try:
        with patch("agent.observability.get_current_run_tree", return_value=run):
            result = asyncio.run(ChatService(deps, graph, request).invoke())
        saved = inquiry_store(deps).get_thread("thread")
        assert saved["status"] == "completed"
        assert saved["latest_result"]["final"] == result["final"]
        assert len(saved["messages"]) == 2
    finally:
        db.close()
