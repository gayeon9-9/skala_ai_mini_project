"""최신 커밋의 합성 UI 시연 서버. 기존 앱/DB/.env를 수정하지 않는다."""
import io, json, logging, os, signal, shutil, socket, subprocess, tarfile, tempfile, time
from pathlib import Path
from datetime import datetime
import httpx
from dotenv.parser import parse_stream

ROOT=Path('/Users/gayeon/all_total_vue_student')
PYTHON=ROOT/'.venv/bin/python'
EVIDENCE=ROOT/'doc/evidence/template_final_20261021'
BACKEND, FRONTEND=8120,8620
PROJECT='skala-helpdesk-template-ui-synthetic-20261021'

def main():
    for port in (BACKEND,FRONTEND):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',port))
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    runtime=Path(tempfile.mkdtemp(prefix='helpdesk-template-ui-'))
    snapshot=runtime/'snapshot';snapshot.mkdir()
    with tarfile.open(fileobj=io.BytesIO(subprocess.check_output(['git','archive',commit],cwd=ROOT))) as archive:
        archive.extractall(snapshot,filter='data')
    if (ROOT/'.rag_cache').is_dir():
        shutil.copytree(ROOT/'.rag_cache',snapshot/'.rag_cache')
    allowed={'OPENAI_API_KEY','LANGSMITH_API_KEY','LANGCHAIN_API_KEY','LANGSMITH_ENDPOINT','LANGCHAIN_ENDPOINT','LANGSMITH_WORKSPACE_ID','LANGCHAIN_WORKSPACE_ID'}
    values={key:os.environ[key] for key in allowed if os.environ.get(key)}
    with (ROOT/'.env').open(encoding='utf-8') as stream:
        for item in parse_stream(stream):
            if item.key in allowed and item.value and item.key not in values:
                values[item.key]=item.value
    if not values.get('OPENAI_API_KEY'):
        raise RuntimeError('OpenAIKeyMissing')
    env={key:os.environ[key] for key in ('PATH','HOME','TMPDIR','LANG','LC_ALL','USER','LOGNAME','SSL_CERT_FILE','SSL_CERT_DIR') if key in os.environ}
    env.update(values)
    tracing=bool(values.get('LANGSMITH_API_KEY') or values.get('LANGCHAIN_API_KEY'))
    env.update(PYTHON_DOTENV_DISABLED='1',PYTHONDONTWRITEBYTECODE='1',APP_REVISION=commit,SESSION_DB_PATH=str(runtime/'session.db'),
               LLM_BACKEND='ollama',SUPERVISOR_VERSION='v2',OLLAMA_BASE_URL='http://127.0.0.1:11434',
               OLLAMA_FAST_MODEL='qwen3:0.6b',OLLAMA_QUALITY_MODEL='qwen3:8b',RAG_EMBEDDING_PROVIDER='openai',
               RAG_EMBEDDING_MODEL='text-embedding-3-small',OPENAI_EMBEDDING_DIMENSIONS='1536',RAG_MIN_SCORE='0.30',
               LANGSMITH_TRACING=str(tracing).lower(),LANGCHAIN_TRACING_V2=str(tracing).lower(),
               LANGSMITH_PROJECT=PROJECT,LANGCHAIN_PROJECT=PROJECT,AGENT_BACKEND_URL=f'http://127.0.0.1:{BACKEND}')
    children=[]; logs=[]
    def interrupted(*_):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,interrupted)
    try:
        with httpx.Client(timeout=5,trust_env=False) as client:
            models=client.get('http://127.0.0.1:11434/api/tags').json()['models']
            names={m['name'] for m in models}
            assert {'qwen3:0.6b','qwen3:8b'}<=names, 'GenerationModelMissing'
        for name,port,module in [('backend',BACKEND,'server'),('frontend',FRONTEND,'web_demo_server')]:
            log=(runtime/f'{name}.log').open('w',encoding='utf-8');logs.append(log)
            code="import logging; [logging.getLogger(n).setLevel(logging.CRITICAL) for n in ('langsmith','httpx','httpcore','urllib3')]; "
            code+=f"import uvicorn; uvicorn.run('{module}:app',host='127.0.0.1',port={port},log_level='warning')"
            children.append(subprocess.Popen([str(PYTHON),'-c',code],cwd=snapshot,env=env,stdout=log,stderr=log,start_new_session=True))
        deadline=time.monotonic()+30; health=None
        with httpx.Client(timeout=1,trust_env=False) as client:
            while time.monotonic()<deadline:
                if any(p.poll() is not None for p in children):raise RuntimeError('ChildStartupFailed')
                try:
                    reply=client.get(f'http://127.0.0.1:{FRONTEND}/health')
                    if reply.status_code==200:
                        health=reply.json();break
                except httpx.RequestError:pass
                time.sleep(.25)
        if not health:raise RuntimeError('StartupTimeout')
        assert health['llm_backend']=='ollama' and health['embedding_provider']=='openai'
        manifest={'started_at':datetime.now().astimezone().isoformat(),'code_revision':commit,'runtime_dir':str(runtime),
                  'session_db':str(runtime/'session.db'),'backend_url':f'http://127.0.0.1:{BACKEND}',
                  'frontend_url':f'http://127.0.0.1:{FRONTEND}','health':health,'rag_min_score':.30,
                  'embedding_dimensions':1536,'tracing_enabled':tracing,'langsmith_project':PROJECT if tracing else None,
                  'synthetic_only':True,'sample_monitoring':True,'pid':os.getpid(),
                  'children':[{'name':name,'pid':p.pid} for name,p in zip(('backend','frontend'),children)],
                  'note':'실제 OpenAI 임베딩과 로컬 Qwen 생성. 기존 서버/DB/.env는 변경하지 않고 커밋 추출본을 별도 포트로 실행. 기존 색인 복사; UI 입력은 합성 문의만 사용.'}
        EVIDENCE.mkdir(parents=True,exist_ok=True)
        (EVIDENCE/'environment.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        (runtime/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({'status':'READY','frontend_url':manifest['frontend_url'],'backend_url':manifest['backend_url'],'runtime_dir':str(runtime),'pid':os.getpid(),'children':manifest['children'],'tracing_enabled':tracing},ensure_ascii=False),flush=True)
        while all(p.poll() is None for p in children):time.sleep(.5)
        raise RuntimeError('ChildExited')
    except KeyboardInterrupt:
        print('Stopping only demo-owned servers',flush=True)
    finally:
        for p in reversed(children):
            if p.poll() is None:p.terminate()
        for p in reversed(children):
            try:p.wait(timeout=12)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid,signal.SIGKILL);p.wait(timeout=3)
        for log in logs:log.close()

if __name__=='__main__':main()
