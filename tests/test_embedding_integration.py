"""실제 검색기·앱 수명에 임베딩 공급자를 연결한다. HTTP만 대체하며 외부 호출은 없다."""

import os

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx

import server
from agent.embeddings import OpenAIEmbedder, OllamaEmbedder, RetrievalError
from agent.retrieval import KnowledgeRetriever


class EmbeddingIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="embedding-integration-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "data").mkdir()
        self.body = "로그인 화면이 멈출 때 브라우저를 확인합니다."
        (self.root / "data/login.md").write_text(self.body)
        self.document = {"document_id": "faq:login", "title": "로그인 안내", "path": "data/login.md",
                         "audience": "user", "systems": ["auth-service"], "version": "1"}
        (self.root / "data/knowledge_catalog.json").write_text(json.dumps({
            "version": "fixture-v1", "documents": [self.document],
        }))
        self.env = patch.dict(os.environ, {"OPENAI_API_KEY": "integration-key-not-real"})
        self.env.start()
        self.addCleanup(self.env.stop)

    async def embedder(self, provider, dimensions=3, error_status=None):
        calls = []
        adapter = OpenAIEmbedder(dimensions=dimensions) if provider == "openai" else OllamaEmbedder()
        await adapter.client.aclose()
        def handler(request):
            payload = json.loads(request.content) if request.content else None
            calls.append({"url": str(request.url), "path": request.url.path, "payload": payload})
            if request.url.path == "/api/tags":
                return httpx.Response(200, json={"models": [{
                    "name": "qwen3-embedding:0.6b", "digest": "fixture-ollama-digest",
                    "capabilities": ["embedding"], "details": {"embedding_length": dimensions},
                }]})
            if error_status:
                return httpx.Response(error_status, json={"error": "private-body integration-key-not-real"})
            texts = payload["input"]
            vectors = [[1.0] + [0.0] * (dimensions - 1) for _ in texts]
            if provider == "openai":
                return httpx.Response(200, json={"model": adapter.model,
                    "data": [{"index": i, "embedding": vec} for i, vec in enumerate(vectors)],
                    "usage": {"prompt_tokens": len(texts)}})
            return httpx.Response(200, json={"embeddings": vectors, "prompt_eval_count": len(texts)})
        base = "https://api.openai.com/v1" if provider == "openai" else "http://ollama.test"
        adapter.client = httpx.AsyncClient(base_url=base, transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(adapter.aclose)
        return adapter, calls

    async def test_switching_provider_creates_independent_cache_and_can_switch_back(self):
        local, local_calls = await self.embedder("ollama")
        cloud, cloud_calls = await self.embedder("openai")
        local_index = await KnowledgeRetriever(root=self.root, embedder=local).build_index()
        cloud_index = await KnowledgeRetriever(root=self.root, embedder=cloud).build_index()
        self.assertNotEqual(local_index["index_version"], cloud_index["index_version"])
        self.assertEqual(len(list((self.root / ".rag_cache").glob("*.json"))), 2)
        local_calls.clear()
        restored = await KnowledgeRetriever(root=self.root, embedder=local).build_index()
        self.assertEqual(restored["index_version"], local_index["index_version"])
        self.assertEqual([call["path"] for call in local_calls], ["/api/tags"])
        self.assertEqual(len(cloud_calls), 1)

    async def test_old_manifest_is_rebuilt_for_section_boundaries_without_deletion(self):
        # 과거 색인에는 절의 적용 조건이 없다. 새 청크 전략에서는 재사용하지 않고
        # 한 번 재생성하되 과거 파일은 지우지 않는다. 이후 새 색인은 재사용한다.
        digest = lambda value: hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                                        separators=(",", ":")).encode()).hexdigest()
        old_instruction = "Retrieve relevant IT helpdesk guidance or incident response procedures for the query."
        chunk = {**self.document, "chunk_id": "faq:login:0", "text": self.body,
                 "document_hash": digest(self.body)}
        manifest = {"schema_version": 1, "catalog_version": "fixture-v1",
                    "embedding": {"model": "qwen3-embedding:0.6b", "digest": "fixture-ollama-digest", "dimensions": 3},
                    "query_instruction": old_instruction, "chunk_size": 900, "chunk_overlap": 100,
                    "content_hash": digest([chunk])}
        signature = digest(manifest)
        cache = self.root / ".rag_cache"
        cache.mkdir()
        (cache / f"{signature}.json").write_text(json.dumps({
            "index_version": signature, "manifest": manifest, "chunks": [chunk], "vectors": [[1, 0, 0]],
        }))
        local, calls = await self.embedder("ollama")
        result = await KnowledgeRetriever(root=self.root, embedder=local).build_index()
        self.assertNotEqual(result["index_version"], signature)
        self.assertEqual([call["path"] for call in calls], ["/api/tags", "/api/embed"])
        self.assertTrue((cache / f"{signature}.json").exists())
        calls.clear()
        restored = await KnowledgeRetriever(root=self.root, embedder=local).build_index()
        self.assertEqual(restored["index_version"], result["index_version"])
        self.assertEqual([call["path"] for call in calls], ["/api/tags"])

    async def test_dimension_and_query_format_change_invalidate_cache(self):
        small, _ = await self.embedder("openai", dimensions=2)
        wider, wider_calls = await self.embedder("openai", dimensions=3)
        first = await KnowledgeRetriever(root=self.root, embedder=small).build_index()
        rag = KnowledgeRetriever(root=self.root, embedder=wider)
        second = await rag.build_index()
        self.assertNotEqual(first["index_version"], second["index_version"])
        wider.query_format = "raw-v2-fixture"
        wider.format_query = lambda query: "revised query: " + query
        third = await rag.build_index()
        self.assertNotEqual(second["index_version"], third["index_version"])
        self.assertEqual(len(wider_calls), 2)

    async def test_query_format_and_provider_metadata_reach_search_result(self):
        for provider in ("openai", "ollama"):
            with self.subTest(provider=provider):
                adapter, calls = await self.embedder(provider)
                result = await KnowledgeRetriever(root=self.root, embedder=adapter).search(
                    " 로그인 화면이 멈춰요 ", "auth-service")
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["provider"], provider)
                self.assertEqual(result["model"], adapter.model)
                self.assertEqual(result["dimensions"], 3)
                self.assertEqual(result["documents"][0]["document_id"], "faq:login")
                sent = [call["payload"]["input"] for call in calls if call["payload"]][-1][0]
                if provider == "openai":
                    self.assertEqual(sent, "로그인 화면이 멈춰요")
                else:
                    self.assertTrue(sent.startswith("Instruct: "))
                    self.assertTrue(sent.endswith("\nQuery:로그인 화면이 멈춰요"))

    async def test_api_failure_is_search_error_and_never_local_fallback(self):
        for status in (401, 429, 500):
            with self.subTest(status=status):
                adapter, calls = await self.embedder("openai", error_status=status)
                with patch("agent.retrieval.OllamaEmbedder", side_effect=AssertionError("fallback forbidden")):
                    result = await KnowledgeRetriever(root=self.root, embedder=adapter).search("로그인", "auth-service")
                self.assertEqual(result["status"], "error")
                self.assertEqual(result["documents"], [])
                self.assertNotIn("private-body", result["error"])
                self.assertNotIn("integration-key", result["error"])
                self.assertEqual(len(calls), 1)
                self.assertFalse(list((self.root / ".rag_cache").glob("*.json")))


class EmbeddingLifespanTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.previous_state = dict(server.app.state._state)
        self.addCleanup(self.restore_state)
        self.llms = ("fake", SimpleNamespace(name="fast-test"), SimpleNamespace(name="quality-test"))

    def restore_state(self):
        server.app.state._state.clear()
        server.app.state._state.update(self.previous_state)

    async def test_configuration_error_happens_before_starting_mcp(self):
        for settings in ({"RAG_EMBEDDING_PROVIDER": "invalid", "RAG_MIN_SCORE": "0.5"},
                         {"RAG_EMBEDDING_PROVIDER": "ollama", "RAG_MIN_SCORE": "nan"}):
            with self.subTest(settings=settings), patch.dict(os.environ, settings), \
                    patch("server._build_llms", return_value=self.llms), patch("server.MCPClient") as mcp:
                with self.assertRaises((RetrievalError, ValueError)):
                    async with server.lifespan(server.app):
                        self.fail("잘못된 구성으로 앱이 시작되면 안 됩니다.")
                mcp.assert_not_called()

    async def test_initialization_failure_closes_mcp_retriever_and_created_store(self):
        mcp = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
        retriever = SimpleNamespace(aclose=AsyncMock())
        store = Mock()
        with patch("server._build_llms", return_value=self.llms), \
                patch("agent.embeddings.create_embedder", return_value=object()), \
                patch("agent.retrieval.KnowledgeRetriever", return_value=retriever), \
                patch("server.MCPClient", return_value=mcp), \
                patch("server.SqliteSessionStore", return_value=store), \
                patch("server.inquiry_store", side_effect=RuntimeError("init failure")):
            with self.assertRaises(RuntimeError):
                async with server.lifespan(server.app):
                    self.fail("초기화 실패 뒤 앱이 시작되면 안 됩니다.")
        mcp.start.assert_awaited_once()
        mcp.stop.assert_awaited_once()
        retriever.aclose.assert_awaited_once()
        store.close.assert_called_once()

    async def test_health_reports_configuration_without_embedding_call(self):
        mcp = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
        embedder = SimpleNamespace(provider="openai", model="text-embedding-3-small",
                                   embed=AsyncMock(side_effect=AssertionError("health is not a paid probe")))
        retriever = SimpleNamespace(embedder=embedder, aclose=AsyncMock())
        store = Mock()
        with patch("server._build_llms", return_value=self.llms), \
                patch("agent.embeddings.create_embedder", return_value=embedder), \
                patch("agent.retrieval.KnowledgeRetriever", return_value=retriever), \
                patch("server.MCPClient", return_value=mcp), \
                patch("server.SqliteSessionStore", return_value=store), \
                patch("server.inquiry_store", return_value=Mock()), \
                patch("server.build_graph", return_value=object()):
            async with server.lifespan(server.app):
                response = await server.health()
                self.assertEqual(response["embedding_provider"], "openai")
                self.assertEqual(response["embedding_model"], "text-embedding-3-small")
                embedder.embed.assert_not_called()
        store.close.assert_called_once()

    async def test_retriever_close_failure_still_closes_database(self):
        # 연결 종료에서 오류가 나더라도 SQLite 연결까지 남겨 두지 않아야 한다.
        # 정상 시작 후 종료 경로에서만 실패시켜 초기화 오류와 구분한다.
        mcp = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
        retriever = SimpleNamespace(aclose=AsyncMock(side_effect=RuntimeError("close failure")))
        store = Mock()
        with patch("server._build_llms", return_value=self.llms), \
                patch("agent.embeddings.create_embedder", return_value=object()), \
                patch("agent.retrieval.KnowledgeRetriever", return_value=retriever), \
                patch("server.MCPClient", return_value=mcp), \
                patch("server.SqliteSessionStore", return_value=store), \
                patch("server.inquiry_store", return_value=Mock()), \
                patch("server.build_graph", return_value=object()):
            with self.assertRaisesRegex(RuntimeError, "close failure"):
                async with server.lifespan(server.app):
                    self.assertIs(server.app.state.deps.session_store, store)
        mcp.stop.assert_awaited_once()
        retriever.aclose.assert_awaited_once()
        store.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
