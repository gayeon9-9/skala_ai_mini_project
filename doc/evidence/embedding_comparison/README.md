# Ollama / OpenAI 임베딩 비교 기록

현재 상태: **구현·오프라인 테스트 완료 / OpenAI 실측 미완료**.
`v1.0-ollama`는 이전 6단계 완료본 `f30ef7848ec6c0af54e5d5015d21d402a1dbb811`이다.
이 폴더의 후속 결과를 과거 `doc/evidence/final/` 자료에 덮어쓰지 않는다.

## 확인된 결과와 아직 확인하지 못한 결과

| 항목 | 결과 | 근거 |
|---|---|---|
| 과거 Ollama FAQ Hit@3 | 6/6 | [과거 raw 결과](../final/rag_test.json) |
| 과거 Ollama Runbook Hit@3 | 4/4 | 같은 파일. 현재 OpenAI와 같은 시점에 측정한 값이 아님 |
| 과거 Ollama 근거 없는 질문 | 2/2 no_match | 같은 파일 |
| 과거 검색 평균 | FAQ 약 .187초 / Runbook 약 .028초 | 색인 준비 후 소표본. cold start 통제 없음 |
| 새 공급자 분리·오류·캐시·평가를 포함한 전체 테스트 | 187개 + 하위 검사 111개 통과 | [오프라인 검증 기록](verification.json) |
| OpenAI 연결 probe | 샌드박스 연결 오류, 권한 확장 실행 거절 | 인증 성공/실패를 판정할 수 없음 |
| OpenAI 품질·속도·토큰·비용 | 미측정 | 실제 API 호출을 재시도하지 않음 |
| 개선율·승자 | 산출하지 않음 | 양쪽 실제 결과가 필요 |

MockTransport 테스트는 외부 서비스에 연결하지 않는다. API의 실제 유효성·품질·성능을
증명하지 않으며, 테스트용 가짜 벡터나 답변을 실제 비교 결과로 넣지 않는다.
OpenAI 실행 화면도 아직 캡처하지 않았다.

## 실행 설정

새 실행 프로필(기본: Qwen 답변 생성 + OpenAI 임베딩):

```bash
bash sh/run_agent_hybrid.sh
```

같은 새 코드에서 기존 검색 방식 선택:

```bash
bash sh/run_agent_hybrid.sh --embedding-provider ollama
```

포트가 사용 중이면 종료하며 다른 서버를 죽이지 않는다. 필요하면 `--port 8110`처럼
별도 포트를 선택한다. 기존에 켜져 있던 8100/8600 서버는 이번 작업에서 재시작하지 않았다.
프런트엔드를 다른 백엔드에 연결할 때는 `AGENT_BACKEND_URL`을 해당 주소로 설정한다.
정확한 과거 코드는 태그를 별도 폴더에 받아 재현하며 실제 대화 DB를 실험에 쓰지 않는다.

`.env`/환경변수의 `OPENAI_API_KEY`는 공개 저장소에 올리지 않는다. 새 런처는 생성 공급자를
Ollama로 고정하고, 임베딩 공급자와 모델을 쌍으로 지정해 과거 `.env`의 Qwen 임베딩 모델이
OpenAI 설정과 섞이지 않게 한다. 직접 `server.py` 실행 시에는 `.env.example`처럼 설정한다.

## 실제 API 실행 허용 후 순서

아래 실행은 OpenAI API 비용이 발생할 수 있다. 현재 실행 권한 거절 상태에서는 수행하지 않았다.

1. 합성 한 문장으로 연결을 확인한다.

```bash
.venv/bin/python tools/build_rag_index.py --provider openai --model text-embedding-3-small --probe
```

2. dev 질문으로 OpenAI 검색 점수 기준을 결정한다. Ollama는 기존 `.50`을 고정한다.

```bash
.venv/bin/python evals/compare_embeddings.py --split dev --calibrate --openai-min-score .50 --output doc/evidence/embedding_comparison/dev.json
```

3. 출력의 `suggested_openai_min_score`를 아래 `DEV_SCORE` 자리에 넣고 고정된 회귀 비교를 한다.

```bash
.venv/bin/python evals/compare_embeddings.py --split test --dev-report doc/evidence/embedding_comparison/dev.json --openai-min-score DEV_SCORE --repetitions 3 --output doc/evidence/embedding_comparison/test.json
```

test 결과를 보고 점수를 다시 고르지 않는다. 모델·문서·평가 질문·검색 코드 해시가
dev 이후 달라지면 실행을 거절한다. 기존에 확인했던 test를 재사용하므로 **회귀 비교**이며,
새로운 독립 최종 평가셋으로 표현하지 않는다. 문서 전체와 양쪽 질문은 동일하게 유지한다.

4. 같은 점수로 Qwen 생성까지 연결한 합성 문의 한 건을 검증한다.

```bash
.venv/bin/python tools/verify_local_model.py --embedding-provider openai --min-score DEV_SCORE --output doc/evidence/embedding_comparison/hybrid_model.json
```

합성 문의·임시 DB·MCP 샘플만 사용하며 LangSmith는 끈다. 한 건 성공은 일반 답변 정확도가 아니다.
선택된 점수는 새 런처의 `--min-score`와 운영 설정에 반영한다.

## 평가 항목과 읽는 방법

- Hit@1/Hit@3, Recall@3, 상위 3개 내 MRR, 제공 정답 밖 후보 수.
- Precision@3는 정답 있는 질문에서 **분모 3 고정**. 실제 반환 수로 나눈 정밀도와 다르다.
- 근거 없는 질문의 no_match 비율, 오류 수, 다른 시스템/독자 문서 노출 수.
- 색인 생성·워밍업·검색 시간을 분리하고, 동일 질문의 공급자 실행 순서를 번갈아 진행한다.
- 검색 평균·중앙값·p95 및 오류를 제외한 성공 표본 시간도 별도로 기록한다.
- 토큰·임베딩 POST 호출·실패를 색인/워밍업/검색으로 구분한다. 금액은 해당 시점 요율로 별도 계산한다.

로컬 모델을 강제 종료하거나 OS 캐시를 비우지 않으므로 완전한 cold start 비교가 아니다.
반복 3회는 서로 다른 질문 3배를 의미하지 않는다. 제공 정답 밖 문서가 실제로 무관하다는
보장도 없으며, 검색 결과를 전체 답변 품질·실제 장애 진단 성능으로 확대 해석하지 않는다.
