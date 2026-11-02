"""Explicit per-row completion detail intent, isolated from ordinary text."""
import copy
import json
import jsonschema
import pytest
from app.business_draft import assemble,adapt
from app.refinement_completion import validate,detail_rows
from app.core import Problem
from tests.test_business_draft import setup,packet,draft_row
from tests.test_detail_semantic_workflow import generated_detail


def two_rows(tmp_path):
    p,_,config,_,excerpts,_=setup(tmp_path)
    p['items']=[item for item in p['items'] if item['kind']!='requirement' or item['id']==p['items'][0]['id']]
    second=copy.deepcopy(p['items'][0]);second.update(id='REQ-second',title='另一条本期需求',target_item_id=None)
    second.pop('detail_contract',None);second.pop('detail_required',None)
    p['items'].append(second)
    return p,config,excerpts


@pytest.mark.parametrize('mode',['all','required'])
def test_only_requested_root_requires_generation_detail_and_references_stay_bound(tmp_path,mode):
    p,config,_=two_rows(tmp_path);before=copy.deepcopy(p)
    request=dict(mode=mode,detail_required_ids=[p['items'][0]['id']])
    messages,_,_=assemble(p,'ingest','已填答案保持原话',config,draft_request=request)
    header=json.loads(messages[0]['content'].split('可信任务头：',1)[1]);context=json.loads(messages[1]['content'][0]['text'])
    assert header['detail_required'] and header['detail_required_row_keys']==['R1']
    assert [(r['row_key'],r['detail_required']) for r in context['current_rows']]==[('R1',True),('R2',False)]
    row_schema=header['schema']['properties']['requirements']['items']
    first=draft_row('R1');second=draft_row('R2')
    # Validate only detail-intent conditions; full completion business values
    # are covered by existing completion tests.
    constraint={'anyOf':row_schema['anyOf']}
    with pytest.raises(jsonschema.ValidationError):jsonschema.validate(first,constraint)
    first['detail_contract']=generated_detail();jsonschema.validate(first,constraint)
    jsonschema.validate(second,constraint)
    second['detail_contract']=generated_detail()
    with pytest.raises(jsonschema.ValidationError):jsonschema.validate(second,constraint)
    selected_schema=row_schema['anyOf'][0]['properties']['detail_contract']
    refs=selected_schema['properties']['interactions']['items']['properties']['rule_refs']['items']
    assert 'enum' in refs or refs=={'not':{}}
    assert context['user_message']=='已填答案保持原话' and p==before


def test_omitted_required_detail_stays_missing_without_enabling_unrequested_rows(tmp_path):
    p,_,excerpts=two_rows(tmp_path);before=copy.deepcopy(p)
    request=dict(mode='all',detail_required_ids=[p['items'][0]['id']])
    canonical,report=adapt(packet(draft_row('R1'),draft_row('R2')),p,excerpts,'ingest',draft_request=request)
    first=next(x for x in report['accepted'] if x['row_key']=='R1')
    second=next(x for x in report['accepted'] if x['row_key']=='R2')
    assert any(x['code']=='DETAIL_REQUIRED' for x in first['field_rejected'])
    assert not second['field_rejected']
    proposals={x['temp_id']:x for x in canonical['proposals']}
    assert proposals[first['candidate_temp_id']]['detail_required']
    assert not proposals[second['candidate_temp_id']].get('detail_required')
    assert p==before


def test_unrequested_detail_is_not_silently_enabled(tmp_path):
    p,_,excerpts=two_rows(tmp_path);row=draft_row('R2');row['detail_contract']=generated_detail()
    canonical,report=adapt(packet(row),p,excerpts,'ingest',draft_request=dict(mode='all',detail_required_ids=[]))
    assert any(x['code']=='DETAIL_NOT_REQUESTED' for x in report['field_rejected'])
    assert all(not x.get('detail_required') and not x.get('detail_contract') for x in canonical['proposals'] if x['kind']=='requirement')


def test_empty_intent_preserves_saved_details_and_legacy_is_unchanged(tmp_path):
    p,config,_=two_rows(tmp_path);p['items'][0]['detail_required']=True
    assert detail_rows(p,dict(mode='all')) is None
    assert detail_rows(p,dict(mode='all',detail_required_ids=[]))==['R1']
    p['items'][-1]['detail_contract']={}
    assert detail_rows(p,dict(mode='required',detail_required_ids=[]))==['R1','R2']


@pytest.mark.parametrize('bad',['duplicate','foreign','alias','nonlist','nonstring','question','prewrite','as_is','superseded','revision_candidate'])
def test_invalid_detail_intent_rejected_before_planning(tmp_path,bad):
    p,_,_=two_rows(tmp_path);identity=p['items'][0]['id'];request=dict(mode='all',detail_required_ids=[identity])
    if bad=='duplicate':request['detail_required_ids']=[identity,identity]
    elif bad in ('foreign','alias'):request['detail_required_ids']=['REQ-foreign' if bad=='foreign' else 'R1']
    elif bad=='nonlist':request['detail_required_ids']=identity
    elif bad=='nonstring':request['detail_required_ids']=[None]
    elif bad in ('question','prewrite'):request['mode']=bad
    elif bad=='as_is':p['items'][0]['applies_to']='as_is'
    elif bad=='superseded':p['items'][0]['superseded_by']='REQ-new'
    else:p['items'][0]['target_item_id']='REQ-origin'
    with pytest.raises(Problem) as caught:validate(p,request)
    assert caught.value.code=='DRAFT_REQUEST_INVALID'


def test_changing_scoped_intent_invalidates_action_plan(tmp_path):
    from app.actions import Actions
    from app.workflow import Workflow
    from tests.test_requirement_recommendation import project
    from tests.test_business_draft_workflow import BusinessProvider
    store,p=project(tmp_path);provider=BusinessProvider([]);actions=Actions(store,Workflow(store,provider))
    body=dict(expected_revision=p['revision'],action='organize',message='',document_type='prd',
        draft_request=dict(mode='all',detail_required_ids=[p['items'][0]['id']]))
    plan=actions.plan(p['id'],body)
    assert plan['detail_required_rows']==[dict(requirement_id=p['items'][0]['id'],row_key='R1',title=p['items'][0]['title'])]
    body['draft_request']['detail_required_ids']=[]
    with pytest.raises(Problem) as caught:actions.start(p['id'],body,plan['plan_hash'],'different-detail-selection',True)
    assert caught.value.code=='STALE_PLAN' and not provider.received


def test_action_freezes_intent_into_task_retry_run_and_trusted_model_input(tmp_path):
    import asyncio
    from app.actions import Actions
    from app.workflow import Workflow
    from tests.test_requirement_recommendation import project
    from tests.test_business_draft_workflow import BusinessProvider
    async def run():
        store,p=project(tmp_path);provider=BusinessProvider([packet(draft_row('R1'),draft_row('R2'))])
        workflow=Workflow(store,provider);actions=Actions(store,workflow)
        request=dict(mode='all',detail_required_ids=[p['items'][0]['id']])
        body=dict(expected_revision=p['revision'],action='organize',message='Q1手工作答原话',document_type='prd',draft_request=request)
        plan=actions.plan(p['id'],body);task=actions.start(p['id'],body,plan['plan_hash'],'scoped-detail-freeze',True)
        await actions.jobs[task['id']]
        saved=store.get_record(p['id'],task['id'],'user_task');run=store.records(p['id'],'run')[-1]
        assert saved['draft_request']==saved['retry_input']['draft_request']==run['draft_request']==request
        header=json.loads(provider.received[0][0]['content'].split('可信任务头：',1)[1])
        context=json.loads(provider.received[0][1]['content'][0]['text'])
        assert header['detail_required_row_keys']==['R1'] and context['current_rows'][0]['detail_required']
        assert not context['current_rows'][1]['detail_required'] and 'Q1手工作答原话' in context['user_message']
        assert any(x['code']=='DETAIL_REQUIRED' for x in run['business_draft_review']['field_rejected'])
        assert all(item.get('selection_status')!='selected' for item in store.get(p['id'])['items'][len(p['items']):])
    asyncio.run(run())
