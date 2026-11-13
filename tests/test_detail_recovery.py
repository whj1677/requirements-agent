"""Negative topology proofs; no synthetic source is a business oracle."""
import copy
import pytest
from app.detail_recovery import facts,guard
from app import detail_semantics
from tests.test_detail_semantics import fixture


def graph():
    nodes=[dict(id=n,kind=k,interaction_id=i) for n,k,i in [
        ('A','action','I1'),('B','action','I2'),('Q','action','I3'),
        ('RA','action','I1'),('RB','action','I2'),('W','wait',None),('Z','end',None)]]
    pairs=[('A','RA'),('RA','Z'),('B','RB'),('RB','Z'),('A','W'),('B','W'),('W','Q'),('Q','RA')]
    return dict(flow=dict(nodes=nodes,edges=[dict(id=f'E{i}',**{'from':a,'to':b}) for i,(a,b) in enumerate(pairs)]))


ENTRIES={'I1':{'A'},'I2':{'B'},'I3':{'Q'}}


def reviewed(status='passed',unknown=True):
    return dict(status=status,findings=[{'affected_paths':['exact.previous.path']}] if status=='failed' else [],
        checks=[dict(scope='I2',dimension='operation_results',source_quotes=[{'source_id':'real-valid-input'}],human_quotes=[],
            traces=[dict(kind='unknown' if unknown else 'normal',edge_ids=['E5'])])])


def test_foreign_only_recovery_blocks_adoption_with_machine_edge_witness():
    detail=graph();analysis=facts(detail,ENTRIES);original=reviewed();before=copy.deepcopy((detail,original))
    result=guard(detail,analysis,original)
    assert result['status']=='pending' and result['findings']==[]
    audit=next(c for c in result['recovery_structure_guard']['checks'] if c['interaction_id']=='I2')
    assert audit['blocks_acceptance'] and audit['original_frontier']==['RB']
    assert audit['reachable_frontiers']==[dict(node_id='RA',owners=['I1'],edge_ids=['E6','E7','E1'])]
    assert (detail,original)==before and result['recovery_structure_guard']['model_status']=='passed'


def test_shared_wait_query_intermediate_and_original_frontiers_are_legal():
    detail=graph();detail['flow']['edges'].append(dict(id='return-b',**{'from':'Q','to':'RB'}))
    result=guard(detail,facts(detail,ENTRIES),reviewed())
    assert result['status']=='passed'
    assert not any(c['blocks_acceptance'] for c in result['recovery_structure_guard']['checks'])


def test_common_unbound_display_frontier_does_not_require_duplicated_nodes():
    detail=graph()
    detail['flow']['nodes']=[n for n in detail['flow']['nodes'] if n['id']!='RB']
    next(n for n in detail['flow']['nodes'] if n['id']=='RA')['interaction_id']=None
    detail['flow']['edges']=[e for e in detail['flow']['edges'] if e['id']!='E3']
    next(e for e in detail['flow']['edges'] if e['id']=='E2')['to']='RA'
    analysis=facts(detail,ENTRIES)
    assert guard(detail,analysis,reviewed())['status']=='passed'
    assert next(o for o in analysis['operations'] if o['interaction_id']=='I2')['direct_frontier']==['RA']


def test_no_unknown_obligation_is_not_invented_from_wait_node():
    detail=graph();result=guard(detail,facts(detail,ENTRIES),reviewed(unknown=False))
    assert result['status']=='passed'
    assert all(c['applicability']=='not_applicable' for c in result['recovery_structure_guard']['checks'])


def test_no_recognizable_result_is_unverifiable_not_business_contradiction():
    detail=graph();detail['flow']['edges']=[e for e in detail['flow']['edges'] if e['id']!='E7']
    detail['flow']['edges'].append(dict(id='wait-loop',**{'from':'Q','to':'W'}))
    result=guard(detail,facts(detail,ENTRIES),reviewed())
    assert result['status']=='passed'
    assert next(c for c in result['recovery_structure_guard']['checks'] if c['interaction_id']=='I2')['status']=='unverifiable'


def test_reentry_policy_is_not_assumed_forbidden():
    detail=graph();detail['flow']['edges'].append(dict(id='resubmit',**{'from':'Q','to':'B'}))
    result=guard(detail,facts(detail,ENTRIES),reviewed())
    assert result['status']=='passed'
    audit=next(c for c in result['recovery_structure_guard']['checks'] if c['interaction_id']=='I2')
    assert audit['status']=='unverifiable' and 'RB' in audit['frontiers_including_reentry']


@pytest.mark.parametrize('second_wait',[False,True])
def test_distinct_unexpanded_wait_is_conservative_but_same_wait_loop_is_not_escape(second_wait):
    detail=graph()
    if second_wait:
        detail['flow']['nodes'].append(dict(id='W2',kind='wait',interaction_id=None))
        detail['flow']['edges'] += [dict(id='nextwait',**{'from':'Q','to':'W2'}),dict(id='laterresult',**{'from':'W2','to':'RB'})]
    else:detail['flow']['edges'].append(dict(id='still-unknown',**{'from':'Q','to':'W'}))
    result=guard(detail,facts(detail,ENTRIES),reviewed())
    assert result['status']==('passed' if second_wait else 'pending')
    audit=next(c for c in result['recovery_structure_guard']['checks'] if c['interaction_id']=='I2')
    assert audit['unexpanded_secondary_waits']==(['W2'] if second_wait else [])


def test_existing_failure_keeps_its_exact_correction_opportunity():
    detail=graph();original=reviewed('failed');result=guard(detail,facts(detail,ENTRIES),original)
    assert result['status']=='failed' and result['findings']==original['findings'] and result['checks']==original['checks']
    assert result['recovery_structure_guard']['status']=='pending'


def test_guard_is_explicit_frozen_plan_opt_in_and_legacy_defaults_off(tmp_path):
    p,_,excerpts,raw,canonical,report,old=fixture(tmp_path)
    assert not old['recovery_guard'] and 'recovery_structure_facts' not in old['rows'][0]
    current=detail_semantics.prepare(raw,canonical,report,excerpts,project=p,protocol_version=3,recovery_guard=True)
    assert current['recovery_guard'] and current['rows'][0]['recovery_structure_facts']['operations']


def test_gate_runs_after_real_reference_validation_without_overriding_model_raw(tmp_path):
    import pytest
    from app.core import digest,Problem
    from tests.test_detail_semantics import response,model_response
    *_,plan=fixture(tmp_path);row=plan['rows'][0];detail=row['detail_contract']
    detail['flow']=graph()['flow']
    detail['flow']['nodes'].append(dict(id='G',kind='start'))
    detail['flow']['edges']+= [dict(id='GA',**{'from':'G','to':'A'}),dict(id='GB',**{'from':'G','to':'B'})]
    template=copy.deepcopy(detail['interactions'][0])
    detail['interactions']=[dict(template,id=i) for i in ENTRIES]
    row['property_text']=detail_semantics.properties(detail)
    row['property_refs']={f'P{i}':dict(path=p,value=v) for i,(p,v) in enumerate(row['property_text'].items())}
    row['candidate_hash']=digest(detail)
    row['required_checks']=[c for c in row['required_checks'] if c['scope']=='detail']+[
        dict(scope=i,dimension=d) for i in ENTRIES for d in detail_semantics.DIMENSIONS]
    expanded=response(plan)
    for check in expanded['rows'][0]['checks']:
        if check['dimension'] not in ('operation_results','actor_paths'):continue
        normal={'I1':['E0','E1'],'I2':['E2','E3'],'I3':['E7','E1']}[check['scope']]
        check['traces']=[dict(kind='normal',edge_ids=normal,explanation='合成正常路径')]
        if check['scope']=='I2':check['traces'].append(dict(kind='unknown',edge_ids=['E5'],explanation='已引用来源的未知路径'))
    value=model_response(plan,expanded);raw=copy.deepcopy(value)
    assert detail_semantics.validate(plan,value)['R1']['status']=='passed'
    plan['recovery_guard']=True
    row['recovery_structure_facts']=facts(detail,{i:detail_semantics.entry_nodes(detail,i) for i in ENTRIES})
    result=detail_semantics.validate(plan,value)['R1']
    assert result['status']=='pending' and result['recovery_structure_guard']['model_status']=='passed'
    assert value==raw
    value['source_quotes'][0]['quote']='伪造原文'
    with pytest.raises(Problem) as caught:detail_semantics.validate(plan,value)
    assert caught.value.code=='DETAIL_REVIEW_INVALID'
