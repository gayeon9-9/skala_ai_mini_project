# OpenAI 임베딩 + Qwen 생성: 실제 UI 확인

2026-10-21에 커밋 `1517e8d1fe970bffc651ea948c30e5f96cb37f2a`를 별도 폴더·포트·임시 DB로 실행했다.
사용자가 보는 화면에서 합성 문의 1건을 전송하고, 답변·출처·새로고침 복원·관리자 상세·LangSmith 수집을 확인했다.
이는 최신 구성이 연결되어 동작하는지 확인한 기록이며, 원본 대비 성능이나 진단 정확도의 통계 평가가 아니다.

## 실행 조건과 입력

| 항목 | 값 |
|---|---|
| 문의 분석 / 답변 생성 | 실제 Ollama `qwen3:0.6b` / `qwen3:8b` |
| 검색 | 실제 OpenAI `text-embedding-3-small`, 1536차원, 문턱값 `.30` |
| 자료 | 기존 FAQ·Runbook, 샘플 모니터링 |
| 포트 | 백엔드 8110 / 화면 8610 |
| 실행 격리 | `git archive` 추출본, 임시 DB, 기존 문서 색인 복사 |
| 화면 확인 | Playwright가 제어하는 Chrome. CUA 연결 실패 후 대체 경로 사용 |
| 추적 | `skala-helpdesk-openai-ui-synthetic-20261021` 프로젝트, 합성 문의만 전송 |

선택 대상은 `auth-service`이며 입력은 다음과 같다.

> 오전 9시부터 로그인 후 화면이 넘어가지 않아요. 오류 메시지는 없어요. 윈도우 크롬을 사용해요.

기존 8100·8600 서버와 실제 대화 DB, `.env`는 변경하지 않았다. 필요한 인증값만 시연 프로세스로 읽었다.
캡처를 위해 응답이나 DOM 내용을 바꾸지 않았다. UI 요청 이후 API와 LangSmith 확인은 읽기만 수행해 생성·임베딩을 다시 호출하지 않았다.

## 관찰 결과

- UI 검사 7개가 모두 통과했다. 답변 존재, 저장 API 응답, 새로고침 후 동일 답변·시스템·접수 유지, JavaScript 오류 없음을 확인했다.
- 실제 OpenAI 검색 결과는 `faq:login` **0.551129**, `faq:browser` **0.371776**, `faq:connectivity` **0.328632**였다.
- Qwen 답변은 `faq:login`, `faq:connectivity`를 인용했고 코드의 답변 규칙 검증은 `passed`였다.
- 관리자 화면에서 동일 접수의 문의 필드·샘플 관측·검색 문서·검증 결과를 읽을 수 있었다.
- LangSmith에서 동일 접수·요청 ID의 `chat_request_stream` 실행 완료와 자식 실행 **31개**를 실제로 읽어 확인했다. 커밋, 모델, 임베딩, 검색 문서 메타데이터가 로컬 저장 결과와 일치했다.
- 실습용 첫 MCP 호출의 timeout 오류 흔적 1건이 자식 실행에 남아 있다. 재시도 후 루트 실행은 오류 없이 완료됐다.

| 시간 | 관측값 | 측정 범위 |
|---|---:|---|
| 브라우저 | 52.491초 | 전송 클릭부터 응답 완료까지 |
| 앱 기록 | 52.288초 | 요청 실행 시작부터 DB 저장 직전까지 |
| LangSmith 루트 | 52.329초 | `chat_request_stream` 실행 |
| Qwen 답변 작성 | 48.859초 | `ollama_quality.write` 자식 실행 |
| 검색 노드 | 0.938초 | `retrieve_node` 자식 실행 |

시간은 한 번의 관측값이다. 부모·자식 실행 구간은 겹치므로 서로 더하지 않으며, 서버 속도 개선률로 해석하지 않는다.

## 함께 남겨야 할 한계

- 문의 분석 모델이 네 필드의 구조를 올바르게 반환하지 못해 **`rules_fallback`**이 원문 근거에서 필드를 복구했다. 이 사례를 LLM 구조화 성공으로 소개하면 안 된다.
- 답변에 샘플임과 추가 진단 필요가 표시되지만, “고부하” 해석이나 로그인 문제와의 인과관계는 실제 장애 로그로 검증하지 않았다.
- 오류 메시지가 없다는 문의에도 504 관련 문서가 추가 인용됐다. 문턱값을 넘은 문서가 모두 질문에 충분히 적절하다는 보장은 없다.
- 규칙 검증 통과는 인용 ID·형식 등의 코드 검사를 통과했다는 뜻이다. 답변의 모든 문장이 근거에서 도출되는지 또는 원인 진단이 맞는지까지 검증한 결과는 아니다.
- 입력에 필요한 정보를 모두 포함했으므로, 이번 UI 캡처는 누락 정보에 대한 추가 질문 경로를 검증하지 않는다.
- 운영 인증·권한·실제 모니터링 연동을 갖춘 배포 검증은 아니다.

## 근거 파일

- [실행 환경](environment.json)
- [UI 검사와 원본 캡처 SHA-256](ui_verification.json)
- [대화 복원 응답](thread.json), [관리자 상세 응답](admin_detail.json)
- [UI 생성 접수의 읽기 API 확인](ui_api_records.json)
- [LangSmith 실제 readback과 노드별 시간](langsmith_readback.json)
- [입력 화면](screenshots/01_input.png), [사용자 답변](screenshots/02_answer.png)
- [출처·문의 정보](screenshots/03_sources_intake.png), [새로고침 복원](screenshots/04_restored.png)
- [관리자 전체 화면](screenshots/05_admin_fullpage.png), [관리자 상단](screenshots/06_admin_top.png)
- [발표용 상세 영역과 실측 보고서를 포함한 전체 캡처 목록·SHA-256](screenshots_manifest.json)
- [저장된 JSON 기반 실측 보고서](measurement_report.html), [RAG 비교 캡처](screenshots/13_baseline_rag_report.png), [공급자 비교 캡처](screenshots/14_embedding_report.png)
- [최종 20장 발표 자료](../../presentation/README.md)

접수 ID: `f9204629-8b4f-4dc5-88fa-e0544c3b2cdc`

LangSmith run ID: `01a15d33-38dd-7df2-b658-ef65b7022704`

촬영용 8110·8610 서버는 종료했다. 기존 8100·8600 서버는 유지했고, 종료 후 두 서버의 health HTTP 200을 확인했다.
