"""HTTP 전송 방식과 무관한 접수·실행·저장 수명 관리.

같은 request_id의 재전송은 저장된 결과를 돌려주며, FinalAnswer는 DB 저장 뒤에만
보낸다. 연결이 끊겨도 문의 원문은 남고 완료하지 못한 요청은 재시도할 수 있다.
"""
import asyncio
import time

from langsmith import traceable
from langsmith.run_helpers import get_current_run_tree

from .inquiry_store import SqliteInquiryStore
from .supervisor_v1 import run_v1
from .observability import annotate_run, completion_metadata


def inquiry_store(deps):
    # 실제 서비스에서는 lifespan이 주입한다. 기존 교육용 생성자/테스트도 호환한다.
    if deps.inquiry_store is None:
        deps.inquiry_store = SqliteInquiryStore(deps.session_store)
    return deps.inquiry_store


class ChatService:
    def __init__(self, deps, graph, request):
        self.deps, self.graph, self.request = deps, graph, request
        self.store = inquiry_store(deps)
        self.started = self.store.begin(
            request.thread_id, request.selected_system, request.message, request.request_id)
        self.state = {
            "query": request.message, "thread_id": request.thread_id,
            "selected_system": request.selected_system,
            "history": self.started["history"], "events": [], "cost_usd": 0.0,
            "intake_previous": self.store.get_thread(request.thread_id)["details"].get("intake", {}),
        }
        self.finished = self.started["replayed"]
        self.started_at = None
        self.trace_metadata = {**(deps.trace_context or {}),
            "inquiry_id": self.started["inquiry_id"], "request_id": request.request_id,
            "thread_id": request.thread_id, "selected_system": request.selected_system,
            "data_source": "sample" if request.selected_system else None}

    def _trace_start(self):
        # traceable 내부에서 실행해야 현재 요청의 root run에 연결된다.
        self.started_at = time.perf_counter()
        annotate_run({**self.trace_metadata, "replayed": self.started["replayed"]})

    def _complete(self, result):
        rt = get_current_run_tree()
        run_id = str(rt.id) if rt else None
        result = {**result, "inquiry_id": self.started["inquiry_id"],
                  "request_id": self.request.request_id, "langsmith_run_id": run_id}
        telemetry = {**self.trace_metadata,
                     **completion_metadata(result, time.perf_counter() - self.started_at),
                     "tracing_enabled": rt is not None}
        annotate_run(telemetry)
        result["telemetry"] = telemetry
        self.store.complete(self.request.request_id, result, run_id=run_id)
        annotate_run({"request_status": "completed"})
        self.finished = True
        return result

    def interrupted(self):
        """SSE 생성기가 시작되기 전에 끊겨도 BackgroundTask에서 상태를 정리한다."""
        if not self.finished:
            self.store.fail(self.request.request_id, "응답 연결이 중단되었습니다.", interrupted=True)
            annotate_run({"request_status": "interrupted"})
            self.finished = True

    @traceable(name="chat_request")
    async def invoke(self):
        self._trace_start()
        if self.started["replayed"]:
            return self.started["result"]
        try:
            if self.deps.version == "v2":
                result = await self.graph.ainvoke(self.state)
            else:
                result = await run_v1(self.deps, self.request.message,
                                      self.request.thread_id, self.state["history"])
            return self._complete(result)
        except asyncio.CancelledError:
            self.interrupted()
            raise
        except Exception as exc:
            # 외부 예외 원문에는 경로·요청 정보가 섞일 수 있어 분류명만 저장한다.
            annotate_run({"request_status": "failed", "error_type": type(exc).__name__})
            self.store.fail(self.request.request_id, type(exc).__name__)
            self.finished = True
            raise

    @traceable(name="chat_request_stream")
    async def stream(self):
        self._trace_start()
        try:
            if self.started["replayed"]:
                result = self.started["result"]
            elif self.deps.version != "v2":
                result = await run_v1(self.deps, self.request.message,
                                      self.request.thread_id, self.state["history"])
                for event in result.get("events", []):
                    yield event
                result = self._complete(result)
            else:
                result, seen = dict(self.state), 0
                async for mode, payload in self.graph.astream(
                        self.state, stream_mode=["updates", "custom"]):
                    if mode == "custom":
                        yield payload
                        continue
                    for node_name, delta in payload.items():
                        if not delta:
                            continue
                        result.update(delta)
                        for event in (delta.get("events") or [])[seen:]:
                            yield {**event, "node": node_name}
                        if delta.get("events"):
                            seen = len(delta["events"])
                result = self._complete(result)
            yield {"type": "FinalAnswer", "reply": result["final"],
                   "selected_system": self.request.selected_system,
                   "data_source": "sample" if self.request.selected_system else None,
                   "inquiry_id": result["inquiry_id"], "request_id": self.request.request_id,
                   "intake": result.get("intake"),
                   "retrieval": result.get("retrieval"), "validation": result.get("validation"),
                   "langsmith_run_id": result.get("langsmith_run_id")}
        except asyncio.CancelledError:
            self.interrupted()
            raise
        except Exception as exc:
            annotate_run({"request_status": "failed", "error_type": type(exc).__name__})
            self.store.fail(self.request.request_id, type(exc).__name__)
            self.finished = True
            yield {"type": "Error", "message": "처리를 완료하지 못했습니다. 같은 요청을 다시 시도해 주세요."}
        finally:
            self.interrupted()
