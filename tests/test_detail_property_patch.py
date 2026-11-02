import asyncio
import copy
import json
import pytest
from app import detail_semantics as review
from app.detail_correction import contract
from app.core import Problem
from tests.test_detail_correction_acceptance import candidate


def setup(tmp_path):
    raw,_,_,outcomes=candidate(tmp_path)
    schema,scopes=contract(raw,outcomes)
    patch=dict(rows=[dict(row_key='R1',updates=[dict(path='detail_contract.fields.F1.required',value='填写时必填')],new_objects=[])])
    return raw,outcomes,schema,scopes,patch


def test_patch_assigns_only_authorized_value_and_preserves_sparse_raw(tmp_path):
    raw,outcomes,schema,scopes,patch=setup(tmp_path);before=copy.deepcopy((raw,patch));assembly=[];normalizations=[]
    result=review.apply_correction(raw,patch,schema,outcomes,protocol_version=2,patch_audit=assembly,normalization_audit=normalizations)
    assert scopes['R1']['allowed_write_paths']==['detail_contract.fields.F1.evidence.required','detail_contract.fields.F1.required']
    assert scopes['R1']['new_object_kinds']==[]
    assert result['requirements'][0]['detail_contract']['fields'][0]['required']=='填写时必填'
    assert result['requirements'][0]['detail_contract']['fields'][0]['evidence']=={}
    assert (raw,patch)==before and assembly[0]['assigned_paths']==['detail_contract.fields.F1.required']
    assert len(normalizations)==2 and assembly[0]['input_candidate_hash']!=assembly[0]['assembled_candidate_hash']


@pytest.mark.parametrize('fault',['wrong_row','duplicate_row','duplicate_path','unknown_path','object_replace','delete','wrong_type','id_write','unallowed_new_kind'])
def test_patch_cannot_expand_write_scope_or_smuggle_operations(tmp_path,fault):
    raw,outcomes,schema,_,patch=setup(tmp_path);row=patch['rows'][0]
    if fault=='wrong_row':row['row_key']='R404'
    elif fault=='duplicate_row':patch['rows'].append(copy.deepcopy(row))
    elif fault=='duplicate_path':row['updates'].append(copy.deepcopy(row['updates'][0]))
    elif fault=='unknown_path':row['updates'][0]['path']='detail_contract.fields.F2.default_value'
    elif fault=='object_replace':row['updates'][0].update(path='detail_contract.fields.F1',value={})
    elif fault=='delete':row['updates'][0]['op']='remove'
    elif fault=='wrong_type':row['updates'][0]['value']={'replacement':'不是字符串'}
    elif fault=='id_write':row['updates'][0]['path']='detail_contract.fields.F1.id'
    else:row['new_objects']=[dict(section='state_transitions',value={'id':'T99'})]
    before=copy.deepcopy((raw,patch))
    with pytest.raises(Problem):review.apply_correction(raw,patch,schema,outcomes,protocol_version=2)
    assert (raw,patch)==before


@pytest.mark.parametrize('collision',[False,True])
def test_new_graph_objects_only_in_existing_graph_authority(tmp_path,collision):
    raw,outcomes,_,_,patch=setup(tmp_path);detail=raw['requirements'][0]['detail_contract']
    outcomes['R1']['findings'].append(dict(dimension='actor_paths',scope='I1',verdict='contradicted',
        affected_paths=['detail_contract.interactions.I1.entry'],traces=[dict(edge_ids=['E1'])]))
    schema,scopes=contract(raw,outcomes)
    node=copy.deepcopy(detail['flow']['nodes'][1]);node['id']='N99' if not collision else detail['flow']['nodes'][0]['id']
    patch['rows'][0]['new_objects']=[dict(section='flow.nodes',value=node)]
    assert scopes['R1']['new_object_kinds']==['flow.nodes','flow.edges']
    if collision:
        with pytest.raises(Problem):review.apply_correction(raw,patch,schema,outcomes,protocol_version=2)
    else:
        result=review.apply_correction(raw,patch,schema,outcomes,protocol_version=2)
        assert result['requirements'][0]['detail_contract']['flow']['nodes'][-1]==node
        # Scope acceptance alone is not topology/semantic acceptance; Workflow still adapts and re-reviews.


@pytest.mark.parametrize('authorized',[False,True])
def test_patch_keeps_exact_source_downgrade_guard(tmp_path,authorized):
    raw,outcomes,_,_,patch=setup(tmp_path)
    raw['requirements'][0]['detail_contract']['fields'][0]['evidence']['required']={'status':'source','sources':[{'source':'S1','locator':'L1'}]}
    if authorized:outcomes['R1']['findings'][0].update(dimension='source_support',verdict='unsupported')
    schema,_=contract(raw,outcomes)
    patch['rows'][0]['updates'].append(dict(path='detail_contract.fields.F1.evidence.required',value={'status':'unknown'}))
    if not authorized:
        with pytest.raises(Problem) as caught:review.apply_correction(raw,patch,schema,outcomes,protocol_version=2)
        assert caught.value.code=='DETAIL_CORRECTION_SCOPE'
    else:assert review.apply_correction(raw,patch,schema,outcomes,protocol_version=2)['requirements'][0]['detail_contract']['fields'][0]['evidence']['required']['status']=='unknown'


def test_patch_cannot_bypass_original_source_alias_validation(tmp_path):
    from tests.test_detail_semantics import fixture
    from app.business_draft import adapt
    p,local,excerpts,raw,_,_,_=fixture(tmp_path)
    outcomes={'R1':dict(status='failed',findings=[dict(scope='detail',dimension='field_conditions',verdict='unclear',
        affected_paths=['detail_contract.fields.F1.required'],traces=[])])}
    schema,_=contract(raw,outcomes)
    patch=dict(rows=[dict(row_key='R1',updates=[dict(path='detail_contract.fields.F1.evidence.required',
        value={'status':'source','sources':[{'source':'S404','locator':'L1'}]})],new_objects=[])])
    merged=review.apply_correction(raw,patch,schema,outcomes,protocol_version=2)
    value,report=adapt(merged,p,excerpts,'clarify',local)
    assert 'BUSINESS_DRAFT_SOURCE' in json.dumps(report)
    assert not any(item.get('detail_contract') for item in value['proposals'])


@pytest.mark.parametrize('old_snapshot',[False,True])
def test_workflow_freezes_patch_protocol_and_preserves_full_legacy(tmp_path,old_snapshot):
    from app.workflow import Workflow
    from tests.test_detail_semantic_workflow import SemanticProvider
    from tests.test_requirement_recommendation import project,target
    async def run():
        store,p=project(tmp_path);provider=SemanticProvider('correct');workflow=Workflow(store,provider)
        config=dict(workflow.config('clarify'),max_calls=4)
        if old_snapshot:config.pop('detail_correction_protocol_version')
        task=workflow.start(p['id'],p['revision'],'clarify','',discussion_target=target(),config_override=config)
        await workflow.tasks[task['id']]
        saved=store.get_record(p['id'],task['id'],'run')
        assert saved['calls']==saved['request_budget']['used']==4
        assert saved['business_draft_review']['detail_semantic_reviews']['R1']['status']=='passed'
        payload=json.loads(provider.received[2][1]['content'][0]['text'])
        assert payload['correction_protocol']==(1 if old_snapshot else 2)
        schema=json.loads(provider.received[2][0]['content'].split('可信任务头：',1)[1])['schema']
        assert ('detail_contract' in schema['properties']['rows']['items']['properties']) is old_snapshot
    asyncio.run(run())
