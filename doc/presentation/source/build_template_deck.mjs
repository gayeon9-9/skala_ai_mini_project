// 사용자 제공 템플릿을 복제하고 PDF 초안·실제 코드·검증 근거로 채운 발표본.
// 실제 UI 캡처는 원본을 그대로 사용하고 실측 결과와 기대 효과를 구분한다.
import fs from 'node:fs/promises';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {FileBlob,PresentationFile} from '/Users/gayeon/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/@oai/artifact-tool/dist/artifact_tool.mjs';
import {applyPresentationChartFont,finalizePresentation} from '/Users/gayeon/.codex/plugins/cache/openai-primary-runtime/presentations/26.1017.22136/skills/presentations/container_tools/artifact_tool_utils.mjs';
const ROOT=path.resolve(import.meta.dirname,'../../..');
const WORK=path.join(ROOT,'doc/presentation');
const BUILD=path.join(WORK,'.build/template_v3');
const TEMPLATE='/Users/gayeon/Downloads/제목을 입력해주세요. (2).pptx';
const SKILL='/Users/gayeon/.codex/plugins/cache/openai-primary-runtime/presentations/26.1017.22136/skills/presentations';
const PY='/Users/gayeon/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3.12';
const FONT='Pretendard';
const C={blue:'#075BEA',ink:'#101010',muted:'#686868',line:'#BCBEC4',white:'#FFFFFF',pale:'#EAF1FF',dark:'#202127'};
const E='doc/evidence/template_final_20261021/screenshots/';
const OLD='doc/evidence/final/screenshots/';
const REPORT='doc/evidence/final_openai/screenshots/';
const p=await PresentationFile.importPptx(await FileBlob.load(TEMPLATE));
const original=[...p.slides.items], ordered=[];
await fs.mkdir(path.join(BUILD,'slides'),{recursive:true});
const fullSha='ba5a883ccc0a4d215107fc444089a253b9736cc9';
function text(s,str,x,y,w,h,size=34,color=C.ink,bold=false,align='left'){
 const t=s.shapes.add({geometry:'textbox',name:str.slice(0,48),position:{left:x,top:y,width:w,height:h},fill:'none',line:{fill:'none',width:0}});
 t.text=str;t.text.style={typeface:FONT,fontSize:size,color,bold,alignment:align,insets:0,autoFit:'none',verticalAlignment:'top'};return t;
}
function box(s,x,y,w,h,fill=C.white,border=C.line){return s.shapes.add({geometry:'roundRect',position:{left:x,top:y,width:w,height:h},fill,line:{fill:border,width:1.1},borderRadius:20});}
function note(s,body,sources=[]){s.speakerNotes.text=body+'\n\n근거\n'+sources.map(v=>v.startsWith('http')?v:'https://github.com/gayeon9-9/skala_ai_mini_project/blob/main/'+v).join('\n');}
function foot(s,str){text(s,str,222,1006,1620,40,23,C.muted);}
function badge(s,str,x,y){box(s,x,y,82,74,C.blue,C.blue);text(s,str,x,y+10,82,56,40,C.white,true,'center');}
async function slide(title,{template=1,toc=false,cover=false}={}){
 const s=original[template].duplicate();
 const idx=p.slides.items.indexOf(s);
 const snap=(await p.inspect({select:`$.slides[${idx}]`,include:['id','elements.id','elements.kind','elements.position','elements.text']})).records[0];
 const remove=[];
 for(const e of snap.elements){
  const q=e.position||{};
  const furniture=e.kind==='image'&&(q.left<145||q.width>1900||(q.top<90&&q.width>1000));
  const globe=cover&&e.kind==='image'&&q.left>850&&q.width>850&&q.height>850;
  if(!(furniture||globe||(toc&&e.kind==='image')))remove.push(e.id);
 }
 if(remove.length)p.delete(remove);
 ordered.push(snap.id);
 text(s,'AI HELP_DESK   |   AI Agent 서비스 Mini Project',220,31,1300,36,23,C.ink,true);
 text(s,String(ordered.length).padStart(2,'0'),1735,31,110,36,23,C.ink,false,'right');
 if(title)text(s,title,222,140,1620,115,title.length>26?64:76,C.ink,true,'center');
 return s;
}
async function image(s,rel,x,y,w,h,alt,crop){
 const file=path.isAbsolute(rel)?rel:path.join(ROOT,rel);const b=await fs.readFile(file);
 const im=s.images.add({blob:b.buffer.slice(b.byteOffset,b.byteOffset+b.byteLength),contentType:/\.jpe?g$/i.test(file)?'image/jpeg':'image/png',alt,fit:'contain',position:{left:x,top:y,width:w,height:h},...(crop?{crop}:{})});return im;
}
function source(rev,file,start,end){const raw=execFileSync('git',['show',`${rev}:${file}`],{cwd:ROOT,encoding:'utf8'}).split('\n');return raw.slice(start-1,end).join('\n');}
function code(s,rev,file,start,end,x,y,w,h,size=29){
 const str=source(rev,file,start,end);const lines=str.split('\n');const common=Math.min(...lines.filter(a=>a.trim()).map(a=>a.match(/^ */)[0].length));
 const display=lines.map(a=>a.slice(common)).join('\n');
 box(s,x,y,w,h,C.dark,C.dark);text(s,display,x+22,y+18,w-44,h-34,size,'#D9EBFD');
 text(s,`${file} · ${rev} · ${start}–${end}행`,x,y+h+10,w,38,22,C.muted);
 return `https://github.com/gayeon9-9/skala_ai_mini_project/blob/${rev}/${file}#L${start}-L${end}`;
}
function table(s,values,x,y,w,h,widths,size=31){
 const t=s.tables.add({rows:values.length,columns:values[0].length,left:x,top:y,width:w,height:h,columnWidths:widths,values});
 t.borders.assign({style:'solid',fill:'#D2D6DF',width:1.2});
 t.cells.block({row:0,column:0,rowCount:values.length,columnCount:values[0].length}).assign({fill:C.white,textStyle:{typeface:FONT,fontSize:size,color:C.ink},margins:{left:20,right:16,top:16,bottom:12}});
 t.cells.block({row:0,column:0,rowCount:1,columnCount:values[0].length}).assign({fill:C.blue,textStyle:{typeface:FONT,fontSize:size,bold:true,color:C.white}});return t;
}
function stageCopy(s,before,after,tech){
 text(s,'기존',246,292,160,46,30,C.muted,true);text(s,before,246,345,680,130,36);
 text(s,'개선',246,503,160,46,30,C.blue,true);text(s,after,246,556,680,170,37,C.ink,true);
 foot(s,tech);
}

// 01. 표지: 사용자 템플릿의 원래 구형 배경과 좌측 아이콘을 유지한다.
{
 const s=await slide('',{template:0,cover:true});
 text(s,'과제 1 · Base AI Agent 기능 개선 / 추가',220,248,1140,58,32,C.blue,true);
 text(s,'AI 기반\nIT 헬프데스크',214,345,1180,230,98,C.blue,true);
 text(s,'고도화 결과 보고',220,595,1040,100,74,C.ink,true);
 text(s,'자연어 문의 접수 · 근거 검색 · 답변 검증 · 이력 관리',224,742,1230,92,34);
 text(s,'G081  성가연',224,884,800,65,44,C.ink,true);
 text(s,'2026. 10. 21',224,957,800,45,28,C.muted);
 note(s,'기존 운영 점검 에이전트를 자연어 IT 문의 접수와 관리자 확인까지 연결하는 헬프데스크로 개선했다. 현재 서비스는 샘플 데이터 기반 실습용이다. 본문 프리텐다드를 유지하고 사용자 제공 템플릿의 배경과 로고를 재사용했다.',['README.md']);
}
// 02. 사용자 PDF 목차를 원본 템플릿의 편집 가능한 글자로 재구성.
{
 const s=await slide('목차',{toc:true});
 const a=[['기존 서비스 분석','기능과 처리 흐름','코드 문제와 사용자 영향'],['개선 목표와 구조','AI 헬프데스크 확장','기술별 역할과 데이터 흐름'],['단계별 개선 사항','기존 → 변경 → 구현 증거','6단계 개선 + 임베딩 선택'],['실행 결과와 비교','같은 입력의 전후 화면','검색 수치와 자동 검사'],['한계와 트레이드오프','추가된 비용·지연·제약','현재 범위와 운영 한계'],['향후 개선과 느낀 점','실제 기업 활용 방향','검증과 기술 선택의 교훈']];
 a.forEach((v,i)=>{const x=257+(i%3)*556,y=337+Math.floor(i/3)*371;text(s,String(i+1).padStart(2,'0'),x+7,y+9,60,50,34,C.white,true,'center');text(s,v[0],x+91,y+6,380,64,36,C.ink,true);text(s,v[1],x+5,y+140,460,56,29,C.muted);text(s,v[2],x+5,y+199,460,56,29,C.muted);});
 note(s,'발표 순서는 문제, 개선, 구현 증거, 전후 측정, 한계와 향후 계획이다. 기존 서비스와 개선 서비스의 비교가 주된 평가이며 임베딩 공급자 비교는 추가 기술 검증이다.');
}
// 03. 기존 서비스 소개: 실제 원본 UI와 기존 기술을 명확히 구분.
{
 const s=await slide('기존 서비스 | Base AI Agent');box(s,220,300,790,670);
 await image(s,'doc/evidence/step_01/screenshots/02_before_targets.jpg',246,332,738,462,'개선 전 원본 운영 에이전트 화면. fake 생성 모델의 실제 실행 화면');
 text(s,'샘플 모니터링 데이터와 Runbook을\n활용하는 실습용 에이전트',265,827,700,105,33,C.ink,true,'center');
 text(s,'시스템 상태를 점검하고\n장애 대응 방법을 안내하는\nAI Agent',1055,306,785,196,52,C.ink,true);
 const a=[['요청 처리','Vue → FastAPI → LangGraph'],['데이터 조회','MCP로 샘플 상태·매뉴얼 조회'],['기존 기반','SQLite·의존성 주입·LangSmith']];
 a.forEach((v,i)=>{const y=554+i*138;text(s,v[0],1060,y,720,43,31,C.blue,true);box(s,1055,y+48,790,80);text(s,v[1],1080,y+69,740,49,31);});
 foot(s,'원본 화면: ee32f7e · fake 생성 모델 · 실제 회사 시스템의 장애 진단 화면이 아님');
 note(s,'MCP, LangGraph, SQLite 메시지 저장, 의존성 주입, LangSmith 추적은 기존에도 있었다. 최초 서비스는 키워드 규칙으로 Runbook을 선택했으며 임베딩 기반 RAG는 없었다.',['doc/evidence/step_01/README.md','https://github.com/gayeon9-9/skala_ai_mini_project/tree/ee32f7e']);
}
// 04. 기존 문제 1·2: 원본 연속 코드, 여백만 정규화.
{
 const s=await slide('기존 코드의 문제점 1 · 2');box(s,220,300,790,670);box(s,1055,300,790,670);
 badge(s,'1',255,335);badge(s,'2',1090,335);
 text(s,'문의 대상 선택 불안정',255,437,718,70,45,C.ink,true);
 const a=code(s,'ee32f7e','agent/ollama_llm.py',174,176,255,532,718,178,23);
 text(s,'서비스를 말하지 않으면\nauth-service를 기본으로 조사',255,787,718,120,37,C.ink,true);
 text(s,'문의 정보 정리 부족',1090,437,718,70,45,C.ink,true);
 const b=code(s,'ee32f7e','agent/graph.py',108,112,1090,532,718,178,21);
 text(s,'질문 전체를 증상으로 전달\n시각·오류·환경은 따로 정리하지 않음',1090,787,718,120,34,C.ink,true);
 foot(s,'대상 확인과 문의 정보 정리를 사용자·관리자에게 맡기는 구조였다');
 note(s,'왼쪽은 실제 Ollama 계획 프롬프트의 기본 대상 규칙이다. 오른쪽은 원문을 symptom에 전달하는 구조이며 전체 원본 흐름에 별도 접수 정보 추출 단계가 없었다. 코드 표시에서는 공통 앞쪽 공백만 제거했다.',[a,b]);
}
// 05. 기존 문제 3·4. 새로고침과 DB 삭제를 서로 다른 원인으로 설명.
{
 const s=await slide('기존 코드의 문제점 3 · 4');box(s,220,300,790,670);box(s,1055,300,790,670);
 badge(s,'3',255,330);badge(s,'4',1090,330);
 text(s,'문서 검색·답변 검증 한계',255,427,720,66,41,C.ink,true);
 const a=code(s,'ee32f7e','agent/graph.py',210,213,255,517,718,172,24);
 text(s,'매칭 실패에도 DB 장애 매뉴얼 선택',255,743,718,65,32,C.ink,true);
 text(s,'답변은 길이·번호 위주로 검사하고\n3회 실패하면 강제로 통과',255,825,718,100,31);
 text(s,'대화 복원·이력 관리 부족',1090,427,718,66,41,C.ink,true);
 const b=code(s,'ee32f7e','web_chat.html',132,136,1090,517,718,172,23);
 text(s,'새로고침할 때 새 대화로 시작',1090,743,718,65,34,C.ink,true);
 text(s,'저장 기능은 있었지만 복원 흐름이 없고\n실행 스크립트는 session.db를 삭제',1090,825,718,100,30);
 foot(s,'“저장 기능 없음”이 아니라 “대화 복원·문의 단위 관리 부족”으로 구분');
 note(s,'원본 validate_node는 길이 30 이상과 1) 포함을 검사했고 3회 실패 후 통과했다. 원본 SQLite는 메시지를 저장했지만 브라우저는 매번 새 thread를 생성했다. sh/run_agent_ollma.sh를 재실행하면 rm -f session.db로 DB를 지웠다. 모든 서버 재시작에서 삭제되는 것은 아니다.',[a,b,'https://github.com/gayeon9-9/skala_ai_mini_project/blob/ee32f7e/agent/graph.py#L274-L282','https://github.com/gayeon9-9/skala_ai_mini_project/blob/ee32f7e/sh/run_agent_ollma.sh#L59-L61']);
}
// 06. 사용자 PDF의 영향 표를 네이티브 표로 유지.
{
 const s=await slide('기존 문제와 사용자·관리자 영향');
 table(s,[['기존 문제','사용자에게 미치는 영향','관리자에게 미치는 영향','개선 방향'],['조회 대상 불명확','다른 서비스의 점검\n결과를 받을 수 있음','실제 문의 대상을\n다시 확인해야 함','서비스 직접 선택\n입력 검증'],['문의 정보 정리 부족','필요한 정보를 몰라\n여러 번 설명해야 함','증상·시각·환경을\n직접 정리해야 함','문의 자동 정리\n부족한 정보 추가 질문'],['검색·답변 검증 한계','상황에 맞지 않는\n안내를 따라 할 수 있음','답변의 근거와 조치를\n다시 검토해야 함','관련 문서 검색\n근거와 답변 일치 검사'],['대화·이력 관리 부족','이전에 설명한 내용을\n다시 입력해야 함','이전 문의와 추가 내용을\n연결하기 어려움','대화 보존·복원\n관리자 문의 조회']],220,310,1625,635,[300,445,460,420],30);
 foot(s,'업무 부담 감소는 기대 효과이며, 실제 처리 시간 절감률은 측정하지 않았다');
 note(s,'현재 PDF의 사용자·관리자 영향 표를 읽기 쉬운 길이로 다듬었다. 문제에서 개선 목표로 이어지는 연결을 설명한다.',['doc/final_report.md']);
}
// 07. 목표와 방향을 합쳐 중복을 줄임.
{
 const s=await slide('개선 목표와 방향');
 const rows=[['AI가 대상을 추측','사용자가 선택한 서비스로 점검 범위 고정'],['문의 원문을 직접 정리','증상·시각·오류·환경 자동 정리'],['근거가 불명확한 안내','문서와 점검 결과에 근거한 답변'],['문의 이력이 이어지지 않음','대화 보존·복원과 관리자 확인']];
 rows.forEach((r,i)=>{const y=302+i*143;box(s,220,y,600,118);text(s,r[0],248,y+35,546,65,35,C.ink,true,'center');text(s,'→',844,y+25,100,76,51,C.muted,true,'center');box(s,980,y,865,118);text(s,r[1],1008,y+35,809,65,35,C.ink,true,'center');});
 text(s,'시스템 점검 챗봇을 자연어 문의 접수·이력 관리가 가능한 AI 헬프데스크로 확장',240,915,1580,80,37,C.blue,true,'center');
 note(s,'초안의 개선 목표와 개선 방향을 하나로 통합했다. 실제 자동 복구를 수행하는 제품이 아니라 접수와 기초 점검, 문서 기반 안내, 관리자 확인을 연결하는 도우미다.',['README.md']);
}
// 08. 편집 가능한 기술 아키텍처. 저장 순서와 모델 역할을 실제 구현에 맞춤.
{
 const s=await slide('개선 서비스의 기술 아키텍처');
 const nodes=[['서비스 선택·자연어 입력','Vue / FastAPI'],['원문 접수·대화 저장','SQLite · 요청 ID'],['문의 정보 정리','Qwen 0.6b · JSON Schema'],['선택 시스템 점검','MCP · 샘플 모니터링'],['관련 문서 검색','RAG · OpenAI 임베딩'],['답변 생성·근거 검증','Qwen 8b · 검증 규칙']];
 const shapes=[];
 nodes.forEach((n,i)=>{const col=i<3?i:5-i;const x=220+col*565,y=315+Math.floor(i/3)*230;const b=box(s,x,y,495,160);shapes.push(b);text(s,String(i+1).padStart(2,'0'),x+22,y+20,70,50,33,C.blue,true);text(s,n[0],x+100,y+21,375,57,33,C.ink,true);text(s,n[1],x+24,y+93,450,49,28,C.muted);});
 for(const [a,b,fromSide,toSide] of [[0,1,'right','left'],[1,2,'right','left'],[2,3,'bottom','top'],[3,4,'left','right'],[4,5,'left','right']])s.shapes.connect(shapes[a],shapes[b],{kind:'straight',fromSide,toSide,line:{fill:C.blue,width:3},tail:{type:'triangle',width:'med',length:'med'}});
 box(s,220,762,1625,117);text(s,'결과 저장 → 사용자 답변 · 대화 복원 · 관리자 목록/상세 조회',250,797,1565,67,37,C.ink,true,'center');
 text(s,'LangGraph: 처리 순서·분기 제어     |     LangSmith: 실행 과정·오류·시간 추적',245,917,1570,60,31,C.blue,true,'center');
 foot(s,'단일 LangGraph 흐름 · OpenAI는 문서/질문의 임베딩 담당 · 실제 로그 분석·자동 복구는 미구현');
 note(s,'원문과 처리 상태를 먼저 저장한 뒤 접수 분석을 시작한다. 선택 시스템은 MCP로 점검하고 FAQ와 Runbook은 별도로 검색한다. 생성·검증 후 결과를 저장하고 응답한다. 모듈이나 노드의 분리는 Multi-Agent 구현과 다르며 이번에는 단일 흐름을 선택했다.',['agent/chat_service.py','agent/graph.py','agent/embeddings.py']);
}
// 09. 1단계.
{
 const s=await slide('1단계 | 시스템 선택과 입력 검증');box(s,220,283,755,690);box(s,1010,283,835,690);
 stageCopy(s,'질문에서 대상 추측\n명시하지 않으면 기본 시스템 조회','서비스를 직접 선택하고\n등록 여부·빈 입력을 검사','적용 기술: Vue · FastAPI / Pydantic · LangGraph   |   기대 효과: 문의 대상과 조회 범위 일치');
 code(s,'ba5a883','agent/graph.py',141,141,246,776,700,110,25);
 text(s,'서비스 선택 영역',1040,326,775,45,30,C.ink,true);
 await image(s,E+'01_input.png',1034,394,786,203,'최신 코드의 서비스 선택 영역 부분 확대',{left:0,top:0,right:.52,bottom:.83});
 text(s,'자연어 입력 영역',1040,658,775,45,30,C.ink,true);
 await image(s,E+'01_input.png',1034,730,786,77,'같은 실제 화면의 문의 입력 문장 부분 확대',{left:0,top:.93,right:.48,bottom:0});
 text(s,'선택한 시스템만 점검 작업으로 생성',1035,909,785,49,29,C.blue,true,'center');
 note(s,'모델이 대상을 다시 고르지 않도록 선택값으로 작업을 만든다. 서버는 입력을 검증하고 MCP 호출 직전에도 대상 일치를 확인한다. 새 화면은 최신 ba5a883의 격리 실습 실행이다.',['agent/graph.py','server.py','doc/evidence/template_final_20261021/README.md']);
}
// 10. 2단계.
{
 const s=await slide('2단계 | 대화·접수 보존과 복원');box(s,220,283,755,690);box(s,1010,283,835,690);
 stageCopy(s,'새로고침하면 새 대화 ID 생성\n실행 스크립트는 대화 DB 삭제','대화 ID와 선택값을 복원하고\n접수·답변을 DB에 보존','적용 기술: SQLite · 대화 ID · 요청 ID   |   기대 효과: 설명을 이어가고 중복 접수 방지');
 text(s,'같은 요청 ID → 저장된 결과 재사용\n처리 중인 요청 → 중복 실행 차단',248,780,697,122,32,C.blue,true);
 await image(s,E+'04_restored.png',1034,330,786,563,'새로고침 후 같은 문의와 답변이 복원된 최신 UI');
 text(s,'복원 확인: 같은 대화 · 같은 접수 · 같은 답변',1035,909,785,49,28,C.blue,true,'center');
 note(s,'localStorage에는 대화 식별자와 선택 시스템을 보존하고 실제 메시지는 SQLite에서 조회한다. 접수 ID, 요청 ID, 처리 상태를 분리하며 재전송은 중복 생성하지 않는다. completed는 답변 처리 완료이며 장애 해결을 의미하지 않는다.',['web_chat.html','agent/chat_service.py','agent/inquiry_store.py','doc/evidence/template_final_20261021/README.md']);
}
// 11. 3단계. 최신 사용자 입력과 실제 접수 필드의 대응을 보여준다.
{
 const s=await slide('3단계 | 자연어 문의를 접수 정보로');
 box(s,220,292,1625,143);text(s,'“오전 9시부터 로그인 후 화면이 넘어가지 않아요.\n오류 메시지는 없어요. 윈도우 크롬을 사용해요.”',260,320,1545,104,38,C.ink,true,'center');
 text(s,'원문 그대로 전달 → 4개 항목으로 정리 · 부족한 정보 질문 · 후속 답변 병합',240,463,1585,72,34,C.blue,true,'center');
 await image(s,E+'07_intake_panel.png',220,548,1625,405,'최신 실행에서 구조화한 증상, 발생 시각, 오류 메시지, 사용자 환경');
 foot(s,'적용 기술: LLM 구조화 출력 · JSON Schema · 원문 근거 검사   |   “없음”과 “미언급” 구분');
 note(s,'원문에서 확인한 표현을 evidence로 보존한다. 오류 메시지 없음은 정보를 안 준 것과 다르므로 다시 묻지 않는다. 모델 출력이 형식이나 원문 근거를 만족하지 않으면 제한된 규칙 추출로 보완한다. 실제 한 건의 성공을 일반 정확도 향상률로 표현하지 않는다.',['agent/intake.py','agent/intake_llm.py','doc/evidence/template_final_20261021/README.md']);
}
// 12. 4단계.
{
 const s=await slide('4단계 | RAG 검색과 문서 출처 제공');box(s,220,283,755,690);box(s,1010,283,835,690);
 stageCopy(s,'고정 키워드로 매뉴얼 선택\n일치하지 않아도 db-down 반환','질문과 문서의 의미를 비교해\n관련 문서와 출처를 제공','적용 기술: 임베딩 · 로컬 벡터 색인 · RAG   |   사용자 FAQ와 운영자 Runbook을 분리');
 text(s,'검색 결과 없음: no_match\n검색 호출 실패: error\n두 상태를 구분해 안내',248,778,697,137,31,C.blue,true);
 await image(s,E+'09_documents_panels.png',1034,330,786,265,'관리자 화면의 사용자 FAQ 검색 결과 부분 확대',{left:0,top:0,right:.507,bottom:0});
 await image(s,E+'09_documents_panels.png',1034,630,786,265,'관리자 화면의 운영자 Runbook 검색 결과 부분 확대',{left:.507,top:0,right:0,bottom:0});
 text(s,'시스템·문서 용도를 제한한 뒤 유사도 검색',1035,909,785,49,28,C.blue,true,'center');
 note(s,'문서 4개 FAQ와 6개 Runbook을 임베딩해 로컬 색인에 보존한다. 시스템과 독자 범위를 필터링한 뒤 기준 점수 이상의 문서를 상위 순위로 선택한다. 유사도 점수는 장애 원인 확률이 아니다. 전체 검색 결과와 실제 답변에 적용할 문서는 구분한다.',['agent/retrieval.py','agent/graph.py','doc/step_04_rag.md']);
}
// 13. 5단계 + 최신 실제 발견 오류.
{
 const s=await slide('5단계 | 근거 없는 오류 안내 차단');
 box(s,220,292,775,645);box(s,1035,292,810,645);
 text(s,'실제 시연에서 발견한 문제',255,327,704,59,37,C.ink,true);
 text(s,'사용자: “오류 메시지는 없어요”\nAI 답변: 문서의 504 오류까지 안내',255,422,704,132,38,C.ink,true);
 text(s,'1차 근거 검증을 적용한 뒤에도\n문서 예시를 문의 상황과 연결하는\n오류를 놓침',255,600,704,176,33);
 text(s,'보완: 생성 전 필터 + 생성 후 검사',1070,327,740,59,37,C.blue,true);
 text(s,'확인되지 않은 HTTP 오류 코드(4xx/5xx)\n포함 문서를 답변 근거에서 제외\n최종 답변에도 같은 코드가 없는지 검사',1070,422,740,177,32,C.ink,true);
 await image(s,E+'08_validation_panel.png',1070,635,740,230,'최신 실행의 답변 검증 결과. 실제 UI 원본 패널 부분 확대',{left:0,top:0,right:0,bottom:.42});
 foot(s,'총 3회 작성·검증 후에도 실패하면 제한된 안내 제공 · 실제 성공 사례 1건, 전체 정확도 보장은 아님');
 note(s,'과거 actual final_openai 캡처에는 504 안내가 있었다. ba5a883에서 verified intake를 기준으로 문서를 필터링하고 답변 검증에도 동일한 제한을 적용했다. 발견한 실패를 숨기지 않고 원인, 변경, 실제 검증 결과를 연결한다. 문서 전체 제외는 유용한 일반 설명까지 제외할 수 있다는 트레이드오프가 있다.',['doc/evidence/post_demo_fixes/README.md','agent/answer_grounding.py','agent/answer_validation.py','doc/evidence/template_final_20261021/README.md']);
}
// 14. 6단계 최종 관리자 화면.
{
 const s=await slide('6단계 | 관리자 문의 목록·상세 조회');
 await image(s,E+'06_admin_top.png',220,302,1090,640,'최신 최종 관리자 화면. 접수 목록과 선택한 문의의 상세');
 text(s,'기존',1370,321,445,44,29,C.muted,true);text(s,'문의별 관리 화면 없음',1370,377,445,98,34);
 text(s,'개선',1370,510,445,44,29,C.blue,true);
 text(s,'상태별 조회·서비스명 확인\n원문과 정리된 정보 확인\n샘플 점검·문서 근거 확인\n실행 추적과 검증 결과 확인',1370,570,445,260,32,C.ink,true);
 text(s,'조회 시 AI를 다시 호출하지 않음',1370,867,445,86,28,C.blue,true);
 foot(s,'적용 기술: FastAPI · SQLite · Vue · LangSmith   |   completed는 답변 처리 완료, 장애 해결 상태가 아님');
 note(s,'관리자는 같은 접수에서 원문, 정보 추출 결과, 샘플 점검, 문서 검색, 검증 및 실행 메타데이터를 확인한다. 관리 화면은 읽기 전용이며 실제 담당자 배정이나 장애 복구를 수행하지 않는다. 관리자 인증과 권한 구분은 후속 범위다.',['server.py','web_admin.html','agent/inquiry_summary.py','doc/evidence/template_final_20261021/README.md']);
}
// 15. 7단계 공급자 선택 + 모듈화/DI. 실제 생성 모델과 임베딩 역할 구분.
{
 const s=await slide('7단계 | 임베딩 선택과 모듈 확장');
 table(s,[['역할','분리한 모듈','교체·검증 방식'],['문의 접수·보존','chat_service · inquiry_store','요청·접수 상태와 DB 책임 분리'],['문의 정보 정리','intake · intake_llm','모델 출력과 원문 근거 검사 분리'],['문서 검색','embeddings · retrieval','Ollama / OpenAI 공급자 선택'],['답변 검증','answer_*: generation · validation · grounding','생성·근거 검사·문서 적용 분리']],220,302,1625,425,[310,665,650],29);
 const a=code(s,'ba5a883','server.py',123,127,220,766,925,169,24);
 text(s,'생성: Ollama Qwen 유지\n임베딩: Ollama ↔ OpenAI\n테스트: FixedEmbedder 주입',1210,780,620,151,32,C.blue,true);
 foot(s,'기존 SupervisorDeps를 활용해 검색기까지 주입 · OpenAI 임베딩은 웹 검색이나 답변 생성 기능이 아님');
 note(s,'모듈화와 DI는 기존에도 일부 있었으며 새로운 접수·검색·검증 책임을 독립 모듈로 확장했다. create_embedder로 공급자를 조립하고 retriever를 SupervisorDeps에 주입한다. 테스트에는 FixedEmbedder를 넣어 외부 API 없이 검색 필터를 검증한다. 기존 Ollama 버전은 v1.0-ollama 태그로 보존했다.',[a,'agent/embeddings.py','tests/test_retrieval.py','doc/step_07_openai_embeddings.md']);
}
// 16. 같은 입력의 서비스 전후 화면; fake vs Qwen 혼합 비교 금지.
{
 const s=await slide('같은 문의의 개선 전·후 화면');
 text(s,'“로그인 후 화면이 넘어가지 않아요. 오류 메시지는 없어요.”',230,278,1605,63,37,C.ink,true,'center');
 text(s,'기존 Base Agent',230,372,780,58,39,C.muted,true);text(s,'6단계 완료 헬프데스크',1062,372,780,58,39,C.blue,true);
 await image(s,OLD+'01_before_natural.jpg',220,488,790,212,'같은 문의에 일반 인사로 답한 원본 fake 생성 UI의 상단 부분 확대',{left:0,top:0,right:0,bottom:623/848});
 await image(s,OLD+'02_after_natural.jpg',1055,488,790,222,'같은 문의에서 접수 정보와 추가 질문을 표시한 6단계 완료 fake 생성 UI의 접수 영역 부분 확대',{left:20/840,top:478/786,right:30/840,bottom:86/786});
 text(s,'원본 화면의 질문·최종 답변 부분 확대',230,760,780,56,29,C.muted,false,'center');
 text(s,'개선 화면의 문의 정보·추가 질문 부분 확대',1060,760,780,56,29,C.muted,false,'center');
 text(s,'일반 대화로 분류 → 점검 없이 인사',230,865,780,75,34,C.ink,true,'center');
 text(s,'선택 시스템 점검 → 접수 정보·추가 질문',1060,865,780,75,34,C.ink,true,'center');
 foot(s,'동일 질문 · 양쪽 fake 생성 모델 · 개선본은 서비스 선택 추가 · 실제 LLM 진단 정확도 비교가 아님');
 note(s,'원본 ee32f7e와 6단계 완료본의 동일 입력 UI 비교다. 원본과 개선본 모두 fake 생성 모델을 사용하며 개선본의 의미 검색은 실제 Ollama 임베딩이다. 선택값이라는 입력 계약 변경은 명시했다. CPU 샘플 수치와 처리시간은 이 장의 비교 대상이 아니다.',['doc/evidence/final/README.md','doc/evidence/final/screenshots.json']);
}
// 17. 원본 규칙 어댑터와 실제 RAG의 검색 방식 비교.
{
 const s=await slide('RAG 도입 전·후 검색 결과');
 box(s,220,295,775,648);box(s,1035,295,810,648);
 text(s,'같은 Runbook 정답 질문 4개',250,325,715,56,35,C.ink,true,'center');
 const chart=s.charts.add('bar',{position:{left:255,top:421,width:700,height:398},categories:['기존 규칙','Ollama RAG'],series:[{name:'정답 문서 포함',values:[1,4],fill:C.blue,points:[{idx:0,fill:'#A8B2C6'},{idx:1,fill:C.blue}]}],barOptions:{direction:'column',grouping:'clustered',gapWidth:90},hasLegend:false,yAxis:{min:0,max:4,majorUnit:1,textStyle:{fontSize:26},majorGridlines:{fill:'#DFE3EB',width:1}},xAxis:{textStyle:{fontSize:29}},dataLabels:{showValue:true,position:'outEnd',textStyle:{fontSize:34,bold:true}},chartFill:C.white,plotAreaFill:C.white});
 applyPresentationChartFont(chart,{fontFamily:FONT});
 text(s,'1/4 → 4/4',250,841,715,72,52,C.blue,true,'center');
 await image(s,REPORT+'13_baseline_rag_report.png',1060,343,760,472,'저장된 원시 JSON으로 만든 검색 비교 실측 보고서의 브라우저 캡처');
 text(s,'기존: 규칙 어댑터\n개선: 검색 상위 3개에 정답 포함 여부',1070,838,750,88,29);
 foot(s,'소수 합성 질문의 검색 방식 비교 · 전체 서비스의 장애 진단 정확도 100%를 의미하지 않음');
 note(s,'기존 규칙에 질문을 합성 monitoring alerts로 전달한 비교용 어댑터와 실제 Ollama RAG를 같은 4개 Runbook 정답 질문으로 비교했다. 원본 전체 요청 흐름의 정확도 측정이 아니다. RAG는 상위 3개에 정답이 포함되는지 평가한다. 오른쪽은 저장 원시 결과로 만든 보고서 화면이며 제품 UI와 구분한다.',['doc/evidence/final/README.md','doc/evidence/final/rag_test.json','doc/evidence/final_openai/README.md']);
}
// 18. 공급자 비교: OpenAI 우위 대신 선택 근거를 명시.
{
 const s=await slide('Ollama · OpenAI 임베딩 실측 비교');
 table(s,[['지표','Ollama','OpenAI'],['Hit@3','100% (30/30)','90% (27/30)'],['평균 검색 시간','36ms','176ms'],['무관 질문 no_match','6/6회','6/6회'],['검색 오류','0건','0건']],220,305,835,500,[350,240,245],30);
 await image(s,REPORT+'14_embedding_report.png',1090,308,755,502,'동일 질문 반복 평가로 확보한 임베딩 공급자 비교 보고서 캡처');
 text(s,'공급자를 교체할 수 있게 만들고, 같은 조건에서 선택할 근거를 확보',245,864,1575,76,41,C.blue,true,'center');
 foot(s,'고유 질문 12개 × 3회 / 공급자 · Hit@3는 정답 질의 10개 × 3회 · 검색 시간만 측정');
 note(s,'Ollama qwen3-embedding:0.6b 임베딩 1024차원/기준점수0.50과 OpenAI text-embedding-3-small 1536차원/0.30을 비교했다. 14개 개발질의로 점수를 조정한 뒤 12개 고유 평가질의(정답10,무관2)를 3회 반복한 알려진 회귀 평가다. 이번 표본에서 OpenAI는 더 느리고 Hit@3도 낮았다. 비용은 실제 사용량과 외부 API 의존성을 설명하며 Ollama 가상 usd를 실제 청구액으로 비교하지 않는다.',['doc/evidence/embedding_comparison/RESULTS.md','doc/evidence/embedding_comparison/test.json']);
}
// 19. 최신 자동검사 + 실제 LangSmith 실행 증거.
{
 const s=await slide('자동 테스트와 실제 실행 추적');
 await image(s,E+'10_tests_trace_report.png',220,295,1050,661,'최신 코드의 pytest 및 실제 LangSmith 조회 결과를 저장 JSON으로 만든 실행 검증 보고서');
 text(s,'단위·통합 테스트',1325,324,500,58,38,C.ink,true);
 text(s,'217개 테스트 + 173개 하위 검사 통과\n입력·복원·추출·검색·답변 검사',1325,410,500,128,30);
 text(s,'LangSmith 실행 추적',1325,585,500,58,38,C.blue,true);
 text(s,'어느 단계가 실행됐는지\n어디서 시간이 걸렸는지\n어떤 근거로 답했는지 확인',1325,675,500,195,32,C.ink,true);
 foot(s,'기능 검사 통과와 실제 LLM 답변 정확도는 별개 · 실측 보고서 화면은 제품 UI와 구분');
 note(s,'최신 코드를 격리 실행하여 자동 검사와 실제 합성 문의를 재확인했다. 테스트 수와 하위 검사 수는 별도로 표시한다. LangSmith는 기존 추적에 문의 ID, 코드/모델/검색 버전 메타데이터를 보완한 것이다. 추적 도구 자체가 답변 오류를 자동으로 고치지는 않는다.',['doc/evidence/template_final_20261021/tests.json','doc/evidence/template_final_20261021/langsmith_readback.json','agent/observability.py']);
}
// 20. 평가 비중 20점에 대응: 얻는점/새부담/대안을 연결.
{
 const s=await slide('한계와 트레이드오프');
 table(s,[['개선 선택','얻은 점','추가된 부담·제약','다음 개선'],['RAG 의미 검색','표현이 달라도\n관련 문서 탐색','유사하지만 무관한 문서 검색\n임베딩·검색 시간 추가','평가 질문 확대\n문서·검색 기준 보완'],['오류 코드 문서 필터','확인되지 않은\n504 안내 억제','문서 전체를 제외하면\n유용한 일반 안내도 누락','문서를 조건별로 분리\n관련 부분만 사용'],['OpenAI 임베딩','외부 공급자 선택\n교체 가능한 구조','과금·외부 전송·네트워크 의존\n이번 표본에서는 지연 증가','운영 조건별 모델 선택\n캐시·실패 처리 검증'],['답변 검증·재작성','근거 없는\n안내 일부 차단','재작성 시간 증가\n정상 안내를 막을 가능성','조건별 규칙 세분화\n실패 사례 회귀 검사']],220,300,1625,570,[290,350,585,400],28);
 text(s,'현재 운영 한계: 샘플 메트릭 사용 · 실제 로그/배포 이력 미연동 · 관리자 인증/권한 미구현',240,916,1585,71,29,C.ink,true,'center');
 note(s,'현재 없는 기능과 개선이 새로 만든 부담을 구분했다. 실제 로그가 없어서 원인을 확정하거나 복구 성공을 판정할 수 없다. 문서 필터를 강하게 하면 유용한 정보도 빠질 수 있고 검증을 재시도하면 시간이 늘어난다. 보안·권한 및 실제 운영 규모에 대한 검증은 별도 과제다.',['doc/evidence/post_demo_fixes/README.md','doc/evidence/embedding_comparison/RESULTS.md','README.md']);
}
// 21. 개인적 경험은 발표자 검토가 가능하도록 노트에 구분.
{
 const s=await slide('향후 개선과 느낀 점');
 box(s,220,300,780,620);box(s,1040,300,805,620);
 text(s,'향후 기업 활용',255,338,708,62,42,C.blue,true);
 text(s,'사내 IT 헬프데스크',255,448,708,57,38,C.ink,true);text(s,'문의 담당자 배정·상태 변경·권한 관리\n실제 FAQ와 사내 규정으로 문서 확대',255,520,708,111,32);
 text(s,'배포 후 상태점검 도우미',255,694,708,57,38,C.ink,true);text(s,'배포 이력·로그·실시간 메트릭 연결\n변경 전후 상태를 근거로 비교',255,766,708,111,32);
 text(s,'개발 과정에서 확인한 점',1075,338,735,62,42,C.ink,true);
 text(s,'기능 추가만큼 실패 사례 검증이 중요했다',1075,453,735,90,35,C.ink,true);
 text(s,'실제 모델을 연결하자 문의 추출과\n504 안내 문제가 드러났고, 규칙과\n테스트로 보완할 수 있었다.',1075,565,735,155,33);
 text(s,'새 기술의 효과는 같은 입력과\n측정 결과로 판단해야 한다.',1075,763,735,109,35,C.blue,true);
 foot(s,'코드·단계별 커밋·실측 자료: github.com/gayeon9-9/skala_ai_mini_project   |   G081 성가연');
 note(s,'느낀 점 문구는 실제 개발에서 관찰한 사례를 바탕으로 작성한 발표 초안이다. 발표자가 자신의 경험과 표현에 맞게 확인할 수 있다. 실제 운영 확장은 아직 구현한 기능과 구분한다. 이번 개선의 핵심은 접수, 기록, 검색, 검증, 관리자 확인을 하나의 흐름으로 연결한 것이다.',['README.md','doc/evidence/post_demo_fixes/README.md']);
}

// 원본 템플릿을 보존하고 최종 슬라이드만 복제본에 남긴다.
p.slides.keep(ordered);p.slides.reorder(ordered);
console.log('Built',p.slides.items.length,'slides');
await fs.writeFile(path.join(BUILD,'final.proto.json'),JSON.stringify(p.toProto()));
const inspect=await p.inspect({select:'$.slides[*]',include:['id','index','title','elements.id','elements.kind','elements.text','elements.position','elements.style','notes.text']});
await fs.writeFile(path.join(BUILD,'final-inspect.ndjson'),inspect.ndjson);
for(let i=0;i<p.slides.items.length;i++){
 const s=p.slides.items[i];const png=await s.export({format:'png',scale:.75});
 await fs.writeFile(path.join(BUILD,`slides/slide-${String(i+1).padStart(2,'0')}.png`),new Uint8Array(await png.arrayBuffer()));
 const layout=await s.export({format:'layout'});await fs.writeFile(path.join(BUILD,`slides/slide-${i+1}.json`),await layout.text());
 console.log('Rendered',i+1);
}
const candidate=path.join(BUILD,'candidate.pptx');await(await PresentationFile.exportPptx(p)).save(candidate);
console.log('Candidate',candidate);
if(process.env.FINALIZE==='1'){
 const output=path.join(WORK,'output/G081_성가연_AI_헬프데스크_템플릿반영_최종.pptx');
 const result=await finalizePresentation({workspaceDir:WORK,candidatePath:candidate,finalPath:output,pythonExecutable:PY,integrityValidatorPath:path.join(SKILL,'container_tools/inspect_presentation_package_integrity.py'),layoutValidatorPath:path.join(SKILL,'container_tools/inspect_presentation_layout_geometry.py'),layoutArgs:['--expected-slide-size-emu','18288000,10287000','--validate-bullet-geometry','--validate-heading-fit',...[6,15,18,20].flatMap(n=>['--require-native-table-slide',String(n)])],explicitTotalSlideCount:21,requiredNativeTableOwnerSlides:[6,15,18,20],requiredNativeChartOwnerSlides:[17],materializeLiteralChartWorkbooks:true,fontPolicy:{basis:'user_request',families:['Pretendard','Pretendard Regular','Pretendard Bold','Pretendard Medium','Pretendard SemiBold']},verifyArtifactToolImport:true,receiptPath:path.join(BUILD,'validation.json')});
 console.log(JSON.stringify(result));
}
