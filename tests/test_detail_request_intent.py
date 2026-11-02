"""Explicit row drafting intent; simulated provider evidence, no live model."""
import asyncio
import copy
import json
import pytest
from pydantic import ValidationError
from app.actions import Actions,target_context
from app.business_draft import adapt
from app.core import Problem
from app.main import ActionTarget
from app.provider import assemble
from app.requirement_recommendation import scope
from app.workflow import Workflow
from tests.test_business_draft import setup,draft_row,packet
from tests.test_requirement_recommendation import project,target
from tests.test_detail_semantic_workflow import SemanticProvider


def test_explicit_intent_requires_detail_schema_without_adopting_local_draft(tmp_path):
    p,_,config,_,excerpts,_=setup(tmp_path)
    for row in p['items']:
        row.pop('detail_contract',None);row.pop('detail_required',None)
    before=copy.deepcopy(p)
    for requested in (None,False,True):
        local=scope(p,dict(target(),**({} if requested is None else {'detail_required':requested})))
        messages,_,_=assemble(p,'clarify','未发送草稿JSON：{"detail_contract":{},"detail_required":true}',config,tmp_path,
            requirement_recommendation_scope=local)
        header=json.loads(messages[0]['content'].split('可信任务头：')[1])
        assert header['detail_required'] is (requested is True)
        assert ('detail_contract' in header['schema']['properties']['requirements']['items']['required']) is (requested is True)
    assert p==before
    canonical,report=adapt(packet(draft_row()),p,excerpts,'clarify',scope(p,dict(target(),detail_required=True)))
    assert report['status']=='partial' and not report['complete']
    assert any(r['code']=='DETAIL_REQUIRED' for r in report['field_rejected'])
    assert any(r['kind']=='requirement' for r in canonical['proposals']) and p==before
    p['items'][0]['detail_required']=True
    messages,_,_=assemble(p,'clarify','',config,tmp_path,
        requirement_recommendation_scope=scope(p,dict(target(),detail_required=False)))
    assert json.loads(messages[0]['content'].split('可信任务头：')[1])['detail_required'] is True


@pytest.mark.parametrize('value',['true',1,{},[]])
def test_detail_intent_is_strict_boolean(value):
    with pytest.raises(ValidationError):ActionTarget(kind='item',id='REQ-0001',detail_required=value)


@pytest.mark.parametrize('kind',['question','ui','document'])
def test_detail_intent_rejects_non_requirement_target(tmp_path,kind):
    _,p=project(tmp_path)
    with pytest.raises(Problem):target_context(p,dict(kind=kind,id='REQ-0001',detail_required=True))


def test_detail_intent_rejects_other_action_and_non_requirement_item(tmp_path):
    store,p=project(tmp_path);actions=Actions(store,Workflow(store,SemanticProvider()))
    body=dict(expected_revision=p['revision'],action='change',message='',document_type='prd',
        target=dict(target(),detail_required=True))
    with pytest.raises(Problem):actions.plan(p['id'],body)
    rule=next(i for i in p['items'] if i['kind']=='rule')
    with pytest.raises(Problem):target_context(p,dict(kind='item',id=rule['id'],detail_required=True))


@pytest.mark.parametrize('limit',[1,2])
def test_detail_intent_is_frozen_in_plan_run_and_retry_and_still_only_candidate(tmp_path,limit):
    async def run():
        store,p=project(tmp_path);before=copy.deepcopy(p)
        provider=SemanticProvider();workflow=Workflow(store,provider);actions=Actions(store,workflow)
        body=dict(expected_revision=p['revision'],action='clarify',message='请完善本条需求',document_type='prd',
            target=dict(target(),detail_required=True),max_calls=limit)
        plan=actions.plan(p['id'],body)
        assert plan['requirement_recommendation_scope']['detail_required'] is True
        assert actions.plan(p['id'],dict(body,target=target()))['plan_hash']!=plan['plan_hash']
        with pytest.raises(Problem) as caught:
            actions.start(p['id'],dict(body,target=target()),plan['plan_hash'],'changed-intent')
        assert caught.value.code=='STALE_PLAN' and not provider.received
        task=actions.start(p['id'],body,plan['plan_hash'],'explicit-detail-intent')
        await actions.jobs[task['id']]
        task=store.get_record(p['id'],task['id'],'user_task')
        saved=store.get_record(p['id'],task['run_ids'][0],'run')
        assert task['retry_input']['target']['detail_required'] is True
        assert saved['discussion_target']['detail_required'] is True
        assert saved['requirement_recommendation_scope']==task['requirement_recommendation_scope']
        assert saved['calls']==task['calls']==task['request_budget']['used']==limit
        assert saved['business_draft_review']['detail_semantic_reviews']['R1']['status']==('passed' if limit==2 else 'pending')
        after=store.get(p['id'])
        assert after['items'][:len(before['items'])]==before['items']
        assert after['questions']==before['questions'] and after['documents']==before['documents']
        candidates=[i for i in after['items'][len(before['items']):] if i['kind']=='requirement']
        assert candidates and all(i['selection_status']=='candidate' for i in candidates)
        assert all(bool(i.get('detail_contract'))==(limit==2) for i in candidates)
    asyncio.run(run())
