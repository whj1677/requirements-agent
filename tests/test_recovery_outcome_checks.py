import asyncio
import copy
import json
import pytest
from app import detail_semantics as review
from app.core import Problem
from app.detail_correction import write_scope,contract
from tests.test_detail_semantics import fixture,response,model_response


def prepared(tmp_path):
    p,_,excerpts,raw,canonical,report,old=fixture(tmp_path)
    plan=review.prepare(raw,canonical,report,excerpts,project=p,recovery_outcome_checks=True)
    return raw,old,plan


def test_each_operation_has_three_independent_source_bound_recovery_results(tmp_path):
    _,old,plan=prepared(tmp_path)
    row=plan['rows'][0]
    assert len(row['required_checks'])==len(old['rows'][0]['required_checks'])+3*len(row['detail_contract']['interactions'])
    assert not old['recovery_outcome_checks']
    value=model_response(plan)
    assert review.validate(plan,value)['R1']['status']=='passed'
    for dim in review.RECOVERY_DIMENSIONS:
        assert sum(c['dimension']==dim for c in value['rows'][0]['checks'])==len(row['detail_contract']['interactions'])


def test_existing_maximum_interaction_capacity_can_return_complete_new_matrix(tmp_path):
    import jsonschema
    _,_,plan=prepared(tmp_path);value=model_response(plan);row=value['rows'][0]
    sample=next(c for c in row['checks'] if c['dimension']=='operation_results')
    row['checks']=[dict(copy.deepcopy(sample),scope=f'I{i}',dimension=d)
        for i in range(150) for d in (*review.DIMENSIONS,*review.RECOVERY_DIMENSIONS)]+[
        c for c in row['checks'] if c['scope']=='detail']
    assert len(row['checks'])==902
    jsonschema.Draft202012Validator(review.SCHEMA).validate(value)
    row['checks'].append(copy.deepcopy(sample))
    with pytest.raises(jsonschema.ValidationError):jsonschema.Draft202012Validator(review.SCHEMA).validate(value)


@pytest.mark.parametrize('fault',['missing','duplicate','no_trace','empty_na','no_source','bad_edge','disconnected','wrong_scope'])
def test_new_recovery_checks_cannot_skip_matrix_or_reference_and_trace_gates(tmp_path,fault):
    _,_,plan=prepared(tmp_path);value=response(plan)
    check=next(c for c in value['rows'][0]['checks'] if c['dimension']=='recovery_rejection')
    if fault=='missing':value['rows'][0]['checks'].remove(check)
    elif fault=='duplicate':value['rows'][0]['checks'].append(copy.deepcopy(check))
    elif fault=='no_trace':check['traces']=[]
    elif fault=='empty_na':check['traces'][0]['explanation']='  '
    elif fault=='no_source':check['source_quotes']=[]
    elif fault in ('bad_edge','disconnected'):
        check['traces']=[dict(kind='exception',edge_ids=['E404'] if fault=='bad_edge' else ['E1','E3'],explanation='合成反例')]
    else:
        path=next(p for p in plan['rows'][0]['property_text'] if p.startswith('detail_contract.fields.'))
        check['candidate_quotes']=[dict(path=path,quote=plan['rows'][0]['property_text'][path])]
    with pytest.raises(Problem):review.validate(plan,model_response(plan,value))


def test_recovery_result_accepts_existing_continuous_trace_without_new_node_kind(tmp_path):
    _,_,plan=prepared(tmp_path);value=response(plan)
    check=next(c for c in value['rows'][0]['checks'] if c['dimension']=='recovery_rejection')
    check['traces']=[dict(kind='exception',edge_ids=['E1','E2','E4'],explanation='离线完整路径契约夹具，不证明真实业务恢复语义。')]
    assert review.validate(plan,model_response(plan,value))['R1']['status']=='passed'


@pytest.mark.parametrize('dimension',review.RECOVERY_DIMENSIONS)
def test_recovery_correction_is_exactly_existing_operation_graph_authority(tmp_path,dimension):
    raw,_,_=prepared(tmp_path);detail=raw['requirements'][0]['detail_contract']
    finding=dict(scope='I1',dimension=dimension,verdict='contradicted',
        affected_paths=['detail_contract.interactions.I1.failure'],traces=[dict(edge_ids=['E1'])])
    assert write_scope(detail,[finding])==write_scope(detail,[dict(finding,dimension='operation_results')])
    outcomes={'R1':dict(status='failed',findings=[finding])};schema,scopes=contract(raw,outcomes)
    assert scopes['R1']['new_object_kinds']==['flow.nodes','flow.edges']
    patch=dict(rows=[dict(row_key='R1',updates=[dict(path='detail_contract.flow.edges.E1.condition',value='核对原请求结果')],new_objects=[])])
    assert review.apply_correction(raw,patch,schema,outcomes,protocol_version=2)
    for path in ('detail_contract.fields.F1.name','detail_contract.state_transitions.T1.trigger'):
        bad=copy.deepcopy(patch);bad['rows'][0]['updates'].append(dict(path=path,value='未授权改动'))
        with pytest.raises(Problem):review.apply_correction(raw,bad,schema,outcomes,protocol_version=2)


@pytest.mark.parametrize('legacy',[False,True])
def test_frozen_old_low_and_new_high_keep_same_calls_and_separate_matrix(tmp_path,legacy):
    from app.workflow import Workflow
    from tests.test_detail_semantic_workflow import SemanticProvider
    from tests.test_requirement_recommendation import project,target
    async def run():
        configs=[]
        class Capture(SemanticProvider):
            async def request(self,config,messages,evidence=None):
                configs.append(copy.deepcopy(config));return await super().request(config,messages,evidence=evidence)
        store,p=project(tmp_path);provider=Capture('correct');workflow=Workflow(store,provider)
        config=dict(workflow.config('clarify'),max_calls=4)
        if legacy:
            config.pop('detail_review_reasoning_effort');config.pop('recovery_outcome_checks')
            config['detail_review_options']['reasoning_effort']='low'
        task=workflow.start(p['id'],p['revision'],'clarify','',discussion_target=target(),config_override=config)
        await workflow.tasks[task['id']]
        saved=store.get_record(p['id'],task['id'],'run')
        assert saved['calls']==saved['request_budget']['used']==4
        assert [c.get('reasoning_effort') for c in configs]==[None,'low' if legacy else 'high',None,'low' if legacy else 'high']
        assert saved['business_draft_review']['detail_semantic_reviews']['R1']['status']=='passed'
        for index in (1,3):
            plan=json.loads(provider.received[index][1]['content'][0]['text']);dims={c['dimension'] for c in plan['rows'][0]['required_checks']}
            assert bool(dims&set(review.RECOVERY_DIMENSIONS)) is (not legacy)
    asyncio.run(run())
