# AI 헬프데스크 발표 구성과 근거

이 문서는 최종 작성 전에 정리한 **18장 내용 준비안**이며 당시의 근거와 설명을 보존한다.
이후 사용자의 19장 Keynote 초안에서 **G081 성가연**을 확인하고, 최신 실제 실행을 반영해
**20장 발표본**으로 완성했다. 최종 구성·파일·실측 범위는 [발표 자료 안내](README.md)를 따른다.
아래 성명 미확인 안내와 18장 추천, 이전 실행 수치는 준비 당시 기록이다.
기능 기준은 `1517e8d`, 원본은 `ee32f7e`다. 1~6단계 평가와 7단계 공급자 비교를 구분한다.

## 먼저 고정할 표현과 수치

| 항목 | 발표에 사용할 표현 | 조건·근거 |
|---|---|---|
| 시스템 점검 | 선택 시스템의 **샘플 지표** 조회 | 실제 운영 서버·로그 미연동 |
| 기존 화면 비교 | fake 생성 모델에서 같은 문의의 처리 흐름 비교 | 실제 MCP, 개선본은 실제 Ollama 임베딩. 실제 LLM 답변 정확도 비교 아님 |
| 규칙 대비 RAG | 같은 Runbook 정답 질문 4개에서 1/4과 4/4 | 기존 규칙 어댑터와 실제 의미 검색. 원본 서비스 전체 진단 정확도 아님 |
| 7단계 임베딩 비교 | Ollama Hit@3 100%, OpenAI 90% | 고유 정답 질의 10개를 3회 반복. 30/30 대 27/30 |
| 문서 없는 질의 | 두 공급자 모두 6/6회 `no_match` | 고유 질의 2개를 3회 반복 |
| 검색 시간 | Ollama 평균 36ms, OpenAI 평균 176ms | 검색 단계만, 공급자별 전체 36회, 동시성 1. 색인 생성·준비 질의 제외 |
| 자동 검사 | 기록 기준 189개 테스트와 111개 하위 검사 통과 | `embedding_comparison/api_verification.json`. 같은 검사를 300개 독립 테스트로 부풀리지 않음 |
| LangSmith | 요청 1개와 하위 실행 35개 실제 수집 확인 | fake 생성 + 실제 MCP 샘플 + 실제 Ollama 임베딩 합성 문의. 규칙 피드백 2개 |
| 실제 Qwen 통합 | OpenAI 검색 + Qwen 생성 합성 한 건, 첫 답변 규칙 검증 통과 | 기록된 호출 시간 47.519505초. 접수 추출은 `rules_fallback` |
| 완료 상태 | 답변과 결과의 저장 완료 | 실제 장애 복구 완료 아님 |

현재 재실행 결과가 추가되면 기록 시점과 증거 파일을 별도로 표기한다. 이전 실측 값을
새 화면의 실측으로 바꿔 설명하지 않는다. 기존 1~6단계와 7단계의 테스트 수는 시점이 다르다.

## 추천 구성: 18장

### 1. AI 기반 IT 헬프데스크

**부제:** 자연어 문의 접수와 문서 근거를 연결한 에이전트 개선

화면에는 제목·부제·과제명 정도만 둔다. 성명·학생 번호는 실제 제출 자료에서 확인한 경우에만 넣는다.

**발표 노트:**
이 프로젝트는 기존 운영 인시던트 실습 코드를 사내 IT 문의 접수 보조 서비스로 확장한 것이다.
직원이 자연어로 문의하면 접수 정보를 정리하고, 선택한 시스템의 샘플 지표와 관련 문서를
확인한 뒤 답변한다. 관리자는 같은 접수의 원문·근거·추가 확인 사항을 조회한다.
RAG, 모듈화, 자동 테스트, LangSmith를 핵심 개선 수단으로 적용했다.

**근거:** `doc/final_report.md` 1·4·7절, `doc/step_07_openai_embeddings.md`.

### 2. 원본 코드의 역할

**화면 문구:**

- FastAPI가 요청을 받고 LangGraph가 분기와 작업 순서를 제어
- MCP가 샘플 모니터링과 정해진 Runbook을 제공
- Vue 채팅 화면, SQLite 메시지 저장, LangSmith 추적 코드 포함

**시각화:** 기존 요청 흐름을 간단한 편집 가능한 순서도로 표현한다. 문서 검색은
`알림 키워드로 Runbook 선택`이라고 표시한다. 임베딩 검색은 원본에 없었다.

**발표 노트:**
기존에도 저장·추적·의존성 주입이 있었다. 이번 과제는 모든 기술을 새로 도입한 작업이 아니다.
이미 있는 흐름을 유지하면서, 실습을 넘어 문의를 끝까지 관리하는 데 필요한 기능을 보완했다.
원본 데이터는 `data/monitoring.json`과 `data/runbooks/`에 준비한 샘플이다.

**근거:** 원본 `ee32f7e:server.py`, `agent/graph.py`, `agent/deps.py`, `agent/session_store.py`.

### 3. 기존 문제와 사용자·관리자 영향

**표:**

| 기존 문제 | 사용자 | 관리자 |
|---|---|---|
| 표현과 대상 추측에 의존 | 문의가 점검으로 연결되지 않을 수 있음 | 조회 대상을 재확인 |
| 기록 삭제·대화 식별 부족 | 증상을 다시 설명 | 후속 답변을 한 문의로 연결하기 어려움 |
| 원문만 저장 | 필요한 정보를 알기 어려움 | 증상·시각·환경을 수동 정리 |
| 키워드 검색·약한 검증 | 관련 문서 누락, 미검증 조언 노출 | 답변과 근거를 다시 검토 |

**발표 노트:**
DB에 저장하는 코드가 있어도 실행 스크립트가 `rm -f session.db`로 삭제했고,
브라우저는 새로고침 때 새 thread를 만들었다. 형식 검증은 길이와 번호만 봤으며,
세 번 실패한 답변도 합격으로 처리했다. 이런 문제는 사용자와 관리자가 다시 확인하는 일을 만든다.
업무 시간 절감량은 측정하지 않았으므로 예상 효과와 실제 관찰을 구분한다.

**근거:** `doc/final_report.md` 2·3절, `doc/step_02_persistence.md`, 아래 코드 비교 C.

### 4. 개선된 문의 처리 구조

**편집 가능한 흐름도:**
시스템 선택 / 원문·접수 저장 / 자연어 정보 정리 / MCP 샘플 점검 /
FAQ·Runbook 검색 / 답변 검증 / 저장 후 사용자 응답 / 관리자 조회.

**짧은 보조 문구:** 사용자에게는 FAQ를, 관리자에게는 운영 Runbook을 연결한다.

**발표 노트:**
사용자는 정해진 양식을 작성하지 않고 자연어로 대화한다. 서버가 필드를 정리하며
모르는 정보만 질문한다. 문서가 없거나 검색에 실패하면 두 상태를 구분한다.
관리자 화면은 저장 결과를 읽으므로 관리자 조회마다 LLM이 새 요약을 추측하지 않는다.

**근거:** `doc/final_report.md` 4·5절, `agent/graph.py`, `agent/chat_service.py`, `agent/inquiry_summary.py`.

### 5. 단계별 구현과 Git 기록

**목록:**

1. 시스템 선택과 입력 검증
2. 대화 보존과 중복 요청 처리
3. 자연어 접수와 후속 답변 병합
4. FAQ·Runbook RAG
5. 답변 근거 검증
6. 관리자 조회와 평가
7. OpenAI·Ollama 임베딩 선택 확장

**발표 노트:**
각 단계의 코드·검사·변경 기록을 별도 커밋으로 남겼다. 원본과 최종의 주요 화면 비교는
통합 이후 수행했다. 7단계는 앞선 RAG를 처음 만든 단계가 아니라 검색 공급자를 교체할 수
있도록 확장한 단계다. 답변 생성은 Qwen으로 유지한다.

**근거:** `doc/evidence/final/README.md` 커밋 표, `doc/step_07_openai_embeddings.md`.
슬라이드에는 커밋 해시를 모두 나열하기보다 발표 노트에 전체 근거를 넣는다.

### 6. 코드 개선 ① 선택한 시스템만 점검

**화면:** 아래 코드 비교 A를 좌우로 배치한다.

**핵심 문구:** 선택값을 검증하고 조회 작업 한 개를 직접 만든다.

**발표 노트:**
원본 fake 계획기는 실습용 병렬 실행을 보여주기 위해 web-01을 추가했다.
실제 모델도 계획 생성 과정에서 대상을 누락하거나 바꿀 수 있으므로, 사용자가 명시적으로
선택한 문의는 모델이 조회 대상을 다시 결정하지 않도록 했다. 실제 도구 호출 직전에도
선택 대상과 일치하는지 검사한다. 이는 시스템 원인 진단 정확도보다 요청 범위 보장의 개선이다.

**근거:** 아래 정확 코드 비교 A, `doc/step_01_code_walkthrough.md`.

### 7. 대화에서 접수 정보 만들기

**예시 입력:** “로그인 후 화면이 넘어가지 않아요. 오류 메시지는 없어요.”

**후속 입력:** “오전 9시부터요. Chrome을 사용하고 있어요.”

**결과 표:** 증상 유지 / 발생 시각 추가 / 오류 메시지 ‘없음’ 유지 / 환경 추가.

**핵심 문구:** 같은 접수로 이어서 저장하고, 새로고침 후에도 복원한다.

**발표 노트:**
미언급·없음·모름·값 있음을 따로 저장한다. “오류 없음”은 누락이 아니므로 다시 묻지 않는다.
새 발화에서 언급하지 않은 필드는 이전 값을 지우지 않는다. 완료 요청을 같은 request_id로
재전송하면 저장 결과를 재사용하여 중복 접수와 도구 호출을 막는다. 실제 작은 Qwen 모델은
추출 스키마 검사에 실패한 사례가 있어 규칙 대체 처리를 표시한다. 이를 LLM 이해 성공으로
설명하지 않는다.

**근거:** `step_02_persistence.md`, `step_03_intake.md`, `evidence/final/api_results.json`.
기존 화면 근거: `evidence/final/screenshots/03_after_restored_followup.jpg`.

### 8. 코드 개선 ② 의미 검색과 근거 제공

**화면:** 아래 코드 비교 B. 문서 준비와 질의 검색은 짧은 두 줄 설명으로 구분한다.

- 문서 준비: FAQ 4개와 Runbook 6개를 벡터로 저장
- 요청 처리: 시스템·용도 범위 안에서 관련 문서를 검색하고 출처를 연결

**발표 노트:**
기존에는 알림에 latency 같은 키워드가 있는지 확인하고, 일치하지 않으면 DB 장애 매뉴얼을
기본 반환했다. 개선본은 문서와 질문을 임베딩으로 변환하고 코사인 유사도를 계산한다.
기준 점수 아래 문서가 전부 제외되면 `no_match`로 처리한다. 문서 10개는 현재 10개 청크다.
직원 FAQ와 운영자 Runbook은 별도 검색하며 운영자 본문을 직원 답변 프롬프트에 섞지 않는다.
유사도는 장애 원인 확률이 아니다.

**근거:** 아래 정확 코드 비교 B, `agent/retrieval.py`, `agent/graph.py:317`, `step_04_rag.md`.

### 9. 코드 개선 ③ 검증 실패를 합격으로 바꾸지 않음

**화면:** 아래 코드 비교 C.

**핵심 문구:** 대상·관측 수치·인용 ID를 검사하고, 반복 실패는 제한된 안내로 종료한다.

**발표 노트:**
형식만 갖춘 답변에도 수치·대상·출처 오류가 있을 수 있다. 검사 결과를 재작성 피드백으로
전달하되 최대 세 번 이후에는 마지막 초안 대신 확인된 관측 정보로 구성한 안내를 반환한다.
검증 전 초안을 일반 말풍선으로 표시하지 않는다. 이 규칙은 명시적 오류 일부를 검사하며
문장 의미 전체나 모든 환각을 증명하지 않는다.

**근거:** 아래 정확 코드 비교 C, `agent/answer_validation.py`, `step_05_validation.md`.

### 10. 동일 문의의 사용자 화면 비교

**문의:** “로그인 후 화면이 넘어가지 않아요.”

**이전 캡션:** 일반 대화로 분류되어 점검을 수행하지 않음.

**개선 캡션:** 선택한 시스템 점검, 관련 FAQ와 접수 정보 표시.

**화면에 명시:** fake 생성 모델의 처리 흐름 비교. 실제 MCP 샘플 조회, 개선본은 실제 임베딩 검색.

**발표 노트:**
질문 문장은 같지만 개선본에는 새 시스템 선택 입력이 있다. 입력 조건 차이를 숨기지 않는다.
이 화면은 문의가 어떤 기능으로 연결되는지를 보여주며 실제 생성 모델의 정확도 비교가 아니다.
기존 캡처는 원본 `ee32f7e`와 6단계 완료본 기준이다. 새 7단계 캡처와 섞을 경우 각 버전과
생성·검색 모델을 별도로 표시한다.

**기존 원본 화면:** `evidence/final/screenshots/01_before_natural.jpg`.
**기존 개선 화면:** `evidence/final/screenshots/02_after_natural.jpg`.
**추가 사용 가능:** `evidence/step_01/screenshots/02_before_targets.jpg`, `02_after_targets.jpg`.

### 11. 관리자가 확인하는 접수 화면

**화면:** 실제 관리자 상세 캡처를 크게 배치한다.

**보조 문구:** 원문·정리된 필드·샘플 관측·FAQ와 Runbook·검증 결과를 한 접수에서 확인.

**발표 노트:**
관리자는 증상과 시각을 다시 추려 적는 대신 정리된 필드와 원문을 대조한다. 이전 완료 요청의
근거와 새 실패 요청이 섞이지 않도록 근거 생성 시각과 최신 여부도 표시한다.
7단계는 실행 당시 답변 모델과 검색 공급자를 저장해 함께 보여준다.
`completed`는 답변 저장 완료이며 장애 복구 완료가 아니다. 현재 관리자 화면은 로컬 실습용이다.

**기존 근거:** `evidence/final/screenshots/04_admin_detail.jpg`, `05_admin_fullpage.jpg`.
**최신 화면:** 재실행 담당자가 확보한 실제 캡처를 사용하고 모델·버전을 주석으로 남긴다.

### 12. 모듈화와 자동 테스트

**파일 역할:**

| 역할 | 대표 모듈 |
|---|---|
| 처리 순서 | `graph.py` |
| 접수·저장 | `intake.py`, `chat_service.py`, `inquiry_store.py` |
| 검색·생성·검증 | `embeddings.py`, `retrieval.py`, `answer_generation.py`, `answer_validation.py` |
| 추적·관리자 응답 | `observability.py`, `inquiry_summary.py` |

**실측 문구:** 기록 기준 189개 테스트와 111개 하위 검사 통과.

**발표 노트:**
기존 `SupervisorDeps`에 검색기와 저장소를 주입해 실제 구현과 테스트 대역을 교체했다.
외부 API 없이 접수 병합·중복 요청·대상 제한·잘못된 인용·검색 오류·검증 종료를 검사한다.
API·그래프·저장·프록시를 연결하는 통합 검사도 포함된다. 테스트 수는 기능 회귀 검증의
증거이며 실제 장애 진단 정확도 비율이 아니다. 최신 재실행 수치는 실행 증거가 있을 때 갱신한다.

**근거:** `evidence/embedding_comparison/api_verification.json`, `agent/deps.py`, `tests/`.

### 13. LangSmith와 실패 분석

**핵심 문구:** 실행 기록에서 오류 지점을 확인하고, 실패 사례를 회귀 테스트로 남겼다.

**확인 결과:** 요청 1개와 하위 실행 35개, 모델·문서·접수 메타데이터, 규칙 점수 2개.

**개선 사례:** 정상 부정 표현 / 관측 알림의 92% / Markdown `1.` 번호를 오차단하던 규칙 보완.

**발표 노트:**
LangSmith는 원래 있던 추적 코드를 확장해 POST와 SSE 요청, 접수 ID, 검색 문서 ID,
검증 결과와 실행 버전을 연결하는 데 사용했다. 실제 수집은 합성 문의 한 건으로 확인했으며
이 수집 실험의 생성 모델은 fake였다. 별도 실제 Qwen 실행에서는 정상 답변의 오차단을
발견했다. 이전 실패 JSON과 원격 점수 0 기록을 보존하고 후속 코드와 검사로 수정했다.
검증 규칙도 오류를 낼 수 있다는 점을 확인했다.

**근거:** `evidence/final/langsmith_check.json`, `langsmith_initial.json`,
`local_model_initial.json`, `local_model_numbering.json`, `step_06_admin_evaluation.md`.

### 14. 기존 규칙과 RAG의 검색 비교

**차트:** 정답 문서 포함 비율 25% 대 100%. 막대 아래에 1/4, 4/4를 함께 표기한다.

**표:**

| 측정 대상 | 기존 규칙 어댑터 | Ollama 의미 검색 |
|---|---:|---:|
| 같은 Runbook 정답 질문 4개 | 1/4 | 4/4 |
| Runbook 근거 없는 질문 1개 | 기본 문서 반환 | `no_match` |
| 새 FAQ 정답 질문 6개 | 비교 대상 없음 | 6/6 |

**화면 하단 조건:** 소수 합성 검색 질문, RAG는 상위 3개 기준. 실제 장애 진단 정확도와 다름.

**발표 노트:**
같은 Runbook 집합과 질문에서 검색 방법을 비교했다. 기존 규칙에는 검색문을 합성 알림으로
넘기는 어댑터를 사용했으므로 원본 전체 서비스의 end-to-end 성능이 아니다.
FAQ는 새 기능이므로 FAQ 추가 효과를 기존 대비 정확도 상승으로 합산하지 않았다.
정답 문서를 상위 후보에서 회수했다는 뜻이며 모든 후보가 적절하다는 뜻도 아니다.

**근거:** `evidence/final/rag_test.json`, `evidence/final/README.md`, `final_report.md` 6절.

### 15. Ollama와 OpenAI 임베딩 비교

**편집 가능한 표 또는 차트:**

| 지표 | Ollama Qwen 임베딩 | OpenAI small 임베딩 |
|---|---:|---:|
| Hit@3 | 100% (30/30) | 90% (27/30) |
| FAQ | 6/6 | 6/6 |
| 운영 Runbook | 4/4 | 3/4 |
| 검색 평균 | 36ms | 176ms |
| 근거 없는 질문 처리 | 6/6회 | 6/6회 |

**주요 결론:** 공급자 교체는 구현했지만, 이 표본에서 OpenAI의 정확도·속도 우위는 없었다.

**발표 노트:**
고유 질의는 12개이고 각 공급자를 세 번씩 실행했다. 정답이 있는 고유 질의 10개가 지표
분모 30회를 구성하며, 근거가 없는 고유 질의는 2개다. Ollama는 기존 문턱값 0.50,
OpenAI는 개발용 평가로 선택한 0.30을 사용했다. OpenAI는 gateway의 패킷 손실·upstream
대기 제한 질의를 놓쳤다. 평균 검색 시간은 약 140ms 길었다. 이는 API 왕복을 포함한 검색
단계 시간이며 전체 응답 시간은 아니다. 이 test는 기존에 확인한 사례의 회귀 비교다.

**근거:** `evidence/embedding_comparison/RESULTS.md`, `test.json`, `dev.json`.

### 16. 현재 구성의 실제 실행

**구성:** 문의 정리 `qwen3:0.6b`, 답변 작성 `qwen3:8b`, 검색 임베딩 `text-embedding-3-small`.

**기록된 실제 확인:** 합성 문의 한 건 완료, 출처 3개, 첫 답변 규칙 검증 통과.

**화면에 함께 표시:** 접수 추출은 규칙 보완 경로를 사용. 샘플 시스템 지표.

**발표 노트:**
OpenAI를 추가했어도 최종 답변 생성은 Qwen이 담당한다. 로컬에는 문서 벡터 색인을 저장하고
OpenAI API는 문서와 질문의 임베딩을 계산한다. 기록된 서비스 호출 시간은 약 47.52초로,
브라우저 전송·렌더링까지 포함한 체감 시간이 아니다. 단일 성공 사례의 규칙 통과를 일반
생성 품질 개선률로 해석하지 않는다. 최신 실행 결과를 사용할 경우 이 기록과 분리해 소개한다.

**근거:** `evidence/embedding_comparison/hybrid_model.json`, `step_07_openai_embeddings.md`.
**시각자료:** 최신 실제 실행 캡처. 기존 fake 캡처를 실제 Qwen 결과로 표시하지 않는다.

### 17. 기업 활용과 운영 전 추가 과제

**활용:**

- IT 헬프데스크: 직원 자연어 문의를 접수하고 관리자 확인 자료 제공
- 배포 후 점검 보조: 선택 대상·점검 기록·문서·추적 구조 재사용

**추가 필요:** 실제 모니터링·배포 이력·오류 로그, 인증·관리자 권한, 문서 갱신, 실제 문의 평가.

**발표 노트:**
현재 결과로 가장 가까운 활용은 문의 접수 보조다. 담당자가 증상·시간·환경을 다시 정리하는
부담을 줄일 수 있지만 절감 시간은 측정하지 않았다. 배포 후 실제 오류 판정에는 로그와
배포 이력 연동이 필요하다. 현재는 자동 복구·Slack 전송·Multi-Agent를 구현하지 않았다.
OpenAI 선택 시 문서와 검색 질문이 외부 API로 전송되므로 기업 환경에 맞춰 공급자를 선택해야 한다.

**근거:** `final_report.md` 7절, `step_06_admin_evaluation.md` 남은 범위, `step_07_openai_embeddings.md` 7절.

### 18. 구현을 통해 얻은 점

**핵심 문구:**

- 문의가 저장되고 이어지는 과정까지 서비스 기능으로 설계했다.
- 검증 규칙의 오차단도 실패 자료와 테스트로 고쳤다.
- 모델 선택은 이름이나 기대보다 같은 조건의 실측 결과로 판단했다.

**발표 노트 가안:**
처음에는 답변이 더 좋아지는 기능을 추가하는 것이 중심이라고 생각했지만, 실제로는 저장과
재시도, 근거 표시, 실패 종료를 함께 다뤄야 문의 서비스가 완성된다는 점을 확인했다.
정상 문장을 검증기가 막는 실패도 있었고, 외부 API가 기존 로컬 검색보다 항상 좋은 결과를
내지도 않았다. 그래서 성공 결과뿐 아니라 실패와 조건을 남기고 변경을 검증하는 과정이 중요했다.
이 회고는 수정·측정 기록을 바탕으로 만든 발표 문안이며 발표자의 실제 경험에 맞춰 표현을 조정한다.

**근거:** `doc/evidence/final/README.md` 실패와 보완, `embedding_comparison/RESULTS.md`.

## 정확한 코드 비교 3쌍

아래 블록은 지정 버전에서 읽은 실제 코드다. 각 블록 안에서는 임의로 줄을 조합하지 않았다.
들여쓰기를 제외한 내용과 기존 주석을 보존했다. PPT에는 핵심 줄을 17pt 이상으로 읽을 수
있게 배치하고 자세한 설명과 전체 GitHub 링크는 발표 노트로 옮긴다.

### A. 추측·추가 대상에서 명시적 선택으로

**이전:** `ee32f7e:agent/fake_llm.py`, `FastLLM.plan`.

```python
if "web-01" not in systems:
    systems.append("web-01")   # 연관 시스템 비교 조회 → Wave 10b 병렬 실행 대상
```

이 부분 이전에는 질문에서 정규식으로 시스템 이름을 추출하고 없으면 auth-service를 사용했다.
이 동작은 fake 모델 계획기의 실습 시뮬레이션임을 반드시 명시한다.

**이후:** `1517e8d:agent/graph.py:141`, `plan_node`.

```python
selected = validate_selected_system(state["selected_system"])
tasks = [{"id": "task-0", "agent": "research", "kind": "monitoring",
          "target": selected, "status": "pending"}]
```

이후 선택 요청은 LLM 계획 생성을 생략하고 선택 대상 한 개의 작업을 만든다.

- 이전: https://github.com/gayeon9-9/skala_ai_mini_project/blob/ee32f7e/agent/fake_llm.py
- 이후: https://github.com/gayeon9-9/skala_ai_mini_project/blob/1517e8d/agent/graph.py#L141

### B. 기본 매뉴얼 반환에서 의미 유사도 기준 검색으로

**이전:** `ee32f7e:agent/graph.py`, `_classify_incident_type`.

```python
for keyword, incident_type in _INCIDENT_TYPE_RULES:
    if keyword in text:
        return incident_type
return "db-down"   # 매칭되는 증상이 없으면 가장 일반적인 장애 대응 절차로 폴백
```

**이후:** `1517e8d:agent/retrieval.py:214`, `KnowledgeRetriever.search`.

```python
vector = _normalise(await self.embedder.embed([prompt]), 1,
                    len(index["vectors"][0]))[0]
ranked = []
for chunk, stored in candidates:
    score = sum(a * b for a, b in zip(vector, stored))
    if score >= self.min_score:
```

이후 코드의 다음 줄은 기준 점수 이상인 후보를 추가한다. 정규화한 벡터의 내적으로
코사인 유사도를 계산하고, 문서별 최고점으로 정렬해 최대 3개를 반환한다. 후보가 없으면
`no_match`, 검색 호출이 실패하면 `error`로 구분한다.

- 이전: https://github.com/gayeon9-9/skala_ai_mini_project/blob/ee32f7e/agent/graph.py
- 이후: https://github.com/gayeon9-9/skala_ai_mini_project/blob/1517e8d/agent/retrieval.py#L214

### C. 최대 시도 강제 합격에서 제한된 안내로

**이전:** `ee32f7e:agent/graph.py`, `validate_node`.

```python
ok = len(draft) >= 30 and "1)" in draft
verdict = "합격" if ok else "불합격"
if not ok and attempts >= 3:   # 실모델 무한 재작성 방지 — 최대 3회 후 통과 처리
    ok, verdict = True, "합격(최대 시도 도달 — 통과 처리)"
```

**이후:** `1517e8d:agent/graph.py:397`, `validate_node`.

```python
ok = checked["ok"]
verdict = "합격" if ok else "불합격"
exhausted = not ok and attempts >= 3
if exhausted:
    verdict = "추가 확인 필요(검증 실패)"
```

`checked`에는 `validate_answer()`의 대상·수치·출처 검사 결과가 들어간다.
이후 `finalize_node()`는 `fallback`이면 `evidence_fallback()`으로 마지막 초안을 교체한다.

- 이전: https://github.com/gayeon9-9/skala_ai_mini_project/blob/ee32f7e/agent/graph.py
- 이후: https://github.com/gayeon9-9/skala_ai_mini_project/blob/1517e8d/agent/graph.py#L397

## 근거 자료 색인

- 전체 설명: `doc/final_report.md`
- 1~6단계 화면·고정 버전·실험 조건: `doc/evidence/final/README.md`
- 7단계 실제 비교와 분모: `doc/evidence/embedding_comparison/RESULTS.md`
- 원본/final HTTP 흐름: `doc/evidence/final/api_results.json`
- 원본 규칙/RAG 검색: `doc/evidence/final/rag_test.json`
- 공급자 비교: `doc/evidence/embedding_comparison/test.json`
- 실제 Qwen + OpenAI 통합: `doc/evidence/embedding_comparison/hybrid_model.json`
- 실제 LangSmith 수집: `doc/evidence/final/langsmith_check.json`
- 최신 기록된 자동 검사: `doc/evidence/embedding_comparison/api_verification.json`
- 코드: https://github.com/gayeon9-9/skala_ai_mini_project/tree/1517e8d
