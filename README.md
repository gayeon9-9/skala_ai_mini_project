# AI 헬프데스크

Base AI Agent 실습 코드를 확장한 **자연어 IT 문의 접수·문서 검색·관리자 조회 서비스**다.
사용자는 시스템을 선택하고 증상을 자연어로 입력한다. 서버는 원문을 저장하고 접수 정보를
정리한 뒤 MCP 샘플 점검, RAG 검색, 답변 검증을 수행한다. 관리자는 같은 접수의 원문과
관측·문서 근거를 확인한다.

기본 실행 구성은 **Ollama Qwen 답변 생성 + OpenAI 임베딩**이다. 문서 벡터와 검색 색인은
로컬에 저장하며, 기존 Ollama 임베딩도 선택할 수 있다. 현재 시스템 지표는 실습 샘플이고
관리자 화면에는 인증이 없으므로 로컬 시연 범위로 사용한다.

## 먼저 볼 자료

- [21장 최종 발표 자료 안내](doc/presentation/README.md)
- [템플릿·프리텐다드 반영 PowerPoint](doc/presentation/output/G081_성가연_AI_헬프데스크_템플릿반영_최종.pptx)
- [기획·분석 보고서](doc/final_report.md)
- [최신 코드의 실제 Qwen·OpenAI 실행과 화면](doc/evidence/template_final_20261021/README.md)
- [최종 시연 후 접수·답변 검증 보완](doc/evidence/post_demo_fixes/README.md)
- [Ollama·OpenAI 검색 실측 비교](doc/evidence/embedding_comparison/RESULTS.md)
- [파일 역할·API·현재 처리 흐름](doc/README.md)

최신 발표본은 `ba5a883`의 접수 추출·오류 안내 보완을 반영하고, 격리 실행에서 다시 확보한
화면·테스트·LangSmith 기록을 사용한다. 이전 20장 발표본과 UI 근거는
[adce28f](https://github.com/gayeon9-9/skala_ai_mini_project/commit/adce28f)에 보존했다.
과거 코드·실패 결과·비교 자료도 해당 시점의 기록으로 유지한다.

## 구현한 기능

| 단계 | 반영 내용 | 변경 기록 |
|---|---|---|
| 1 | 시스템 선택, 빈 입력·미등록 대상 검증, 선택한 대상만 조회 | [시스템 선택](doc/step_01_system_selection.md) |
| 2 | 대화·접수 저장, 새로고침 복원, 요청 ID를 통한 중복 처리 방지 | [저장과 복원](doc/step_02_persistence.md) |
| 3 | 증상·발생 시각·오류·환경 정리, 후속 답변 병합, 추가 질문 | [자연어 접수](doc/step_03_intake.md) |
| 4 | FAQ·Runbook 의미 검색, 용도·시스템 필터, 출처 표시 | [RAG](doc/step_04_rag.md) |
| 5 | 선택 대상·관측 수치·인용 검사, 재작성 한도와 제한된 안내 | [답변 검증](doc/step_05_validation.md) |
| 6 | 관리자 목록·상세, POST/SSE 공통 추적, 검색 평가 도구 | [관리자와 평가](doc/step_06_admin_evaluation.md) |
| 7 | Ollama/OpenAI 임베딩 선택, 캐시 분리, 공급자별 비교 | [임베딩 공급자](doc/step_07_openai_embeddings.md) |

최종 시연 뒤 확인한 접수·답변 문제는 [별도 후속 기록](doc/evidence/post_demo_fixes/README.md)으로 보완한다.

- 문의 추출 JSON Schema에서 상태별 `value`·`evidence` 형식을 구분하고 출력 예시를 추가했다.
- 원문 검사를 거친 누적 접수 정보를 답변 작성 문맥에 구조화해서 전달한다.
- 접수된 오류 메시지에 없는 HTTP 4xx/5xx 코드가 제목·본문에 포함된 문서는 답변 작성 근거에서 문서 전체를 제외한다. 원본 검색 결과와 관리자 조회 근거는 보존한다.
- 프롬프트·답변 검증·fake 작성기·제한된 안내에 같은 문서 선택 규칙을 적용한다. 적용할 문서가 모두 제외되면 답변 생성 LLM을 호출하지 않고 고정된 제한 안내를 검증한다.
- 생성 답변에 미확인 코드가 남으면 반려한다. 기존 최대 3회 작성·검증 흐름과 제한된 안내를 유지하고, 모델이 붙인 내부 항목명 표시는 검증 전에 정리한다.

HTTP 코드는 접수 오류 메시지의 상태가 `provided`이고 값에 포함된 경우만 허용한다.
`없음`·`모름`·`미언급`이거나 문서·샘플 지표에만 등장하는 코드는 허용 근거로 삼지 않는다.
이 검사는 HTTP 4xx/5xx 형식의 명시적인 불일치를 다루며 모든 문장의 의미나 장애 원인을
판정하지 않는다. 기존 PPT와 실측은 `adce28f` 시점 자료로 보존한다.

사용자는 양식을 채울 필요가 없다. 예를 들어 `auth-service`를 선택하고 다음처럼 입력한다.

> 오전 9시부터 로그인 후 화면이 넘어가지 않아요. 오류 메시지는 없어요. 윈도우 크롬을 사용해요.

접수 항목은 `미언급`, `없음`, `모름`, `값 있음`을 구분한다. 관리자 화면의 `completed`는
**답변 저장 완료**이며 실제 장애가 복구됐다는 뜻은 아니다.

## 로컬 실행

저장소 루트에서 실행한다. Python 가상환경과 Ollama가 필요하며, 아래 기본 구성은 실제
OpenAI 임베딩 API를 호출한다. 문서 청크와 검색 질문을 전송하고 사용량에 따라 과금된다.

### 1. Python 환경과 생성 모델 준비

```bash
bash sh/setup_venv.sh
```

Ollama 앱 또는 `ollama serve`를 실행한 상태에서, 모델이 없으면 내려받는다.

```bash
ollama pull qwen3:0.6b
ollama pull qwen3:8b
```

`qwen3:0.6b`는 문의 정보 정리, `qwen3:8b`는 답변 작성에 사용한다. 임베딩 모델과
답변 생성 모델은 별도 설정이다. 모델 파일은 저장소에 포함하지 않는다.

### 2. 환경 설정

프로젝트에 `.env`가 없는 경우에만 [.env.example](.env.example)을 복사한다.

```bash
cp -n .env.example .env
```

`.env`의 `OPENAI_API_KEY`를 본인 키로 설정한다. 실제 키와 대화 DB는 Git에 올리지 않는다.
LangSmith를 사용하지 않을 때는 `LANGCHAIN_TRACING_V2=false`, `LANGSMITH_TRACING=false`로
설정한다. 추적을 켤 때는 본인의 LangSmith 키·프로젝트를 설정하고 합성 문의로 확인한다.

| 설정 | 기본 실행에서의 역할 |
|---|---|
| `OPENAI_API_KEY` | OpenAI 임베딩 인증 |
| `OLLAMA_FAST_MODEL` / `OLLAMA_QUALITY_MODEL` | 기본 `qwen3:0.6b` / `qwen3:8b` |
| `OPENAI_EMBEDDING_DIMENSIONS` | 기본 OpenAI 구성 1536차원 |
| `SESSION_DB_PATH` | 별도 시연 DB 경로. 생략하면 루트의 `session.db` |
| `LANGCHAIN_TRACING_V2`, `LANGCHAIN_API_KEY`, `LANGCHAIN_PROJECT` | 선택적인 외부 실행 추적 |

### 3. 백엔드와 웹 화면 실행

첫 번째 터미널에서 백엔드를 실행한다.

```bash
bash sh/run_agent_hybrid.sh
```

이 실행기는 생성 공급자를 Ollama로 고정하고 OpenAI `text-embedding-3-small`을 검색에 사용한다.
기본 문턱값은 `.30`이다. 기존 8100 포트가 사용 중이면 기존 서버를 종료하지 않고 실행을 거부한다.

두 번째 터미널에서 웹 프록시를 실행한다.

```bash
.venv/bin/python web_demo_server.py
```

- 사용자 화면: [http://localhost:8600](http://localhost:8600)
- 관리자 화면: [http://localhost:8600/admin](http://localhost:8600/admin)
- 백엔드 상태: [http://localhost:8100/health](http://localhost:8100/health)

각 터미널에서 `Ctrl+C`로 종료한다. 웹은 Vue 3 CDN을 사용하는 정적 HTML이므로 별도
Node 빌드가 없으며, 브라우저에서 CDN에 접근할 수 있어야 한다.

다른 포트로 실행할 때는 백엔드와 프록시 연결 주소를 함께 맞춘다.

```bash
# 터미널 1
bash sh/run_agent_hybrid.sh --port 8110
# 터미널 2
AGENT_BACKEND_URL=http://127.0.0.1:8110 .venv/bin/python -m uvicorn web_demo_server:app --host 127.0.0.1 --port 8610
```

### Ollama 임베딩으로 비교 실행

Ollama가 실행 중일 때 임베딩 모델을 준비하고 공급자를 명시한다.

```bash
ollama pull qwen3-embedding:0.6b
bash sh/run_agent_hybrid.sh --embedding-provider ollama
```

생성 모델은 Qwen으로 유지하며 임베딩은 로컬 `qwen3-embedding:0.6b`, 문턱값은 `.50`을
사용한다. 처음 검색할 때 색인을 만들며 이후 `.rag_cache/`의 호환되는 색인을 재사용한다.
문서나 임베딩 구성이 바뀌면 새 색인을 만든다. [공급자 비교 안내](doc/evidence/embedding_comparison/README.md)에서
별도 색인 생성과 같은 질문의 평가 방법을 확인할 수 있다.

## 코드 구성

| 파일·디렉터리 | 역할 |
|---|---|
| `server.py` | FastAPI 요청 검증·API, 시작/종료 시 의존성 조립 |
| `web_chat.html`, `web_admin.html`, `web_demo_server.py` | 사용자·관리자 화면과 같은 출처 프록시 |
| `agent/graph.py`, `agent/deps.py` | LangGraph 처리 순서와 의존성 주입 |
| `agent/chat_service.py`, `agent/inquiry_store.py` | 접수·재시도·저장·응답의 공통 수명 관리 |
| `agent/intake.py`, `agent/intake_llm.py` | 문의 필드 추출·원문 확인·후속 답변 병합 |
| `agent/embeddings.py`, `agent/retrieval.py` | 공급자별 임베딩, 로컬 색인, 필터와 검색 |
| `agent/answer_generation.py`, `agent/answer_validation.py` | 근거를 전달하는 생성 문맥과 답변 검사 |
| `agent/answer_grounding.py` | 접수 오류 메시지의 허용 HTTP 코드 추출, 작성 문서 선택과 미확인 코드 검사 |
| `agent/observability.py`, `agent/inquiry_summary.py` | 실행 추적 정보와 관리자 조회 결과 |
| `mcp_server.py`, `data/monitoring.json` | MCP 도구와 샘플 시스템 지표 |
| `data/faq/`, `data/runbooks/`, `data/knowledge_catalog.json` | 사용자 FAQ·운영자 문서·검색 메타데이터 |
| `tests/`, `evals/`, `tools/` | 자동 검사, 품질 평가, 재현·연결 확인 도구 |

## 검증과 실측 기록

자동 검사는 임시 DB와 모델·도구 대역으로 실행하며, 실제 품질 평가는 별도로 수행한다.

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

최종 시연 후 접수·문서 적용·표시 보완은 **217개 테스트와 173개 하위 검사**를 통과했다.
기존 189개에서 회귀 테스트 28개를 추가했다. [최종 검사 원본](doc/evidence/post_demo_fixes/tests.json)과
[실제 Qwen 접수 추출 전후](doc/evidence/post_demo_fixes/intake_comparison.json)를 함께 확인할 수 있다.
같은 합성 문의 한 건에서 기존 코드는 `rules_fallback`, 수정 후는 경고 없는 `source=model`이었다.
이를 전체 문의 정확도나 속도 개선율로 해석하지 않는다.
문서 적용 필터를 더하기 전의 [1차 검사 211개·하위 검사 170개](doc/evidence/post_demo_fixes/initial_tests.json)도 별도 보존했다.

1차 실제 생성에서는 미확인 504가 반복돼 3회 작성 후 제한 안내로 종료했다. 문서 적용 필터를
추가한 [후속 실제 실행](doc/evidence/post_demo_fixes/hybrid_model.json)은 `source=model`, 경고 0,
답변 검증 1회 통과였고 구체적인 504 코드는 나오지 않았다. 그래프 실행은 각각 141.44초와
49.47초였지만 단일 실행이고 샘플 관측·캐시·경합을 통제하지 않아 속도 개선율로 비교하지 않는다.
이 실행은 내부 항목명 표시 정리를 추가하기 전 원본이며, 이후 표시 보완은 [저장 응답 재생 검사](doc/evidence/post_demo_fixes/rendering_replay.json)로
확인했다. 최종 자동 검사와 후속 근거의 범위는 [보완 기록](doc/evidence/post_demo_fixes/README.md)을 따른다.

2026-10-21의 [7단계 검증 기록](doc/evidence/embedding_comparison/api_verification.json)은
**189개 테스트와 111개 하위 검사 통과**다. 이는 기록 당시 결과이며 현재 버전을 새로
실행한 결과와 구분한다.

| 측정 | 기록된 결과 | 해석 범위 |
|---|---|---|
| 기존 규칙 대 Ollama RAG | 같은 Runbook 정답 질문 4개에서 1/4 대 4/4 | 규칙 어댑터와 상위 3개 검색 비교 |
| Ollama 대 OpenAI 임베딩 | Hit@3 100%(30/30) 대 90%(27/30) | 고유 정답 질의 10개를 3회 반복 |
| 공급자별 검색 평균 | Ollama 36ms, OpenAI 176ms | 검색 단계만, 전체 사용자 대기시간 제외 |
| 이전 UI 시연 (`1517e8d`) | 브라우저 약 52.49초, LangSmith 하위 실행 31개 | 접수·504 안내 보완 전 기록 |
| 최신 템플릿 UI 시연 (`ba5a883`) | 브라우저 48.392초, LangSmith 하위 실행 30개 | 보완 후 동일 접수 복원·관리자 조회 확인 |

UI 두 행은 각각 단일 실행이며 입력·샘플 관측·캐시 조건이 완전히 같지 않다.
시간 차이를 속도 개선률로 계산하지 않는다. [이전 시연](doc/evidence/final_openai/README.md)과
[템플릿용 최신 시연](doc/evidence/template_final_20261021/README.md)을 구분해서 확인한다.
최신 시연의 앱 처리 시간 48.194초와 LangSmith 루트 시간 48.211초는 브라우저 시간과
측정 구간이 다르다. 위 수치는 해당 커밋의 역사적 기록이며 이후 코드의 성능을 뜻하지 않는다.

공급자 비교는 총 고유 질의 12개(정답 있는 10개·근거 없는 2개)를 각각 3회 실행했다.
이번 소표본에서는 OpenAI의 정확도·속도 우위를 확인하지 못했다. [원본/6단계 화면 비교](doc/evidence/final/README.md)는
fake 생성 모델을 썼고, [Qwen UI 확인](doc/evidence/final_openai/README.md)은 실제 Qwen을
썼다. 이 두 결과를 같은 모델 조건의 답변 정확도 비교로 합치지 않는다.

## 현재 한계와 후속 범위

- 샘플 관측으로 실제 장애 원인·배포 성공 여부를 확정하지 않는다. 운영 로그·메트릭·배포 이력은 미연동이다.
- `adce28f`에 보존한 최종 UI 시연 당시 문의는 작은 모델의 추출 형식 검사에 실패해 원문 규칙으로 보완했다. 이때의 `rules_fallback`은 LLM 추출 성공이 아니며, 후속 수정의 결과와 구분한다.
- 관련 문서를 찾거나 규칙 검증에 통과해도 답변의 모든 의미·인과관계가 증명되지는 않는다. 정상 답변의 오차단과 무관한 안내를 함께 점검해야 한다.
- 미확인 HTTP 코드가 섞인 문서는 일반적인 안내까지 함께 제외될 수 있다. 적용 조건별 문서 분할은 이 보수적인 제한을 완화할 후속 과제다.
- 사용자·관리자 인증과 권한 구분이 없다. thread ID나 접수 ID는 인증 수단이 아니다.
- 현재 저장·재시작 복구는 단일 서버 워커 기준이다. 대규모 조회·다중 워커 운영은 추가 설계가 필요하다.
- 자동 복구, 담당자 배정, 외부 메시지 전송, Multi-Agent는 후속 범위다.

단계별 변경과 검증을 별도 커밋으로 남기는 원칙은 [작업 기록 절차](doc/change_evidence_workflow.md)에
정리했다. 기존 Ollama 완료본은 [v1.0-ollama](https://github.com/gayeon9-9/skala_ai_mini_project/tree/v1.0-ollama),
전체 변경은 [커밋 이력](https://github.com/gayeon9-9/skala_ai_mini_project/commits/main/)에서 확인할 수 있다.
