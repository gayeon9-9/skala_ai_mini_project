"""실제 Qwen + OpenAI 임베딩으로 합성 다회차 접수 사례를 반복 평가한다.

기존 서버/DB/색인은 건드리지 않는다. 임시 DB·캐시, 실제 MCP 샘플을 사용하며
LangSmith 전송은 끈다. OpenAI에는 합성 질문과 실습 문서를 전송한다.
반려·fallback·시간 초과도 분모에 남기고 원본 응답을 저장한다. 검사 성공률은
명시한 접수/흐름 계약의 통과율이며 답변 전체의 의미 정확도나 원인 진단율이 아니다.
"""
import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def check_turn(turn, result):
    """LLM 응답을 다시 LLM으로 채점하지 않고 사전에 정한 접수 계약만 확인한다."""
    from agent.answer_grounding import unsupported_http_error_codes
    intake = result.get('intake') or {}
    fields = intake.get('fields') or {}
    checks = {'terminal_answer': bool(result.get('final')) and
              result.get('validation', {}).get('status') in {'passed', 'fallback'}}
    for name, status in turn.get('statuses', {}).items():
        checks[f'{name}_status'] = fields.get(name, {}).get('status') == status
    for name, value in turn.get('contains', {}).items():
        checks[f'{name}_value'] = value in (fields.get(name, {}).get('value') or '')
    if 'question_field' in turn:
        checks['next_question'] = intake.get('last_question_field') == turn['question_field']
        checks['question_present'] = bool(intake.get('questions')) == (turn['question_field'] is not None)
    if 'retrieval_status' in turn:
        retrieval = result.get('retrieval') or {}
        checks['retrieval_status'] = retrieval.get('status') == turn['retrieval_status']
        if turn['retrieval_status'] == 'no_match':
            checks['no_documents'] = retrieval.get('documents') == []
    checks['no_unreported_http_code'] = not unsupported_http_error_codes(result.get('final', ''), intake)
    return checks


def summary(rows, expected_turns):
    # 미실행/예외도 성공으로 세지 않는다. fallback은 완료와 생성 성공을 구분한다.
    return {'expected_turns': expected_turns, 'recorded_turns': len(rows),
            'contract_passed_turns': sum(bool(r.get('checks')) and all(r['checks'].values()) for r in rows),
            'validation_status_counts': dict(Counter(r.get('result', {}).get('validation', {}).get('status', 'error') for r in rows)),
            'model_intake_turns': sum(r.get('result', {}).get('intake', {}).get('source') == 'model' for r in rows),
            'error_turns': sum('error_type' in r for r in rows)}


def save(output, report):
    # 한 요청이 끝날 때마다 보존한다. 다음 요청에서 오류가 나도 앞선 실패/성공을 잃지 않는다.
    temp = output.with_suffix('.tmp')
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    temp.replace(output)


async def evaluate(args):
    os.environ.update(PYTHON_DOTENV_DISABLED='1', LANGSMITH_TRACING='false', LANGCHAIN_TRACING_V2='false')
    from langsmith import tracing_context
    from agent.chat_service import ChatService
    from agent.deps import SupervisorDeps
    from agent.graph import build_graph
    from agent.inquiry_store import SqliteInquiryStore
    from agent.mcp_client import MCPClient
    from agent.ollama_llm import OllamaFastLLM, OllamaQualityLLM
    from agent.retrieval import KnowledgeRetriever
    from agent.embeddings import create_embedder
    from agent.session_store import SqliteSessionStore
    from tools.embedding_support import load_embedding_environment
    load_embedding_environment(keys=('OPENAI_API_KEY',))
    cases = json.loads(args.cases.read_text())
    if args.case_id:
        cases['cases'] = [c for c in cases['cases'] if c['id'] == args.case_id]
        if not cases['cases']: raise ValueError('평가 사례 ID를 확인하세요.')
    expected = sum(len(c['turns']) for c in cases['cases']) * args.repeat
    report = {'created_at': datetime.now(timezone.utc).isoformat(),
              'code_revision': subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
              'source_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in sorted((ROOT/'agent').glob('*.py'))},
              'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'cases_sha256': hashlib.sha256(args.cases.read_bytes()).hexdigest(),
              'case_version':cases['version'], 'selected_case_ids':[c['id'] for c in cases['cases']], 'repeats':args.repeat,
              'models':{'intake':'qwen3:0.6b','answer':'qwen3:8b','embedding':'text-embedding-3-small','dimensions':1536},
              'min_score':0.30, 'sample_monitoring':True, 'langsmith_enabled':False,
              'scope':cases['scope'], 'results':[], 'status':'running', 'timeout_seconds':args.timeout,
              'note':'모델 응답 원문을 보존한다. 최초 색인 구축 시간은 요청 시간과 별도이며 속도 개선률이 아니다.'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    save(args.output, report)
    with tempfile.TemporaryDirectory(prefix='helpdesk-live-eval-') as temp:
        store=SqliteSessionStore(Path(temp)/'session.db')
        rag=KnowledgeRetriever(embedder=create_embedder(provider='openai',model='text-embedding-3-small',dimensions=1536),
                               cache_dir=Path(temp)/'rag',min_score=.30)
        mcp=MCPClient(flaky_first_tool_call=False)
        deps=SupervisorDeps(fast_llm=OllamaFastLLM('qwen3:0.6b',.00015),
                           quality_llm=OllamaQualityLLM('qwen3:8b',.005),mcp=mcp,
                           session_store=store,version='v2',inquiry_store=SqliteInquiryStore(store),retriever=rag)
        graph=build_graph(deps)
        try:
            await mcp.start()
            start=time.perf_counter()
            report['index']=await rag.build_index()
            report['index_build_seconds']=round(time.perf_counter()-start,3)
            for repeat in range(args.repeat):
                for case in cases['cases']:
                    thread='synthetic-eval-'+str(uuid4())
                    for number, turn in enumerate(case['turns'],1):
                        service=None
                        row={'case_id':case['id'],'repeat':repeat+1,'turn':number,'input':turn['message']}
                        start=time.perf_counter()
                        try:
                            req=SimpleNamespace(thread_id=thread,request_id=str(uuid4()),selected_system=case['system'],message=turn['message'])
                            service=ChatService(deps,graph,req)
                            with tracing_context(enabled=False):
                                async with asyncio.timeout(args.timeout):
                                    result=await service.invoke()
                            row['result']={k:result.get(k) for k in ('final','draft','intake','retrieval','validation','validation_attempts','events')}
                            row['checks']=check_turn(turn,result)
                        except Exception as exc:
                            row['error_type']=type(exc).__name__
                            row['checks']={'request_completed':False}
                        finally:
                            if service is not None: service.interrupted()
                        row['seconds']=round(time.perf_counter()-start,3)
                        report['results'].append(row)
                        report['summary']=summary(report['results'],expected)
                        save(args.output,report)
                        print(json.dumps({k:row[k] for k in ('case_id','repeat','turn','checks','seconds')},ensure_ascii=False),flush=True)
        except Exception as exc:
            report['setup_error_type']=type(exc).__name__
        finally:
            report['embedding_usage']=rag.embedder.usage_snapshot()
            try: await mcp.stop()
            except Exception as exc: report['cleanup_error_type']=type(exc).__name__
            await rag.aclose()
            store.close()
    report['summary']=summary(report['results'],expected)
    report['status']='completed' if len(report['results'])==expected else 'incomplete'
    save(args.output,report)
    print(json.dumps(report['summary'],ensure_ascii=False),flush=True)
    return 0 if report['summary']['contract_passed_turns']==expected else 1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--cases',type=Path,default=ROOT/'evals/helpdesk_scenarios.json')
    parser.add_argument('--case-id', help='실패 사례만 재확인할 때 선택. 생략하면 전체 실행')
    parser.add_argument('--repeat',type=int,choices=range(1,4),default=2)
    parser.add_argument('--timeout',type=int,choices=range(30,301),default=180)
    args=parser.parse_args()
    if args.output.exists():parser.error('기존 평가 결과를 덮어쓰지 않습니다. 새 output 경로를 사용하세요.')
    return asyncio.run(evaluate(args))


if __name__=='__main__':
    raise SystemExit(main())
