"""절의 조건 보존과 실제 저장 답변의 해석 오류를 외부 API 없이 재현한다."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from agent.answer_claims import claim_errors
from agent.answer_generation import build_answer_prompt
from agent.answer_grounding import applicable_documents
from agent.answer_validation import validate_answer
from agent.document_sections import markdown_sections
from agent.retrieval import KnowledgeRetriever
from test_retrieval import FixedEmbedder
from test_answer_grounding import intake_for

ROOT = Path(__file__).resolve().parents[1]


class SectionRetrievalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.rag = KnowledgeRetriever(root=ROOT, embedder=FixedEmbedder(), cache_dir=self.temp.name)

    async def test_general_advice_survives_without_504_and_raw_result_is_unchanged(self):
        result = await self.rag.search('페이지가 열리지 않아요', 'gateway-service')
        docs = result['documents']
        raw = copy.deepcopy(docs)
        applied = applicable_documents(docs, intake_for())
        doc = next(d for d in applied if d['document_id'] == 'faq:connectivity')
        self.assertIn('연결 환경', doc['text'])
        self.assertNotIn('504', doc['text'])
        self.assertTrue(doc['excluded_section_ids'])
        self.assertEqual(docs, raw)
        _, prompt = build_answer_prompt('페이지가 열리지 않아요',
            [{'source':'knowledge','data':d} for d in docs] +
            [{'source':'intake','data':intake_for()}])
        context = json.loads(prompt)
        self.assertNotIn('504', context['user_documents'][0]['text'])
        self.assertTrue(context['user_documents'][0]['applied_section_ids'])

    async def test_reported_504_keeps_both_sections_and_citation_id(self):
        docs = (await self.rag.search('504', 'gateway-service'))['documents']
        doc = applicable_documents(docs, intake_for('provided','HTTP 504'))[0]
        self.assertIn('504', doc['text'])
        self.assertIn('연결 환경', doc['text'])
        self.assertEqual(doc['document_id'], 'faq:connectivity')
        self.assertEqual(doc['excluded_section_ids'], [])

    async def test_long_conditional_section_cannot_escape_parent_condition(self):
        root = Path(self.temp.name)
        (root/'guide.md').write_text('## 일반 안내\n환경을 기록합니다.\n\n## HTTP 504인 경우\n' + '조건부 조치 안내입니다. ' * 250)
        (root/'catalog.json').write_text(json.dumps({'version':'test','documents':[
            {'document_id':'faq:a','title':'접속 안내','path':'guide.md','audience':'user','systems':['*'],'version':'1'}]}))
        rag = KnowledgeRetriever(root=root, catalog_path=root/'catalog.json', embedder=FixedEmbedder())
        docs = (await rag.search('문의','gateway-service'))['documents']
        self.assertGreater(len(docs[0]['sections']), 3)
        applied = applicable_documents(docs,intake_for())[0]
        self.assertIn('환경을 기록',applied['text'])
        self.assertNotIn('조건부 조치',applied['text'])

    async def test_section_below_threshold_is_not_added_to_answer(self):
        class Scores(FixedEmbedder):
            async def embed(self, texts):
                return [[0,0,1] if '일반 접속 확인' in t else [1,0,0] for t in texts]
        rag = KnowledgeRetriever(root=ROOT,embedder=Scores(),cache_dir=Path(self.temp.name)/'scores')
        docs = (await rag.search('문의','gateway-service'))['documents']
        applied = applicable_documents(docs,intake_for())
        self.assertTrue(all('연결 환경' not in d['text'] for d in applied))


class SectionAndClaimTests(unittest.TestCase):
    def test_h3_and_fenced_headings_keep_parent_scope(self):
        parts=markdown_sections('## 504 오류\n상위 조건\n### 세부 안내\n내용\n```md\n## 일반\n```\n## 정상 안내\n확인')
        self.assertEqual([p[0] for p in parts],['504 오류','정상 안내'])
        self.assertIn('## 일반',parts[0][1])

    def test_legacy_result_stays_conservative_and_malformed_sections_fail_closed(self):
        doc={'title':'안내','text':'504 오류: 환경 기록','document_id':'faq:a'}
        self.assertEqual(applicable_documents([doc],intake_for()),[])
        self.assertEqual(applicable_documents([{**doc,'text':'안전한 듯한 본문','sections':None}],intake_for()),[])

    def test_specific_document_title_applies_to_all_sections(self):
        doc={'title':'504 오류 안내','sections':[{'text':'다시 시도','chunk_id':'x'}]}
        self.assertEqual(applicable_documents([doc],intake_for()),[])

    def test_saved_answer_that_passed_previous_validator_is_now_rejected(self):
        p=json.loads((ROOT/'doc/evidence/template_final_20261021/admin_detail.json').read_text())
        collected=[{'source':'monitoring','data':d} for d in p['observations']]+[
            {'source':'knowledge','data':d} for d in p['retrieval']['documents']]
        result=validate_answer(p['answer'],'auth-service',collected,p['retrieval'],p['intake'])
        self.assertFalse(result['ok'])
        self.assertTrue(any('무관' in e for e in result['errors']))
        self.assertTrue(any('캐시' in e for e in result['errors']))
        self.assertTrue(any('가능성' in e for e in result['errors']))

    def test_observations_uncertainty_and_non_destructive_advice_are_allowed(self):
        docs={'faq:browser':{'text':'다른 브라우저에서 확인합니다.'}}
        text='샘플 CPU 83.4%이며 실제 원인은 추가 확인이 필요합니다.\n문서의 적용 여부는 추가 확인이 필요합니다. [faq:browser]\n캐시를 삭제하지 마세요.'
        self.assertEqual(claim_errors(text,docs),[])

    def test_supported_action_is_allowed_and_unknown_source_is_not_support(self):
        text='캐시 삭제 후 다시 확인하세요.'
        self.assertTrue(claim_errors(text,{}))
        self.assertEqual(claim_errors(text,{'faq:a':{'text':'캐시 삭제 후 확인합니다.'}}),[])
