# 1단계 코드 비교: 선택한 시스템만 기본 점검하기

이 문서는 기존 실습 에이전트를 사내 문의 접수 도구로 바꾸는 첫 단계인 **시스템 선택과 입력 검증**을 설명한다. 변경 전후 코드를 고정된 커밋으로 비교하고, 같은 입력의 화면과 실행 기록을 연결해 이후 발표에서도 다시 확인할 수 있도록 작성했다.

이번 단계에서 구현한 것은 **사용자가 선택한 시스템을 요청부터 실제 조회까지 유지하는 흐름**이다. 실제 회사 시스템의 장애 원인을 판정하거나, 관리자에게 문의를 발송하거나, 문서를 의미 기반으로 검색하는 기능까지 구현한 것은 아니다.

## 1. 비교 기준과 증거 읽는 방법

| 구분 | 기준 | 용도 |
|---|---|---|
| 변경 전 | [`ee32f7e`](https://github.com/gayeon9-9/skala_ai_mini_project/tree/ee32f7e298517e33aed8d0831a7643eceb756650) | 시스템 선택 기능을 추가하기 전 코드 |
| 변경 후 | [`7dd329f`](https://github.com/gayeon9-9/skala_ai_mini_project/tree/7dd329fdcfce4558fc1d257fbae8286656e45183) | 1단계 시스템 선택·검증·프록시·화면·테스트 코드 |
| 기능 설명 | [1단계 기능 문서](step_01_system_selection.md) | 실행 방법, API 형식, 현재 구현 범위 |
| 실행 증거 | [비교 실행 기록](evidence/step_01/README.md) | 실제 실행 조건, API 결과, 화면 파일, 확인 결과 |

아래 코드 링크는 전체 커밋 해시로 고정되어 있다. 이후 코드를 수정해도 이 문서에서 설명하는 변경 전후 버전은 달라지지 않는다.

본문의 사례 표는 **해당 코드에서 확인해야 할 비교 지점**이다. 실제 실행 결과와 캡처 완료 여부는 비교 실행 기록을 기준으로 확인한다. 코드 설명만으로 실행 시간이나 진단 정확도를 측정했다고 해석하지 않는다.

### 실습 장치와 개선 대상을 구분하기

기존 프로젝트에는 기능을 보여주기 위해 의도적으로 만든 동작이 있다.

| 기존 동작 | 원래 실습 목적 | 이번 단계에서의 처리 |
|---|---|---|
| fake 계획이 `web-01`을 추가 | 여러 조회 작업의 병렬 실행 설명 | 명시적으로 선택한 시스템의 점검에서는 이 계획 함수를 호출하지 않음 |
| fake 계획에 불필요한 analysis 작업 포함 | gate가 모델의 모순을 제거하는 과정 설명 | 선택 경로는 모순된 작업을 만들지 않고 조회 하나를 코드로 생성 |
| 최초 모니터링 도구 호출에서 타임아웃 발생 | 재시도와 복구 과정 설명 | 이번 단계에서도 남아 있는 실습 장치 |
| fake 첫 초안이 “재시작하면 됩니다.” | 답변 검수·재작성 과정 설명 | 이번 단계에서도 남아 있음. 직원에게 최종 안내처럼 보이는 문제는 후속 과제 |

따라서 “기존 개발자가 실수로 다른 서버를 넣었다”보다는 **“기존 실습 동작을 직원의 명시적 대상 선택에 맞는 실행 경로로 분리했다”**고 설명하는 것이 정확하다. 실모델을 사용하면 기존 분류·계획 결과도 달라질 수 있으므로, fake에서 재현한 결과를 모든 LLM의 동작으로 일반화하지 않는다.

## 2. 비교할 사례와 화면 경로

아래 변경 전후 입력의 차이는 선택창의 도입을 포함한다. 변경 전에는 `selected_system`이라는 별도 입력이 없었고, 변경 후에는 선택값을 요청에 포함한다.

| 사례 | 같은 사용자 문장 | 변경 전 코드의 비교 지점 | 변경 후 명시적 선택 경로의 비교 지점 |
|---|---|---|---|
| A. 자연어 증상 | `로그인 후 화면이 넘어가지 않아요` | fake 분류의 장애 키워드와 일치하지 않아 일반 대화로 이동 | `auth-service`를 선택하면 해당 시스템의 기본 점검으로 이동 |
| B. 조회 대상 | `auth-service 로그인 지연을 확인해줘` | fake 계획에 `auth-service`와 `web-01` 포함 | `auth-service` 선택 시 해당 시스템의 모니터링 작업 하나 생성 |
| C. 정상 샘플 | `web-01 장애 점검해줘` | 정상 샘플에도 증상 매칭 실패 시 `db-down` 매뉴얼 선택 | `web-01` 선택 시 장애 근거가 없으면 매뉴얼 조회 생략 |
| D. 접수 화면 | `로그인 후 화면이 넘어가지 않아요` 입력 후, 전송 전 | 자유 텍스트 입력만 제공 | 시스템 선택, 선택 전 전송 제한, 샘플 데이터 안내 제공 |

화면 비교 파일 경로는 다음과 같다. 화면과 API의 실행 조건·결과는 [비교 실행 기록](evidence/step_01/README.md)에서 함께 확인한다.

| 사례 | 변경 전 화면 | 변경 후 화면 |
|---|---|---|
| A | [01_before_natural.jpg](evidence/step_01/screenshots/01_before_natural.jpg) | [01_after_natural.jpg](evidence/step_01/screenshots/01_after_natural.jpg) |
| B | [02_before_targets.jpg](evidence/step_01/screenshots/02_before_targets.jpg) | [02_after_targets.jpg](evidence/step_01/screenshots/02_after_targets.jpg) |
| C | [03_before_healthy.jpg](evidence/step_01/screenshots/03_before_healthy.jpg) | [03_after_healthy.jpg](evidence/step_01/screenshots/03_after_healthy.jpg) |
| D | [04_before_input.jpg](evidence/step_01/screenshots/04_before_input.jpg) | [04_after_input.jpg](evidence/step_01/screenshots/04_after_input.jpg) |

스크린샷에서는 답변 말풍선뿐 아니라 **계획의 대상 목록, 실제 모니터링 대상, 매뉴얼 조회 또는 생략 기록**을 함께 봐야 한다. 특정 CPU 수치가 달라진 것만으로 개선 효과를 설명하면 안 된다. 기존 모니터링 도구가 호출마다 샘플 수치에 난수 변동을 더하기 때문이다.

## 3. 요청이 이동하는 전체 경로

변경 후 화면 요청은 다음 순서로 처리된다.

```text
화면에서 시스템 선택 + 증상 입력
    ↓ selected_system, message, thread_id
web_demo_server.py: 같은 출처에서 요청 중계
    ↓ 선택값을 그대로 전달
server.py: 목록·공백·지원 버전 검증
    ↓ _initial_state()로 초기 상태 생성
graph.py: 선택 경로로 분류 → 대상 하나로 계획
    ↓ 실제 도구 호출 직전 대상 재검사
MCP: 선택한 시스템의 샘플 상태 조회
    ↓ 관측된 이상 상태와 알림이 규칙에 맞을 때만
관련 매뉴얼 조회 → 기존 답변 작성·검수
    ↓
화면에 진행 기록과 답변 표시
```

시스템 선택을 프롬프트에만 붙인 것이 아니다. 화면, API, 그래프, 실제 도구 호출 전에 각각 선택값을 전달하거나 확인하는 코드를 넣었다.

## 4. 자연어 증상이 일반 대화로 빠지는 경로를 분리했다

### 변경 전: fake 모델이 질문의 단어로 분류

기존 `route_node()`는 사용자가 어떤 시스템을 점검하고 싶은지 별도 정보 없이 `query`만 모델에 전달했다.

출처: [변경 전 graph.py의 route_node](https://github.com/gayeon9-9/skala_ai_mini_project/blob/ee32f7e298517e33aed8d0831a7643eceb756650/agent/graph.py#L75).

```python
    result, usage = deps.fast_llm.route(state["query"])
    event = {"type": "RouteDecided", "route": result["route"]}
    return {"route": result["route"], "needs_analysis": result["needs_analysis"],
            "cost_usd": _cost(state, usage),
            "events": state.get("events", []) + [event]}
```

fake 분류 구현은 다음과 같다. 출처: [변경 전 fake_llm.py의 route](https://github.com/gayeon9-9/skala_ai_mini_project/blob/ee32f7e298517e33aed8d0831a7643eceb756650/agent/fake_llm.py#L26).

```python
        incident = bool(re.search(r"장애|진단|다운|지연|급증|incident|티켓", query))
        result = {"route": "incident" if incident else "chat", "needs_analysis": False}
        return result, self._usage(query, str(result))
```

`re.search()`는 지정한 단어가 질문에 있는지 찾는다. `로그인 후 화면이 넘어가지 않아요`에는 이 단어들이 없으므로 fake 분류는 `chat`을 선택한다. 뒤의 그래프는 이 결과를 보고 `chat_answer`로 이동하며 모니터링 조회를 하지 않는다.

직원 입장에서는 자신의 증상을 자연스럽게 적었는데 점검이 시작되지 않는 문제가 된다. 다만 이것은 fake 모델의 규칙으로 재현하는 사례이며, 실제 LLM이 반드시 같은 문장을 일반 대화로 분류한다는 증거는 아니다.

### 변경 후: 선택된 대상이 있으면 기본 점검으로 이동

출처: [변경 후 graph.py의 route_node](https://github.com/gayeon9-9/skala_ai_mini_project/blob/7dd329fdcfce4558fc1d257fbae8286656e45183/agent/graph.py#L79).

```python
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
```

- `state.get("selected_system") is not None`: 사용자가 대상 선택 정보를 보낸 요청인지 구분한다. 빈 문자열도 이 조건에는 들어오지만, 이어지는 검증에서 거부한다.
- `validate_selected_system(...)`: 등록 목록에 있는 대상인지 확인한다. 모르는 이름을 기본 서버로 바꾸지 않는다.
- `route: "incident"`: 기존 그래프의 점검 경로 이름을 재사용한다. 이 값은 실제 장애를 확정했다는 뜻이 아니다.
- `needs_analysis: False`: 이번 선택 흐름은 시스템 하나의 기본 상태 조회부터 시작한다.
- `events`: 어떤 기준으로 경로를 정했는지 진행 기록에 남긴다.

이 분기에서 반환하므로 선택 요청은 아래의 `deps.fast_llm.route()`를 실행하지 않는다. **선택값이 없는 요청은 기존 분류 함수를 계속 사용한다.**

## 5. 조회 대상은 모델이 다시 정하지 않도록 했다

### 변경 전: fake 계획이 web-01을 추가

출처: [변경 전 fake_llm.py의 plan](https://github.com/gayeon9-9/skala_ai_mini_project/blob/ee32f7e298517e33aed8d0831a7643eceb756650/agent/fake_llm.py#L40).

```python
        systems = re.findall(r"[a-z][a-z0-9-]+-(?:service|\d+)", query) or ["auth-service"]
        if "web-01" not in systems:
            systems.append("web-01")   # 연관 시스템 비교 조회 → Wave 10b 병렬 실행 대상
        tasks = [
            {"id": f"task-{i}", "agent": "research", "kind": "monitoring",
             "target": s, "status": "pending"}
            for i, s in enumerate(dict.fromkeys(systems))
        ]
```

`re.findall()`은 질문에서 시스템 이름 형태를 찾는다. 아무 이름도 찾지 못하면 `auth-service`로 시작한다. 이어서 병렬 실습을 위해 `web-01`을 추가한다.

따라서 사례 B에서는 auth와 web 두 시스템의 모니터링 작업이 만들어진다. 이는 병렬 실행을 보여주기 위한 의도된 구성이다. 하지만 직원이 점검 대상을 직접 선택하는 문의 화면에서는 **선택하지 않은 시스템도 조사하는 동작**이 된다.

### 변경 후: 선택한 대상의 작업 하나를 코드로 생성

출처: [변경 후 graph.py의 plan_node](https://github.com/gayeon9-9/skala_ai_mini_project/blob/7dd329fdcfce4558fc1d257fbae8286656e45183/agent/graph.py#L115).

```python
        selected = validate_selected_system(state["selected_system"])
        tasks = [{"id": "task-0", "agent": "research", "kind": "monitoring",
                  "target": selected, "status": "pending"}]
        event = {"type": "PlanCreated", "task_count": 1, "targets": [selected],
                 "source": "explicit_selection"}
        return {"tasks": tasks, "needs_analysis": False,
                "selected_system": selected,
                "events": state.get("events", []) + [event]}
```

이 코드는 선택값이 있는 `if` 분기 안에 있다. 각 항목의 의미는 다음과 같다.

| 항목 | 의미 |
|---|---|
| `agent: "research"` | 조회 단계가 실행할 작업 |
| `kind: "monitoring"` | 매뉴얼이 아닌 모니터링 도구 조회 |
| `target: selected` | 사용자가 선택하고 검증받은 시스템 |
| `status: "pending"` | 아직 실행되지 않은 작업 |
| `task_count: 1` | 최초 계획의 작업 개수. 이후 관련 매뉴얼 작업은 별도로 생길 수 있음 |

선택 경로는 LLM의 `plan()`과 계획에 사용하던 MCP Prompt 요청을 건너뛴다. 단일 대상은 이미 사용자가 정했으므로 모델이 다시 추측할 필요가 없기 때문이다. fake 계획의 추가 시스템이나 모순된 analysis 작업도 이 경로에는 들어오지 않는다.

### 실제 도구 호출 직전에도 대상 검사

계획 단계만 믿지 않고 `research_node()`에서도 아직 실행하지 않은 모니터링 작업을 모두 확인한다.

출처: [변경 후 graph.py의 research_node](https://github.com/gayeon9-9/skala_ai_mini_project/blob/7dd329fdcfce4558fc1d257fbae8286656e45183/agent/graph.py#L164).

```python
        for idx in research_indices:
            task = tasks[idx]
            if task["kind"] == "monitoring" and task["target"] != selected:
                raise ValueError(
                    f"선택한 시스템({selected})과 조회 대상({task['target']})이 다릅니다.")
```

이 검사는 `asyncio.gather()`로 작업을 실행하기 전에 수행된다. 올바른 작업이 목록 앞에 있고 잘못된 작업이 뒤에 있어도, **목록 전체의 검증이 끝나기 전에는 조회를 시작하지 않는다.** 이는 향후 계획 생성 코드를 수정하면서 대상이 섞이는 경우에도 실제 호출을 막기 위한 확인이다.

## 6. 화면 선택값을 API에서도 검증한다

화면에 선택창만 추가하면 API를 직접 호출하는 요청까지 검증할 수 없다. 이번 변경에서는 서버의 요청 모델과 시스템 목록 검증을 함께 추가했다.

### 6.1 변경 전 요청에는 질문과 대화 ID만 존재

출처: [변경 전 server.py의 ChatRequest](https://github.com/gayeon9-9/skala_ai_mini_project/blob/ee32f7e298517e33aed8d0831a7643eceb756650/server.py#L35).

```python
class ChatRequest(BaseModel):   # Wave 1 — Pydantic 요청 검증
    message: str
    thread_id: str = "default"
```

문자열 타입은 검사하지만, 공백만 있는 문자열을 별도로 거부하지는 않았다. 대상 시스템은 독립된 필드가 아니므로 이후 질문 내용이나 모델 계획에서 추측했다.

### 6.2 변경 후 요청에 selected_system과 검증 추가

출처: [변경 후 server.py의 ChatRequest](https://github.com/gayeon9-9/skala_ai_mini_project/blob/7dd329fdcfce4558fc1d257fbae8286656e45183/server.py#L37).

```python
    selected_system: str | None = None

    @field_validator("message", "thread_id")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("공백만 입력할 수 없습니다.")
        return value

    @field_validator("selected_system")
    @classmethod
    def validate_system(cls, value: str | None) -> str | None:
        return validate_selected_system(value) if value is not None else None
```

`field_validator`는 요청을 모델로 만들 때 실행하는 검증 함수다. `strip()`은 앞뒤 공백을 정리하고, 정리한 결과가 비어 있으면 요청을 거부한다. `selected_system`은 다음의 공통 목록 검증 함수를 사용한다.

출처: [신규 system_catalog.py의 validate_selected_system](https://github.com/gayeon9-9/skala_ai_mini_project/blob/7dd329fdcfce4558fc1d257fbae8286656e45183/agent/system_catalog.py#L35).

```python
def validate_selected_system(value: str) -> str:
    """공백을 정리하고 등록된 시스템인지 확인한다. 추측이나 자동 대체는 하지 않는다."""
    name = value.strip()
    if not name:
        raise ValueError("조회할 시스템을 선택해주세요.")
    if name not in get_system_names():
        raise ValueError("등록되지 않은 시스템입니다. 시스템 목록에서 다시 선택해주세요.")
    return name
```

`get_system_names()`의 원본은 MCP 도구도 사용하는 `data/monitoring.json`이다. 화면용 이름 목록과 실행 허용 목록을 각각 하드코딩하지 않으므로 둘이 어긋나는 것을 줄인다. 목록 파일이 없거나 손상되면 임의의 기본 시스템을 만드는 대신 `SystemCatalogError`로 처리하고 서버는 HTTP 503을 반환한다.

서버는 화면에도 같은 목록을 제공한다. 출처: [변경 후 server.py의 systems](https://github.com/gayeon9-9/skala_ai_mini_project/blob/7dd329fdcfce4558fc1d257fbae8286656e45183/server.py#L132).

```python
@app.get("/systems")
async def systems():
    """화면의 선택 목록. 실시간 운영 시스템이 아닌 샘플 목록임을 함께 전달한다."""
    return {"systems": get_system_names(), "data_source": "sample"}
```

### 6.3 일반 요청과 SSE 요청에 같은 검증 적용

기존 `/chat`은 `_handle_chat()`에서 `ainvoke()`를 호출하고, `/chat/stream`은 별도 코드에서 `astream()`을 호출했다. `_handle_chat()`만 고치면 현재 웹 화면이 사용하는 스트리밍 경로에는 선택값이 전달되지 않는다.

변경 후에는 두 경로가 모두 `ChatRequest`와 `_initial_state()`를 사용한다. SSE 쿼리도 먼저 요청 모델로 변환한다.

출처: [변경 후 server.py의 _stream_request](https://github.com/gayeon9-9/skala_ai_mini_project/blob/7dd329fdcfce4558fc1d257fbae8286656e45183/server.py#L58).

```python
    try:
        return ChatRequest(message=message, thread_id=thread_id, selected_system=selected_system)
    except ValidationError as exc:
        errors = [{**error, "loc": ("query", *error["loc"])} for error in exc.errors()]
        raise RequestValidationError(errors) from exc
```

출처: [변경 후 server.py의 _initial_state](https://github.com/gayeon9-9/skala_ai_mini_project/blob/7dd329fdcfce4558fc1d257fbae8286656e45183/server.py#L172).

```python
    return {
        "query": req.message, "thread_id": req.thread_id,
        "selected_system": req.selected_system,
        "history": deps.session_store.load(req.thread_id),
        "events": [], "cost_usd": 0.0,
    }
```

이 초기 상태에 `selected_system`이 포함되어야 그래프의 새 분기를 실행할 수 있다. `/chat`은 이 상태를 `ainvoke()`에, `/chat/stream`은 `astream()`에 전달한다.

SSE는 응답 헤더가 전송된 뒤에는 HTTP 상태를 입력 오류인 422로 바꿀 수 없다. 따라서 스트리밍 생성기 안이 아니라 **SSE 응답을 만들기 전에 검증**한다. 명시적 선택을 이해하지 못하는 v1 버전에 선택값을 보내면 무시하지 않고 HTTP 409를 반환한다.

| 요청 상태 | API 처리 |
|---|---|
| 등록된 시스템 선택 + 유효한 질문 | v2 선택 점검 경로 |
| 공백 질문·대화 ID | HTTP 422 |
| 빈 선택값·미등록 선택값 | HTTP 422 |
| 선택값은 있지만 `SUPERVISOR_VERSION=v1` | HTTP 409 |
| 시스템 목록 파일을 읽을 수 없거나 형식이 잘못됨 | HTTP 503 |
| `selected_system` 생략 또는 JSON `null` | 기존 API 동작 유지 |

## 7. 정상 샘플을 기본 DB 장애 매뉴얼로 연결하지 않는다

### 변경 전: 증상 키워드를 못 찾으면 db-down

출처: [변경 전 graph.py의 _classify_incident_type](https://github.com/gayeon9-9/skala_ai_mini_project/blob/ee32f7e298517e33aed8d0831a7643eceb756650/agent/graph.py#L205).

```python
    for keyword, incident_type in _INCIDENT_TYPE_RULES:
        if keyword in text:
            return incident_type
    return "db-down"   # 매칭되는 증상이 없으면 가장 일반적인 장애 대응 절차로 폴백
```

상태와 알림에서 `latency`, `cpu`, `memory` 등의 단어를 찾고, 하나도 맞지 않으면 `db-down`을 반환했다. `web-01` 샘플은 `healthy`이며 알림이 없으므로, 정상 샘플에서도 DB 장애 매뉴얼을 추가 조사할 수 있는 구조였다.

직원에게는 “정상 샘플 조회와 무관한 장애 대응 지침이 나온다”는 혼란이 생긴다. 관리자에게 전달할 자료로 사용하려면 관측 결과와 매뉴얼의 적용 근거도 구분되어야 한다.

### 변경 후: 선택 대상의 이상 상태와 매칭 근거가 있을 때만

분류 함수는 이제 매칭에 실패하면 `None`을 반환한다. 선택 경로에서는 조회 결과 중 다음 조건을 만족한 항목만 분류에 사용한다.

출처: [변경 후 graph.py의 replan_check_node](https://github.com/gayeon9-9/skala_ai_mini_project/blob/7dd329fdcfce4558fc1d257fbae8286656e45183/agent/graph.py#L255).

```python
        evidence = [c for c in collected if c.get("source") == "monitoring"
                    and c["data"].get("system_name") == selected
                    and c["data"].get("status") in {"degraded", "down"}]
        incident_type = _classify_incident_type(evidence)
        if incident_type is None:
            # _after_replan_check는 이 값을 보고 analysis로 이동한다. 매뉴얼이
            # 없다는 이유만으로 research를 반복하지 않고, 관측 범위 안에서 안내한다.
            verdict = {"type": "ReplanCheck", "round": rounds,
                       "is_sufficient": True, "runbook_skipped": True,
                       "reason": "선택한 시스템에서 매뉴얼과 일치하는 장애 근거가 없습니다."}
            return {"events": state["events"] + [verdict]}
```

확인하는 조건은 **모니터링 출처인지, 선택한 시스템인지, `degraded` 또는 `down`인지**다. `healthy`, `unknown`, 알 수 없는 증상은 매뉴얼을 임의로 선택하지 않고 생략 이유를 남긴다.

여기서 `is_sufficient: True`는 “이 그래프에서 매뉴얼 추가 조사 루프를 더 돌리지 않는다”는 제어 값이다. 사용자 문의의 원인이 밝혀졌거나 해결되었다는 의미가 아니다. 이후 답변 작성 단계로 진행하므로 최종 안내 표현과 사실 검증은 별도의 개선 과제로 남아 있다.

기존 선택 없는 요청은 다음 분기로 호환 동작을 유지한다.

```python
    else:
        # 시스템 선택 없이 호출하던 기존 실습 API의 기본 동작은 유지한다.
        incident_type = _classify_incident_type(collected) or "db-down"
```

따라서 발표에서 “기본 DB 매뉴얼 선택을 프로젝트 전체에서 없앴다”고 설명하면 안 된다. **새 화면의 명시적 선택 경로에서 근거 없는 기본 선택을 막았다**고 설명해야 한다.

## 8. 화면과 중계 서버도 선택값을 전달한다

### 8.1 화면: 빈 선택으로 시작하고 목록은 서버에서 조회

변경 전에는 자유 텍스트와 보내기 버튼만 있었다. 변경 후에는 서버에서 목록을 받아 선택창을 구성하고, 처음부터 특정 시스템을 자동 선택하지 않는다.

출처: [변경 후 web_chat.html의 선택 상태](https://github.com/gayeon9-9/skala_ai_mini_project/blob/7dd329fdcfce4558fc1d257fbae8286656e45183/web_chat.html#L174).

```javascript
    const selectedSystem = ref('');
    const catalogLoading = ref(true);
    const catalogError = ref('');
    const dataSource = ref('');
    const canSend = computed(() => !sending.value && !catalogLoading.value &&
      !catalogError.value && systems.value.includes(selectedSystem.value) && !!input.value.trim());
```

`canSend`는 점검 중인지, 목록 로딩 또는 오류가 있는지, 선택값이 실제 목록에 있는지, 질문이 비어 있지 않은지를 함께 확인한다. 버튼의 비활성화뿐 아니라 `onSubmit()` 내부에서도 `if (!canSend.value) return;`으로 확인한다.

목록이 실패하면 목록 재시도와 오류 안내를 제공한다. 화면에는 `data_source: "sample"`을 사용해 실제 운영 지표가 아니라는 문구를 표시한다. 질문 말풍선에는 요청 당시의 점검 대상을 함께 남긴다.

### 8.2 화면: SSE 요청에 selected_system 추가

변경 전 출처: [기존 web_chat.html의 onSubmit](https://github.com/gayeon9-9/skala_ai_mini_project/blob/ee32f7e298517e33aed8d0831a7643eceb756650/web_chat.html#L148).

```javascript
      const url = `/chat/stream?message=${encodeURIComponent(text)}&thread_id=${encodeURIComponent(thread)}`;
```

변경 후 출처: [새 web_chat.html의 요청 생성](https://github.com/gayeon9-9/skala_ai_mini_project/blob/7dd329fdcfce4558fc1d257fbae8286656e45183/web_chat.html#L241).

```javascript
      const params = new URLSearchParams({ message: text, thread_id: thread, selected_system: target });
      const url = `/chat/stream?${params}`;
      const es = new EventSource(url);
```

`target`은 전송 시점의 선택값이다. 진행 중에는 선택창과 입력창을 잠가 화면의 현재 선택과 실행 중인 대상이 혼동되지 않게 한다.

### 8.3 중계 서버: 선택값과 오류 상태를 유지

화면은 8600 포트의 중계 서버에 접속하고 실제 에이전트 API는 8100 포트에 있다. 브라우저가 보낸 선택값을 중계 서버가 버리면 백엔드는 선택 없는 기존 요청으로 처리하므로, 프록시에도 필드를 추가했다.

출처: [변경 후 web_demo_server.py의 proxy_stream](https://github.com/gayeon9-9/skala_ai_mini_project/blob/7dd329fdcfce4558fc1d257fbae8286656e45183/web_demo_server.py#L83).

```python
async def proxy_stream(message: str, thread_id: str = "web-demo",
                       selected_system: str | None = None):
    params = {"message": message, "thread_id": thread_id}
    # 생략과 빈 문자열은 다르다. 빈 선택도 그대로 넘겨 백엔드가 422로 검증한다.
    if selected_system is not None:
        params["selected_system"] = selected_system
```

또한 중계 서버는 백엔드 응답 헤더를 먼저 받은 뒤, 백엔드가 정상 응답을 보냈을 때만 SSE를 중계한다. 변경 전에는 프록시의 스트리밍 응답 안에서 백엔드 연결을 시작했으므로 백엔드 입력 오류를 프록시 HTTP 상태에 제대로 반영하기 어려웠다.

```python
    if not upstream.is_success:
        try:
            await upstream.aread()
            return _forward_response(upstream)
        except httpx.RequestError:
            return _backend_unavailable()
        finally:
            await close_upstream()
```

`_forward_response()`는 백엔드의 상태 코드와 본문을 유지한다. 백엔드에 연결하지 못하면 503, 스트리밍 도중 연결이 끊기면 `Error` 이벤트를 전달하고 연결을 정리한다.

브라우저의 `EventSource`는 422·409·503 응답의 본문을 읽어서 표시하지 못하므로 화면에서는 일반 오류 안내를 제공한다. **API와 프록시가 정확한 상태를 유지하는 것과, 화면이 개별 오류의 상세 본문을 표시하는 것은 구분해야 한다.** 화면은 오류 시 자동 재연결을 끊고 다시 입력할 수 있게 상태를 복구한다.

## 9. 답변 작성에 선택 대상과 샘플 문맥 전달

출처: [변경 후 graph.py의 write_node](https://github.com/gayeon9-9/skala_ai_mini_project/blob/7dd329fdcfce4558fc1d257fbae8286656e45183/agent/graph.py#L312).

```python
        write_query = (
            f"선택한 대상: {selected} (실습용 샘플 데이터)\n"
            f"사용자 증상: {state['query']}"
        )
```

이 문맥을 기존 작성 모델의 `write()`에 전달한다. fake 작성기는 `query`를 답변에 그대로 포함하므로 긴 내부 지침을 붙이지 않았다. 서버의 초기 `query`나 저장할 사용자 질문을 이 문자열로 바꾼 것은 아니며, 작성 함수에 전달하는 별도 변수다.

선택 대상과 샘플임을 알려주는 문맥은 답변 작성에 도움을 주지만, 그 자체로 사실 검증이 구현되지는 않는다. 실제 대상 제한은 앞의 계획 생성·도구 호출 검사가 담당한다.

일반 응답의 `metadata`와 SSE의 `FinalAnswer`에는 선택한 시스템과 `data_source: "sample"`이 포함된다. 이 값은 어느 대상에 대한 답변인지 확인하기 위한 응답 정보이며, 선택값을 독립된 문의 항목으로 DB에 저장한 것은 아니다.

## 10. 직원과 관리자에게 달라지는 점

| 관점 | 변경 전의 불편 또는 한계 | 이번 단계의 변화 | 아직 구현하지 않은 부분 |
|---|---|---|---|
| 문의하는 직원 | 시스템 이름과 특정 키워드를 문장에 넣어야 기대한 점검이 시작될 수 있음 | 시스템을 별도로 고르고 자연어로 증상 입력 | 증상에서 발생 시각·오류 문구를 추출하고 추가 질문하기 |
| 문의하는 직원 | 문장에 쓴 대상과 실제 조회 대상을 파악하기 어려움 | 질문과 진행 기록에 선택 대상을 표시하고 그 대상만 조회 | 문의 ID를 기준으로 작성 중 문의 이어가기 |
| 문의하는 직원 | 정상 샘플에도 맞지 않는 DB 장애 매뉴얼이 연결될 수 있음 | 선택 경로는 정상·unknown·미매칭 상황에서 매뉴얼 생략 이유 표시 | 안전한 최종 안내 형식과 내용 검증 |
| 담당 관리자 | 어떤 시스템에 관한 문의인지 다시 확인해야 할 수 있음 | 요청·응답·화면에 명시적 대상이 생겨 이후 요약의 입력 근거 마련 | 관리자 목록·접수 요약·담당자 배정·외부 발송 |
| 개발·운영 담당자 | API 두 경로와 프록시의 선택 정보 전달이 분리되어 있음 | 공통 입력 검증과 초기 상태를 사용하고 프록시가 선택값·오류 상태 전달 | 실제 모니터링 연동·운영 지표의 정확성 검증 |

관리자 관련 효과는 **명시적인 대상 정보를 이후 접수 요약에 활용할 수 있게 된 것**이다. 아직 관리자 화면이 생겼거나 관리자의 실제 처리 시간이 줄었다고 설명하면 안 된다.

## 11. 테스트와 실행 증거로 확인할 내용

자동 테스트의 기준 코드:

- [대상 선택·API·그래프 테스트](https://github.com/gayeon9-9/skala_ai_mini_project/blob/7dd329fdcfce4558fc1d257fbae8286656e45183/tests/test_selected_system.py)
- [웹 프록시 전달·오류·연결 정리 테스트](https://github.com/gayeon9-9/skala_ai_mini_project/blob/7dd329fdcfce4558fc1d257fbae8286656e45183/tests/test_web_proxy.py)

다음 항목을 테스트 코드와 실제 실행 기록에서 구분해 확인한다.

| 검증 항목 | 확인할 증거 |
|---|---|
| 자연어 증상도 선택 대상으로 점검 | POST·SSE에서 경로가 incident이고 조회 대상이 선택값 하나인지 |
| 문장에 다른 시스템 이름이 있어도 선택값 유지 | 선택값과 충돌하는 질문을 보내도 실제 도구 호출은 선택 대상 하나인지 |
| 입력 오류는 실행 전에 차단 | 빈 질문·대화 ID·선택값·미등록 선택값에 422, 그래프·도구 호출 없음 |
| 목록 파일 문제를 다른 서버로 대체하지 않음 | 목록 오류 시 503, 그래프·도구 호출 없음 |
| v1이 선택값을 조용히 무시하지 않음 | 명시적 선택 요청에 409 |
| 선택 없는 기존 일반 대화 유지 | 기존 클라이언트의 POST·SSE 일반 대화가 계속 동작 |
| 정상·unknown·미매칭에서 DB 매뉴얼 조회 없음 | `runbook_skipped` 기록과 실제 Resource 호출 여부 |
| 잘못된 연구 작업이 섞여도 조회 전 차단 | 다른 대상 작업을 직접 넣었을 때 예외와 도구 호출 0회 |
| 프록시가 선택값·422·409를 유지 | 중계된 요청 필드와 HTTP 응답 상태 |
| 프록시 연결 실패·스트림 중단 처리 | 503 또는 Error 이벤트, 연결 정리 여부 |

단위·통합 테스트는 기록용 MCP 대역과 임시 SQLite를 사용한다. 실제 비교 화면은 실행한 버전, fake 설정, 샘플 데이터, API 결과를 [실행 증거 문서](evidence/step_01/README.md)와 함께 확인한다. 테스트 통과와 브라우저 화면 확인은 서로 다른 증거다.

이번 비교에서 확인할 것은 실행 경로와 대상·검증·매뉴얼 선택 동작이다. 다음 주장은 별도 측정 없이 사용하지 않는다.

- “응답 속도가 빨라졌다”: 실습용 고정 지연, 최초 타임아웃, 재시도, 샘플 변동이 있으므로 대상 수 변화만으로 판단하지 않는다.
- “진단 정확도가 향상됐다”: 실제 장애 정답 데이터와 평가를 사용하지 않았다.
- “관리자 업무 시간이 줄었다”: 관리자 업무 실험을 수행하지 않았다.
- “RAG로 정확한 근거를 찾는다”: 이번 버전은 기존 키워드 매뉴얼 선택이며 의미 기반 문서 검색이 아니다.

## 12. 명시적 선택 경로와 기존 API의 범위

화면은 시스템 선택을 필수로 요구한다. 반면 API는 기존 실습 클라이언트를 위해 `selected_system` 생략 또는 JSON `null`을 허용한다.

| 구분 | 명시적 선택 있음 | 선택 없음 또는 JSON null |
|---|---|---|
| 분류 | 코드에서 점검 경로 선택 | 기존 fast 모델의 route 호출 |
| 최초 계획 | 선택한 시스템의 조회 작업 하나 | 기존 모델 계획. fake의 추가 web-01 등 실습 동작 유지 |
| 계획 프롬프트 | 사용하지 않으므로 요청하지 않음 | 기존 MCP Prompt 탐색·호출 |
| 매뉴얼 미매칭 | 근거 부족 기록 후 생략 | 기존 db-down 기본 선택 유지 |
| 대상 추측 | 하지 않음 | 기존 대상 추측·기본값 유지 |

따라서 개선 결과는 **화면 및 명시적 선택 API 경로의 결과**로 한정해서 설명한다. 선택 없는 API까지 새 문의 접수 방식으로 완전히 전환한 것은 아니다.

## 13. 다음 개선에서 해결할 부분

이번 비교 화면에 아래 항목이 보여도 1단계의 대상 선택 변경으로 해결됐다고 설명하지 않는다.

1. **반려된 첫 초안 표시**: fake 첫 답변 “재시작하면 됩니다.”가 토큰 이벤트로 전달된다. 현재 화면은 시도별 말풍선을 만들어 반려된 답변도 남길 수 있다. 직원용 화면에서는 검증 전 문구를 최종 안내와 구분하거나 표시를 보류해야 한다.
2. **약한 최종 답변 검사**: 검증은 여전히 30자 이상과 `1)` 포함 여부를 사용하고 세 번째 시도에 통과시킨다. 시스템·수치·근거의 일치 검증과 최대 실패 시의 대체 안내가 필요하다.
3. **후속 질문과 문의 저장**: DB에는 기존 대화 메시지를 저장한다. 선택 시스템·증상·진행 상태를 가진 별도 문의 저장 구조는 아직 없고, 화면의 `thread_id`는 새로고침 시 다시 만들어진다.
4. **정상 지표와 사용자 문제의 구분**: 매뉴얼 생략은 개선했지만 “서버 지표 정상”이 “직원 문제 해결”로 읽히지 않도록 답변 형식을 추가해야 한다.
5. **RAG와 출처**: 매뉴얼은 기존 키워드 규칙으로 고른다. 직원용 FAQ·기본 확인 문서와 운영자용 기술 매뉴얼을 구분해 검색하고, 사용한 문서와 발췌를 답변에 연결하는 단계가 남아 있다.
6. **관리자 접수 요약**: 현재 선택값은 요청·응답·화면에 있다. 문의 목록과 요약 저장·관리자 조회는 다음 단계이며 외부 메시지 전송도 구현하지 않았다.

## 14. PPT에 옮길 순서와 보존 방법

| 슬라이드 | 보여줄 내용 | 사용할 코드·화면 |
|---|---|---|
| 1. 개선 목표 | 직원이 시스템을 고르고 증상을 자연어로 입력하는 기본 점검 | 사례 D 전후 화면, 변경 범위 |
| 2. 자연어 접수 문제 | 특정 단어가 없어 일반 대화로 빠지는 fake 사례와 선택 분기 | 사례 A 전후, route 코드 |
| 3. 대상 결정 방식 | 실습용 추가 시스템 대신 선택한 시스템 하나만 조회 | 사례 B 전후, fake 계획·새 계획·호출 전 검사 |
| 4. 근거 없는 매뉴얼 | 정상 샘플에서 기본 DB 매뉴얼로 연결되는 경로 개선 | 사례 C 전후, 분류·재계획 코드 |
| 5. 요청 전체에 적용 | 화면→프록시→API→그래프가 선택값을 유지하고 잘못된 입력 차단 | ChatRequest·catalog·SSE 검증, 테스트·API 증거 |
| 6. 결과와 다음 단계 | 확인한 동작, 사용자·관리자 관점, 남은 답변·세션·RAG 과제 | 실행 기록, 테스트 결과, 범위·한계 표 |

각 단계는 **변경 전 코드 → 구체적인 불편 → 변경 후 코드 → 검증 → 화면**의 순서로 설명한다. 화면의 답변 문구만 보여주는 것보다 실제 조회 대상과 진행 이벤트를 함께 제시하면 변경 결과를 확인하기 쉽다.

이후 단계를 진행할 때에는 이 문서의 기준 커밋과 이미 확보한 화면을 교체하지 않는다. 중간 단계에서는 변경 파일·동작·검증 결과를 간단히 기록하고, 발표용 전후 비교는 원본과 6단계 완료본으로 준비한다. 중간 캡처는 필요한 경우에만 추가하며, 이후 기능을 추가한 화면으로 1단계의 과거 결과를 대신 제시하지 않는다. 현재 작업 방식은 [변경 기록 절차](change_evidence_workflow.md)를 따른다.
