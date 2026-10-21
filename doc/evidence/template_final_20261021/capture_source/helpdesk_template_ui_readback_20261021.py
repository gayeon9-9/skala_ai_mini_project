"""시연 UI가 만든 합성 접수만 읽어 LangSmith 수집을 검증한다. 모델 재호출 없음."""
import json, logging, sys, time
from pathlib import Path
from datetime import datetime
import httpx
ROOT=Path('/Users/gayeon/all_total_vue_student')
sys.path.insert(0,str(ROOT))
from tools.verify_langsmith import tracing_configuration, descendants
from langsmith import Client
from urllib3.util.retry import Retry
EVIDENCE=ROOT/'doc/evidence/template_final_20261021'
for name in ('langsmith','httpx','httpcore','urllib3'):logging.getLogger(name).setLevel(logging.CRITICAL)
manifest=json.loads((EVIDENCE/'environment.json').read_text())
with httpx.Client(timeout=15,trust_env=False) as local:
    response=local.get(manifest['frontend_url']+'/admin/inquiries');response.raise_for_status()
    listing=response.json()
    details=[]
    for item in listing['items']:
        response=local.get(manifest['frontend_url']+'/admin/inquiries/'+item['inquiry_id']);response.raise_for_status()
        details.append(response.json())
api={'checked_at':datetime.now().astimezone().isoformat(),'synthetic':True,'source':manifest['frontend_url'],
     'read_only':True,'list':listing,'details':details}
(EVIDENCE/'ui_api_records.json').write_text(json.dumps(api,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
config=tracing_configuration(ROOT/'.env')
client=Client(**config,timeout_ms=(3000,10000),retry_config=Retry(total=0),omit_traced_runtime_info=True)
records=[]
for inquiry in details:
    for request in inquiry['requests']:
        if request['status']!='completed' or not request.get('run_id'):continue
        item={'inquiry_id':inquiry['inquiry_id'],'request_id':request['request_id'],'run_id':request['run_id']}
        for attempt in range(4):
            try:
                run=client.read_run(request['run_id'],load_child_runs=True)
                metadata=(run.extra or {}).get('metadata',{})
                children=descendants(run)
                item['root_duration_seconds']=(run.end_time-run.start_time).total_seconds() if run.end_time else None
                item['children']=[{'name':child.name,'duration_seconds':(child.end_time-child.start_time).total_seconds() if child.end_time else None,'has_error':bool(child.error)} for child in children]
                keys=['code_revision','fast_model','quality_model','embedding_provider','embedding_model','embedding_dimensions',
                      'selected_system','inquiry_id','request_id','thread_id','validation_status','retrieved_document_ids','index_version','processing_seconds']
                item.update(name=run.name,completed=run.end_time is not None,has_error=bool(run.error),child_count=len(children),
                            child_names=sorted(set(child.name for child in children)),metadata={k:metadata.get(k) for k in keys},
                            run_url=client.get_run_url(run=run,project_name=manifest['langsmith_project']))
                item['checks']={'root_name':run.name=='chat_request_stream','root_completed':run.end_time is not None,
                                'root_no_error':not bool(run.error),'children_present':bool(children),
                                'inquiry_matches':metadata.get('inquiry_id')==inquiry['inquiry_id'],
                                'request_matches':metadata.get('request_id')==request['request_id'],
                                'revision_matches':metadata.get('code_revision')==manifest['code_revision'],
                                'embedding_openai':metadata.get('embedding_provider')=='openai',
                                'embedding_model_matches':metadata.get('embedding_model')=='text-embedding-3-small',
                                'fast_model_matches':metadata.get('fast_model')==manifest['health']['fast_llm'],
                                'quality_model_matches':metadata.get('quality_model')==manifest['health']['quality_llm']}
                item['verified']=all(item['checks'].values())
                if item['verified']:break
            except Exception as exc:item['error_type']=type(exc).__name__
            if attempt<3:time.sleep(1)
        records.append(item)
report={'checked_at':datetime.now().astimezone().isoformat(),'synthetic':True,'project':manifest['langsmith_project'],
        'scope':'UI에서 만든 합성 접수의 원격 추적 readback. 추가 생성/임베딩 호출 없음. 품질 점수 아님.',
        'runs':records,'verified':bool(records) and all(item.get('verified',False) for item in records)}
(EVIDENCE/'langsmith_readback.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'inquiries':len(details),'runs':len(records),'verified':report['verified'],
                  'items':[{'run_id':r['run_id'],'verified':r.get('verified'),'validation':r.get('metadata',{}).get('validation_status'),
                            'documents':r.get('metadata',{}).get('retrieved_document_ids'),'error_type':r.get('error_type')} for r in records]},ensure_ascii=False))
