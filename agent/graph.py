"""v2 Supervisor — 실제 langgraph.StateGraph (Wave 4·5·6·7·8·10b·15 통합).

route → (chat 직행 | incident 파이프라인)
incident: plan(MCP Prompt 절차 반영) → gate(권위 게이트) → research(가드+병렬+재시도)
          → replan_check(부족 시 gap task 후 재조사) → analysis(needs_analysis=True일 때만
          실제 실행) → write → validate(반려 시 재작성) → finalize

모든 노드는 SupervisorDeps를 functools.partial로 주입받는 순수 함수다(Wave 13).
각 노드는 @traceable로 LangSmith에 계층 트리로 기록된다.
"""
import asyncio
import json
import time
from functools import partial
from pathlib import Path
from typing import Any, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, StateGraph
from langsmith import traceable

from .deps import SupervisorDeps
from .retry import with_retry
from .system_catalog import validate_selected_system


def _load_known_systems() -> set[str]:
    """ollama_llm.py/openai_llm.py와 동일한 소스(data/monitoring.json)에서
    시스템 목록을 읽는다 — plan_node가 MCP Prompt에 넘길 system_name을
    쿼리에서 멤버십 매칭으로 뽑아낼 때 쓴다."""
    path = Path(__file__).resolve().parent.parent / "data" / "monitoring.json"
    try:
        return set(json.loads(path.read_text()).keys())
    except (OSError, json.JSONDecodeError):
        return {"auth-service", "web-01"}


KNOWN_SYSTEMS = _load_known_systems()


def _extract_system_name(query: str) -> str:
    """정규식 대신 실제 KNOWN_SYSTEMS 멤버십 매칭 — MCP Prompt(incident_analysis_
    prompt)의 system_name은 "시스템: X" 한 줄짜리 컨텍스트라 항상 정해진 이름
    중 하나로만 고정한다(여러 개 언급돼도 join하지 않음 — 실제 조사 대상 목록은
    이후 plan()의 tasks가 별도로 결정). 쿼리에서 가장 먼저 등장하는 시스템을
    고르고, 하나도 안 걸리면 가장 흔한 사례인 auth-service로 폴백."""
    matches = [(query.index(s), s) for s in KNOWN_SYSTEMS if s in query]
    return min(matches)[1] if matches else "auth-service"


class AgentState(TypedDict, total=False):
    query: str
    thread_id: str
    history: list[dict]
    intake_previous: dict
    intake: dict
    retrieval: dict
    operator_retrieval: dict
    validation: dict
    # 화면에서 직원이 직접 고른 시스템. None이면 기존 자유 대화/진단 경로를 쓴다.
    selected_system: str | None
    route: str
    needs_analysis: bool
    tasks: list[dict]
    gate_removed: int
    collected: list[dict]
    replan_rounds: int
    retry_count: int
    parallel_elapsed: float
    draft: str
    validation_attempts: int
    validation_feedback: str
    final: str
    last_prompt: str
    cost_usd: float
    events: list[dict]


def _cost(state: AgentState, usage: dict) -> float:
    return round(state.get("cost_usd", 0.0) + usage["usd"], 6)


@traceable(name="route_node")
async def route_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """Wave 5 — 모든 쿼리가 무거운 파이프라인을 탈 필요는 없다."""
    if state.get("selected_system") is not None:
        # "로그인이 안 돼요"처럼 장애 키워드가 없는 문장도, 시스템을 선택해 접수한
        # 문의라면 기본 점검 대상으로 취급한다. 모델이 일반 잡담으로 돌리거나
        # 지원하지 않는 시스템을 임의의 기본 시스템으로 바꾸지 못하게 한다.
        selected = validate_selected_system(state["selected_system"])
        event = {"type": "RouteDecided", "route": "incident",
                 "source": "explicit_selection", "selected_system": selected}
        return {"route": "incident", "needs_analysis": False,
                "selected_system": selected,
                "events": state.get("events", []) + [event]}
    result, usage = deps.fast_llm.route(state["query"])
    event = {"type": "RouteDecided", "route": result["route"]}
    return {"route": result["route"], "needs_analysis": result["needs_analysis"],
            "cost_usd": _cost(state, usage),
            "events": state.get("events", []) + [event]}


@traceable(name="chat_answer_node")
async def chat_answer_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """Wave 5 direct 경로 — 히스토리 기반 응답 (Wave 12 세션 증명 지점).

    토큰 단위 스트리밍 — get_stream_writer()는 /chat/stream처럼 custom 모드로 소비될
    때만 실제로 전송되고, /chat(ainvoke)처럼 스트리밍하지 않는 호출에서는 자동으로
    no-op이 된다."""
    writer = get_stream_writer()
    reply, usage = await deps.fast_llm.chat(
        state["query"], state.get("history", []),
        on_token=lambda piece: writer({"type": "AnswerToken", "delta": piece}))
    return {"final": reply, "last_prompt": usage.get("prompt"),
            "cost_usd": _cost(state, usage),
            "events": state["events"] + [{"type": "Finalize", "pipeline": "chat-direct"}]}


@traceable(name="intake_node")
async def intake_node(state: AgentState, deps: SupervisorDeps) -> dict:
    if state.get("selected_system") is None:
        return {}  # 선택 없는 기존 교육용 진단 API는 기존 흐름을 유지한다.
    from .intake import analyze_intake
    intake = await analyze_intake(state["query"], state.get("intake_previous", {}), deps.fast_llm)
    return {"intake": intake, "cost_usd": _cost(state, {"usd": 0, **intake.get("usage", {})}),
            "events": state["events"] + [{"type": "IntakeUpdated", "source": intake["source"],
                "missing_fields": intake["missing_fields"]}]}


@traceable(name="plan_node")
async def plan_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """Wave 4 — Plan/Task 분해. Wave 2의 MCP Prompt가 표준 절차를 깔아준다.

    resource와 마찬가지로 이름을 어느 서버가 들고 있는지 미리 가정하지 않고,
    find_prompt()로 연결된 서버들을 먼저 조회해 찾은 뒤에만 get_prompt를 보낸다."""
    if state.get("selected_system") is not None:
        # 기본 점검의 대상은 사용자의 선택이 결정한다. 여기서 모델에게 다시 계획을
        # 맡기면 fake 모델의 web-01 추가나 실모델의 대상 누락/변경이 끼어들 수 있다.
        # 따라서 선택한 시스템을 조회하는 작업 하나만 코드로 만들고, 사용하지 않을
        # MCP 진단 절차 프롬프트도 요청하지 않는다.
        selected = validate_selected_system(state["selected_system"])
        tasks = [{"id": "task-0", "agent": "research", "kind": "monitoring",
                  "target": selected, "status": "pending"}]
        event = {"type": "PlanCreated", "task_count": 1, "targets": [selected],
                 "source": "explicit_selection"}
        return {"tasks": tasks, "needs_analysis": False,
                "selected_system": selected,
                "events": state.get("events", []) + [event]}
    prompt_server = await deps.mcp.find_prompt("incident_analysis_prompt")
    procedure = await deps.mcp.get_prompt(
        "incident_analysis_prompt",
        {"system_name": _extract_system_name(state["query"]), "symptom": state["query"]},
        server=prompt_server,
    )
    result, usage = deps.fast_llm.plan(state["query"], procedure)
    targets = [t["target"] for t in result["tasks"]
               if t["agent"] == "research" and t.get("target")]
    event = {"type": "PlanCreated", "task_count": len(result["tasks"]),
             "targets": targets}
    return {"tasks": result["tasks"], "needs_analysis": result["needs_analysis"],
            "cost_usd": _cost(state, usage), "events": state["events"] + [event]}


@traceable(name="gate_node")
async def gate_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """Wave 6 — 권위 게이트. needs_analysis=False인데 섞여 들어온 analysis task를
    프롬프트가 아니라 코드로 강제 제거한다."""
    tasks = state["tasks"]
    if not state.get("needs_analysis", False):
        kept = [t for t in tasks if t["agent"] != "analysis"]
        removed = len(tasks) - len(kept)
    else:
        kept, removed = tasks, 0
    event = {"type": "GateApplied", "removed_analysis_tasks": removed}
    return {"tasks": kept, "gate_removed": removed,
            "events": state["events"] + [event]}


@traceable(name="research_node")
async def research_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """Wave 15 가드 + Wave 10b 병렬 + Wave 9 재시도 + Wave 2 Tool/Resource 실호출."""
    tasks = state["tasks"]
    research_indices = [i for i, t in enumerate(tasks)
                        if t["agent"] == "research" and t["status"] == "pending"]
    if state.get("selected_system") is not None:
        selected = validate_selected_system(state["selected_system"])
        # 앞 단계에서 계획을 고정했더라도, 실제 도구 호출 직전에 다시 확인한다.
        # 모든 대상을 먼저 검사해야 잘못된 작업 하나와 정상 작업 하나가 병렬로
        # 실행되면서 다른 시스템을 먼저 조회하는 일을 막을 수 있다.
        for idx in research_indices:
            task = tasks[idx]
            if task["kind"] == "monitoring" and task["target"] != selected:
                raise ValueError(
                    f"선택한 시스템({selected})과 조회 대상({task['target']})이 다릅니다.")
    # Wave 15 — 객체가 아니라 '실행할 목록'을 검사한다 (not research_agent 버그의 수정판)
    if not research_indices:
        return {"events": state["events"] +
                [{"type": "ResearchSkipped", "reason": "pending research task 0개"}]}

    retry_total = state.get("retry_count", 0)

    # URI를 코드에 하드코딩하지 않는다. 여러 MCP 서버가 연결돼 있고 그중 어느 서버가
    # "get_incident_runbook" resource를 제공하는지 모른다고 가정하고, 먼저 연결된
    # 서버들을 조회(list)해 유효한 서버를 찾은 뒤에만 그 서버로 요청한다.
    needs_runbook = any(tasks[i]["kind"] != "monitoring" for i in research_indices)
    runbook_server = runbook_uri_template = None
    if needs_runbook:
        runbook_server, runbook_uri_template = await deps.mcp.find_resource_template(
            "get_incident_runbook")

    async def run_one(idx: int) -> dict:
        task = tasks[idx]
        await asyncio.sleep(0.35)   # 태스크당 고정 지연 — 병렬 효과를 실측하기 위함
        if task["kind"] == "monitoring":
            data, retries = await with_retry(
                deps.mcp.call_tool, "get_monitoring_status",
                {"system_name": task["target"]})
            return {"idx": idx, "source": "monitoring", "data": data, "retries": retries}
        uri = runbook_uri_template.format(incident_type=task["target"])
        data = await deps.mcp.read_resource(uri, server=runbook_server)
        return {"idx": idx, "source": "runbook", "data": data, "retries": 0}

    started = time.perf_counter()
    results = await asyncio.gather(*(run_one(i) for i in research_indices))
    elapsed = round(time.perf_counter() - started, 2)

    new_tasks = [dict(t) for t in tasks]
    collected = list(state.get("collected", []))
    events = list(state["events"])
    for r in results:
        new_tasks[r["idx"]]["status"] = "done"
        collected.append({"source": r["source"], "data": r["data"]})
        retry_total += r["retries"]
        events.append({"type": "TaskCompleted", "id": new_tasks[r["idx"]]["id"],
                       "source": r["source"], "target": new_tasks[r["idx"]]["target"],
                       "retries": r["retries"]})
    return {"tasks": new_tasks, "collected": collected, "retry_count": retry_total,
            "parallel_elapsed": elapsed, "events": events}


# 수집된 모니터링 결과(상태·알림 문구)를 보고 어떤 runbook이 맞는지 고른다. LLM에게
# 다시 묻는 대신 코드 레벨 규칙으로 판단한다 — gate(Wave 6)·replan(Wave 7)과 같은 설계
# 원칙: 모델의 판단을 그대로 믿지 않고, 실측 데이터로 검증 가능한 결정은 코드가 내린다.
_INCIDENT_TYPE_RULES = [
    ("latency", "high-latency"),
    ("pool", "high-latency"),
    ("cpu", "cpu-spike"),
    ("memory", "memory-leak"),
    ("heap", "memory-leak"),
    ("timeout", "network-timeout"),
    ("network", "network-timeout"),
    ("disk", "disk-full"),
    ("down", "db-down"),
]


def _classify_incident_type(collected: list[dict]) -> str | None:
    text = " ".join(
        f"{c['data'].get('status', '')} " + " ".join(c["data"].get("alerts", []))
        for c in collected if c.get("source") == "monitoring"
    ).lower()
    for keyword, incident_type in _INCIDENT_TYPE_RULES:
        if keyword in text:
            return incident_type
    # 매칭 실패는 장애 종류를 모른다는 뜻이다. 기본 점검에서 이를 DB 장애로
    # 바꾸면 정상 시스템에도 장애 매뉴얼을 제시하게 되므로, 판단은 호출자가 한다.
    return None


@traceable(name="replan_check_node")
async def replan_check_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """Wave 7 — 1차 수집 결과 평가. runbook이 없으면 gap task만 추가해 재조사."""
    if state.get("selected_system") is not None:
        # 접수 경로는 관측 알림으로 파일 하나를 강제로 고르지 않고 다음 RAG 노드에서
        # 증상·문서 용도·시스템 범위를 사용한다. 선택 없는 실습 API는 기존 경로 유지.
        return {"events": state["events"] + [{"type": "ReplanCheck", "round": 0,
            "is_sufficient": True, "runbook_skipped": True,
            "reason": "관련 문서는 다음 RAG 단계에서 검색합니다."}]}
    has_runbook = any(c["source"] == "runbook" for c in state.get("collected", []))
    rounds = state.get("replan_rounds", 0)
    if has_runbook or rounds >= 1:
        verdict = {"type": "ReplanCheck", "round": rounds, "is_sufficient": True}
        return {"events": state["events"] + [verdict]}
    collected = state.get("collected", [])
    # 선택 없는 기존 실습 API의 유형 규칙은 비교·호환용으로 남긴다.
    incident_type = _classify_incident_type(collected) or "db-down"
    gap_task = {"id": "task-gap-runbook", "agent": "research", "kind": "runbook",
                "target": incident_type, "status": "pending"}
    verdict = {"type": "ReplanCheck", "round": rounds, "is_sufficient": False,
               "gap_task": gap_task["id"], "target": incident_type}
    return {"tasks": state["tasks"] + [gap_task], "replan_rounds": rounds + 1,
            "events": state["events"] + [verdict]}


@traceable(name="analysis_node")
async def analysis_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """Wave 6 보완 — needs_analysis=True가 정당한 경우(모델의 자기모순이 아니라
    실제로 패턴 비교가 필요하다고 판단한 경우)를 위한 실행 경로. gate가 살려둔
    agent=='analysis' task가 있을 때만 수집된 monitoring 결과들을 비교 분석한다."""
    tasks = state["tasks"]
    pending = [t for t in tasks if t["agent"] == "analysis" and t["status"] == "pending"]
    if not pending:
        return {"events": state["events"] +
                [{"type": "AnalysisSkipped", "reason": "analysis task 0개"}]}
    summary, usage = deps.fast_llm.analyze(state.get("collected", []))
    new_tasks = [dict(t) for t in tasks]
    for t in new_tasks:
        if t["agent"] == "analysis" and t["status"] == "pending":
            t["status"] = "done"
    collected = state.get("collected", []) + [{"source": "analysis", "data": summary}]
    event = {"type": "AnalysisCompleted", "length": len(summary)}
    return {"tasks": new_tasks, "collected": collected,
            "cost_usd": _cost(state, usage), "events": state["events"] + [event]}


@traceable(name="retrieve_node", run_type="retriever")
async def retrieve_node(state: AgentState, deps: SupervisorDeps) -> dict:
    if state.get("selected_system") is None:
        return {}
    selected = state["selected_system"]
    symptom = state.get("intake", {}).get("fields", {}).get("symptom", {}).get("value")
    query = symptom or state["query"]
    if deps.retriever is None:
        user = operator = {"status": "error", "documents": [], "error": "검색기가 설정되지 않았습니다."}
    else:
        alerts = [str(a) for item in state.get("collected", []) if item["source"] == "monitoring"
                  for a in item["data"].get("alerts", [])]
        async def search(text, audience):
            try:
                return await deps.retriever.search(text, selected, audience=audience, k=3)
            except Exception:
                return {"status": "error", "documents": [], "error": "문서 검색을 완료하지 못했습니다."}
        user, operator = await asyncio.gather(search(query, "user"),
            search(query + "\n" + " ".join(alerts), "operator"))
    user = {**user, "documents": [doc for doc in user.get("documents", [])
                                   if doc.get("audience") == "user"]}
    if user["status"] == "ok" and not user["documents"]:
        user["status"] = "no_match"
    # 운영자 매뉴얼은 별도 필드에 저장한다. 사용자 답변 생성 문맥에 섞지 않는다.
    collected = state.get("collected", []) + [
        {"source": "knowledge", "data": doc} for doc in user.get("documents", [])
        if doc.get("audience") == "user"]
    collected.append({"source": "retrieval_status", "data": {"status": user["status"]}})
    return {"retrieval": user, "operator_retrieval": operator, "collected": collected,
            "events": state["events"] + [{"type": "RetrievalCompleted", "status": user["status"],
                "document_ids": [doc["document_id"] for doc in user.get("documents", [])]}]}


@traceable(name="write_node")
async def write_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """근거를 바탕으로 내부 초안을 작성한다. 검증 전 토큰은 사용자에게 보내지 않는다."""
    attempt = state.get("validation_attempts", 0) + 1
    write_query = state["query"]
    if state.get("selected_system") is not None:
        selected = validate_selected_system(state["selected_system"])
        # 선택값은 실행 대상을 제한하는 코드에서 이미 사용했다. 작성 단계에도
        # 대상과 샘플 데이터라는 점을 전달하되, query를 그대로 출력하는 fake
        # 모델에서 긴 내부 지침이 사용자 답변에 노출되지 않도록 짧게 구성한다.
        # 이 문맥 추가 자체가 답변의 사실 검증이나 자동 조치를 구현하지는 않는다.
        write_query = (
            f"선택한 대상: {selected} (실습용 샘플 데이터)\n"
            f"사용자 증상: {state['query']}"
        )
        # 후속 발화가 '오전 9시요'여도 최초 증상을 잃지 않도록 누적 접수 내용을 전달한다.
        fields = state.get("intake", {}).get("fields", {})
        labels = {"symptom": "접수 증상", "occurred_at": "발생 시각",
                  "error_message": "오류 메시지", "environment": "사용 환경"}
        for key, field in fields.items():
            if field.get("status") != "unmentioned":
                value = field.get("value") or {"none": "없음", "unknown": "모름"}.get(field.get("status"), "미확인")
                write_query += f"\n{labels.get(key, key)}: {value}"
    draft, usage = await deps.quality_llm.write(
        write_query, state.get("collected", []),
        attempt, state.get("validation_feedback"),
        # 검증 전 문장은 전송하지 않는다. 진행 단계 이벤트와 저장된 최종 답변만 공개한다.
        on_token=None)
    return {"draft": draft, "last_prompt": usage.get("prompt"),
            "cost_usd": _cost(state, usage),
            "events": state["events"] + [{"type": "DraftWritten", "attempt": attempt,
                                          "length": len(draft)}]}


@traceable(name="validate_node")
async def validate_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """형식뿐 아니라 선택 대상·문서 인용·관측값의 일치를 검사한다."""
    draft = state["draft"]
    attempts = state.get("validation_attempts", 0) + 1
    if state.get("selected_system") is not None:
        from .answer_validation import validate_answer
        checked = validate_answer(draft, state["selected_system"],
                                  state.get("collected", []), state.get("retrieval", {}))
    else:
        # 선택 없는 실습용 경로는 이전 형식 검사를 유지한다.
        valid = len(draft) >= 30 and "1)" in draft
        checked = {"ok": valid, "errors": [] if valid else ["30자 이상과 번호 절차가 필요합니다."],
                   "citations": []}
    ok = checked["ok"]
    verdict = "합격" if ok else "불합격"
    exhausted = not ok and attempts >= 3
    if exhausted:
        verdict = "추가 확인 필요(검증 실패)"
    feedback = None if ok or exhausted else "\n".join(checked["errors"])
    event = {"type": "Validation", "attempt": attempts, "verdict": verdict,
             "errors": checked["errors"]}
    return {"validation_attempts": attempts, "validation_feedback": feedback,
            "validation": {"status": "passed" if ok else ("fallback" if exhausted else "retry"),
                           "errors": checked["errors"], "citations": checked["citations"]},
            "events": state["events"] + [event]}


@traceable(name="finalize_node")
async def finalize_node(state: AgentState, deps: SupervisorDeps) -> dict:
    final = state["draft"]
    if state.get("validation", {}).get("status") == "fallback":
        from .answer_generation import evidence_fallback
        final = evidence_fallback(state.get("selected_system"), state.get("collected", []))
    questions = state.get("intake", {}).get("questions", [])
    if questions:
        final += "\n\n추가 확인: " + " ".join(questions)
    return {"final": final,
            "events": state["events"] + [{"type": "Finalize", "pipeline": "incident-v2"}]}


def _after_route(state: AgentState) -> str:
    return "chat_answer" if state["route"] == "chat" else "plan"


def _after_replan_check(state: AgentState) -> str:
    last = state["events"][-1]
    return "analysis" if last.get("is_sufficient") else "research"


def _after_validate(state: AgentState) -> str:
    return "finalize" if state.get("validation_feedback") is None else "write"


def build_graph(deps: SupervisorDeps):
    g = StateGraph(AgentState)
    node = lambda fn: partial(fn, deps=deps)   # Wave 13 — 명시적 의존성 주입
    g.add_node("route", node(route_node))
    g.add_node("chat_answer", node(chat_answer_node))
    g.add_node("intake", node(intake_node))
    g.add_node("plan", node(plan_node))
    g.add_node("gate", node(gate_node))
    g.add_node("research", node(research_node))
    g.add_node("replan_check", node(replan_check_node))
    g.add_node("analysis", node(analysis_node))
    g.add_node("write", node(write_node))
    g.add_node("retrieve", node(retrieve_node))
    g.add_node("validate", node(validate_node))
    g.add_node("finalize", node(finalize_node))

    g.set_entry_point("route")
    g.add_conditional_edges("route", _after_route,
                            {"chat_answer": "chat_answer", "plan": "intake"})
    g.add_edge("chat_answer", END)
    g.add_edge("intake", "plan")
    g.add_edge("plan", "gate")
    g.add_edge("gate", "research")
    g.add_edge("research", "replan_check")
    g.add_conditional_edges("replan_check", _after_replan_check,
                            {"analysis": "analysis", "research": "research"})
    g.add_edge("analysis", "retrieve")
    g.add_edge("retrieve", "write")
    g.add_edge("write", "validate")
    g.add_conditional_edges("validate", _after_validate,
                            {"finalize": "finalize", "write": "write"})
    g.add_edge("finalize", END)
    return g.compile()
