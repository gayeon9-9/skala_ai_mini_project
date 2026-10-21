"""관리자 화면의 API 경계와 상태 표시를 실제 HTML setup 코드로 확인한다.

Vue 반응성/fetch만 대체한다. 외부 LLM·브라우저·사용자 DB 없이 늦은 응답,
조회 실패, 이전 완료 근거와 최신 요청 상태의 구분을 확인하는 검사다.
"""
from pathlib import Path
import shutil
import subprocess
import unittest


ADMIN_HARNESS = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const script = fs.readFileSync(process.argv[1], 'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
const response = (payload, status = 200) => ({ status, ok: status < 400, json: async () => payload });
const emptyList = { items: [], limit: 20, offset: 0, has_more: false };
const settle = () => new Promise(resolve => setImmediate(resolve));
async function boot(handler = () => response(emptyList)) {
  let app;
  const urls = [];
  const context = {
    URLSearchParams, TypeError,
    Vue: { ref: value => ({ value }), computed: getter => ({ get value() { return getter(); } }),
           createApp: config => ({ mount() { app = config.setup(); } }) },
    fetch: async url => { urls.push(url); return handler(url); },
  };
  vm.runInNewContext(script, context);
  await settle();
  return { app, urls };
}
"""


@unittest.skipUnless(shutil.which("node"), "관리자 화면 상태 검증은 Node.js가 필요하다.")
class AdminUiTests(unittest.TestCase):
    def run_browser(self, scenario):
        html = Path(__file__).resolve().parents[1] / "web_admin.html"
        result = subprocess.run(
            ["node", "-e", ADMIN_HARNESS + "\n(async () => {\n" + scenario +
             "\n})().catch(error => { console.error(error); process.exitCode = 1; });", str(html)],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_latest_failure_keeps_previous_evidence_explicit(self):
        self.run_browser(r"""
const payload = { inquiry_id: 'inq-1', status: 'failed', selected_system: 'auth-service',
  evidence_request_id: 'request-before', evidence_is_current: false, answer: '이전 완료 답변',
  intake: { fields: { symptom: { status: 'provided', value: '로그인 실패' },
    error_message: { status: 'none' }, environment: { status: 'unknown' } } },
  observations: [{ system_name: 'auth-service', cpu_percent: 12 }],
  validation: { status: 'fallback', errors: ['인용 누락'] },
  retrieval: { status: 'ok', documents: [{ audience: 'user', document_id: 'faq:login' },
                                       { audience: 'operator', document_id: 'operator-leak' }] },
  operator_retrieval: { status: 'ok', documents: [{ audience: 'operator', document_id: 'runbook:slow' }] },
  requests: [{ request_id: 'old', status: 'completed' }, { request_id: 'new', status: 'failed' }],
};
const { app } = await boot(url => response(url.includes('?') ? emptyList : payload));
await app.selectInquiry('inq-1');
assert.equal(app.previousEvidence.value, true);
assert.equal(app.statusLabel(app.detail.value.status), '처리 실패');
assert.equal(app.statusLabel('completed'), '답변 저장');
assert.equal(app.detail.value.answer, '이전 완료 답변');
assert.deepEqual(Array.from(app.intakeRows.value, field => field.statusLabel), ['값 있음', '미언급', '없음', '모름']);
assert.equal(app.documentGroups.value[0].documents.length, 1);
assert.equal(app.documentGroups.value[0].documents[0].document_id, 'faq:login');
assert.equal(app.documentGroups.value[1].documents[0].document_id, 'runbook:slow');
assert.equal(app.requests.value[0].request_id, 'new');
assert.ok(app.validationLabel.value.includes('추가 확인'));
""")

    def test_late_detail_response_cannot_overwrite_new_selection(self):
        self.run_browser(r"""
const waiting = {};
const { app } = await boot(url => url.includes('?') ? response(emptyList)
  : new Promise(resolve => { waiting[url] = resolve; }));
const first = app.selectInquiry('first');
const second = app.selectInquiry('second');
waiting['/admin/inquiries/second'](response({ inquiry_id: 'second', status: 'completed' }));
await second;
waiting['/admin/inquiries/first'](response({ inquiry_id: 'first', status: 'failed' }));
await first;
assert.equal(app.selectedId.value, 'second');
assert.equal(app.detail.value.inquiry_id, 'second');
assert.equal(app.detailLoading.value, false);
assert.equal(app.detailStale.value, false);
""")

    def test_detail_fetch_error_keeps_old_result_with_stale_marker(self):
        self.run_browser(r"""
const { app } = await boot(url => url.includes('?') ? response(emptyList)
  : url.endsWith('/old') ? response({ inquiry_id: 'old', answer: '보존할 답변' })
  : response({}, 404));
await app.selectInquiry('old');
await app.selectInquiry('missing');
assert.equal(app.detail.value.inquiry_id, 'old');
assert.equal(app.detail.value.answer, '보존할 답변');
assert.equal(app.selectedId.value, 'missing');
assert.ok(app.detailError.value.includes('찾지 못'));
assert.equal(app.detailStale.value, true);
""")

    def test_pagination_filter_and_list_failure_preserve_last_result(self):
        self.run_browser(r"""
let fail = false;
const { app, urls } = await boot(url => {
  if (fail) return response({}, 503);
  const params = new URLSearchParams(url.split('?')[1]);
  return response({ items: [{ inquiry_id: 'keep' }], limit: 20,
    offset: Number(params.get('offset')), has_more: true });
});
await app.loadList(20);
assert.equal(app.offset.value, 20);
app.statusFilter.value = 'failed';
await app.changeFilter();
assert.equal(app.offset.value, 0);
assert.ok(urls.at(-1).includes('status=failed'));
assert.equal(app.hasMore.value, true);
fail = true;
await app.loadList(20);
assert.equal(app.offset.value, 0);
assert.equal(app.items.value[0].inquiry_id, 'keep');
assert.ok(app.listError.value);
assert.equal(app.listLoading.value, false);
""")

    def test_late_list_response_and_empty_evidence_are_handled(self):
        self.run_browser(r"""
const waiting = [];
let first = true;
const { app } = await boot(url => {
  if (first) { first = false; return response(emptyList); }
  return new Promise(resolve => waiting.push(resolve));
});
const old = app.loadList(20);
const latest = app.loadList(40);
waiting[1](response({ ...emptyList, offset: 40, items: [{ inquiry_id: 'latest' }] }));
await latest;
waiting[0](response({ ...emptyList, offset: 20, items: [{ inquiry_id: 'old' }] }));
await old;
assert.equal(app.offset.value, 40);
assert.equal(app.items.value[0].inquiry_id, 'latest');
assert.equal(app.previousEvidence.value, false);
assert.equal(app.observations.value.length, 0);
assert.equal(app.metric(null), '미확인');
assert.equal(app.metric(0), '0%');
assert.equal(app.elapsed(2.5), '2.50초');
assert.ok(app.retrievalLabel('no_match').includes('근거가 없'));
assert.ok(app.retrievalLabel('error').includes('실패'));
""")


if __name__ == "__main__":
    unittest.main()
