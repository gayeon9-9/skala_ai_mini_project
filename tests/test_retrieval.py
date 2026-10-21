"""고정 벡터로 검색 계약을 검증한다. 한국어 의미 검색 정확도 테스트는 아니다."""

import asyncio
import json
import math
import tempfile
import unittest
from pathlib import Path

import httpx

from agent.retrieval import KnowledgeRetriever, OllamaEmbedder, _normalise, RetrievalError


class FixedEmbedder:
    def __init__(self):
        self.digest = "fixed-v1"
        self.calls = []
        self.invalid = None

    async def identity(self):
        return {"model": "fixed-test-only", "digest": self.digest, "dimensions": 3}

    async def embed(self, texts):
        self.calls.append(texts)
        await asyncio.sleep(0)
        if self.invalid is not None:
            return self.invalid
        return [[0, 0, 1] if "unrelated" in text else [1, 0, 0] for text in texts]


class RetrievalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rag-unit-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "data").mkdir()
        self.docs = [
            {"document_id": "faq:a", "title": "Login guide", "path": "data/a.md",
             "audience": "user", "systems": ["auth-service"], "version": "1"},
            {"document_id": "faq:b", "title": "Payment guide", "path": "data/b.md",
             "audience": "user", "systems": ["payments-service"], "version": "1"},
            {"document_id": "runbook:a", "title": "Operator guide", "path": "data/c.md",
             "audience": "operator", "systems": ["*"], "version": "1"},
        ]
        for doc in self.docs:
            (self.root / doc["path"]).write_text("Guide body. " * 100)
        self.catalog = self.root / "data/knowledge_catalog.json"
        self.save_catalog()
        self.embedder = FixedEmbedder()
        self.rag = KnowledgeRetriever(root=self.root, embedder=self.embedder)

    def save_catalog(self):
        self.catalog.write_text(json.dumps({"version": "test", "documents": self.docs}))

    async def test_audience_system_filter_and_document_dedup(self):
        result = await self.rag.search("login", "auth-service")
        self.assertEqual(result["status"], "ok")
        self.assertEqual([d["document_id"] for d in result["documents"]], ["faq:a"])
        self.assertEqual(result["documents"][0]["score"], 1)
        operator = await self.rag.search("login", "auth-service", audience="operator")
        self.assertEqual([d["document_id"] for d in operator["documents"]], ["runbook:a"])
        self.assertGreater((await self.rag.build_index())["chunk_count"], len(self.docs))

    async def test_no_match_is_distinct_from_embedding_error(self):
        result = await self.rag.search("unrelated", "auth-service")
        self.assertEqual(result["status"], "no_match")
        self.assertEqual(result["documents"], [])
        self.embedder.invalid = [[math.nan, 0, 0]]
        failed = await self.rag.search("login", "auth-service")
        self.assertEqual(failed["status"], "error")
        self.assertEqual(failed["documents"], [])

    async def test_document_model_and_metadata_changes_invalidate_cache(self):
        first = await self.rag.build_index()
        count = len(self.embedder.calls)
        self.assertEqual((await self.rag.build_index())["index_version"], first["index_version"])
        self.assertEqual(len(self.embedder.calls), count)
        (self.root / "data/a.md").write_text("changed text")
        second = await self.rag.build_index()
        self.assertNotEqual(first["index_version"], second["index_version"])
        self.embedder.digest = "fixed-v2"
        third = await self.rag.build_index()
        self.assertNotEqual(second["index_version"], third["index_version"])
        self.docs[0]["systems"] = ["payments-service"]
        self.save_catalog()
        fourth = await self.rag.build_index()
        self.assertNotEqual(third["index_version"], fourth["index_version"])

    async def test_new_instance_loads_disk_cache_without_embedding_documents(self):
        await self.rag.build_index()
        second_embedder = FixedEmbedder()
        second = KnowledgeRetriever(root=self.root, embedder=second_embedder)
        await second.build_index()
        self.assertEqual(second_embedder.calls, [])
        await second.search("login", "auth-service")
        self.assertEqual(len(second_embedder.calls), 1)
        self.assertTrue(second_embedder.calls[0][0].startswith("Instruct:"))

    async def test_corrupted_disk_cache_is_rebuilt_from_documents(self):
        result = await self.rag.build_index()
        path = self.root / ".rag_cache" / f"{result['index_version']}.json"
        path.write_text("{broken")
        other = FixedEmbedder()
        await KnowledgeRetriever(root=self.root, embedder=other).build_index()
        self.assertEqual(len(other.calls), 1)
        self.assertIsInstance(json.loads(path.read_text())["vectors"], list)

    async def test_parallel_requests_embed_document_batch_once(self):
        results = await asyncio.gather(*[
            self.rag.search("login", "auth-service") for _ in range(3)
        ])
        self.assertTrue(all(r["status"] == "ok" for r in results))
        batches = [call for call in self.embedder.calls if len(call) > 1]
        self.assertEqual(len(batches), 1)

    async def test_invalid_metadata_and_outside_paths_fail_closed(self):
        for path in ("../outside.md", "/etc/passwd"):
            with self.subTest(path=path):
                self.docs[0]["path"] = path
                self.save_catalog()
                result = await self.rag.search("login", "auth-service")
                self.assertEqual(result["status"], "error")
                self.assertEqual(self.embedder.calls, [])

    async def test_empty_query_invalid_scope_and_no_system_candidates(self):
        self.assertEqual((await self.rag.search(" ", "auth-service"))["status"], "no_match")
        self.assertEqual((await self.rag.search("hi", "auth-service", "invalid"))["status"], "error")
        self.assertEqual((await self.rag.search("hi", ""))["status"], "error")
        self.assertEqual((await self.rag.search("hi", "auth-service", k=0))["status"], "error")
        self.assertEqual(self.embedder.calls, [])
        self.assertEqual((await self.rag.search("hi", "log-service"))["status"], "no_match")

    async def test_missing_model_reports_error_without_autopull(self):
        calls = []
        def handler(request):
            calls.append(request.url.path)
            return httpx.Response(200, json={"models": []})
        embedder = OllamaEmbedder()
        await embedder.client.aclose()
        embedder.client = httpx.AsyncClient(base_url="http://test",
                                            transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(embedder.aclose)
        result = await KnowledgeRetriever(root=self.root, embedder=embedder).search("hi", "auth-service")
        self.assertEqual(result["status"], "error")
        self.assertEqual(calls, ["/api/tags"])

    def test_vector_shape_finiteness_and_normalisation(self):
        for vectors in ([], [[0, 0, 0]], [[1, 2]], [[float("inf"), 0, 0]], [[True, 0, 0]]):
            with self.subTest(vectors=vectors), self.assertRaises(RetrievalError):
                _normalise(vectors, 1, 3)
        self.assertEqual(_normalise([[3, 0, 4]], 1, 3), [[0.6, 0, 0.8]])


if __name__ == "__main__":
    unittest.main()
