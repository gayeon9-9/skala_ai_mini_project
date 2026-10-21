"""시스템 선택값이 API부터 실제 조회 대상까지 유지되는지 검증한다.

실제 MCP 프로세스나 LLM API를 실행하지 않고, 저장소도 임시 SQLite만 사용한다.
실행: .venv/bin/python -m unittest discover -s tests -p test_selected_system.py -v
"""
import os

# server를 import하기 전에 .env 로딩과 원격 실행 추적을 끈다.
# 개발자의 실제 API 키나 LangSmith 설정에 테스트 실행이 의존하지 않게 한다.
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient

import server
from agent.deps import SupervisorDeps
from agent.fake_llm import FastLLM, QualityLLM
from agent.graph import build_graph, research_node
from agent.session_store import SqliteSessionStore


ROOT = Path(__file__).resolve().parents[1]


class RecordingFastLLM(FastLLM):
    """기존 fake 응답을 쓰면서 분류·계획이 실행됐는지 관찰한다."""

    def __init__(self):
        super().__init__("test-fast", usd_per_1k_tokens=0.00015)
        self.calls = []

    def route(self, query):
        self.calls.append("route")
        return super().route(query)

    def plan(self, query, procedure_prompt):
        self.calls.append("plan")
        return super().plan(query, procedure_prompt)

    async def chat(self, query, history, on_token=None):
        self.calls.append("chat")
        return await super().chat(query, history, on_token=on_token)


class RecordingMCP:
    """저장소의 샘플 지표와 매뉴얼을 읽는 네트워크 없는 MCP 대역이다."""

    def __init__(self):
        self.monitoring = json.loads((ROOT / "data/monitoring.json").read_text())
        self.status_overrides = {}
        self.operations = []

    async def call_tool(self, name, arguments):
        self.operations.append(("call_tool", name, dict(arguments)))
        if name != "get_monitoring_status":
            raise AssertionError(f"예상하지 않은 도구: {name}")
        target = arguments["system_name"]
        data = self.monitoring[target]
        # 난수 변동 없이 같은 입력에 같은 수치를 돌려준다.
        result = {
            "system_name": target,
            "status": data["status"],
            "cpu_percent": data["cpu_base"],
            "memory_percent": data["memory_base"],
            "last_checked": "2026-10-20T09:00:00",
            "alerts": list(data.get("alerts", [])),
        }
        result.update(self.status_overrides.get(target, {}))
        return result

    async def find_resource_template(self, name):
        self.operations.append(("find_resource_template", name))
        return "ops", "runbook://incident-response/{incident_type}"

    async def read_resource(self, uri, server):
        self.operations.append(("read_resource", uri, server))
        incident_type = uri.rsplit("/", 1)[-1]
        return (ROOT / "data/runbooks" / f"{incident_type}.md").read_text()

    async def find_prompt(self, name):
        self.operations.append(("find_prompt", name))
        return "ops"

    async def get_prompt(self, name, arguments, server):
        self.operations.append(("get_prompt", name, dict(arguments), server))
        return "모니터링 조회 후 관련 매뉴얼을 확인하세요."

    @property
    def monitoring_targets(self):
        return [op[2]["system_name"] for op in self.operations if op[0] == "call_tool"]

    @property
    def resource_uris(self):
        return [op[1] for op in self.operations if op[0] == "read_resource"]


def sse_events(response):
    """한 번의 테스트 응답에서 SSE JSON 이벤트만 꺼낸다."""
    return [json.loads(line[6:]) for line in response.text.splitlines()
            if line.startswith("data: ") and line != "data: [DONE]"]


class SelectedSystemTests(unittest.TestCase):
    def setUp(self):
        temp_dir = tempfile.TemporaryDirectory(prefix="selected-system-test-")
        self.addCleanup(temp_dir.cleanup)
        self.store = SqliteSessionStore(Path(temp_dir.name) / "session.db")
        self.addCleanup(self.store.close)
        self.fast_llm = RecordingFastLLM()
        self.mcp = RecordingMCP()
        self.deps = SupervisorDeps(
            fast_llm=self.fast_llm,
            quality_llm=QualityLLM("test-quality", usd_per_1k_tokens=0.002),
            mcp=self.mcp,
            session_store=self.store,
            version="v2",
        )
        server.app.state.deps = self.deps
        server.app.state.graph = build_graph(self.deps)
        server.app.state.llm_backend = "fake"
        server.app.state.instance_id = id(self.deps)
        # context manager를 사용하지 않아 실제 MCP를 띄우는 lifespan은 실행하지 않는다.
        self.client = TestClient(server.app)
        self.addCleanup(self.client.close)

    def test_systems_returns_six_sample_systems(self):
        response = self.client.get("/systems")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "systems": list(self.mcp.monitoring), "data_source": "sample",
        })
        self.assertEqual(len(response.json()["systems"]), 6)
        self.assertEqual(self.mcp.operations, [])

    def test_catalog_failure_stops_requests_instead_of_guessing_target(self):
        # 목록 파일 문제를 입력 오류로 돌리거나 기본 시스템으로 우회하지 않아야 한다.
        with tempfile.TemporaryDirectory(prefix="broken-catalog-test-") as temp_dir:
            path = Path(temp_dir) / "monitoring.json"
            for content in (None, "{broken", "[]", "{}", '{"auth-service": null}'):
                with self.subTest(content=content):
                    if content is not None:
                        path.write_text(content, encoding="utf-8")
                    with patch("agent.system_catalog.MONITORING_PATH", path):
                        responses = [
                            self.client.get("/systems"),
                            self.client.post("/chat", json={
                                "message": "로그인이 안 돼요.", "selected_system": "auth-service",
                            }),
                            self.client.get("/chat/stream", params={
                                "message": "로그인이 안 돼요.", "selected_system": "auth-service",
                            }),
                        ]
                    for response in responses:
                        self.assertEqual(response.status_code, 503, response.text)
                        self.assertIn("시스템 목록", response.json()["detail"])
            self.assertEqual(self.mcp.operations, [])
            self.assertEqual(self.fast_llm.calls, [])

    def test_post_uses_selection_for_natural_or_conflicting_symptoms(self):
        # 자연어 증상에 키워드가 없거나 다른 시스템명이 있어도 선택값만 조회한다.
        for index, (selected, message) in enumerate([
            ("cache-service", "업무 화면이 안 열려요."),
            ("log-service", "auth-service가 느려요. payments-service도 확인해줘."),
        ]):
            with self.subTest(selected=selected):
                self.mcp.operations.clear()
                self.fast_llm.calls.clear()
                thread_id = f"post-{index}"
                response = self.client.post("/chat", json={
                    "message": message, "thread_id": thread_id,
                    "selected_system": selected,
                })
                self.assertEqual(response.status_code, 200, response.text)
                body = response.json()
                self.assertEqual(body["metadata"]["route"], "incident")
                self.assertEqual(body["metadata"]["selected_system"], selected)
                self.assertEqual(body["metadata"]["data_source"], "sample")
                self.assertEqual(self.mcp.monitoring_targets, [selected])
                self.assertEqual(self.fast_llm.calls, [])
                self.assertFalse(any(op[0] in {"find_prompt", "get_prompt"}
                                     for op in self.mcp.operations))
                plans = [event for event in body["events"] if event["type"] == "PlanCreated"]
                self.assertEqual(plans[0]["targets"], [selected])
                self.assertIn(selected, body["reply"])
                self.assertEqual(self.store.load(thread_id), [
                    {"role": "user", "content": message},
                    {"role": "assistant", "content": body["reply"]},
                ])
        # 4단계부터 선택 접수는 RAG에서 문서를 검색한다. MCP는 대상 점검만 담당한다.
        self.assertEqual(self.mcp.resource_uris, [])

    def test_sse_uses_selection_for_natural_or_conflicting_symptoms(self):
        for index, message in enumerate([
            "로그인 버튼을 눌러도 화면이 그대로예요.",
            "auth-service 장애를 진단하고 web-01도 확인해줘.",
        ]):
            with self.subTest(message=message):
                self.mcp.operations.clear()
                self.fast_llm.calls.clear()
                thread_id = f"sse-{index}"
                response = self.client.get("/chat/stream", params={
                    "message": message, "thread_id": thread_id,
                    "selected_system": "payments-service",
                })
                self.assertEqual(response.status_code, 200, response.text)
                self.assertIn("text/event-stream", response.headers["content-type"])
                self.assertTrue(response.text.rstrip().endswith("data: [DONE]"))
                events = sse_events(response)
                self.assertEqual(self.mcp.monitoring_targets, ["payments-service"])
                self.assertEqual(self.fast_llm.calls, [])
                self.assertEqual(next(e for e in events if e["type"] == "RouteDecided")["route"],
                                 "incident")
                answer = next(e for e in events if e["type"] == "FinalAnswer")
                self.assertEqual(answer["selected_system"], "payments-service")
                self.assertEqual(answer["data_source"], "sample")
                self.assertIn("payments-service", answer["reply"])
                self.assertEqual(self.store.load(thread_id), [
                    {"role": "user", "content": message},
                    {"role": "assistant", "content": answer["reply"]},
                ])

    def test_invalid_or_blank_input_fails_before_graph_and_tools(self):
        graph = Mock(ainvoke=AsyncMock(), astream=Mock())
        server.app.state.graph = graph
        invalid_inputs = [
            {"selected_system": "billing-service"},
            {"selected_system": ""},
            {"selected_system": "   "},
            {"message": "   "},
            {"thread_id": "   "},
        ]
        for changes in invalid_inputs:
            for endpoint in ("/chat", "/chat/stream"):
                with self.subTest(changes=changes, endpoint=endpoint):
                    payload = {"message": "로그인이 안 돼요.", "thread_id": "invalid",
                               "selected_system": "auth-service", **changes}
                    response = (self.client.post(endpoint, json=payload) if endpoint == "/chat"
                                else self.client.get(endpoint, params=payload))
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertNotIn("text/event-stream", response.headers["content-type"])
        graph.ainvoke.assert_not_called()
        graph.astream.assert_not_called()
        self.assertEqual(self.mcp.operations, [])
        self.assertEqual(self.fast_llm.calls, [])
        self.assertEqual(self.store.load("invalid"), [])

    def test_v1_rejects_selection_before_running_agent(self):
        self.deps.version = "v1"
        for endpoint in ("/chat", "/chat/stream"):
            with self.subTest(endpoint=endpoint):
                payload = {"message": "로그인이 안 돼요.", "selected_system": "auth-service"}
                response = (self.client.post(endpoint, json=payload) if endpoint == "/chat"
                            else self.client.get(endpoint, params=payload))
                self.assertEqual(response.status_code, 409, response.text)
                self.assertIn("v2", response.json()["detail"])
        self.assertEqual(self.mcp.operations, [])
        self.assertEqual(self.fast_llm.calls, [])

    def test_legacy_general_chat_without_selection_still_works(self):
        # 선택 필드가 없는 기존 클라이언트의 일반 대화 경로도 유지한다.
        response = self.client.post("/chat", json={"message": "안녕", "thread_id": "legacy"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["metadata"]["route"], "chat")
        self.assertIsNone(response.json()["metadata"]["selected_system"])
        self.assertEqual(self.fast_llm.calls, ["route", "chat"])
        self.assertEqual(self.mcp.operations, [])
        response = self.client.get("/chat/stream", params={"message": "안녕", "thread_id": "legacy"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(any(event["type"] == "FinalAnswer" for event in sse_events(response)))
        self.assertEqual(self.fast_llm.calls, ["route", "chat", "route", "chat"])
        self.assertEqual(self.mcp.operations, [])

    def test_no_runbook_for_healthy_unknown_or_unmatched_selected_evidence(self):
        # 정상·조회 불명·알 수 없는 증상을 DB 장애로 바꾸어 안내하지 않는지 확인한다.
        for status, alerts in [
            ("healthy", ["latency 관련 이전 알림"]),
            ("unknown", ["network 상태를 조회할 수 없음"]),
            ("degraded", ["unrecognized symptom"]),
        ]:
            with self.subTest(status=status):
                self.mcp.operations.clear()
                self.mcp.status_overrides["auth-service"] = {"status": status, "alerts": alerts}
                response = self.client.post("/chat", json={
                    "message": "로그인이 안 돼요.", "selected_system": "auth-service",
                })
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(self.mcp.monitoring_targets, ["auth-service"])
                self.assertFalse(any(op[0] in {"find_resource_template", "read_resource"}
                                     for op in self.mcp.operations))
                replan = next(event for event in response.json()["events"]
                              if event["type"] == "ReplanCheck")
                self.assertTrue(replan["is_sufficient"])
                self.assertTrue(replan["runbook_skipped"])
                self.assertTrue(replan["reason"])

    def test_research_rejects_mixed_targets_before_any_rpc(self):
        # 정상 작업이 앞에 있어도 모든 대상을 먼저 검사해 다른 시스템 조회를 막는다.
        state = {"selected_system": "auth-service", "events": [], "tasks": [
            {"id": "allowed", "agent": "research", "kind": "monitoring",
             "target": "auth-service", "status": "pending"},
            {"id": "wrong", "agent": "research", "kind": "monitoring",
             "target": "web-01", "status": "pending"},
        ]}
        with self.assertRaisesRegex(ValueError, "조회 대상"):
            asyncio.run(research_node(state, self.deps))
        self.assertEqual(self.mcp.operations, [])


if __name__ == "__main__":
    unittest.main()
