"""임베딩 공급자 계약 검사. MockTransport만 사용하며 실제 API/모델 호출은 없다."""

import json
import os
import unittest
from unittest.mock import patch

import httpx

from agent.embeddings import (
    OPENAI_BASE_URL, QUERY_INSTRUCTION, OpenAIEmbedder, OllamaEmbedder,
    RetrievalError, create_embedder,
)


def response_body(count=1, dimensions=3, model="text-embedding-3-small", tokens=7):
    return {"model": model, "data": [
        {"index": i, "embedding": [float(i + 1)] * dimensions} for i in range(count)
    ], "usage": {"prompt_tokens": tokens, "total_tokens": tokens}}


class EmbeddingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env = patch.dict(os.environ, {"OPENAI_API_KEY": "test-key-not-real"})
        self.env.start()
        self.addCleanup(self.env.stop)

    async def openai(self, handler, dimensions=3):
        embedder = OpenAIEmbedder(dimensions=dimensions)
        await embedder.client.aclose()
        embedder.client = httpx.AsyncClient(base_url=OPENAI_BASE_URL,
                                            transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(embedder.aclose)
        return embedder

    async def test_openai_request_raw_query_reorder_and_usage(self):
        calls = []
        def handler(request):
            calls.append(request)
            body = response_body(2)
            body["data"].reverse()
            return httpx.Response(200, json=body)
        embedder = await self.openai(handler)
        vectors = await embedder.embed([embedder.format_query("  로그인 오류  "), "문서 본문"])
        self.assertEqual(vectors, [[1.0] * 3, [2.0] * 3])
        self.assertEqual(str(calls[0].url), "https://api.openai.com/v1/embeddings")
        self.assertEqual(calls[0].headers["authorization"], "Bearer test-key-not-real")
        self.assertEqual(json.loads(calls[0].content), {
            "model": "text-embedding-3-small", "input": ["로그인 오류", "문서 본문"],
            "encoding_format": "float", "dimensions": 3,
        })
        self.assertEqual(embedder.usage_snapshot(), {"successful_api_calls": 1, "input_tokens": 7,
                                                    "failed_api_calls": 0, "unknown_usage_api_calls": 0})
        snapshot = embedder.usage_snapshot()
        snapshot["input_tokens"] = 100
        self.assertEqual(embedder.usage_snapshot()["input_tokens"], 7)

    async def test_openai_batches_at_most_64_inputs(self):
        sizes = []
        def handler(request):
            count = len(json.loads(request.content)["input"])
            sizes.append(count)
            return httpx.Response(200, json=response_body(count, tokens=count))
        embedder = await self.openai(handler)
        result = await embedder.embed([f"document {i}" for i in range(65)])
        self.assertEqual(sizes, [64, 1])
        self.assertEqual(len(result), 65)
        self.assertEqual(embedder.usage_snapshot()["successful_api_calls"], 2)
        self.assertEqual(embedder.usage_snapshot()["input_tokens"], 65)

    async def test_failure_on_later_batch_preserves_prior_usage_and_never_retries(self):
        calls = []
        def handler(request):
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(200, json=response_body(64, tokens=80))
            return httpx.Response(429, json={"error": "private-response"})
        embedder = await self.openai(handler)
        with self.assertRaises(RetrievalError):
            await embedder.embed(["hello"] * 65)
        self.assertEqual(len(calls), 2)
        self.assertEqual(embedder.usage_snapshot()["successful_api_calls"], 1)
        self.assertEqual(embedder.usage_snapshot()["failed_api_calls"], 1)
        self.assertEqual(embedder.usage_snapshot()["input_tokens"], 80)

    async def test_missing_key_is_lazy_and_makes_no_requests(self):
        calls = []
        embedder = await self.openai(lambda request: calls.append(request))
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            with self.assertRaises(RetrievalError):
                await embedder.identity()
            with self.assertRaises(RetrievalError):
                await embedder.embed(["hello"])
        self.assertEqual(calls, [])
        self.assertEqual(embedder.usage_snapshot()["failed_api_calls"], 0)

    async def test_empty_or_invalid_inputs_fail_before_api_call(self):
        calls = []
        embedder = await self.openai(lambda request: calls.append(request))
        for value in ([], [""], [" "], [1], "hello", ["valid", None]):
            with self.subTest(value=value), self.assertRaises(RetrievalError):
                await embedder.embed(value)
        self.assertEqual(calls, [])

    async def test_auth_rate_limit_timeout_and_connection_messages_are_sanitised(self):
        for status, word in ((401, "인증"), (403, "권한"), (429, "한도"), (500, "HTTP 500")):
            with self.subTest(status=status):
                calls = []
                def handler(request):
                    calls.append(request)
                    return httpx.Response(status, json={"error": "test-key-not-real private-response"})
                embedder = await self.openai(handler)
                with self.assertRaises(RetrievalError) as caught:
                    await embedder.embed(["hello"])
                self.assertIn(word, str(caught.exception))
                self.assertNotIn("private-response", str(caught.exception))
                self.assertNotIn("test-key", repr(caught.exception))
                self.assertEqual(len(calls), 1)
                self.assertEqual(embedder.usage_snapshot()["failed_api_calls"], 1)
        for error_type in (httpx.ReadTimeout, httpx.ConnectError):
            def handler(request):
                raise error_type("private-response test-key-not-real", request=request)
            embedder = await self.openai(handler)
            with self.assertRaises(RetrievalError) as caught:
                await embedder.embed(["hello"])
            self.assertNotIn("private-response", str(caught.exception))

    async def test_malformed_response_rejected_with_usage_counted(self):
        mutations = {
            "model": lambda body: body.update(model="other-model"),
            "count": lambda body: body["data"].append({"index": 1, "embedding": [1, 1, 1]}),
            "index_range": lambda body: body["data"][0].update(index=4),
            "index_bool": lambda body: body["data"][0].update(index=True),
            "dimension": lambda body: body["data"][0].update(embedding=[1, 2]),
            "zero": lambda body: body["data"][0].update(embedding=[0, 0, 0]),
            "nan": lambda body: body["data"][0].update(embedding=[float("nan"), 1, 1]),
            "boolean": lambda body: body["data"][0].update(embedding=[True, 1, 1]),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                body = response_body()
                mutate(body)
                embedder = await self.openai(lambda request: httpx.Response(200, content=json.dumps(body)))
                with self.assertRaises(RetrievalError):
                    await embedder.embed(["hello"])
                self.assertEqual(embedder.usage_snapshot()["failed_api_calls"], 1)
                self.assertEqual(embedder.usage_snapshot()["input_tokens"], 7)

    async def test_duplicate_indices_rejected(self):
        body = response_body(2)
        body["data"][1]["index"] = 0
        embedder = await self.openai(lambda request: httpx.Response(200, json=body))
        with self.assertRaises(RetrievalError):
            await embedder.embed(["a", "b"])

    async def test_openai_identity_has_config_revision_not_fake_remote_digest(self):
        calls = []
        embedder = await self.openai(lambda request: calls.append(request))
        identity = await embedder.identity()
        self.assertEqual(identity, {"provider": "openai", "model": "text-embedding-3-small",
                                    "dimensions": 3, "query_format": "raw", "cache_revision": "openai-v1"})
        self.assertNotIn("digest", identity)
        self.assertEqual(calls, [])

    async def test_ollama_identity_and_query_format_preserve_cache_contract(self):
        embedder = OllamaEmbedder()
        await embedder.client.aclose()
        calls = []
        def handler(request):
            calls.append(request.url.path)
            if request.url.path == "/api/tags":
                return httpx.Response(200, json={"models": [{
                    "name": "qwen3-embedding:0.6b", "digest": "original-digest",
                    "capabilities": ["embedding"], "details": {"embedding_length": 3},
                }]})
            self.assertFalse(json.loads(request.content)["truncate"])
            return httpx.Response(200, json={"embeddings": [[1, 0, 0]], "prompt_eval_count": 5})
        embedder.client = httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(embedder.aclose)
        self.assertEqual(await embedder.identity(), {"model": "qwen3-embedding:0.6b",
                                                     "digest": "original-digest", "dimensions": 3})
        self.assertEqual(embedder.format_query(" 질문 "), f"Instruct: {QUERY_INSTRUCTION}\nQuery:질문")
        self.assertEqual(embedder.query_format, QUERY_INSTRUCTION)
        await embedder.embed(["hello"])
        self.assertEqual(embedder.usage_snapshot()["input_tokens"], 5)
        self.assertEqual(embedder.usage_snapshot()["successful_api_calls"], 1)

    async def test_factory_defaults_overrides_and_provider_model_conflicts(self):
        with patch.dict(os.environ, {}, clear=True):
            default = create_embedder()
            self.addAsyncCleanup(default.aclose)
            self.assertIsInstance(default, OllamaEmbedder)
            self.assertEqual(default.model, "qwen3-embedding:0.6b")
            openai = create_embedder(provider="openai")
            self.addAsyncCleanup(openai.aclose)
            self.assertEqual(openai.dimensions, 1536)
            large = create_embedder(provider="openai", model="text-embedding-3-large")
            self.addAsyncCleanup(large.aclose)
            self.assertEqual(large.dimensions, 3072)
        with patch.dict(os.environ, {"RAG_EMBEDDING_PROVIDER": "openai", "RAG_EMBEDDING_MODEL": "qwen3-embedding:0.6b"}):
            with self.assertRaises(RetrievalError):
                create_embedder()
        for kwargs in ({"provider": "invalid"}, {"provider": "ollama", "model": "text-embedding-3-small"},
                       {"provider": "openai", "model": "other"}, {"provider": "openai", "dimensions": 3072},
                       {"provider": "openai", "base_url": "https://untrusted.example/v1"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(RetrievalError):
                create_embedder(**kwargs)

    async def test_factory_explicit_provider_overrides_env_but_does_not_reuse_wrong_model(self):
        with patch.dict(os.environ, {"RAG_EMBEDDING_PROVIDER": "ollama", "RAG_EMBEDDING_MODEL": "text-embedding-3-large",
                                     "OPENAI_EMBEDDING_DIMENSIONS": "1024", "OPENAI_BASE_URL": "https://untrusted.example"}):
            selected = create_embedder(provider="openai")
            self.addAsyncCleanup(selected.aclose)
            self.assertEqual(selected.dimensions, 1024)
            self.assertEqual(selected.base_url, OPENAI_BASE_URL)


if __name__ == "__main__":
    unittest.main()
