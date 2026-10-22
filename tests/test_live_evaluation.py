"""실제 모델 호출 없이 평가 도구의 채점·분모 계약을 확인한다."""
from evals.evaluate_live_helpdesk import check_turn, summary


def test_empty_or_failed_requests_never_count_as_success():
    rows=[{'error_type':'TimeoutError','checks':{'request_completed':False}},{}]
    result=summary(rows,4)
    assert result['expected_turns']==4
    assert result['contract_passed_turns']==0
    assert result['error_turns']==1
    assert result['validation_status_counts']=={'error':2}


def test_fallback_is_reported_separately_from_validation_pass():
    rows=[{'checks':{'terminal':True},'result':{'validation':{'status':'fallback'}}}]
    assert summary(rows,1)['validation_status_counts']=={'fallback':1}


def test_missing_fields_and_no_match_are_checked_independently():
    turn={'statuses':{'error_message':'none'},'retrieval_status':'no_match','question_field':None}
    result={'final':'담당자의 추가 확인이 필요합니다.','validation':{'status':'passed'},
            'intake':{'fields':{'error_message':{'status':'none'}},'questions':[],'last_question_field':None},
            'retrieval':{'status':'ok','documents':[{'document_id':'faq:x'}]}}
    checks=check_turn(turn,result)
    assert checks['error_message_status']
    assert not checks['retrieval_status']
    assert not checks['no_documents']
