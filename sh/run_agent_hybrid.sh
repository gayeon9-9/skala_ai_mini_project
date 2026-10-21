#!/usr/bin/env bash
# 새 기본 실행: Qwen 답변 생성 + OpenAI 임베딩. 다른 서버나 Ollama 데몬을 종료하지 않는다.
# 기존 검색 방식 비교: bash sh/run_agent_hybrid.sh --embedding-provider ollama
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ]; then
    echo "프로젝트 가상환경이 없습니다. bash sh/setup_venv.sh를 먼저 실행해주세요."
    exit 1
fi
exec .venv/bin/python tools/run_hybrid.py "$@"
