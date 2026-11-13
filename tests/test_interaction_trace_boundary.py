import copy
import pytest
from app.detail_recovery import facts,guard,trace_witness
from tests.test_detail_recovery import graph,ENTRIES,reviewed


def test_recovery_suffix_keeps_foreign_only_negative_guard_and_raw_path():
    detail=graph();analysis=facts(detail,ENTRIES);outcome=reviewed();check=outcome['checks'][0]
    check.update(dimension='recovery_success');check['traces']=[dict(kind='normal',edge_ids=['E6','E7','E1'])]
    before=copy.deepcopy(outcome)
    proof=trace_witness(detail,ENTRIES,analysis,'I2','W','Z',True)
    assert proof['recovery_premise']==dict(wait_id='W',entry_to_wait_edge_ids=['E5'])
    assert guard(detail,analysis,outcome)['status']=='pending' and outcome==before
    assert not trace_witness(detail,ENTRIES,analysis,'I2','W','Z',False)


@pytest.mark.parametrize('fault',['foreign_wait','foreign_prefix','unreachable'])
def test_recovery_premise_rejects_other_operation_or_unreachable_wait(fault):
    detail=graph()
    if fault=='foreign_wait':next(n for n in detail['flow']['nodes'] if n['id']=='W')['interaction_id']='I1'
    elif fault=='foreign_prefix':next(e for e in detail['flow']['edges'] if e['id']=='E5')['to']='A'
    else:detail['flow']['edges']=[e for e in detail['flow']['edges'] if e['id']!='E5']
    assert 'recovery_premise' not in trace_witness(detail,ENTRIES,facts(detail,ENTRIES),'I2','W','Z',True)


def boundary_graph():
    return dict(flow=dict(nodes=[dict(id='S',kind='start'),dict(id='A',kind='action',interaction_id='I1'),
        dict(id='B',kind='action',interaction_id='I2'),dict(id='C',kind='action',interaction_id='I3')],
        edges=[dict(id='e1',**{'from':'S','to':'A'}),dict(id='e2',**{'from':'A','to':'B'}),dict(id='e3',**{'from':'A','to':'C'})]))


@pytest.mark.parametrize('fault',['none','empty','unbound','same_operation','shared_next','foreign_tail','decision'])
def test_action_boundary_requires_all_actual_outgoing_foreign_entries(fault):
    detail=boundary_graph();entries={'I1':{'A'},'I2':{'B'},'I3':{'C'}}
    if fault=='empty':detail['flow']['edges']=detail['flow']['edges'][:1]
    elif fault=='unbound':detail['flow']['nodes'][1].pop('interaction_id')
    elif fault=='same_operation':detail['flow']['nodes'][2]['interaction_id']='I1'
    elif fault=='shared_next':detail['flow']['nodes'][2].pop('interaction_id')
    elif fault=='foreign_tail':entries['I2']={'not-B'}
    elif fault=='decision':detail['flow']['nodes'][1]['kind']='decision'
    proof=trace_witness(detail,entries,{'operations':[]},'I1','S','A')
    assert bool(proof.get('interaction_boundary'))==(fault=='none')


def test_submission_handoff_is_structure_only_and_does_not_erase_business_failure():
    detail=boundary_graph();detail['flow']['nodes'][1]['label']='提交后待审批，审批成功才生效'
    proof=trace_witness(detail,{'I1':{'A'},'I2':{'B'},'I3':{'C'}},{'operations':[]},'I1','S','A')
    assert proof.get('interaction_boundary') and 'verdict' not in proof
    outcome=dict(status='failed',findings=[{'explanation':'来源要求审批成功才生效'}],checks=[])
    result=guard(detail,{'operations':[],'version':'test','limitation':'structure only'},outcome)
    assert result['status']=='failed' and result['findings']==outcome['findings']


def review_boundary_case():
    from app import detail_semantics as review
    flow=dict(nodes=[dict(id='S',kind='start'),dict(id='A',kind='action',interaction_id='I1'),
        dict(id='B',kind='action',interaction_id='I2'),dict(id='Z',kind='end')],
        edges=[dict(id='e1',**{'from':'S','to':'A'}),dict(id='e2',**{'from':'A','to':'B'}),dict(id='e3',**{'from':'B','to':'Z'})])
    detail=dict(flow=flow,interactions=[dict(id='I1',name='提交待审批'),dict(id='I2',name='审批')],
        fields=[],state_transitions=[],applicability={'flow':{'applicable':True,'reason':'业务操作'}})
    texts=review.properties(detail)
    quote=dict(source_id='source',excerpt_id='excerpt',quote='审批成功才生效')
    checks=[]
    for scope in ('I1','I2','detail'):
        for dimension in (('coverage',) if scope=='detail' else ('operation_results','actor_paths')):
            paths=[p for p in texts if p.endswith('.applicable')] if scope=='detail' else ['detail_contract.interactions.'+scope+'.name']
            checks.append(dict(scope=scope,dimension=dimension,verdict='supported',candidate_quotes=[dict(path=p,quote=texts[p]) for p in paths],
                source_quotes=[quote],human_quotes=[],affected_paths=[],traces=[dict(kind='normal',edge_ids=['e3'] if scope=='I2' else ['e1'],explanation='离线结构夹具，非成功时点业务结论')],explanation='结构',correction='无'))
    origin=dict(row_key='R1',detail_contract=detail,property_text=texts,required_checks=[dict(scope=c['scope'],dimension=c['dimension']) for c in checks])
    return dict(rows=[origin],sources=[dict(source_id='source',excerpt_id='excerpt',text='审批成功才生效')],human_statements=[]),dict(rows=[dict(row_key='R1',checks=checks)])


@pytest.mark.parametrize('coverage_first',[True,False])
def test_global_coverage_reuses_exact_same_row_boundary_after_all_checks(coverage_first):
    from app.detail_semantics import _validate_expanded
    plan,response=review_boundary_case()
    if coverage_first:response['rows'][0]['checks'].sort(key=lambda c:c['scope']!='detail')
    before=copy.deepcopy(response);outcome=_validate_expanded(plan,response)['R1']
    assert outcome['status']=='passed' and response==before
    audit=next(a for a in outcome['structural_trace_witnesses'] if a['scope']=='detail')
    assert audit['edge_ids']==['e1'] and audit['reused_operation_witness']['scope']=='I1'


@pytest.mark.parametrize('fault',['no_own_supported','different_edges','other_row','foreign_prefix','no_source'])
def test_global_boundary_cannot_be_declared_or_borrowed(fault):
    from app.detail_semantics import _validate_expanded
    from app.core import Problem
    plan,response=review_boundary_case();checks=response['rows'][0]['checks']
    if fault in ('different_edges','other_row'):
        if fault=='other_row':
            original=copy.deepcopy(plan['rows'][0]);original['row_key']='R2';plan['rows'].insert(0,original)
            original_response=copy.deepcopy(response['rows'][0]);original_response['row_key']='R2';response['rows'].insert(0,original_response)
        for c in checks:
            if c['scope']=='I1':c['traces'][0]['edge_ids']=['e1','e2','e3']
    elif fault=='no_own_supported':
        for c in checks:
            if c['scope']=='I1':c.update(verdict='contradicted',affected_paths=[c['candidate_quotes'][0]['path']])
    elif fault=='foreign_prefix':
        for c in checks:
            if c['scope']=='I1':c['traces'][0]['edge_ids']=['e3']
    else:next(c for c in checks if c['scope']=='detail')['source_quotes']=[]
    with pytest.raises(Problem):_validate_expanded(plan,response)


def shared_recovery_case():
    from app import detail_semantics as review
    plan,response=review_boundary_case();origin=plan['rows'][0];detail=origin['detail_contract']
    detail['interactions']=detail['interactions'][:1]
    detail['flow']=dict(nodes=[dict(id='S',kind='start'),dict(id='A',kind='action',interaction_id='I1'),
        dict(id='W',kind='wait'),dict(id='X',kind='action'),dict(id='Z',kind='end')],edges=[
        dict(id=name,**{'from':a,'to':b}) for name,a,b in [('enter','S','A'),('normal','A','X'),('end','X','Z'),('unknown','A','W'),('refresh','W','X')]])
    checks=[c for c in response['rows'][0]['checks'] if c['scope']=='I1']
    for c in checks:c['traces'][0]['edge_ids']=['enter','normal','end']
    recovery=copy.deepcopy(checks[0]);recovery.update(dimension='recovery_success');recovery['traces'][0]['edge_ids']=['refresh','end'];checks.append(recovery)
    response['rows'][0]['checks']=checks;origin['property_text']=review.properties(detail)
    origin['required_checks']=[dict(scope=c['scope'],dimension=c['dimension']) for c in checks]
    return plan,response,recovery


@pytest.mark.parametrize('kind',['normal','unknown'])
def test_shared_unbound_recovery_uses_only_current_validated_wait_premise(kind):
    from app.detail_semantics import _validate_expanded
    plan,response,recovery=shared_recovery_case();recovery['traces'][0]['kind']=kind;before=copy.deepcopy(response)
    result=_validate_expanded(plan,response)['R1']
    assert result['status']=='passed' and response==before
    assert next(x for x in result['structural_trace_witnesses'] if x['dimension']=='recovery_success')['recovery_premise']['entry_to_wait_edge_ids']==['unknown']


@pytest.mark.parametrize('fault',['foreign_wait','foreign_prefix','missing_source','disconnected','nonrecovery','failed_next_trace'])
def test_shared_result_does_not_relax_other_guards_or_reuse_previous_trace_proof(fault):
    from app.detail_semantics import _validate_expanded
    from app.core import Problem
    plan,response,recovery=shared_recovery_case();detail=plan['rows'][0]['detail_contract']
    if fault=='foreign_wait':next(n for n in detail['flow']['nodes'] if n['id']=='W')['interaction_id']='I2'
    elif fault=='foreign_prefix':
        detail['flow']['nodes'].append(dict(id='F',kind='action',interaction_id='I2'))
        next(e for e in detail['flow']['edges'] if e['id']=='unknown')['to']='F'
        detail['flow']['edges'].append(dict(id='foreign-wait',**{'from':'F','to':'W'}))
    elif fault=='missing_source':recovery['source_quotes']=[]
    elif fault=='disconnected':recovery['traces'][0]['edge_ids']=['end','refresh']
    elif fault=='nonrecovery':
        recovery['dimension']='success_timing';plan['rows'][0]['required_checks'][-1]['dimension']='success_timing'
    else:
        failed=copy.deepcopy(recovery);failed.update(dimension='recovery_rejection',verdict='contradicted',affected_paths=[failed['candidate_quotes'][0]['path']])
        response['rows'][0]['checks'].append(failed);plan['rows'][0]['required_checks'].append(dict(scope='I1',dimension='recovery_rejection'))
    with pytest.raises(Problem):_validate_expanded(plan,response)
