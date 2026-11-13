"""Trace labels describe the scoped scenario, not an invented success result."""
import copy
import json
import pytest
from app import detail_semantics as review
from app.business_draft import adapt
from app.core import Problem
from tests.test_detail_semantics import fixture,response,model_response
from tests.test_detail_semantic_workflow import generated_detail


def refusal_plan(tmp_path):
    p,scope,excerpts,raw,*_=fixture(tmp_path)
    excerpts[0]['text']='合成场景：非本人直接提交释放请求时只能拒绝；不允许释放成功，负责人及记录不变。'
    detail=generated_detail();interaction=detail['interactions'][0]
    interaction.update(name='非本人直接提交释放请求',actor='非当前负责人',
        preconditions='请求人不是当前负责人',success='不适用：非本人不能释放成功',failure='拒绝，负责人和记录不变')
    for name in ('name','actor','preconditions','success','failure'):
        interaction['evidence'][name]=dict(status='source',sources=[dict(source='S1',locator='L1')])
    detail['interactions']=[interaction]
    detail['flow']=dict(nodes=[dict(id='ND-entry',kind='start',label='非本人直接请求',interaction_id='I1'),
        dict(id='ND-denied',kind='end',label='拒绝且记录不变',interaction_id='I1')],
        edges=[dict(id='ED-denied',**{'from':'ND-entry','to':'ND-denied'},condition='请求人不是负责人')])
    detail['state_transitions']=[];detail['applicability']['state_transitions']=dict(applicable=False,reason='本场景不改变状态')
    raw['requirements'][0]['detail_contract']=detail
    canonical,report=adapt(raw,p,excerpts,'clarify',scope)
    plan=review.prepare(raw,canonical,report,excerpts,protocol_version=3,recovery_outcome_checks=True)
    expanded=response(plan)
    for check in expanded['rows'][0]['checks']:
        if check['dimension'] in ('operation_results','actor_paths'):
            check['traces']=[dict(kind='normal',edge_ids=['ED-denied'],explanation='来源限定本场景完成结果为拒绝，不是释放成功。')]
    return plan,model_response(plan,expanded)


def test_source_bound_rejection_completion_can_be_explicit_normal_without_success_path(tmp_path):
    plan,value=refusal_plan(tmp_path);before=copy.deepcopy((plan,value))
    outcome=review.validate(plan,value)['R1']
    assert outcome['status']=='passed' and plan['rows'][0]['source_claims']
    assert plan['rows'][0]['detail_contract']['interactions'][0]['success']=='不适用：非本人不能释放成功'
    assert len(value['rows'][0]['source_checks'])==len(plan['rows'][0]['source_claims'])
    assert (plan,value)==before


def test_rejection_trace_is_not_relabelled_by_program_and_full_source_review_is_required(tmp_path):
    plan,value=refusal_plan(tmp_path)
    complete=copy.deepcopy(value)
    for check in value['rows'][0]['checks']:
        for trace in check['traces']:
            if trace['edge_ids']:trace['kind']='exception'
    before=copy.deepcopy(value)
    with pytest.raises(Problem,match='完整正常路径'):review.validate(plan,value)
    assert value==before
    value=complete
    value['rows'][0]['source_checks'].pop()
    with pytest.raises(Problem):review.validate(plan,value)


@pytest.mark.parametrize('fault',['missing_normal','mutually_exclusive_edges'])
def test_ordinary_operation_guards_are_not_relaxed(tmp_path,fault):
    *_,plan=fixture(tmp_path);expanded=response(plan)
    for check in expanded['rows'][0]['checks']:
        if fault=='missing_normal':
            for trace in check['traces']:
                if trace['kind']=='normal':trace['kind']='exception'
        elif check['dimension']=='operation_results':check['traces'][0]['edge_ids']=['E1','E2','E3','E4']
    value=model_response(plan,expanded);before=copy.deepcopy(value)
    with pytest.raises(Problem):review.validate(plan,value)
    assert value==before


def test_wire_descriptions_and_prompt_explain_labels_and_order_without_changing_enum(tmp_path):
    plan,_=refusal_plan(tmp_path);messages=review.messages(plan)
    schema=json.loads(messages[0]['content'].split('可信任务头：',1)[1])['schema']
    props=schema['properties']['rows']['items']['properties']['checks']['items']['properties']['traces']['items']['properties']
    assert props['kind']['enum']==['normal','exception','unknown','not_applicable']
    assert '不等同业务成功' in props['kind']['description'] and '连续单路径' in props['edge_ids']['description']
    assert '不得为凑normal新增允许或成功业务边' in messages[0]['content']
    assert '互斥出边分为独立trace' in messages[0]['content']
