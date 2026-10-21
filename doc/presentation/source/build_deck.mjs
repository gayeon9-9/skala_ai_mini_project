// 실제 코드·측정 JSON·원본 화면을 조합하는 발표 자료 빌더.
// 생성 모델의 성공률과 기능 검사 통과 수를 서로 다른 지표로 유지한다.
import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
const ROOT = path.resolve(import.meta.dirname, '../../..');
const WORK = path.join(ROOT, 'doc/presentation');
const BUILD = path.join(WORK, '.build');
const RUNTIME = process.env.RUNTIME_NODE_MODULES || '/Users/gayeon/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules';
process.env.RUNTIME_NODE_MODULES = RUNTIME;
const SKILL = '/Users/gayeon/.codex/plugins/cache/openai-primary-runtime/presentations/26.1017.22136/skills/presentations';
const {Presentation, PresentationFile} = await import(pathToFileURL(path.join(RUNTIME, '@oai/artifact-tool/dist/artifact_tool.mjs')));
const {applyPresentationChartFont, finalizePresentation} = await import(pathToFileURL(path.join(SKILL,'container_tools/artifact_tool_utils.mjs')));
const FONT = 'Apple SD Gothic Neo', CODE = 'Menlo';
const C = {ink:'#182942', blue:'#3265D5', muted:'#5B687A', pale:'#EAF0FC', bg:'#F7F9FC', line:'#D5DDEA', green:'#1C715A', amber:'#9B5B18'};
const json = async p => JSON.parse(await fs.readFile(path.join(ROOT,p),'utf8'));
const ui=await json('doc/evidence/final_openai/ui_verification.json');
const detail=await json('doc/evidence/final_openai/admin_detail.json');
const cmp=await json('doc/evidence/embedding_comparison/test.json');
const test=await json('doc/evidence/embedding_comparison/api_verification.json');
const oll=cmp.summaries.ollama.all, op=cmp.summaries.openai.all;
let trace={};try{trace=await json('doc/evidence/final_openai/langsmith_readback.json')}catch{}
const p=Presentation.create({slideSize:{width:1440,height:810}});
await fs.mkdir(BUILD,{recursive:true});
await fs.mkdir(path.join(BUILD,'slides'),{recursive:true});
function text(s, str, x,y,w,h,size=30,color=C.ink,bold=false,font=FONT) {
 const t=s.shapes.add({geometry:'textbox',position:{left:x,top:y,width:w,height:h},fill:'none',line:{fill:'none',width:0}});
 t.text=str;t.text.style={typeface:font,fontSize:size,color,bold,insets:0,autoFit:'none',verticalAlignment:'top'};return t;
}
function slide(title,subtitle=''){
 const s=p.slides.add();s.background.fill=C.bg;
 text(s,'AI HELPDESK  /  AI 서비스 Mini Project',70,24,1120,25,16,C.muted);
 text(s,title,70,75,1300,70,44,C.ink,true);
 if(subtitle)text(s,subtitle,72,146,1290,55,24,C.muted);
 const n=p.slides.items.length;text(s,'G081 · 성가연',70,766,1040,24,16,C.muted);text(s,String(n).padStart(2,'0'),1320,765,50,28,18,C.blue,true);
 return s;
}
function note(s,str,sources=[]){s.speakerNotes.text=str+'\n\n근거\n'+sources.map(v=>v.startsWith('http')?v:'https://github.com/gayeon9-9/skala_ai_mini_project/blob/main/'+v).join('\n');}
function footer(s,str){text(s,str,72,710,1290,43,20,C.muted);}
function rows(s,items,x=72,y=220,w=1280,step=110){items.forEach((a,i)=>{text(s,a[0],x,y+i*step,w,42,31,C.blue,true);text(s,a[1],x,y+i*step+46,w,65,26,C.ink);});}
function table(s,values,x,y,w,h,widths,size=26){
 const tb=s.tables.add({rows:values.length,columns:values[0].length,left:x,top:y,width:w,height:h,columnWidths:widths,values});
 tb.borders.assign({style:'solid',fill:C.line,width:1});
 tb.cells.block({row:0,column:0,rowCount:values.length,columnCount:values[0].length}).assign({fill:'#FFFFFF',textStyle:{typeface:FONT,fontSize:size,color:C.ink},margins:{left:18,right:16,top:13,bottom:10}});
 tb.cells.block({row:0,column:0,rowCount:1,columnCount:values[0].length}).assign({fill:C.pale,textStyle:{typeface:FONT,fontSize:size,bold:true,color:C.blue}});
 return tb;
}
async function img(s,rel,x,y,w,h,alt,crop){const bytes=await fs.readFile(path.isAbsolute(rel)?rel:path.join(ROOT,rel));s.images.add({blob:bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength),contentType:rel.endsWith('.jpg')?'image/jpeg':'image/png',alt,fit:'contain',crop,position:{left:x,top:y,width:w,height:h}});}
function code(s,str,x,y,w,h,size=23){const t=text(s,str,x,y,w,h,size,C.ink,false,CODE);t.fill='#FFFFFF';t.line={fill:C.line,width:1};t.text.insets=20;return t;}
function pair(s,before,after,options={}){text(s,'기존 코드',72,215,610,40,29,C.muted,true);text(s,'개선 코드',752,215,610,40,29,C.blue,true);code(s,before,72,270,610,options.h||260,options.size||22);code(s,after,752,270,610,options.h||260,options.size||22);}
const E='doc/evidence/final_openai/screenshots/';
const OLD='doc/evidence/final/screenshots/';

// 01 표지: 초안의 청색 계열과 16:9 화면비를 유지하고 계획을 결과 보고로 갱신한다.
{
 const s=slide('');
 text(s,'Base AI Agent 기능 개선 · 과제 1',75,146,1240,50,28,C.blue,true);
 text(s,'AI 헬프데스크\n고도화 결과 보고',72,242,1250,205,72,C.ink,true);
 text(s,'자연어 문의 접수 · 문서 근거 검색 · 관리자 조회',76,493,1240,58,31,C.muted);
 text(s,'G081  성가연',76,620,950,50,36,C.blue,true);
 text(s,'2026. 10. 21',1080,628,280,40,26,C.muted);
 note(s,'기존 운영 에이전트를 사내 IT 문의 접수 보조 서비스로 확장했다. 사용자는 자연어로 문의하고, 관리자는 같은 접수에서 원문·정리된 정보·점검 근거를 확인한다. 이번 보고는 실제 구현과 측정 결과를 기준으로 한다.',['doc/final_report.md','doc/step_07_openai_embeddings.md']);
}
// 02 원본 구조
{
 const s=slide('기존 서비스와 데이터','Base AI Agent는 샘플 시스템을 점검하고 Runbook을 참고하는 실습용 에이전트였다');
 rows(s,[['요청 처리','Vue 채팅 → FastAPI → LangGraph의 계획·점검·답변 흐름'],['데이터 조회','MCP로 monitoring.json과 미리 작성한 Runbook 조회'],['기존 기반','SQLite 메시지 저장, 의존성 주입, LangSmith 추적 코드 포함']],72,235,1250,137);
 footer(s,'원본은 임베딩 기반 RAG가 아닌 키워드 규칙으로 문서를 선택했다');
 note(s,'기존에 있던 저장·추적·의존성 주입을 모두 신규 도입으로 주장하지 않는다. 이번 작업에서는 이 기반을 유지하고 접수 단위 저장, 안전한 재시도, 의미 검색, 결과 검증과 관리 화면을 보완했다. 실제 운영 서버의 로그나 실시간 모니터링이 아닌 샘플 데이터다.',['doc/final_report.md','data/monitoring.json','https://github.com/gayeon9-9/skala_ai_mini_project/tree/ee32f7e']);
}
// 03 영향
{
 const s=slide('기존 문제와 사용자·관리자 영향');
 table(s,[['문제','사용자에게 미치는 영향','관리자에게 미치는 영향'],['대상 추측·기본 대상 사용','문의한 시스템과 조회 범위가 달라짐','조회 대상을 다시 확인'],['대화 식별·보존 부족','새로고침 후 증상을 다시 설명','후속 답변을 한 문의로 연결하기 어려움'],['원문 중심 기록','어떤 정보를 더 알려야 할지 모름','증상·시각·환경을 수동 정리'],['키워드 검색·형식 위주 검증','문서 누락·부정확한 조언 노출 가능','답변과 근거를 다시 검토']],72,200,1296,420,[310,500,486],25);
 footer(s,'업무 시간 절감은 기대 효과다. 실제 절감 시간이나 장애 해결률은 측정하지 않았다');
 note(s,'원본 실행 스크립트는 session.db를 삭제했고 브라우저는 새로고침 때 새 thread를 만들었다. 원본 검증은 길이와 번호를 확인했고 최대 시도 이후에는 실패 답변을 합격 처리했다. 기능이 존재하는 것과 문의의 전체 생명주기를 안정적으로 지원하는 것은 달랐다.',['doc/final_report.md','doc/step_02_persistence.md','doc/step_05_validation.md']);
}
// 04 단계
{
 const s=slide('단계별 개선·추가 사항','1~6단계 구현 후, 7단계에서 임베딩 공급자 선택과 실측 비교를 추가했다');
 table(s,[['단계','반영 내용','사용자·관리자 효과'],['1','시스템 선택·입력 검증','선택한 시스템으로 점검 범위 고정'],['2','SQLite 저장·복원·요청 ID','대화 유지, 중복 접수·재실행 방지'],['3','자연어 접수·후속 정보 병합','증상·시각·환경을 구조화'],['4','FAQ·Runbook RAG·출처','사용자 안내와 운영자 자료 분리'],['5','근거 검증·재작성 한도','검증 실패 시 제한된 안내 제공'],['6','관리자 조회·평가·추적','동일 접수의 원문과 근거 확인'],['7','Ollama / OpenAI 임베딩 선택','기존 버전 보존, 공급자별 비교 가능']],72,215,1296,462,[105,500,691],24);
 note(s,'각 단계의 코드·검사·변경 기록을 별도 커밋으로 보존했다. 7단계는 RAG를 처음 도입한 단계가 아니라 기존 검색기에 OpenAI 공급자를 추가한 확장이다. 답변 생성은 Ollama Qwen을 유지한다.',['doc/implementation_plan.md','doc/evidence/final/README.md','doc/step_07_openai_embeddings.md']);
}
// 05 명시적으로 요청된 기술 아키텍처: 편집 가능한 다이어그램
{
 const s=slide('개선된 처리 흐름과 모델 역할','사용자는 자연어로 문의하고, 원문은 분석을 시작하기 전에 접수로 저장한다');
 const flow=[['시스템 선택','Vue / FastAPI'],['원문 접수·저장','SQLite · 요청 ID'],['문의 정보 정리','Qwen 0.6b'],['MCP 샘플 점검','선택 시스템만'],['FAQ·Runbook 검색','OpenAI 임베딩'],['답변 작성·검증','Qwen 8b + 규칙']];
 for(let i=0;i<flow.length;i++){
  const x=72+(i%3)*452,y=225+Math.floor(i/3)*180;
  const sh=s.shapes.add({geometry:'rect',position:{left:x,top:y,width:390,height:130},fill:'#FFFFFF',line:{fill:C.line,width:1}});
  text(s,String(i+1).padStart(2,'0'),x+18,y+18,50,40,25,C.blue,true);text(s,flow[i][0],x+75,y+20,295,45,27,C.ink,true);text(s,flow[i][1],x+75,y+72,295,35,23,C.muted);
  if(i%3<2)text(s,'→',x+400,y+40,45,50,35,C.blue,true);
 }
 text(s,'3 → 4',1070,365,240,35,22,C.blue,true);
 text(s,'결과 저장 → 사용자 답변 / 관리자 목록·상세 조회',72,620,1280,50,30,C.blue,true);
 footer(s,'LangGraph: 처리 순서·분기  |  LangSmith: 실행 추적  |  Multi-Agent는 미적용');
 note(s,'단일 LangGraph가 처리 순서와 분기를 제어한다. 원문과 요청 상태는 분석 전 저장하고, 분석 결과와 답변은 처리 후 같은 접수에 연결한다. OpenAI는 문서와 질문을 숫자 벡터로 변환하는 역할이며 웹 검색이나 최종 답변 작성에 쓰지 않는다. 현재 색인은 로컬 파일에 저장한다.',['agent/graph.py','agent/chat_service.py','agent/embeddings.py','doc/step_07_openai_embeddings.md']);
}
// 06 코드 시스템 선택
{
 const s=slide('코드 개선 ① 선택한 시스템만 점검','모델의 계획에 대상 선택을 맡기던 경로에 명시적인 범위 제한을 추가했다');
 text(s,'기존 fake 계획기의 예시',72,212,620,40,27,C.muted,true);
 code(s,'if "web-01" not in systems:\n    systems.append("web-01")',72,268,1296,115,25);
 text(s,'개선: 선택값을 검증하고 작업을 직접 생성',72,418,1260,42,28,C.blue,true);
 code(s,'selected = validate_selected_system(state["selected_system"])\ntasks = [{"id": "task-0", "agent": "research", "kind": "monitoring",\n          "target": selected, "status": "pending"}]',72,477,1296,155,25);
 footer(s,'도구 호출 직전에도 대상 일치 여부 확인 · 빈 입력과 등록되지 않은 시스템은 거부');
 note(s,'기존 fake 모델은 실습용 병렬 실행을 위해 web-01을 추가했다. 이는 모든 실제 모델이 항상 web-01을 조회했다는 뜻이 아니다. 개선 후 명시적으로 선택한 요청은 모델이 대상을 다시 결정하지 않으며 조회 직전에도 같은 대상인지 검사한다. 코드 첫 블록은 연속된 실제 코드 두 줄에서 설명 주석만 제외했다.',['https://github.com/gayeon9-9/skala_ai_mini_project/blob/ee32f7e/agent/fake_llm.py','https://github.com/gayeon9-9/skala_ai_mini_project/blob/1517e8d/agent/graph.py#L141','doc/step_01_code_walkthrough.md']);
}
// 07 접수 데이터와 복원
{
 const s=slide('대화가 이어지는 자연어 문의 접수','사용자가 형식을 맞추지 않아도 원문 근거를 붙여 접수 항목을 정리한다');
 await img(s,E+'08_intake_panel.png',72,220,1296,372,'실제 관리자 화면의 자연어 문의 정보 패널');
 text(s,'미언급 · 없음 · 모름 · 값 있음',72,625,650,40,29,C.blue,true);
 text(s,'같은 접수로 저장·복원하고 후속 답변을 병합',72,675,1296,42,27,C.ink);
 note(s,'최신 실제 실행의 접수 정보다. 증상·오전 9시·오류 없음·윈도우 크롬이 저장되었다. 작은 Qwen 모델이 필드 스키마를 지키지 못해 원문에서 명확한 표현만 규칙으로 보완했다. 화면에서도 이 경로를 표시한다. 새로고침 후 동일 답변과 접수 정보가 복원되는 것을 이번 UI 실행에서 확인했다. 같은 request_id 재전송의 중복 방지는 별도 자동 검사로 확인한다.',['agent/intake.py','agent/intake_llm.py','agent/inquiry_store.py','doc/evidence/final_openai/ui_verification.json']);
}
// 08 RAG 코드
{
 const s=slide('코드 개선 ② 키워드 규칙을 의미 검색으로','FAQ 4개 + Runbook 6개 · 시스템과 사용자/운영자 용도를 먼저 제한한다');
 text(s,'기존: 매칭이 없으면 기본 문서 반환',72,215,1296,40,28,C.muted,true);
 code(s,'for keyword, incident_type in _INCIDENT_TYPE_RULES:\n    if keyword in text:\n        return incident_type\nreturn "db-down"',72,269,1296,175,24);
 text(s,'개선: 유사도 점수로 후보를 선택',72,467,1296,40,28,C.blue,true);
 code(s,'for chunk, stored in candidates:\n    score = sum(a * b for a, b in zip(vector, stored))\n    if score >= self.min_score:\n        ranked.append({**chunk, "score": round(score, 6),\n                       **({"raw_score": score} if self.include_raw_scores else {})})',72,518,1296,181,24);
 footer(s,'기준 이상 문서 없음: no_match  /  검색 호출 실패: error  /  최종 안내에 출처 표시');
 note(s,'질문과 문서를 임베딩으로 변환하고 정규화된 벡터의 내적으로 코사인 유사도를 계산한다. 화면의 개선 코드는 실제 연속 5줄이며 기준 점수 이상인 후보를 추가한다. 사용자 FAQ와 운영자 Runbook을 분리하며 운영 명령어를 사용자 답변 프롬프트에 넣지 않는다. 현재 문서 10개는 10개 청크다. 유사도는 장애 원인 확률이 아니다.',['https://github.com/gayeon9-9/skala_ai_mini_project/blob/ee32f7e/agent/graph.py','https://github.com/gayeon9-9/skala_ai_mini_project/blob/1517e8d/agent/retrieval.py#L217','doc/step_04_rag.md']);
}
// 09 검증 코드
{
 const s=slide('코드 개선 ③ 검증 실패를 안전하게 종료','형식 검사에 더해 선택 대상·관측 수치·문서 인용을 검사한다');
 text(s,'기존: 3회 실패 후 강제 합격',72,215,1296,40,28,C.muted,true);
 code(s,'ok = len(draft) >= 30 and "1)" in draft\nverdict = "합격" if ok else "불합격"\nif not ok and attempts >= 3:\n    ok, verdict = True, "합격(최대 시도 도달 — 통과 처리)"',72,270,1296,170,24);
 text(s,'개선: 실패 상태를 유지하고 제한된 안내 제공',72,475,1296,40,28,C.blue,true);
 code(s,'ok = checked["ok"]\nverdict = "합격" if ok else "불합격"\nexhausted = not ok and attempts >= 3\nif exhausted:\n    verdict = "추가 확인 필요(검증 실패)"',72,528,1296,170,24);
 note(s,'원본 코드의 최대 시도 분기에서 설명 주석만 제외했다. 개선 후 validate_answer의 오류를 재작성 피드백으로 사용한다. 횟수를 다 쓰면 finalize_node가 마지막 초안을 evidence_fallback으로 교체한다. 검증 전 초안을 일반 사용자 말풍선으로 노출하지 않는다. 이 검증은 규칙 기반이므로 모든 문장 의미와 인과 관계를 보장하지 않는다.',['https://github.com/gayeon9-9/skala_ai_mini_project/blob/ee32f7e/agent/graph.py','https://github.com/gayeon9-9/skala_ai_mini_project/blob/1517e8d/agent/graph.py#L397','agent/answer_validation.py']);
}
// 10 전후 화면
{
 const s=slide('같은 문의의 사용자 화면 비교','“로그인 후 화면이 넘어가지 않아요. 오류 메시지는 없어요.” · 개선본은 시스템 선택 추가');
 text(s,'이전: 일반 대화로 분류',72,210,610,40,28,C.muted,true);
 await img(s,OLD+'01_before_natural.jpg',72,262,500,123,'원본 답변 말풍선 부분 확대',{left:20/840,top:156/848,right:600/840,bottom:638/848});
 text(s,'점검 없이 일반 인사로 응답',800,288,560,65,30,C.ink);
 text(s,'개선: 접수 정보와 추가 확인 항목 표시',72,400,1296,40,28,C.blue,true);
 await img(s,OLD+'02_after_natural.jpg',72,450,880,247,'6단계 개선본의 접수 정보 영역 부분 확대',{left:20/840,top:478/786,right:30/840,bottom:86/786});
 text(s,'증상·오류 없음 보존\n시각·환경 추가 확인',1000,517,366,105,27,C.ink);
 footer(s,'실제 화면 부분 확대 · fake 생성 모델의 흐름 비교 · 전체 원본 화면과 촬영 조건 별도 보관');
 note(s,'이 장은 원본과 6단계 완료본의 기능 흐름 비교다. 질문 문장은 같지만 개선본에는 시스템 선택이라는 새 입력이 있다. 두 화면은 fake 생성 모델을 사용했으므로 실제 Qwen 답변의 정확도 전후 비교로 해석하지 않는다. 이후 최신 실제 Qwen+OpenAI 화면은 별도 장에서 보여준다.',['doc/evidence/final/README.md','doc/evidence/final/screenshots.json']);
}
// 11 최신 사용자
{
 const s=slide('최종 실행: Qwen 답변 + OpenAI 검색','합성 문의 한 건 · auth-service 선택 · 실제 생성·검색 호출');
 await img(s,E+'02_answer.png',72,211,1296,415,'최신 Qwen+OpenAI 사용자 답변 말풍선 전체 부분 확대',{left:20/1440,top:488/1100,right:440/1440,bottom:298/1100});
 text(s,ui.browser_elapsed_seconds.toFixed(2)+'초',72,647,300,55,43,C.blue,true);
 text(s,'첫 답변 규칙 검증 통과',480,658,450,45,29,C.ink,true);
 text(s,'새로고침 후 동일 답변 복원',945,658,425,45,27,C.ink);
 footer(s,'실제 답변 영역 확대 · 시간은 화면 요청부터 완료까지 1건 관측 · 진단 정확도를 뜻하지 않음');
 note(s,'2026-10-21 최신 1517e8d 격리본에서 합성 문의를 실제 UI로 제출했다. 질문은 오전 9시부터 로그인 후 화면이 넘어가지 않음, 오류 메시지 없음, 윈도우 크롬 사용이다. 실제 OpenAI small 임베딩과 Qwen 생성을 사용했다. 브라우저 경과 시간과 서버 저장 전 처리 시간은 범위가 다르다. 화면 원본은 수정하지 않았고 현재 장에서는 전체를 축소 배치했다. 접수 추출은 rules_fallback이었다.',['doc/evidence/final_openai/ui_verification.json','doc/evidence/final_openai/admin_detail.json']);
}
// 12 관리자
{
 const s=slide('관리자 조회: 같은 접수에서 원문과 근거 확인','답변 저장 상태, 사용자 정보, 샘플 관측과 문서를 연결해서 보여준다');
 text(s,'실제 관리자 상세 화면 · 점검과 검증 영역',72,215,1296,45,29,C.blue,true);
 await img(s,E+'09_monitoring_panel.png',72,280,620,333,'실제 관리자 화면의 샘플 점검 패널');
 await img(s,E+'10_validation_panel.png',748,280,620,333,'실제 관리자 화면의 규칙 검증 패널');
 text(s,'문의 ID로 원문·답변·문서·처리 이력 연결',72,655,1296,45,30,C.ink,true);
 footer(s,'답변 저장은 장애 복구 완료가 아님 · 현재 로컬 시연용 · 로그인·관리자 권한 미적용');
 note(s,'이번 UI 실행에서 생성한 문의를 관리자 화면에서 열어 같은 inquiry_id와 선택 시스템을 확인했다. 관리자 조회는 저장된 결과를 읽으며 새 모델 호출로 임의 요약을 만들지 않는다. 실무에 도입하려면 인증·조회 권한·담당자 배정·실제 운영 데이터 연결을 추가해야 한다.',['web_admin.html','agent/inquiry_summary.py','doc/evidence/final_openai/ui_verification.json']);
}
// 13 기술/모듈
{
 const s=slide('사용한 기술과 기능별 모듈','기존 의존성 주입 구조를 활용해 검색기·저장소·모델을 교체할 수 있도록 확장');
 table(s,[['기술·역할','관련 파일','이번 반영'],['LangGraph · 흐름 제어','agent/graph.py','접수 → 점검 → 검색 → 검증 연결'],['MCP · 외부 도구 호출','agent/mcp_client.py','선택 시스템의 샘플 조회'],['SQLite · 접수 저장','chat_service.py / inquiry_store.py','복원·중복 요청·상태 관리'],['RAG · 공급자 선택','embeddings.py / retrieval.py','Ollama·OpenAI 임베딩 분리'],['생성·검증 모듈','answer_generation.py / answer_validation.py','근거 프롬프트·제한된 종료'],['DI · 단위/통합 검사','agent/deps.py / tests/','의존성 교체·핵심 오류 재현'],['LangSmith · 추적','observability.py / server.py','문의·문서·모델·검증 메타데이터']],72,215,1296,460,[330,480,486],23);
 note(s,'LangGraph 노드만 여러 개로 나눴다고 Multi-Agent가 되는 것은 아니다. 이 프로젝트는 단일 제어 흐름을 유지한다. 기존 SupervisorDeps를 활용하고 검색과 생성과 검증을 역할별 모듈로 나눴다. 테스트 대역은 연결 오류와 저장 규칙 등을 재현하며 실제 모델 품질은 별도 API 실험으로 확인한다.',['agent/deps.py','agent/graph.py','doc/implementation_plan.md','doc/step_07_openai_embeddings.md']);
}
// 14 검사와 실추적
{
 const s=slide('자동 검사와 실제 LangSmith 추적','기능 회귀 검사와 실제 모델 실행 기록을 구분해서 확인했다');
 text(s,String(test.pytest.passed)+' + '+String(test.pytest.subtests_passed),72,230,600,90,64,C.blue,true);
 text(s,'테스트 189개 + 하위 검사 111개 통과',75,330,620,55,29,C.ink,true);
 text(s,'접수 병합 · 중복 요청 · 대상 제한\n검색 오류 · 잘못된 인용 · 실패 종료',75,425,620,110,28,C.muted);
 text(s,'최신 UI 요청 1건',820,230,540,65,39,C.blue,true);
 text(s,'LangSmith 하위 실행 31개 수집',820,327,540,65,29,C.ink,true);
 text(s,'MCP 시간 초과 1회 → 재시도 성공\nOpenAI 1536차원·검색 문서 ID 확인',820,423,540,110,27,C.muted);
 footer(s,'검사 통과 수는 답변 정확도가 아님 · 원격 수집 확인과 규칙 검증도 서로 다른 증거');
 note(s,'기록 기준 pytest 189개와 subtest 111개가 통과했다. 이번 최종 UI 실행의 실제 LangSmith root는 chat_request_stream이며 하위 실행 31개가 원격에 수집됐다. 기존 fake 생성 실험의 35개 하위 실행과 혼동하지 않는다. LangSmith는 오류 위치와 시간을 보여주는 도구이며 자동으로 정확도를 높이는 기능이 아니다. 실제 trace readback 증거를 기록했다.',['doc/evidence/embedding_comparison/api_verification.json','doc/evidence/final_openai/langsmith_readback.json','agent/observability.py']);
}
// 15 기준 검색 비교
{
 const s=slide('기존 규칙 대비 RAG 검색 결과','같은 Runbook 정답 질문 4개 · 정답 문서가 검색 결과에 포함되는지 비교');
 const ch=s.charts.add('bar',{position:{left:72,top:225,width:650,height:420},categories:['기존 규칙','Ollama RAG'],series:[{name:'정답 문서 포함률',values:[0.25,1],fill:C.blue}],hasLegend:false,barOptions:{direction:'column',gapWidth:110},xAxis:{textStyle:{fontSize:24,typeface:FONT,color:C.ink}},yAxis:{min:0,max:1.2,majorUnit:0.25,numberFormatCode:'0%',textStyle:{fontSize:20,typeface:FONT},majorGridlines:{fill:C.line,width:1}},dataLabels:{showValue:true,position:'outEnd',numberFormatCode:'0%',textStyle:{fontSize:30,typeface:FONT,bold:true}},chartFill:C.bg});
 applyPresentationChartFont(ch,{fontFamily:FONT});
 text(s,'1/4 → 4/4',820,240,510,90,56,C.blue,true);
 text(s,'기본 문서 반환 대신\n근거 없음(no_match)을 구분',820,365,510,110,31,C.ink,true);
 text(s,'추가된 FAQ 정답 질문은 6/6\n기존 FAQ 기능과의 비교는 아님',820,530,510,90,27,C.muted);
 footer(s,'기존 키워드 어댑터 vs 실제 RAG 검색 · 소수 합성 질문 · 원본 전체 서비스 정확도와 다름');
 note(s,'검색 평가만 비교했다. 원본 규칙에 질문 문장을 합성 알림으로 넘기는 어댑터를 사용했으므로 end-to-end 장애 진단 정확도로 표현하면 안 된다. 같은 Runbook 질문 4개에서 정답 포함이 1개에서 4개로 늘었다. FAQ는 새 기능이므로 기존 정확도 증가에 합산하지 않았다. RAG는 상위 3개에 정답이 포함된 Hit@3 기준이며 반환된 모든 문서가 적절하다는 의미는 아니다.',['doc/evidence/final/rag_test.json','doc/evidence/final/README.md']);
}
// 16 공급자 비교
{
 const s=slide('Ollama와 OpenAI 임베딩 실측 비교','고유 질의 12개 × 3회 반복 · 동일 문서·질의·상위 3개 · 개발용 질문으로 기준 점수 결정');
 table(s,[['지표','Ollama Qwen 임베딩','OpenAI small 임베딩'],['Hit@3 · 정답 질문 10개×3회',`${Math.round(oll.hit_at_3*100)}% (30/30)`,`${Math.round(op.hit_at_3*100)}% (27/30)`],['FAQ / Runbook · 고유 질문','6/6  /  4/4','6/6  /  3/4'],['검색 평균 · 전체 36회',`${Math.round(oll.latency.mean_seconds*1000)} ms`,`${Math.round(op.latency.mean_seconds*1000)} ms`],['검색 p95',`${Math.round(oll.latency.p95_seconds*1000)} ms`,`${Math.round(op.latency.p95_seconds*1000)} ms`],['근거 없는 질문 · 2개×3회','6/6회 no_match','6/6회 no_match']],72,215,1296,375,[490,403,403],26);
 text(s,'이 표본에서는 OpenAI의 정확도·속도 우위가 확인되지 않았다',72,628,1296,55,32,C.blue,true);
 footer(s,'검색 단계 시간이며 답변 생성 시간 제외 · 알려진 질문을 반복한 회귀 평가 · 일반화 한계');
 note(s,'Ollama는 1024차원·문턱값 0.50, OpenAI small은 1536차원·개발용 14개 검색 검사로 선택한 문턱값 0.30이다. 테스트 결과를 보고 문턱값을 조정하지 않았다. OpenAI가 놓친 질문은 gateway-service의 패킷 손실과 upstream 호출 대기 제한 초과였다. 평균 검색 지연은 약 140ms 더 길었다. 이전 보존 버전은 v1.0-ollama다. 총 청구 금액은 측정하지 않았으며 로컬 Qwen의 이론적 usage.usd를 OpenAI 실청구로 사용하지 않는다.',['doc/evidence/embedding_comparison/RESULTS.md','doc/evidence/embedding_comparison/test.json','doc/evidence/embedding_comparison/dev.json']);
}
// 17 실제 수치 화면 캡처
{
 const s=slide('실측 결과 화면','저장된 실행 JSON에서 만든 측정 결과 보고서 · 제품 실행 화면과 구분');
 await img(s,E+'13_baseline_rag_report.png',72,215,625,450,'저장된 RAG 비교 측정 결과 보고서의 실제 브라우저 캡처');
 await img(s,E+'14_embedding_report.png',745,215,625,450,'저장된 임베딩 공급자 비교 측정 결과 보고서의 실제 브라우저 캡처');
 footer(s,'원본 JSON과 화면 캡처를 함께 보관 · 결과 보고서의 수치는 재실행하거나 임의로 보정하지 않음');
 note(s,'이 화면은 실행 도중의 콘솔이 아니라 저장된 실제 JSON 결과를 읽기 좋게 표시한 정적 측정 보고서를 브라우저에서 캡처한 것이다. 원본 파일과 HTML 생성기를 함께 보관하여 수치를 추적할 수 있다. 앞의 기능 화면 비교는 fake 생성 조건이며 이 장의 검색 비교는 실제 임베딩 호출 결과다.',['doc/evidence/final_openai/measurement_report.html','doc/evidence/final/rag_test.json','doc/evidence/embedding_comparison/test.json']);
}
// 18 한계
{
 const s=slide('현재 한계와 다음 개선 우선순위');
 table(s,[['확인한 한계','다음 개선'],['작은 Qwen의 접수 스키마 실패','구조화 출력·추출 프롬프트 개선 후 실제 문의 평가'],['오류 없음 문의에도 504 문서 안내','관련성·사실/추론 구분 기준과 검증 사례 보강'],['검색 표본이 작고 OpenAI Runbook 누락','질의·문서 범위 확대, 개발/평가 질문 분리 유지'],['샘플 관측·관리자 인증 미적용','실제 로그·지표와 사내 인증·권한 연동']],72,215,1296,400,[620,676],27);
 footer(s,'현재 서비스는 기초 문의 접수 보조 · 실제 장애 원인 확정과 자동 복구는 구현 범위 밖');
 note(s,'규칙 검증을 통과한 최신 답변에서도 고부하 해석과 원인 가능성을 사람이 검토해야 한다. 실제 모델 이해가 성공한 것과 규칙 대체로 기능이 이어진 것을 구분한다. 현재 평균 응답 시간, 업무 시간 절감, 운영 장애 해결률은 충분한 표본으로 측정하지 않았다. 외부 API 선택 시 문서와 질의가 전송되므로 실제 기업에서는 데이터 정책에 맞는 공급자를 선택해야 한다.',['doc/evidence/final_openai/admin_detail.json','doc/evidence/embedding_comparison/RESULTS.md','doc/step_06_admin_evaluation.md']);
}
// 19 기업 활용
{
 const s=slide('기업에서 활용할 수 있는 방식','현재 기능을 기반으로 확장 가능한 업무를 구분했다');
 text(s,'사내 IT 헬프데스크',72,228,610,60,36,C.blue,true);
 text(s,'직원 문의 → 접수 항목 정리\n→ 관련 FAQ 안내 → 관리자 확인',72,328,610,135,31,C.ink);
 text(s,'현재 구현과 가장 가까운 활용\n담당자 배정·처리 상태·권한을 추가',72,535,610,105,27,C.muted);
 text(s,'배포 후 상태 점검 보조',752,228,610,60,36,C.blue,true);
 text(s,'점검 대상 선택 → 지표·문서 확인\n→ 점검 근거와 문의 이력 기록',752,328,610,135,31,C.ink);
 text(s,'실제 배포 이력·오류 로그 연동 필요\n현재 데이터만으로 장애 원인 확정 불가',752,535,610,105,27,C.muted);
 footer(s,'기대 효과: 재설명·수동 정리 부담 감소 · 실제 업무 시간 절감량은 아직 측정하지 않음');
 note(s,'현재의 자연어 접수·저장·검색·검증·관리자 조회는 IT 헬프데스크 문의 접수 보조에 바로 연결되는 구조다. 배포 후 점검은 실제 운영 지표와 배포 전후 기준선, 로그가 추가되어야 한다. 두 활용 모두 자동 복구나 담당자에게 외부 메시지를 전송하는 기능은 아직 포함하지 않는다.',['doc/final_report.md','doc/step_06_admin_evaluation.md']);
}
// 20 회고·추적 가능한 결과
{
 const s=slide('개발에서 확인한 점과 결과 자료');
 rows(s,[['답변 이후의 흐름도 서비스 기능이다','저장·복원·재시도와 관리자 확인까지 이어져야 문의가 남는다'],['검증 코드도 실패 사례로 검증해야 한다','정상 답변의 오차단을 기록하고 회귀 테스트로 보완했다'],['모델 선택은 같은 조건의 측정으로 판단한다','외부 API 도입이 항상 정확도나 속도 향상을 뜻하지는 않았다']],72,218,1296,133);
 text(s,'단계별 코드 · 비교 화면 · 실제 측정 결과',72,650,1100,40,28,C.blue,true);
 const link=text(s,'github.com/gayeon9-9/skala_ai_mini_project',72,701,1296,36,24,C.muted);
 link.text.get('github.com/gayeon9-9/skala_ai_mini_project').link={uri:'https://github.com/gayeon9-9/skala_ai_mini_project',isExternal:true};
 note(s,'원본 ee32f7e, 6단계 완료 기록 f30ef78, Ollama 보존 태그 v1.0-ollama, 실제 OpenAI 비교 기록 1517e8d를 Git에서 확인할 수 있다. 최종 화면 캡처와 보고서는 후속 문서 커밋으로 남긴다. 새로운 기능을 늘리는 것뿐 아니라 같은 입력과 실패 사례를 남겨 무엇이 바뀌었는지 설명할 수 있는 것이 중요했다.',['doc/evidence/final/README.md','doc/evidence/embedding_comparison/RESULTS.md','https://github.com/gayeon9-9/skala_ai_mini_project/commits/main','https://github.com/gayeon9-9/skala_ai_mini_project/tree/v1.0-ollama']);
}

// PNG는 시각 검토용 빌드 자료이고 최종 전달물에는 포함하지 않는다.
for(let i=0;i<p.slides.items.length;i++){
 const blob=await p.export({slide:p.slides.items[i],format:'png',scale:1});
 await fs.writeFile(path.join(BUILD,'slides',String(i+1).padStart(2,'0')+'.png'),new Uint8Array(await blob.arrayBuffer()));
}
const candidate=path.join(BUILD,'candidate.pptx');
await (await PresentationFile.exportPptx(p)).save(candidate);
await fs.writeFile(path.join(BUILD,'deck.json'),JSON.stringify(p.toProto()));
const finalPath=path.join(WORK,'output','G081_성가연_AI_헬프데스크_결과보고_v2.pptx');
const result=await finalizePresentation({workspaceDir:WORK,candidatePath:candidate,finalPath,
 pythonExecutable:'/Users/gayeon/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3.12',
 integrityValidatorPath:path.join(SKILL,'container_tools/inspect_presentation_package_integrity.py'),
 layoutValidatorPath:path.join(SKILL,'container_tools/inspect_presentation_layout_geometry.py'),
 layoutArgs:['--expected-slide-size-emu','13716000,7715250','--validate-bullet-geometry','--validate-heading-fit',...[3,4,13,16,18].flatMap(n=>['--require-native-table-slide',String(n)])],
 requiredNativeTableOwnerSlides:[3,4,13,16,18],requiredNativeChartOwnerSlides:[15],materializeLiteralChartWorkbooks:true,
 fontPolicy:{basis:'design',families:[FONT,CODE]},verifyArtifactToolImport:true,
 receiptPath:path.join(BUILD,'validation-v2.json')});
console.log(JSON.stringify({slides:p.slides.items.length,finalPath,result},null,2));
