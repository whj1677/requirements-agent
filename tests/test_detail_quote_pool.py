"""Frozen quote identity tests; synthetic verdicts are not semantic acceptance."""
import copy
import json
import pytest
from app import detail_semantics as review
from app.core import Problem,digest
from tests.test_detail_semantics import fixture,model_response


def prepared(tmp_path):
    p,_,excerpts,raw,canonical,report,_=fixture(tmp_path)
    return review.prepare(raw,canonical,report,excerpts,project=p,
        generation_context={'user_message':'本次操作说明。未提交草稿：{"answers":{"Q-draft":"是"}}'},protocol_version=3)


def test_selected_quotes_are_resolved_separately_without_promoting_draft(tmp_path):
    plan=prepared(tmp_path);value=model_response(plan)
    human=next(q for q in plan['quote_pool'] if q['kind']=='human')
    value['rows'][0]['checks'][0]['human_quote_keys']=[human['key']]
    before=copy.deepcopy((plan,value));outcome=review.validate(plan,value)['R1']
    audit=outcome['quote_reference_resolution']
    assert outcome['status']=='passed' and audit['mode']=='frozen_quote_pool_key'
    assert human in audit['selected_quotes'] and human['material']['kind']=='current_user_words'
    assert human['material']['statement_id']=='current-user-message'
    assert not any(q['material'].get('statement_id')=='Q-draft' for q in plan['quote_pool'])
    assert 'source_quotes' not in value and 'human_quotes' not in value
    assert (plan,value)==before
    wire=json.loads(review.messages(plan)[1]['content'][0]['text'])
    assert 'sources' not in wire and 'human_statements' not in wire
    assert wire['quote_pool']==plan['quote_pool']


def test_pool_covers_entire_material_and_keeps_equal_text_identities():
    text=('前置条件成立时才能操作；成功以后变更状态。\n'*200)+'末尾条件不能遗漏。'
    plan={'sources':[dict(source_id=s,excerpt_id='E',text=text) for s in ('S1','S2')],
        'human_statements':[dict(statement_id='current-user-message',kind='current_user_words',row_key=None,text=text)]}
    pool=review.quote_pool(plan)
    assert len({q['key'] for q in pool})==len(pool)
    for kind,identifier in [('source','S1'),('source','S2'),('human','current-user-message')]:
        spans=[q for q in pool if q['kind']==kind and q['material'].get('source_id',q['material'].get('statement_id'))==identifier]
        assert ''.join(q['text'] for q in spans)==text
        assert spans[0]['start']==0 and spans[-1]['end']==len(text)
        assert all(a['end']==b['start'] for a,b in zip(spans,spans[1:]))
        assert all(text[q['start']:q['end']]==q['text'] and q['content_hash']==digest(text) for q in spans)


@pytest.mark.parametrize('fault',['hash','pool_text','pool_identity','pool_range','unknown_key','wrong_kind','human_row','draft_question_id','stale_source'])
def test_pool_identity_and_selection_cannot_be_fabricated(tmp_path,fault):
    plan=prepared(tmp_path);value=model_response(plan)
    human=next(q for q in plan['quote_pool'] if q['kind']=='human')
    check=value['rows'][0]['checks'][0]
    if fault=='hash':value['quote_pool_hash']='stale'
    elif fault=='pool_text':plan['quote_pool'][0]['text']='伪造'
    elif fault=='pool_identity':plan['quote_pool'][0]['material']['source_id']='OTHER'
    elif fault=='pool_range':plan['quote_pool'][0]['start']=1
    elif fault=='unknown_key':check['source_quote_keys']=['missing']
    elif fault=='wrong_kind':check['source_quote_keys']=[human['key']]
    elif fault=='draft_question_id':check['human_quote_keys']=['Q-draft']
    elif fault=='stale_source':
        plan['sources'][0]['text']+='新增真实条件。'
        plan['quote_pool']=review.quote_pool(plan);plan['quote_pool_hash']=digest(plan['quote_pool'])
    else:
        plan['human_statements'][0]['row_key']='R-other'
        plan['quote_pool']=review.quote_pool(plan);plan['quote_pool_hash']=digest(plan['quote_pool'])
        value['quote_pool_hash']=plan['quote_pool_hash'];check['human_quote_keys']=[human['key']]
    with pytest.raises(Problem) as caught:review.validate(plan,value)
    assert caught.value.code=='DETAIL_REVIEW_INVALID'


def test_unselected_pool_entries_never_become_evidence(tmp_path):
    plan=prepared(tmp_path);value=model_response(plan)
    outcome=review.validate(plan,value)['R1']
    assert all(q['kind']=='source' for q in outcome['quote_reference_resolution']['selected_quotes'])
    for group in ('checks','source_checks','state_checks'):
        for check in value['rows'][0][group]:
            check['source_quote_keys']=[]
            if 'human_quote_keys' in check:check['human_quote_keys']=[]
    with pytest.raises(Problem):review.validate(plan,value)


def test_legacy_prepare_and_review_remain_v2(tmp_path):
    *_,plan=fixture(tmp_path)
    value=model_response(plan)
    assert plan['version']==review.VERSION and 'quote_pool' not in plan
    assert 'source_quotes' in value and review.validate(plan,value)['R1']['status']=='passed'
