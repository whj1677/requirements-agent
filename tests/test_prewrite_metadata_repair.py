import copy
import pytest
from app.core import Problem
from app.prewrite import validate_result
from app.prewrite_repair import prepare, apply, messages
from app.provider import input_metrics
from tests.test_prewrite_consistency import isolated_store, review, QUESTION


def broken(p):
    value=review(p,blocker=True);f=value['findings'][0]
    f.update(severity='warning',decision_question='是否需要先查询原请求？',
        evidence_quotes=[dict(object_id='REQ-0001',path='statement',quote='模型概括而非原文')])
    return value


def patch_for(p):
    return dict(quotes=[dict(finding=0,quote=0,text=p['items'][0]['statement'])],
                decisions=[dict(decision=0,finding=0)])


def test_only_invalid_slots_change_and_human_decision_still_required(tmp_path):
    _,p=isolated_store(tmp_path);value=broken(p);before=copy.deepcopy(value)
    plan=prepare(p,value);fixed=apply(plan,patch_for(p))
    validate_result(p,fixed);assert value==before
    assert fixed['findings'][0]['message']==before['findings'][0]['message']
    assert fixed['findings'][0]['decision_question']==QUESTION
    assert fixed['result']==before['result']
    assert input_metrics(messages(plan))


@pytest.mark.parametrize('fault',['invent_quote','new_slot','extra_business','omit_decision','wrong_finding'])
def test_patch_cannot_invent_or_change_other_slots(tmp_path,fault):
    _,p=isolated_store(tmp_path);plan=prepare(p,broken(p));patch=patch_for(p)
    if fault=='invent_quote':patch['quotes'][0]['text']='凭空编造'
    if fault=='new_slot':patch['quotes'][0]['quote']=1
    if fault=='extra_business':patch['result']={'assessment':'ready_for_human_review'}
    if fault=='omit_decision':patch['decisions']=[]
    if fault=='wrong_finding':patch['decisions'][0]['finding']=1
    with pytest.raises(Problem):apply(plan,patch)


def test_unknown_location_and_valid_quote_are_not_editable(tmp_path):
    _,p=isolated_store(tmp_path);value=broken(p)
    value['findings'][0]['evidence_quotes'][0]['path']='does_not_exist'
    assert prepare(p,value) is None
    value=broken(p);value['findings'][0]['evidence_quotes'][0]['quote']=p['items'][0]['statement']
    plan=prepare(p,value);assert plan['quote_slots']==[]
    with pytest.raises(Problem):apply(plan,patch_for(p))


@pytest.mark.parametrize('invalid_patch',[False,True])
def test_workflow_repairs_once_and_never_auto_confirms(tmp_path,invalid_patch):
    from tests.test_prewrite_consistency import run_action
    store,p=isolated_store(tmp_path);value=broken(p);patch=patch_for(p)
    if invalid_patch:patch['quotes'][0]['text']='模型再次改写了事实'
    task,provider,_=run_action(store,p,[value,patch],action='prewrite')
    assert len(provider.received)==2
    assert store.get(p['id'])['items']==p['items']
    assert not store.get(p['id'])['documents']
    assert task['status']==('failed' if invalid_patch else 'awaiting_user'),task


def test_unverified_optional_comment_does_not_interrupt_or_fabricate_a_quote(tmp_path):
    from app.prewrite import normalize_quotes
    from tests.test_prewrite_consistency import run_action
    store,p=isolated_store(tmp_path);value=broken(p)
    value['result'].update(assessment='ready_for_human_review',required_decisions=[])
    value['findings'][0].pop('decision_question')
    normalized,changes=normalize_quotes(p,value)
    assert changes and not normalized['findings'][0]['evidence_quotes']
    assert '引文未核实' in normalized['findings'][0]['message']
    task,provider,_=run_action(store,p,[value],action='prewrite')
    assert task['status']=='succeeded' and len(provider.received)==1
    assert store.get(p['id'])['items']==p['items']
    # The same broken citation on a blocking review still needs repair.
    value['result']['assessment']='needs_changes'
    normalized,changes=normalize_quotes(p,value)
    assert not changes
    with pytest.raises(Problem):validate_result(p,normalized)
