import fs from 'node:fs/promises';
import crypto from 'node:crypto';
import { chromium } from '/Users/gayeon/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs';
const out='/Users/gayeon/all_total_vue_student/doc/evidence/template_final_20261021';
const base='http://127.0.0.1:8620';
const query='오전 9시부터 로그인 후 화면이 넘어가지 않아요. 오류 메시지는 없어요. 윈도우 크롬을 사용해요.';
const browser=await chromium.launch({channel:'chrome',headless:true});
const ctx=await browser.newContext({viewport:{width:1440,height:1050},deviceScaleFactor:1});
const page=await ctx.newPage();
const errors=[]; page.on('pageerror',e=>errors.push(e.message));
const shots=[];
async function shot(p,name,description,opts={}) {const file=`screenshots/${name}.png`;await p.screenshot({path:`${out}/${file}`,...opts});const b=await fs.readFile(`${out}/${file}`);shots.push({file,description,url:p.url(),captured_at:new Date().toISOString(),sha256:crypto.createHash('sha256').update(b).digest('hex')});}
async function json(name,value){await fs.writeFile(`${out}/${name}.json`,JSON.stringify(value,null,2)+'\n');}
try {
 await page.goto(base,{waitUntil:'networkidle'});
 await page.locator('#selected-system').waitFor({state:'visible'});
 await page.waitForFunction(()=>document.querySelector('#selected-system')?.options.length===7);
 await page.locator('#selected-system').selectOption('auth-service');
 await page.getByRole('textbox',{name:'장애 증상'}).fill(query);
 await shot(page,'01_input','선택한 서비스와 입력 문의. 최신 커밋의 실제 UI.');
 const started=performance.now();
 await page.getByRole('button',{name:'보내기',exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('.row.agent .bubble') && document.querySelector('form button')?.textContent.trim()==='보내기',{},{timeout:240000});
 const elapsed=(performance.now()-started)/1000;
 const answer=await page.locator('.row.agent .bubble').last().innerText();
 const alltext=await page.locator('body').innerText();
 const inquiry=(alltext.match(/접수 ID: ([a-f0-9-]+)/)||[])[1];
 if(!inquiry)throw new Error('InquiryIdMissing');
 const dresp=await ctx.request.get(`${base}/admin/inquiries/${inquiry}`);const detail=await dresp.json();
 await json('admin_detail',detail);
 const tresp=await ctx.request.get(`${base}/threads/${detail.thread_id}`);const thread=await tresp.json();await json('thread',thread);
 await page.locator('.row.agent .bubble').last().scrollIntoViewIfNeeded();
 await shot(page,'02_answer','검증 이후 제공된 실제 답변. 답변·DOM 수정 없음.');
 await page.locator('details.intake-card').last().scrollIntoViewIfNeeded();
 await shot(page,'03_sources_intake','사용자 문서 검색 결과와 구조화된 문의 정보.');
 await page.reload({waitUntil:'networkidle'});
 await page.waitForFunction(()=>!!document.querySelector('.row.agent .bubble'));
 const restored=await page.locator('.row.agent .bubble').last().innerText();
 await page.locator('details.intake-card').last().scrollIntoViewIfNeeded();
 await shot(page,'04_restored','새로고침으로 같은 문의·답변·정보가 복원된 화면.');
 const selected=await page.locator('#selected-system').inputValue();
 const sameInquiry=(await page.locator('body').innerText()).includes(`접수 ID: ${inquiry}`);
 const admin=await ctx.newPage();admin.on('pageerror',e=>errors.push(e.message));
 await admin.goto(`${base}/admin`,{waitUntil:'networkidle'});
 await admin.locator('.case').first().click();
 await admin.locator('.detail .answer').waitFor();
 await shot(admin,'05_admin_fullpage','동일 문의의 관리자 상세 전체 화면.',{fullPage:true});
 await shot(admin,'06_admin_top','접수 목록·문의 정보·기초 점검·답변 검증.');
 const info=admin.locator('.panel').filter({has:admin.getByRole('heading',{name:'사용자가 알려준 정보',exact:true})});
 await info.screenshot({path:`${out}/screenshots/07_intake_panel.png`});
 const verify=admin.locator('.panel').filter({has:admin.getByRole('heading',{name:'답변 검증',exact:true})});
 await verify.screenshot({path:`${out}/screenshots/08_validation_panel.png`});
 const docs=admin.locator('.grid').filter({has:admin.getByRole('heading',{name:'사용자 안내 자료',exact:true})});
 await docs.screenshot({path:`${out}/screenshots/09_documents_panels.png`});
 for(const [name,description] of [['07_intake_panel','관리자 화면의 문의 정리 영역 그대로 캡처'],['08_validation_panel','관리자 화면의 검증 결과 영역 그대로 캡처'],['09_documents_panels','사용자 안내와 운영자 확인 자료 영역 그대로 캡처']]){const file=`screenshots/${name}.png`;const b=await fs.readFile(`${out}/${file}`);shots.push({file,description,url:admin.url(),captured_at:new Date().toISOString(),sha256:crypto.createHash('sha256').update(b).digest('hex')});}
 const report={captured_at:new Date().toISOString(),input:query,selected_system:'auth-service',inquiry_id:inquiry,thread_id:detail.thread_id,browser_elapsed_seconds:elapsed,checks:{thread_http_ok:tresp.ok(),admin_http_ok:dresp.ok(),restored_answer_equal:restored===answer,selected_system_retained:selected==='auth-service',same_inquiry:sameInquiry,has_answer:!!answer,no_page_errors:errors.length===0,intake_model_source:detail.intake?.source==='model',no_unreported_504:!answer.includes('504'),no_internal_markers:!(/\[(sample_monitoring|verified_intake|confirmed_http_error_codes)\]/.test(answer))},page_errors:errors,screenshots:shots,notes:['최신 실제 UI 내용 변경 없음','합성 문의 1건·샘플 모니터링·격리 임시 DB','브라우저 시간은 클릭부터 완료까지의 단일 관측이며 성능 향상률 아님']};
 await json('ui_verification',report);
 console.log(JSON.stringify({inquiry_id:inquiry,elapsed,checks:report.checks,validation:detail.validation,intake_source:detail.intake?.source,answer},null,2));
} catch(e){await shot(page,'failure','실패 시 실제 화면');await json('ui_failure',{error_type:e.name,message:e.message,page_errors:errors,screenshots:shots});throw e;}finally {await browser.close();}
