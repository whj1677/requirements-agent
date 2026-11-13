"""No-slot scope properties remain reviewed, without weakening ordinary own refs."""
import copy
import json
import jsonschema
import pytest
from app import detail_semantics as review
from app import detail_correction
from app.detail_draft import model_schema
from app.core import Problem
from tests.test_detail_semantics import fixture


def test_no_slot_properties_remain_in_full_review_and_have_explicit_policy(tmp_path):
    *_,plan=fixture(tmp_path)
    packet=review.challenge_plan(plan)
    scope=[f for f in packet['fields'] if '.applicability.' in f['path'][1]]
    assert len(scope)==8 and len(packet['fields'])==len(plan['rows'][0]['property_refs'])
    assert all(f['reference_policy']=='scope_without_independent_evidence_slot' and f['sources']==[] for f in scope)
    for section,shape in model_schema()['properties']['applicability']['properties'].items():
        assert set(shape['properties'])=={'applicable','reason'} and shape['additionalProperties'] is False
    assert all('reference_policy' not in f for f in packet['fields'] if f not in scope)
    messages=review.challenge_messages(packet)
    header=json.loads(messages[0]['content'].split('可信任务头：',1)[1])
    sent=json.loads(messages[1]['content'][0]['text'])
    assert header['scope_reference_fields']==[f['id'] for f in scope]
    assert sent['context']['excerpts'] and sent['fields']==[dict(f,own_sources=[dict(**s,text=next(x['text'] for x in packet['context']['excerpts'] if x['source']==s['source'] and x['locator']==s['locator'])) for s in f['sources']]) for f in packet['fields']]
    response=dict(candidate_hash=packet['candidate_hash'],input_hash=packet['input_hash'],checked_field_ids=[f['id'] for f in packet['fields']],findings=[])
    assert review.challenge_outcomes(plan,packet,response,{'R1':dict(status='passed',findings=[],checks=[])})['R1']['status']=='passed'
    response['checked_field_ids'].remove(scope[0]['id'])
    with pytest.raises(Problem):review.challenge_outcomes(plan,packet,response,{'R1':dict(status='passed',findings=[],checks=[])})


def test_false_scope_reason_can_be_precisely_corrected_but_not_bool_or_other_reason(tmp_path):
    _,_,excerpts,raw,_,_,plan=fixture(tmp_path)
    reason='detail_contract.applicability.flow.reason'
    raw['requirements'][0]['detail_contract']['applicability']['flow']['reason']='需要管理员强制释放流程。'
    detail=copy.deepcopy(raw['requirements'][0]['detail_contract'])
    plan['rows'][0]['detail_contract']=detail
    plan['rows'][0]['property_refs']={f'P{i:03d}':dict(path=p,value=v) for i,(p,v) in enumerate(sorted(review.properties(detail).items()),1)}
    packet=review.challenge_plan(plan);field=next(f for f in packet['fields'] if f['path'][1]==reason)
    response=dict(candidate_hash=packet['candidate_hash'],input_hash=packet['input_hash'],checked_field_ids=[f['id'] for f in packet['fields']],
        findings=[dict(id=field['id'],quote=field['text'],reason='原资料不包含管理员强制释放，不能扩大范围。',sources=[])])
    outcomes=review.challenge_outcomes(plan,packet,response,{'R1':dict(status='passed',findings=[],checks=[])})
    schema,scopes=detail_correction.contract(raw,outcomes)
    assert scopes['R1']['allowed_write_paths']==[reason] and scopes['R1']['new_object_kinds']==[]
    patch=dict(rows=[dict(row_key='R1',updates=[dict(path=reason,value='按本期已给操作组织流程。')],new_objects=[])])
    corrected=review.apply_correction(raw,patch,schema,outcomes,protocol_version=2)
    assert corrected['requirements'][0]['detail_contract']['applicability']['flow']==dict(applicable=detail['applicability']['flow']['applicable'],reason='按本期已给操作组织流程。')
    for path in ('detail_contract.applicability.flow.applicable','detail_contract.applicability.fields.reason','detail_contract.fields.F1.meaning'):
        bad=copy.deepcopy(patch);bad['rows'][0]['updates'][0]['path']=path
        with pytest.raises(Problem):review.apply_correction(raw,bad,schema,outcomes,protocol_version=2)
    assert raw['requirements'][0]['detail_contract']==detail


def test_scope_reference_keeps_only_saved_answer_identity(tmp_path):
    p,local,excerpts,raw,canonical,report,_=fixture(tmp_path)
    from app.business_draft import _input
    # Build through prepare, including actual current user words and a saved answer.
    question=next(q for q in p['questions'] if local['requirement_id'] in q.get('related_refs',[]))
    question['answer']='原已保存回答，仅本期范围。'
    context=_input(p,excerpts,'临时当前指令，不是已保存决定。',local)
    plan=review.prepare(raw,canonical,report,excerpts,project=p,generation_context=context)
    assert any(s['kind']=='current_user_words' for s in plan['human_statements'])
    saved=[s for s in plan['human_statements'] if s['kind'] in ('saved_answer','saved_group_words')]
    assert any(s['kind']=='saved_answer' for s in saved)
    before=copy.deepcopy(plan)
    packet=review.challenge_plan(plan)
    assert packet['context']['saved_human_statements']==saved
    assert not any(s['kind']=='current_user_words' for s in packet['context']['saved_human_statements'])
    assert len(packet['fields'])==len(plan['rows'][0]['property_refs'])
    assert len([f for f in packet['fields'] if f.get('reference_policy')])==8
    assert all('reference_policy' not in f for f in packet['fields'] if '.applicability.' not in f['path'][1])
    assert plan==before
