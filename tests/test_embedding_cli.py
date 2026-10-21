"""실행 설정이 생성/검색을 혼동하거나 기존 포트의 프로세스를 종료하지 않는지 검사한다."""

import asyncio
from argparse import Namespace
from contextlib import redirect_stdout
import io
import json
import os
from unittest.mock import AsyncMock, patch

import pytest
import httpx

from agent.embeddings import RetrievalError
from tools import build_rag_index, run_hybrid
from tools.embedding_support import load_embedding_environment


def test_key_only_loading_leaves_tracing_generation_and_provider_alone():
    values = {"OPENAI_API_KEY": "test-secret", "LLM_BACKEND": "openai",
              "LANGCHAIN_TRACING_V2": "true", "RAG_EMBEDDING_PROVIDER": "openai"}
    with patch.dict(os.environ, {}, clear=True), patch("tools.embedding_support.dotenv_values", return_value=values):
        load_embedding_environment(keys=("OPENAI_API_KEY",))
        assert dict(os.environ) == {"OPENAI_API_KEY": "test-secret"}


@pytest.mark.parametrize("provider,model", [("openai", "text-embedding-3-small"),
                                          ("ollama", "qwen3-embedding:0.6b")])
def test_launcher_keeps_qwen_generation_and_explicit_provider_model_pair(provider, model):
    args = Namespace(embedding_provider=provider, embedding_model=None, min_score=.50)
    with patch.dict(os.environ, {"LLM_BACKEND": "openai", "RAG_EMBEDDING_MODEL": "stale-model"}, clear=True), \
            patch("dotenv.load_dotenv"):
        run_hybrid.configure(args)
        assert os.environ["LLM_BACKEND"] == "ollama"
        assert os.environ["RAG_EMBEDDING_PROVIDER"] == provider
        assert os.environ["RAG_EMBEDDING_MODEL"] == model
        assert os.environ["RAG_MIN_SCORE"] == "0.5"


def test_busy_port_stops_before_server_or_key_check():
    with patch("tools.run_hybrid.configure"), patch("tools.run_hybrid.socket.socket") as socket, \
            patch("uvicorn.run") as serve, patch("agent.embeddings.OpenAIEmbedder._api_key") as key:
        socket.return_value.__enter__.return_value.bind.side_effect = OSError("busy")
        with pytest.raises(SystemExit) as error:
            run_hybrid.main([])
        assert error.value.code == 2
        serve.assert_not_called()
        key.assert_not_called()


def test_local_generation_preflight_honours_model_overrides():
    response = httpx.Response(200, request=httpx.Request("GET", "http://local/api/tags"),
                              json={"models": [{"name": "test-fast"}, {"name": "test-quality"}]})
    with patch.dict(os.environ, {"OLLAMA_FAST_MODEL": "test-fast", "OLLAMA_QUALITY_MODEL": "test-quality"}), \
            patch("httpx.get", return_value=response) as get:
        assert run_hybrid.check_generation_models() == {"fast": "test-fast", "quality": "test-quality"}
    assert get.call_args.args[0] == "http://127.0.0.1:11434/api/tags"


def test_missing_local_generation_model_stops_before_paid_search_can_start():
    response = httpx.Response(200, request=httpx.Request("GET", "http://local/api/tags"), json={"models": []})
    with patch("tools.run_hybrid.configure"), patch("tools.run_hybrid.socket.socket"), \
            patch("httpx.get", return_value=response), patch("agent.embeddings.OpenAIEmbedder._api_key"), \
            patch("uvicorn.run") as serve:
        with pytest.raises(SystemExit) as error:
            run_hybrid.main([])
        assert error.value.code == 2
        serve.assert_not_called()


def test_cli_invalid_configuration_returns_safe_json_instead_of_traceback():
    args = Namespace(provider="bad", model=None, base_url=None, dimensions=None, cache_dir=None)
    output = io.StringIO()
    with patch("tools.build_rag_index.load_embedding_environment"), \
            patch("tools.build_rag_index.create_embedder", side_effect=RetrievalError("설정 오류")), \
            redirect_stdout(output):
        code = asyncio.run(build_rag_index.run(args))
    assert code == 1
    assert json.loads(output.getvalue()) == {"status": "error", "error": "설정 오류"}


def test_probe_calls_embedding_but_health_does_not():
    # health는 키 존재·문서 구성만 확인한다. paid probe만 실제 임베딩 함수를 부른다.
    embedder = Namespace(provider="openai", model="text-embedding-3-small",
                         identity=AsyncMock(return_value={"dimensions": 2}),
                         embed=AsyncMock(return_value=[[1.0, 0.0]]),
                         usage_snapshot=lambda: {}, aclose=AsyncMock())
    retriever = Namespace(embedder=embedder, health=AsyncMock(return_value={"status": "ok"}),
                         aclose=AsyncMock())
    for probe in (False, True):
        output = io.StringIO()
        args = Namespace(provider="openai", model=None, base_url=None, dimensions=None,
                         cache_dir=None, health=not probe, probe=probe)
        with patch("tools.build_rag_index.load_embedding_environment"), \
                patch("tools.build_rag_index.create_embedder", return_value=embedder), \
                patch("tools.build_rag_index.KnowledgeRetriever", return_value=retriever), \
                redirect_stdout(output):
            assert asyncio.run(build_rag_index.run(args)) == 0
        assert json.loads(output.getvalue())["remote_verified"] == probe
        assert embedder.embed.await_count == int(probe)
