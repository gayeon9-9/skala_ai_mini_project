"""실제 반복 평가에서 발견한 부분 추출 실패를 원문 기반으로 보완한다."""
import unittest
from agent.intake import analyze_intake, empty_fields
from test_intake import FixedLLM, field


class SupplementTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_none_does_not_prevent_recovery_of_unknown_fields(self):
        query='로그인 후 화면이 넘어가지 않아요. 발생 시각은 모르겠어요. 오류 메시지는 없어요. 사용 환경은 모르겠어요.'
        fields=empty_fields()
        fields['error_message']=field(None,'오류 메시지는 없어요','none')
        result=await analyze_intake(query,{},FixedLLM({'fields':fields}))
        self.assertEqual(result['source'],'model_with_rules')
        self.assertEqual(result['fields']['symptom']['status'],'provided')
        self.assertEqual(result['fields']['occurred_at']['status'],'unknown')
        self.assertEqual(result['fields']['environment']['status'],'unknown')
        self.assertEqual(result['questions'],[])
        self.assertEqual(set(result['supplemented_fields']),{'symptom','occurred_at','environment'})

    async def test_empty_model_output_only_recovers_explicit_information(self):
        result=await analyze_intake('로그인이 안 돼요',{},FixedLLM({'fields':empty_fields()}))
        self.assertEqual(result['source'],'rules_fallback')
        self.assertEqual(result['fields']['occurred_at']['status'],'unmentioned')
        self.assertEqual(result['fields']['environment']['status'],'unmentioned')
        self.assertEqual(result['last_question_field'],'occurred_at')

    async def test_valid_model_field_and_previous_values_are_preserved(self):
        prior={'fields':{'occurred_at':field('오전 9시')}}
        result=await analyze_intake('사파리에서 화면이 멈춰요',prior,
                                   FixedLLM({'fields':{'symptom':field('화면이 멈춰요')}}))
        self.assertEqual(result['fields']['symptom']['value'],'화면이 멈춰요')
        self.assertEqual(result['fields']['occurred_at']['value'],'오전 9시')
        self.assertEqual(result['fields']['environment']['value'],'사파리')

    async def test_no_matching_rule_does_not_invent_supplements(self):
        result=await analyze_intake('확인 부탁드립니다',{},FixedLLM({'fields':empty_fields()}))
        self.assertEqual(result['source'],'model')
        self.assertEqual(result['supplemented_fields'],[])
        self.assertTrue(all(f['status']=='unmentioned' for f in result['fields'].values()))
