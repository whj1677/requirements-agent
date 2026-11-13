import copy
import pytest
from app import detail_semantics as review
from app.business_draft import adapt
from app.core import Problem
from tests.test_detail_semantics import fixture,model_response,response,validate_fixture


def complete_plan(tmp_path):
    p,local,excerpts,raw,_,_,_=fixture(tmp_path)
    detail=raw['requirements'][0]['detail_contract']
    detail['applicability']['state_transitions']=dict(applicable=True,reason='')
    detail['state_transitions']=[dict(id='T'+str(n),from_state='原姓名',to_state='新姓名',trigger='保存成功',
        condition='服务明确成功',prohibited_actions='未成功不覆盖原值') for n in (1,2)]
    for field in ('required','constraints'):
        detail['fields'][0].setdefault('evidence',{})[field]=dict(status='source',sources=[dict(source='S1',locator='L1')])
    canonical,report=adapt(raw,p,excerpts,'clarify',local)
    plan=review.prepare(raw,canonical,report,excerpts)
    return plan


def test_compact_keys_cover_every_source_property_and_transition_without_repeating_quotes(tmp_path):
    plan=complete_plan(tmp_path);value=model_response(plan);before=copy.deepcopy((plan,value))
    result=review.validate(plan,value)['R1']
    assert result['status']=='passed'
    assert len(value['rows'][0]['source_checks'])==2 and len(value['rows'][0]['state_checks'])==2
    assert len(value['source_quotes'])==1
    assert all('candidate_quotes' not in c for c in value['rows'][0]['checks'])
    assert result['property_reference_resolution']['mode']=='frozen_property_key'
    assert (plan,value)==before


@pytest.mark.parametrize('fault',['hash','property','pool_quote','pool_reference','source_missing','source_duplicate','source_other_basis','state_missing','state_duplicate','state_wrong_property','state_missing_dimension','state_other_scope','supported_scope'])
def test_compact_protocol_rejects_forgery_incomplete_matrix_and_scope(tmp_path,fault):
    plan=complete_plan(tmp_path);value=model_response(plan);row=value['rows'][0]
    if fault=='hash':row['candidate_hash']='old-candidate'
    elif fault=='property':row['checks'][0]['candidate_keys']=['P404']
    elif fault=='pool_quote':value['source_quotes'][0]['quote']='非当前来源原句'
    elif fault=='pool_reference':row['checks'][0]['source_quote_keys']=['Q404']
    elif fault=='source_missing':row['source_checks'].pop()
    elif fault=='source_duplicate':row['source_checks'][1]=copy.deepcopy(row['source_checks'][0])
    elif fault=='source_other_basis':
        plan['sources'].append(dict(source_id='OTHER',excerpt_id='EX-OTHER',text='另一对象的真实来源'))
        value['source_quotes'].append(dict(key='QOTHER',source_id='OTHER',excerpt_id='EX-OTHER',quote='另一对象的真实来源'))
        row['source_checks'][0]['source_quote_keys']=['QOTHER']
    elif fault=='state_missing':row['state_checks'].pop()
    elif fault=='state_duplicate':row['state_checks'][1]=copy.deepcopy(row['state_checks'][0])
    elif fault=='state_wrong_property':row['state_checks'][0]['candidate_keys']=row['state_checks'][1]['candidate_keys']
    elif fault=='state_missing_dimension':row['state_checks'][0]['verdicts'].pop('success_timing')
    elif fault=='state_other_scope':
        row['state_checks'][0]['verdicts']['success_timing']='contradicted'
        row['state_checks'][0]['candidate_keys']+=row['state_checks'][1]['candidate_keys']
        row['state_checks'][0]['affected_keys']=row['state_checks'][1]['candidate_keys'][:1]
    else:row['checks'][0]['affected_keys']=row['checks'][0]['candidate_keys'][:1]
    with pytest.raises(Problem):review.validate(plan,value)


def test_only_uniquely_empty_supported_scope_is_normalized(tmp_path):
    plan=complete_plan(tmp_path);value=model_response(plan)
    value['rows'][0]['checks'][0].pop('affected_keys')
    value['rows'][0]['state_checks'][0].pop('affected_keys')
    before=copy.deepcopy(value);fixed,changes=review.normalize_review(value)
    assert len(changes)==2 and value==before
    assert review.validate(plan,fixed)['R1']['status']=='passed'
    bad=copy.deepcopy(value);bad['rows'][0]['checks'][0]['verdict']='contradicted'
    fixed,changes=review.normalize_review(bad)
    assert 'affected_keys' not in fixed['rows'][0]['checks'][0]
    with pytest.raises(Problem):review.validate(plan,fixed)


def test_same_operation_shares_verified_traces_and_no_unsourced_exception_is_invented(tmp_path):
    *_,plan=fixture(tmp_path);expanded=response(plan)
    for check in expanded['rows'][0]['checks']:
        if check['dimension']=='operation_results':check['traces']=[]
        if check['dimension']=='actor_paths':check['traces']=[t for t in check['traces'] if t['kind']=='normal']
    outcome=validate_fixture(plan,expanded)['R1']
    assert outcome['status']=='passed'
    assert outcome['trace_coverage'][0]['from_dimensions']==['actor_paths']


def test_missing_path_can_be_source_bound_failure_without_fabricated_trace(tmp_path):
    *_,plan=fixture(tmp_path);expanded=response(plan)
    for check in expanded['rows'][0]['checks']:
        if check['dimension']=='operation_results':check['traces']=[]
        if check['dimension']=='actor_paths':
            check.update(verdict='contradicted',traces=[],affected_paths=[check['candidate_quotes'][0]['path']],
                explanation='现有条件阻断来源要求的操作者入口，因此不存在可展示的完整拒绝路径。')
    assert validate_fixture(plan,expanded)['R1']['status']=='failed'


def test_unbound_correction_key_stays_failed_but_never_authorizes_mutation(tmp_path):
    *_,plan=fixture(tmp_path);value=model_response(plan);check=value['rows'][0]['checks'][0]
    check.update(verdict='contradicted',affected_keys=['P404'])
    finding=review.validate(plan,value)['R1']['findings'][0]
    assert finding['correction_unbound_keys']==['P404'] and not finding['affected_paths']


def test_each_state_timing_failure_is_preserved_independently(tmp_path):
    plan=complete_plan(tmp_path);value=model_response(plan);state=value['rows'][0]['state_checks'][1]
    state['verdicts']['success_timing']='contradicted';state['affected_keys']=state['candidate_keys'][:1]
    outcome=review.validate(plan,value)['R1']
    assert outcome['status']=='failed' and outcome['findings'][0]['scope']=='state:T2'
    assert outcome['findings'][0]['state_verdicts']['object_identity']=='supported'


@pytest.mark.parametrize('extra_change',[None,'value','evidence'])
def test_state_correction_only_authorizes_explicit_affected_property(tmp_path,extra_change):
    from tests.test_detail_semantic_workflow import generated_detail
    p,local,excerpts,raw,_,_,_=fixture(tmp_path)
    raw['requirements'][0]['detail_contract']=generated_detail()
    detail=raw['requirements'][0]['detail_contract']
    detail['applicability']['state_transitions']=dict(applicable=True,reason='')
    detail['state_transitions']=[dict(id='T1',from_state='原姓名',to_state='新姓名',
        trigger='保存成功',condition='服务明确成功',prohibited_actions='未成功不覆盖原值',
        evidence={key:dict(status='proposal',reason='离线协议夹具') for key in
            ('from_state','to_state','trigger','condition','prohibited_actions')})]
    canonical,report=adapt(raw,p,excerpts,'clarify',local)
    plan=review.prepare(raw,canonical,report,excerpts);value=model_response(plan)
    trigger=next(key for key,prop in plan['rows'][0]['property_refs'].items()
        if prop['path']=='detail_contract.state_transitions.T1.trigger')
    check=value['rows'][0]['state_checks'][0]
    check['verdicts']['success_timing']='unclear';check['affected_keys']=[trigger]
    outcomes=review.validate(plan,value)
    schema,_=review.correction_messages(raw,plan,outcomes,excerpts)
    fixed=copy.deepcopy(detail);state=fixed['state_transitions'][0]
    state['trigger']='服务端确认保存成功'
    state['evidence']['trigger']['reason']='按已定位属性纠正'
    if extra_change=='value':state['prohibited_actions']='其他用户可不受限覆盖原值'
    if extra_change=='evidence':state['evidence']['prohibited_actions']['reason']='未经授权改写依据'
    correction=dict(rows=[dict(row_key='R1',detail_contract=fixed)])
    if extra_change:
        with pytest.raises(Problem) as caught:review.apply_correction(raw,correction,schema,outcomes)
        assert caught.value.code=='DETAIL_CORRECTION_SCOPE'
    else:
        result=review.apply_correction(raw,correction,schema,outcomes)
        assert result['requirements'][0]['detail_contract']==fixed
    assert raw['requirements'][0]['detail_contract']==detail


@pytest.mark.parametrize('extra_change',[False,True])
def test_state_failure_can_bind_exact_cross_facet_property_without_object_authority(tmp_path,extra_change):
    from tests.test_detail_semantic_workflow import generated_detail
    p,local,excerpts,raw,_,_,_=fixture(tmp_path)
    detail=generated_detail();raw['requirements'][0]['detail_contract']=detail
    detail['applicability']['state_transitions']=dict(applicable=True,reason='')
    detail['state_transitions']=[dict(id='T1',from_state='原姓名',to_state='新姓名',trigger='保存成功',
        condition='服务明确成功',prohibited_actions='未成功不覆盖原值',
        evidence={k:dict(status='proposal',reason='离线夹具') for k in
            ('from_state','to_state','trigger','condition','prohibited_actions')})]
    canonical,report=adapt(raw,p,excerpts,'clarify',local)
    plan=review.prepare(raw,canonical,report,excerpts);value=model_response(plan)
    paths=['detail_contract.interactions.I1.entry','detail_contract.flow.nodes.N2.label']
    keys=[key for key,prop in plan['rows'][0]['property_refs'].items() if prop['path'] in paths]
    assert len(keys)==2
    state=value['rows'][0]['state_checks'][0];state['verdicts']['actor_perspective']='contradicted'
    state['candidate_keys']+=keys;state['affected_keys']=keys
    before=copy.deepcopy(value);outcomes=review.validate(plan,value)
    assert value==before and outcomes['R1']['status']=='failed'
    schema,_=review.correction_messages(raw,plan,outcomes,excerpts)
    fixed=copy.deepcopy(detail);fixed['interactions'][0]['entry']='经核对的入口'
    next(n for n in fixed['flow']['nodes'] if n['id']=='N2')['label']='核对操作者后提交'
    if extra_change:fixed['interactions'][0]['pending']='未经定位的额外改写'
    correction=dict(rows=[dict(row_key='R1',detail_contract=fixed)])
    if extra_change:
        with pytest.raises(Problem) as caught:review.apply_correction(raw,correction,schema,outcomes)
        assert caught.value.code=='DETAIL_CORRECTION_SCOPE'
    else:assert review.apply_correction(raw,correction,schema,outcomes)['requirements'][0]['detail_contract']==fixed


@pytest.mark.parametrize('kind',['exception','unknown'])
@pytest.mark.parametrize('fault',[None,'supported','no_source','unbound','empty_explanation'])
def test_missing_path_counterexample_requires_failed_exact_source_binding(tmp_path,kind,fault):
    *_,plan=fixture(tmp_path);value=model_response(plan)
    check=next(c for c in value['rows'][0]['checks'] if c['dimension']=='actor_paths')
    check.update(verdict='unclear',affected_keys=check['candidate_keys'][:1],
        traces=[dict(kind=kind,edge_ids=[],explanation='来源要求的操作者入口被已有条件排除，无完整路径。')])
    if fault=='supported':check.update(verdict='supported',affected_keys=[])
    if fault=='no_source':check['source_quote_keys']=[]
    if fault=='unbound':check['affected_keys']=['P404']
    if fault=='empty_explanation':check['traces'][0]['explanation']=' '
    before=copy.deepcopy(value)
    if fault:
        with pytest.raises(Problem):review.validate(plan,value)
    else:
        outcome=review.validate(plan,value)['R1']
        assert outcome['status']=='failed'
        assert outcome['findings'][0]['traces'][0]['evidence_kind']=='missing_path_counterexample'
    assert value==before
