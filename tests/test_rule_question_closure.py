"""Isolated rule-answer regressions; synthetic decisions never alter daily data."""
import copy

import pytest

from app.core import Problem
from app.understanding import accept_answer_update, annotate_candidates, record_answer, validate
from tests.test_runtime import empty_ingest


def answered_project(kinds=('rule',)):
    items=[dict(id=kind.upper()+'-original',kind=kind) for kind in kinds]
    question=dict(id='Q-rule',status='answered',answer='明确选择仅导出已勾选的任务。',
                  related_refs=[item['id'] for item in items],understanding_status='pending')
    return dict(items=items,questions=[question])


def revision(item):
    return dict(temp_id='TMP-'+item['id'],kind=item['kind'],action='revise',target_item_id=item['id'],
                title='人工回答对应修订',statement='仅导出已勾选的任务。',applies_to='to_be',
                epistemic_status='reported',source_refs=[],related_refs=[],answer_refs=['Q-rule'])


@pytest.mark.parametrize('kind',['rule','acceptance'])
def test_rule_or_acceptance_only_answer_requires_explicit_current_revision(kind):
    p=answered_project((kind,));q=p['questions'][0];candidate=revision(p['items'][0])
    response=empty_ingest();response['proposals']=[candidate]
    validate(response,p,[])
    before=copy.deepcopy(p['items'])
    annotate_candidates(p,[candidate])
    assert q['understanding_status']=='candidate_ready' and p['items']==before
    accept_answer_update(p,candidate)
    assert q['understanding_status']=='applied'
    assert q['applied_item_ids']==[p['items'][0]['id']]
    assert q['applied_requirement_ids']==[]
    record_answer(q);q['answer']='改为导出所有任务。'
    assert q['applied_item_ids']==[]
    with pytest.raises(Problem,match='回答已变化'):
        accept_answer_update(p,candidate)
    assert q['understanding_status']=='pending'


def test_mixed_question_waits_until_each_directly_related_item_is_adopted():
    p=answered_project(('requirement','rule','acceptance'));q=p['questions'][0]
    candidates=[revision(item) for item in p['items']]
    annotate_candidates(p,candidates)
    for candidate in candidates[:-1]:
        accept_answer_update(p,candidate)
        assert q['understanding_status']=='candidate_ready'
    accept_answer_update(p,candidates[-1])
    assert q['understanding_status']=='applied'
    assert q['applied_item_ids']==sorted(item['id'] for item in p['items'])
    assert q['applied_requirement_ids']==['REQUIREMENT-original']


@pytest.mark.parametrize('mutation',['unrelated','wrong_kind','add','unanswered','revision_of_revision'])
def test_answer_binding_cannot_unlock_an_unrelated_or_nonrevision_candidate(mutation):
    p=answered_project();candidate=revision(p['items'][0]);q=p['questions'][0]
    if mutation=='unrelated':q['related_refs']=[]
    elif mutation=='wrong_kind':candidate['kind']='acceptance'
    elif mutation=='add':candidate.update(action='add',target_item_id=None)
    elif mutation=='unanswered':q.update(status='open',answer=None)
    else:p['items'][0]['target_item_id']='RULE-other-original'
    response=empty_ingest();response['proposals']=[candidate]
    with pytest.raises(Problem) as caught:validate(response,p,[])
    assert caught.value.code=='REFERENCE_INVALID'
    with pytest.raises(Problem):accept_answer_update(p,candidate)
    assert q['understanding_status']=='pending'


def test_failed_multi_answer_acceptance_does_not_partially_update_a_question():
    p=answered_project();candidate=revision(p['items'][0])
    second=dict(p['questions'][0],id='Q-changed')
    p['questions'].append(second);candidate['answer_refs'].append(second['id'])
    annotate_candidates(p,[candidate]);second['answer']='更改后的回答'
    before=copy.deepcopy(p)
    with pytest.raises(Problem,match='回答已变化'):accept_answer_update(p,candidate)
    assert p==before


def test_rule_answer_api_candidate_acceptance_updates_stable_rule_and_document(tmp_path):
    from app.contracts import validate_response
    from app.document_reader import reader_document
    from app.prd import compile_plan
    from app.product_flow import BEHAVIOR, clarification_focus, missing
    from app.workflow import apply_response
    from tests.helpers import prepared
    from tests.test_runtime import client

    c,app=client(tmp_path);store=app.state.store;p=prepared(store)
    with store.edit(p['id'],p['revision'],'isolated rule-only decision') as (p,_):
        p['product_context']={'scope_ids':['REQ-0001']}
        p['items'][0]['behavior']={key:'合成已知' for key in BEHAVIOR}
        p['questions'][0].update(related_refs=['RULE-0001'],blocking=True)
    root='/api/projects/'+p['id']
    with c:
        result=c.post(root+'/questions/Q-0001',json={'expected_revision':p['revision'],'answer':'相接边界允许，实际重叠仍拒绝。'})
        assert result.status_code==200,result.text
        p=store.get(p['id']);q=p['questions'][0]
        assert q['understanding_status']=='pending'
        assert any('修订仍待核对' in issue for issue in missing(p,3))
        target=next(row for row in clarification_focus(p)['answer_revision_targets'] if row['question_id']==q['id'])
        assert target['item_ids']==['RULE-0001'] and target['requirement_ids']==[]

        original=copy.deepcopy(p['items'][1]);candidate=revision(original)
        candidate.update(answer_refs=[q['id']],statement=q['answer'],source_refs=q['answer_source_refs'],related_refs=['REQ-0001'])
        response=empty_ingest();response['proposals']=[candidate];response['used_source_refs']=q['answer_source_refs']
        validate_response(response,'ingest',p,[e for source in p['sources'] for e in source['excerpts']])
        with store.edit(p['id'],p['revision'],'synthetic grounded revision proposal') as (p,_):
            apply_response(p,response)
        assert p['items'][1]==original
        assert p['questions'][0]['understanding_status']=='candidate_ready'
        proposal=p['items'][-1]
        result=c.post(root+'/items/'+proposal['id'],json={'expected_revision':p['revision'],'selection_status':'selected'})
        assert result.status_code==200,result.text
        p=store.get(p['id']);q=p['questions'][0]
        assert p['items'][1]['id']=='RULE-0001' and p['items'][1]['statement']==q['answer']
        assert p['items'][1]['source_refs']==q['answer_source_refs']
        assert p['items'][-1]['selection_status']=='deferred'
        assert q['understanding_status']=='applied' and q['applied_item_ids']==['RULE-0001']
        assert not any('修订仍待核对' in issue for issue in missing(p,3))

        plan=dict(plan_version='2',sections=[dict(section_key='function',context_refs=[],
                  normative_refs=['REQ-0001','RULE-0001','AC-0001'],discussion_refs=[])])
        apply_response(p,compile_plan(plan,p,'prd'))
        content=reader_document(p['documents']['prd'])
        text=[b['text'] for s in content['sections'] for b in s['blocks'] if b['ref_ids']==[q['id']]]
        assert any(value.startswith('已应用于当前草稿条款的回答：') for value in text)


def test_legacy_applied_requirement_ids_are_retained_when_adopting_related_rule():
    p=answered_project(('requirement','rule'));q=p['questions'][0]
    q.update(understanding_status='applied',applied_requirement_ids=['REQUIREMENT-original'])
    candidate=revision(p['items'][1]);annotate_candidates(p,[candidate]);accept_answer_update(p,candidate)
    assert q['understanding_status']=='applied'
    assert q['applied_item_ids']==['REQUIREMENT-original','RULE-original']


@pytest.mark.parametrize('kind',['rule','acceptance'])
def test_reference_requires_explicit_scope_decision_and_keeps_source_identity(tmp_path,kind):
    from app.document_reader import export_readiness
    from app.prd import compile_plan
    from app.requirements import delivery_items
    from app.workflow import apply_response
    from tests.helpers import prepared
    from tests.test_runtime import client

    c,app=client(tmp_path);store=app.state.store;p=prepared(store)
    with store.edit(p['id'],p['revision'],'synthetic reference rule') as (p,_):
        p['product_context']={'scope_ids':['REQ-0001']}
        p['questions']=[]
        item=next(i for i in p['items'] if i['kind']==kind)
        item.update(applies_to='reference',selection_status='candidate')
        iid=item['id']
    before=copy.deepcopy(item);root='/api/projects/'+p['id']
    with c:
        selected=c.post(root+'/items/'+iid,json={'expected_revision':p['revision'],'selection_status':'selected'})
        assert selected.status_code==200,selected.text
        p=store.get(p['id']);item=next(i for i in p['items'] if i['id']==iid)
        assert item['applies_to']=='reference' and iid not in {i['id'] for i in delivery_items(p)}
        plan=dict(plan_version='2',sections=[dict(section_key='function',context_refs=[],
                  normative_refs=['REQ-0001'],discussion_refs=[])])
        with store.edit(p['id'],p['revision'],'synthetic current document',bump=False) as (p,_):
            apply_response(p,compile_plan(plan,p,'prd'))
        assert export_readiness(p,'prd')['ready']
        prior_version=next(i for i in p['items'] if i['id']==iid).get('content_version',1)
        decision='业务负责人明确将此参考约束用于本期导出。'
        result=c.post(root+'/items/'+iid+'/include-scope',json={'expected_revision':p['revision'],'reason':decision})
        assert result.status_code==200,result.text
        p=store.get(p['id']);item=next(i for i in p['items'] if i['id']==iid)
        assert item['applies_to']=='to_be' and item['selection_status']=='selected'
        assert item['id']==before['id'] and item['statement']==before['statement']
        assert all(ref in item['source_refs'] for ref in before['source_refs'])
        assert iid in {i['id'] for i in delivery_items(p)}
        assert item['content_version']==prior_version+1
        assert item['scope_decision']['previous_applies_to']=='reference'
        assert any(decision in excerpt['text'] for source in p['sources'] for excerpt in source['excerpts'])
        readiness=c.get(root).json()['document_export_readiness']['prd']
        assert not readiness['ready'] and any(issue['code']=='STALE_REVISION' for issue in readiness['issues'])
        download=c.get(root+'/documents/prd/zip')
        assert download.status_code==409 and download.json()['code']=='STALE_REVISION'
        repeat=c.post(root+'/items/'+iid+'/include-scope',json={'expected_revision':p['revision'],'reason':decision})
        assert 400<=repeat.status_code<500
        assert store.get(p['id'])==p


@pytest.mark.parametrize('invalid',['blank_reason','unlinked','revision_candidate','requirement','stale_revision'])
def test_reference_inclusion_rejects_ambiguous_scope_without_mutation(tmp_path,invalid):
    from tests.helpers import prepared
    from tests.test_runtime import client

    c,app=client(tmp_path);store=app.state.store;p=prepared(store)
    with store.edit(p['id'],p['revision'],'synthetic scope counterexample') as (p,_):
        p['product_context']={'scope_ids':['REQ-0001']}
        item=p['items'][1];item['applies_to']='reference'
        if invalid=='unlinked':
            item['related_refs']=[];p['items'][0]['related_refs']=[]
        elif invalid=='revision_candidate':item['target_item_id']='RULE-0001'
        elif invalid=='requirement':item['kind']='requirement'
        iid=item['id']
    before=copy.deepcopy(p)
    payload={'expected_revision':p['revision']-(1 if invalid=='stale_revision' else 0),
             'reason':'   ' if invalid=='blank_reason' else '明确纳入本期。'}
    with c:
        result=c.post('/api/projects/'+p['id']+'/items/'+iid+'/include-scope',json=payload)
        assert 400<=result.status_code<500,result.text
        assert store.get(p['id'])==before
