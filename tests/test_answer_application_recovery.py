"""Regression for the real trial's constraint mismatch and human recovery gap."""
import copy
import asyncio

import pytest
from fastapi.testclient import TestClient

from app.core import Problem, digest
from app.ingest import preserve_response, response_anchor
from app.main import create_app
from app.provider import answer_binding_tasks
from app.provider import DEFAULT, origin
from app.store import Store
from app.workflow import Workflow
from app.understanding import (answer_target, answer_targets, application_hash,
                              confirm_manual_answer, invalidate_answer_applications)
from tests.helpers import prepared
from tests.test_answer_binding_repair import acceptance_revision, project_with_answer
from tests.test_runtime import FakeProvider


def answered_project(tmp_path):
    store=Store(tmp_path)
    p=prepared(store)
    q=p['questions'][0]
    q.update(question='保存时段重叠时怎样处理？', status='answered',
             answer=p['items'][1]['statement'],understanding_status='pending',
             related_refs=['RULE-0001'],answer_source_refs=copy.deepcopy(p['items'][1]['source_refs']))
    return p,q


def implementation_map(p, q):
    return {q['id']: [dict(item_id=i['id'], field='statement', quote=i['statement'])
                     for i in answer_targets(p, q)]}


def test_constraint_has_typed_binding_and_wrong_kind_still_rejected(tmp_path):
    p,q=answered_project(tmp_path)
    item=next(i for i in p['items'] if i['id']=='RULE-0001')
    item['kind']='constraint'
    assert answer_targets(p,q)==[item]
    assert answer_binding_tasks(p)[0]['kind']=='constraint'
    candidate=dict(item,action='revise',target_item_id=item['id'])
    assert answer_target(p,q,candidate)==item
    candidate['kind']='rule'
    assert answer_target(p,q,candidate) is None


def test_structural_kind_repair_keeps_target_content_and_valid_binding(tmp_path):
    _,p=project_with_answer(tmp_path,acceptance_related=True)
    target=next(i for i in p['items'] if i['id']=='AC-0001')
    target['kind']='constraint'
    original=acceptance_revision(p,valid_binding=True)
    original['proposals'][0]['kind']='rule'
    repaired=copy.deepcopy(original)
    repaired['proposals'][0]['kind']='constraint'
    excerpts=p['sources'][0]['excerpts']
    anchor=response_anchor(original,excerpts,'clarify',p)
    preserve_response(anchor,repaired,excerpts,'clarify',original,p)
    for field,value in [('statement','无依据新业务规则'),('target_item_id','REQ-0001'),('action','add'),('answer_refs',[])]:
        changed=copy.deepcopy(repaired);changed['proposals'][0][field]=value
        with pytest.raises(Problem):preserve_response(anchor,changed,excerpts,'clarify',original,p)


def test_workflow_repairs_wrong_constraint_kind_and_preserves_human_adoption(tmp_path):
    async def scenario():
        store,p=project_with_answer(tmp_path,acceptance_related=True)
        with store.edit(p['id'],p['revision'],'合成约束与授权',bump=False) as (draft,_):
            next(i for i in draft['items'] if i['id']=='AC-0001')['kind']='constraint'
            draft['grants'][origin(DEFAULT)]={'source_ids':[s['id'] for s in draft['sources']]}
        p=store.get(p['id'])
        invalid=acceptance_revision(p,valid_binding=True)
        invalid['proposals'][0]['kind']='rule'
        corrected=copy.deepcopy(invalid);corrected['proposals'][0]['kind']='constraint'
        workflow=Workflow(store,FakeProvider([invalid,corrected]))
        run=workflow.start(p['id'],p['revision'],'clarify','合成约束回答修复')
        await workflow.tasks[run['id']]
        return store,p,store.get_record(p['id'],run['id'],'run')
    store,p,run=asyncio.run(scenario())
    assert run['result_applied'] and run['calls']==2 and run['repair_count']==1
    saved=store.get(p['id'])
    candidate=next(i for i in saved['items'] if i.get('target_item_id')=='AC-0001')
    assert candidate['kind']=='constraint' and candidate['selection_status']=='candidate'
    assert candidate['answer_refs']==['Q-0001']
    assert saved['questions'][0]['understanding_status']=='candidate_ready'


@pytest.mark.parametrize('mutation',['unchecked','stale_answer','stale_clause','wrong_target','missing_target','unselected','unreviewed_behavior'])
def test_manual_recovery_rejects_missing_review_and_stale_or_wrong_identity(tmp_path,mutation):
    p,q=answered_project(tmp_path);item=answer_targets(p,q)[0]
    a=digest(q['answer']);hashes={item['id']:application_hash(item)};checked=True
    if mutation=='unchecked':checked=False
    elif mutation=='stale_answer':q['answer']='已变更回答'
    elif mutation=='stale_clause':item['statement']='已变更原条款'
    elif mutation=='wrong_target':hashes={'REQ-0001':next(iter(hashes.values()))}
    elif mutation=='missing_target':hashes={}
    elif mutation=='unselected':item['selection_status']='candidate'
    else:item['behavior_review_required']=True;item['behavior_review']={}
    before=copy.deepcopy(p)
    with pytest.raises(Problem):confirm_manual_answer(p,q,a,hashes,checked)
    assert p==before


def test_manual_recovery_retains_business_truth_and_invalidates_after_clause_edit(tmp_path):
    p,q=answered_project(tmp_path);item=answer_targets(p,q)[0]
    before=copy.deepcopy(item)
    confirm_manual_answer(p,q,digest(q['answer']),{item['id']:application_hash(item)},True,
                          answer_implementations=implementation_map(p,q))
    assert q['understanding_status']=='applied' and item==before
    assert q['answer_applications'][item['id']]['method']=='manual_review'
    item['statement']='新版本条款，需要重新核对是否仍包含回答'
    invalidate_answer_applications(p)
    assert q['understanding_status']=='pending' and q['applied_item_ids']==[]
    assert answer_binding_tasks(p)[0]['target_item_id']==item['id']


def test_http_manual_recovery_no_model_and_stale_page_conflicts(tmp_path):
    app=create_app(tmp_path,access_token='synthetic-login',env_path=tmp_path/'.env')
    p=prepared(app.state.store)
    pid=p['id']
    with app.state.store.edit(pid,p['revision'],'合成已保存回答') as (draft,_):
        q=draft['questions'][0]
        q.update(question='保存时段重叠时怎样处理？',status='answered',
                 answer=draft['items'][1]['statement'],understanding_status='pending',related_refs=['RULE-0001'])
    qid=app.state.store.get(pid)['questions'][0]['id'];path=f'/api/projects/{pid}/questions/{qid}/application-review'
    with TestClient(app) as c:
        csrf=c.post('/api/session/login',json={'key':'synthetic-login'}).json()['csrf']
        c.headers.update({'Origin':'http://testserver','X-CSRF-Token':csrf})
        review=c.get(path).json()
        body=dict(expected_revision=review['expected_revision'],answer_hash=review['answer_hash'],
                  clause_hashes={t['item']['id']:t['clause_hash'] for t in review['targets']},review_confirmed=True,
                  answer_implementations={qid:[dict(item_id=t['item']['id'],field='statement',
                                                   quote=t['item']['statement']) for t in review['targets']]})
        assert c.post(path,json={**body,'review_confirmed':False}).status_code==409
        result=c.post(path,json=body)
        assert result.status_code==200,result.text
        assert result.json()['questions'][0]['understanding_status']=='applied'
        assert c.post(path,json=body).status_code==409
        assert app.state.store.records(pid,'run')==[]
        current=app.state.store.get(pid)
        with app.state.store.edit(pid,current['revision'],'合成需求变更') as (draft,_):
            next(i for i in draft['items'] if i['id']=='RULE-0001')['statement']='修订规则'
        assert app.state.store.get(pid)['questions'][0]['understanding_status']=='pending'


def test_legacy_application_invalidates_when_original_clause_changes(tmp_path):
    p,q=answered_project(tmp_path)
    q.update(understanding_status='applied',applied_item_ids=['RULE-0001'])
    before=copy.deepcopy(p)
    answer_targets(p,q)[0]['statement']='已修订规则，需要再次核对'
    invalidate_answer_applications(p,before)
    assert q['understanding_status']=='pending' and q['applied_item_ids']==[]


def test_new_direct_target_does_not_inherit_previous_application(tmp_path):
    p,q=answered_project(tmp_path)
    confirm_manual_answer(p,q,digest(q['answer']),{i['id']:application_hash(i) for i in answer_targets(p,q)},True,
                          answer_implementations=implementation_map(p,q))
    q['related_refs'].append('AC-0001')
    invalidate_answer_applications(p)
    assert q['understanding_status']=='pending'
    assert 'AC-0001' not in q['applied_item_ids']


def test_current_explicit_review_survives_its_own_clause_revision_transaction(tmp_path):
    p,q=answered_project(tmp_path);before=copy.deepcopy(p)
    item=answer_targets(p,q)[0]
    item['statement']=q['answer']+' 人工修订并核对。'
    confirm_manual_answer(p,q,digest(q['answer']),{item['id']:application_hash(item)},True,
                          answer_implementations=implementation_map(p,q))
    invalidate_answer_applications(p,before)
    assert q['understanding_status']=='applied'
