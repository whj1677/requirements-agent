import copy
import pytest
from app.core import Problem
from app.prewrite import validate_result
from app.prewrite_repair import prepare, apply, messages
from app.provider import input_metrics
from tests.test_prewrite_consistency import isolated_store, review, QUESTION


def broken(p):
    value=review(p,blocker=True);f=value['findings'][0]
    f.update(severity='blocker',decision_question='是否需要先查询原请求？',
        evidence_quotes=[dict(object_id='REQ-0001',path='statement',quote='模型概括而非原文')])
    value['findings'].append(dict(type='other',severity='blocker',message='另一项独立检查',
        suggested_resolution='核对显示方式',related_refs=['REQ-0001'],source_refs=[],decision_question='结果如何显示？'))
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
    if fault=='wrong_finding':patch['decisions'][0]['finding']=99
    with pytest.raises(Problem):apply(plan,patch)


def test_unknown_location_and_valid_quote_are_not_editable(tmp_path):
    _,p=isolated_store(tmp_path);value=broken(p)
    value['findings'][0]['evidence_quotes'][0]['path']='does_not_exist'
    assert prepare(p,value) is None
    value=broken(p);value['findings'][0]['evidence_quotes'][0]['quote']=p['items'][0]['statement']
    plan=prepare(p,value);assert plan['quote_slots']==[]
    with pytest.raises(Problem):apply(plan,patch_for(p))


def test_prewrite_current_node_is_distinct_from_deferred_history_and_quote_stays_strict(tmp_path):
    import json
    from app.provider import assemble,DEFAULT
    from app.prewrite import review_project
    _,p=isolated_store(tmp_path)
    root=p['items'][0]
    current=root['detail_contract']['flow']['nodes'][0]
    current['label']='用户点击刷新状态'
    historical=copy.deepcopy(root)
    historical.update(id='REQ-old-proposal',action='revise',target_item_id=root['id'],selection_status='deferred')
    historical['detail_contract']['flow']['nodes'][0]['label']='用户点击刷新取得确定结果'
    p['items'].append(historical)
    before=copy.deepcopy(p)
    messages,_,_=assemble(review_project(p),'review','',DEFAULT,tmp_path)
    context=json.loads(messages[1]['content'][0]['text'])
    assert root['id'] in context['review_scope']['normative_item_ids']
    assert historical['id'] not in context['review_scope']['normative_item_ids']
    assert next(i for i in context['items'] if i['id']==root['id'])['detail_contract']['flow']['nodes'][0]['label']==current['label']
    assert '不得把旧候选的文字引用到当前原条目ID下' in context['review_scope']['instruction']
    value=broken(p);value['result']['required_decisions']=[]
    value['findings']=value['findings'][:1]
    value['findings'][0]['evidence_quotes']=[dict(object_id=root['id'],
        path='detail_contract.flow.nodes.'+current['id']+'.label',quote='用户点击刷新取得确定结果')]
    repair=prepare(p,value)
    assert repair['quote_slots'][0]['actual_text']=='用户点击刷新状态'
    with pytest.raises(Problem,match='覆盖且仅覆盖错误引文'):
        apply(repair,dict(quotes=[],decisions=[]))
    assert p==before


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
    value['findings']=value['findings'][:1]
    value['findings'][0]['severity']='warning'
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
