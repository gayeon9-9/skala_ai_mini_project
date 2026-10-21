# 7단계 — Qwen 답변 생성 + OpenAI 임베딩 선택

이 단계는 기존 1~6단계를 유지하고 **문서 검색에 쓰는 임베딩 공급자**를 교체할 수 있게 한다.
새 실행 프로필의 기본 구성은 `Ollama Qwen 생성 + OpenAI text-embedding-3-small 임베딩`이다.
기존 버전은 [v1.0-ollama](https://github.com/gayeon9-9/skala_ai_mini_project/tree/v1.0-ollama)
태그에 고정했다. 이전 커밋·평가·캡처는 덮어쓰지 않는다.

**검증 상태:** OpenAI 실제 연결, 동일 질문 검색 비교, Qwen 통합 문의 1건을 확인했다.
최초 구현 시 187개 테스트 + 111개 하위 검사가 통과했고, 후속 기준 점수 반영은
[추가 검증 기록](evidence/embedding_comparison/api_verification.json)에 남겼다.
처음에는 실행 권한 거절로 미측정이었으나 사용자의 후속 테스트 요청으로 실제 호출했다.
**이번 소표본에서는 OpenAI의 검색 정확도·속도 우위가 확인되지 않았다.**
[실측 결과](evidence/embedding_comparison/RESULTS.md)를 함께 읽는다.

## 1. 기존 코드와 동작

`server.py`가 `OllamaEmbedder`를 직접 생성하고 `KnowledgeRetriever`에 넣었다.
문서와 질문을 `qwen3-embedding:0.6b`로 변환하고, 로컬 코드가 코사인 유사도로
관련 문서를 고른 뒤 Qwen 생성 모델이 문서·샘플 시스템 점검 결과를 읽어 답했다.

```python
# 기존 구조를 설명하기 위한 축약 예시
retriever = KnowledgeRetriever(embedder=OllamaEmbedder(...))
```

이전에도 의미 검색을 수행했다. OpenAI 추가를 “키워드 검색에서 처음 RAG로 전환”한
것으로 설명하면 안 된다. 그 전환은 이미 4단계에서 완료했다.

## 2. 기존 구조의 한계

- 서버 조립 코드가 Ollama에 고정되어 다른 임베딩 API를 비교하려면 코드를 수정해야 했다.
- Qwen 전용 `Instruct: ... Query: ...` 질의 형식이 검색기에 직접 들어 있었다.
- 생성 모델과 임베딩 모델을 모두 로컬에서 운영해야 했다.
- 두 임베딩 공급자를 같은 질문으로 비교하는 전용 평가 도구가 없었다.

이는 기존 모델이 부정확했다는 뜻은 아니다. 공급자 교체와 운영 선택의 제약이다.

## 3. 사용자·관리자 영향

사용자는 실제 검색 품질이 좋아질 경우 더 적절한 근거를 받을 수 있다. 이번에는
FAQ 검색 품질이 같았고 운영자 질문 1건에서는 OpenAI가 정답을 놓쳤다.
관리자는 검색 공급자를 선택하고 저장된 문의에서 답변 모델과
문서 검색 모델을 구분할 수 있다. 연결 오류는 “관련 문서 없음”과 구분된다.

## 4. 개선 방향

생성 모델과 검색 모델을 독립적으로 설정하고, 임베딩 공급자별 입력 형식과 캐시를 분리한다.
OpenAI 실패 시 다른 공급자로 자동 우회하지 않는다. 점수 기준은 개발용 사례에서
결정하고 고정한 뒤 같은 회귀 질문으로 비교한다. 문서에 없는 내용을 외부 검색으로
찾거나 OpenAI에게 최종 답변을 생성시키는 기능은 이번 범위에 포함하지 않는다.

## 5. 반영 파일과 코드

| 파일 | 변경 내용 |
|---|---|
| `agent/embeddings.py` | Ollama/OpenAI 어댑터, 공급자 factory, 원문 질의/Qwen 지시문 분리, API 사용량, 민감정보 없는 오류 |
| `agent/retrieval.py` | 제공자별 질의 변환, 모델·차원·질의 형식에 따른 캐시 분리, 검색 메타데이터 |
| `server.py` | factory로 검색기 조립, 초기화 실패 시 자원 정리, 무료 health 구성 정보 |
| `tools/build_rag_index.py` | 공급자별 색인 생성, 무료 구성 확인과 실제 연결 probe 분리 |
| `tools/run_hybrid.py`, `sh/run_agent_hybrid.sh` | Qwen + OpenAI 기본 실행, Ollama 비교 옵션, 기존 포트 보호, 로컬 생성 모델 사전 확인 |
| `tools/verify_local_model.py` | 실제 Qwen 생성 흐름에 임베딩 공급자 선택 옵션 추가 |
| `evals/compare_embeddings.py` | 동일 질문·문서 비교, dev 점수 기준 선택, test 조건 검증, 사용량·지연·오류 포함 집계 |
| `agent/observability.py`, `agent/inquiry_summary.py`, `web_admin.html` | 실행 당시 답변/검색 모델을 저장·관리자 화면에 표시 |
| `.env.example` | 키를 제외한 새 구성 예시. 실제 `.env`는 변경·커밋하지 않음 |
| `tests/test_embedding*.py` | 응답 검증, 캐시 호환, 오류, 설정, 관리자 메타데이터, 평가 공식 검사 |

```python
# 현재 서버의 검색기 조립. 생성 모델 선택(_build_llms)과 독립적이다.
retriever = KnowledgeRetriever(embedder=create_embedder(), min_score=min_score)
```

OpenAI 어댑터는 공식 `/v1/embeddings`에 `model`, `input`, `encoding_format="float"`,
`dimensions`를 전달한다. 응답의 입력별 `index`를 검사해 문서 순서를 맞추고,
모델·차원·유한 숫자·영벡터를 검증한다. 문서는 최대 64개씩 요청한다.
기존 Qwen의 캐시 서명은 유지하고 OpenAI 캐시는 별도로 생성한다.

키는 서버 환경에서만 읽는다. OpenAI 모델의 원격 가중치 해시는 API가 제공하지 않아
구성 버전을 기록하며, 가중치를 고정한 것처럼 표현하지 않는다.

## 6. 전후 비교와 측정 상태

| 항목 | 기존 Ollama 버전 | 새 버전 |
|---|---|---|
| 최종 답변 | Qwen | Qwen 유지 |
| 임베딩 | Ollama 고정 | OpenAI/Ollama 선택, 새 실행 프로필은 OpenAI 기본 |
| 문서 벡터 저장·검색 | 로컬 | 로컬 유지 |
| 임베딩 연산 위치 | 로컬 | OpenAI 선택 시 외부 API |
| 관리자 실행 정보 | 주로 코드·시간 정보 | 답변 모델과 검색 공급자·모델·차원 추가 |
| API 오류 | Ollama 연결 오류 처리 | OpenAI 인증·한도·시간 초과 구분, 다른 공급자로 자동 전환하지 않음 |
| 동일 회귀 질문 Hit@3 | 정답 질문 10/10(3회 반복 30/30) | 9/10(27/30) |
| 평균 검색 시간 | .036초 | .176초 |

같은 작은 test 평가에서 FAQ는 양쪽 6/6, Runbook은 Ollama 4/4·OpenAI 3/4였다.
이는 모든 후보가 관련 있거나 모든 답변이 정확하다는 뜻이 아니다. OpenAI가 더 빠르거나
정확해졌다고 발표하지 않는다. 전체 답변 지연과 분리한 검색 단계만 비교한 결과다.
[상세 측정 상태·재현 순서](evidence/embedding_comparison/README.md)를 따른다.

## 7. 활용 예시와 한계

- 로컬 모델 설치 부담을 줄이고 싶은 환경에서는 OpenAI 임베딩을 선택할 수 있다.
- 문서를 외부로 전송할 수 없는 환경에서는 기존 Ollama 임베딩 구성을 유지할 수 있다.
- 발표에서는 공급자 교체 가능한 구조, 기존 캐시 호환, 오류 처리·자동 검증을 구현 성과로 설명한다.
- 측정 결과가 요구사항에 맞는지 보고 공급자를 선택한다. 생성 모델이 그대로라 전체 답변 시간이
  크게 줄거나 문서 밖 지식까지 답할 수 있게 됐다고 설명하지 않는다.

OpenAI 사용 시 문서 청크와 검색 질문은 외부로 전송되고 입력량에 따라 과금된다.
API 한도·네트워크 연결에 의존한다. 서버 입력 8,000자는 API 토큰 상한을 보장하지
않으며, 상한 초과는 검색 오류로 처리한다. OpenAI 문턱값 `.30`은 **dev 평가에서 고정한 값**이며
test 실패 결과를 보고 낮추지 않았다. Ollama는 기존 `.50`을 유지한다.

공식 계약: [Embeddings API](https://developers.openai.com/api/reference/resources/embeddings/methods/create),
[임베딩 가이드](https://developers.openai.com/api/docs/guides/embeddings).
