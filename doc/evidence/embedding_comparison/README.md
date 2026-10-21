# Ollama / OpenAI 임베딩 비교 기록

현재 상태: **구현·실제 OpenAI 연결·검색 비교·Qwen 통합 테스트 완료**.
[실측 결과와 해석](RESULTS.md)에 동일 조건 비교를 정리했다.
`v1.0-ollama`는 이전 6단계 완료본 `f30ef7848ec6c0af54e5d5015d21d402a1dbb811`이다.
이 폴더의 후속 결과를 과거 `doc/evidence/final/` 자료에 덮어쓰지 않는다.

## 확인된 결과

| 항목 | 결과 | 근거 |
|---|---|---|
| 과거 Ollama FAQ Hit@3 | 6/6 | [과거 raw 결과](../final/rag_test.json) |
| 과거 Ollama Runbook Hit@3 | 4/4 | 같은 파일. 현재 OpenAI와 같은 시점에 측정한 값이 아님 |
| 과거 Ollama 근거 없는 질문 | 2/2 no_match | 같은 파일 |
| 과거 검색 평균 | FAQ 약 .187초 / Runbook 약 .028초 | 색인 준비 후 소표본. cold start 통제 없음 |
| 최초 구현 시 전체 테스트 | 187개 + 하위 검사 111개 통과 | [오프라인 검증 기록](verification.json) |
| 최초 OpenAI 연결 시도 | 샌드박스 연결 오류, 권한 확장 실행 거절 | 위 verification.json은 당시 기록으로 보존 |
| 후속 OpenAI 실제 연결 | 성공, 1536차원, 입력 5토큰 | [probe.json](probe.json) |
| dev 기준 점수 | OpenAI .30 선택, Ollama 기존 .50 유지 | [dev.json](dev.json)의 calibration.curve |
| 같은 test 정답 Hit@3 | Ollama 30/30, OpenAI 27/30 | [test.json](test.json), 정답 질문 10개×3회 |
| 같은 test 평균 검색 시간 | Ollama .036초, OpenAI .176초 | 같은 파일, 무관 질문 포함 제공자별 36회 |
| Qwen + OpenAI 통합 | 1건 완료, 첫 규칙 검증 통과, 47.52초 | [hybrid_model.json](hybrid_model.json), 정보 추출은 규칙 보완 |
| 후속 기본값·테스트·증거 검증 | 테스트 189개 + 하위 검사 111개 통과, 증거 검사 13개 통과 | [api_verification.json](api_verification.json) |

MockTransport 테스트는 외부 서비스에 연결하지 않는다. API의 실제 유효성·품질·성능을
증명하지 않으며, 테스트용 가짜 벡터나 답변을 실제 비교 결과로 넣지 않는다.
이번 추가 검증은 실제 API/그래프 결과로 남겼으며 새 화면 캡처는 만들지 않았다.

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

## 실제 테스트 재현 순서

아래 실행은 OpenAI API 비용이 발생한다. 후속 사용자 요청으로 실행을 허용받아 한 번 수행했다.
현재 파일은 해당 실행 원본이다. 다시 측정하려면 output을 다른 파일명으로 지정해 원본을 보존한다.

1. 합성 한 문장으로 연결을 확인한다.

```bash
.venv/bin/python tools/build_rag_index.py --provider openai --model text-embedding-3-small --probe
```

2. dev 질문으로 OpenAI 검색 점수 기준을 결정한다. Ollama는 기존 `.50`을 고정한다.

```bash
.venv/bin/python evals/compare_embeddings.py --split dev --calibrate --openai-min-score .50 --output doc/evidence/embedding_comparison/dev.json
```

3. 출력의 `suggested_openai_min_score`를 고정해 회귀 비교한다. 이번 dev에서는 `.30`이었다.

```bash
.venv/bin/python evals/compare_embeddings.py --split test --dev-report doc/evidence/embedding_comparison/dev.json --openai-min-score .30 --repetitions 3 --output doc/evidence/embedding_comparison/test.json
```

test 결과를 보고 점수를 다시 고르지 않는다. 모델·문서·평가 질문·검색 코드 해시가
dev 이후 달라지면 실행을 거절한다. 기존에 확인했던 test를 재사용하므로 **회귀 비교**이며,
새로운 독립 최종 평가셋으로 표현하지 않는다. 문서 전체와 양쪽 질문은 동일하게 유지한다.

4. 같은 점수로 Qwen 생성까지 연결한 합성 문의 한 건을 검증한다.

```bash
.venv/bin/python tools/verify_local_model.py --embedding-provider openai --min-score .30 --output doc/evidence/embedding_comparison/hybrid_model.json
```

합성 문의·임시 DB·MCP 샘플만 사용하며 LangSmith는 끈다. 한 건 성공은 일반 답변 정확도가 아니다.
선택된 `.30`은 새 런처와 `.env.example`, provider가 openai인 서버의 기본값에 반영했다.
실제 `.env`나 기존 실행 중 서버는 바꾸지 않았다. 과거 `.env`에 다른 점수가 있으면 직접
server.py 실행 시 그 값이 우선하므로, 새 런처 또는 설정 예시를 기준으로 실행한다.

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
