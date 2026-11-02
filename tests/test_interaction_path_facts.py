"""Graph reachability is inspectable context, never an automatic business verdict."""
import copy
import json

from app.requirement_details import interaction_path_facts, detail_issues
from tests.test_detail_business_draft import sample_detail


def flow_fixture():
    detail=sample_detail()
    base=detail['interactions'][0]
    detail['interactions']=[dict(base,id='I1',name='认领',success='本次认领成功',failure='拒绝或结果未知'),
        dict(base,id='I2',name='释放',success='本次释放成功',failure='拒绝或结果未知'),
        dict(base,id='I3',name='手动刷新',success='展示原操作确定结果',failure='仍未知继续等待')]
    def node(i,kind,owner=''):
        return dict(id=i,label=i,kind=kind,actor='值班员',interaction_id=owner)
    detail['flow']=dict(nodes=[node('S','start'),node('C','decision','I1'),node('OK','action','I1'),
        node('L','decision','I2'),node('END','end'),node('WAIT','wait'),
        node('REFRESH','decision','I3')],edges=[
        dict(id='E1',**{'from':'S','to':'C'},condition='用户认领'),
        dict(id='E2',**{'from':'S','to':'L'},condition='用户主动释放'),
        dict(id='E3',**{'from':'C','to':'OK'},condition='认领成功'),
        dict(id='E4',**{'from':'C','to':'WAIT'},condition='认领结果未知'),
        dict(id='E5',**{'from':'OK','to':'END'},condition='本次操作结束'),
        dict(id='E6',**{'from':'L','to':'END'},condition='释放确定'),
        dict(id='E7',**{'from':'L','to':'WAIT'},condition='释放结果未知'),
        dict(id='E8',**{'from':'WAIT','to':'REFRESH'},condition='用户手动刷新'),
        dict(id='E9',**{'from':'REFRESH','to':'OK'},condition='原认领成功'),
        dict(id='E10',**{'from':'REFRESH','to':'WAIT'},condition='仍未知')])
    detail['state_transitions']=[dict(id='T1',from_state='本次请求未决',to_state='本次请求结果确定',
        trigger='手动刷新',condition='服务端返回本次请求的确定结果',prohibited_actions='不自动重发')]
    detail['applicability']['state_transitions']=dict(applicable=True,reason='本次请求状态')
    return detail


def test_shared_wait_and_cycle_project_literal_boundaries_without_new_blockers():
    detail=flow_fixture();before=copy.deepcopy(detail)
    issues=detail_issues(dict(id='REQ-test',detail_contract=detail))
    facts=interaction_path_facts(detail)
    first=facts['interactions'][0]
    assert first['interaction']==detail['interactions'][0]
    by_id={row['node']['id']:row for row in first['associated_nodes']}
    assert by_id['C']['reachable_end_node_ids']==['END']
    assert by_id['C']['reachable_wait_node_ids']==['WAIT']
    assert by_id['C']['first_cross_interaction_edges']==[detail['flow']['edges'][7]]
    refresh=facts['interactions'][2]['associated_nodes'][0]
    assert refresh['reachable_wait_node_ids']==['WAIT']
    assert refresh['reachable_end_node_ids']==[]
    assert refresh['first_cross_interaction_edges']==[detail['flow']['edges'][8]]
    assert facts['state_transitions']==detail['state_transitions']
    assert not [issue for issue in issues if issue['code'].startswith('DETAIL_FLOW_')]
    assert detail_issues(dict(id='REQ-test',detail_contract=detail))==issues and detail==before


def test_success_forced_into_next_operation_is_visible_but_not_auto_repaired_or_blocked():
    detail=flow_fixture()
    detail['flow']['edges'][4]['to']='L'
    before=copy.deepcopy(detail)
    facts=interaction_path_facts(detail)
    success=next(row for row in facts['interactions'][0]['associated_nodes'] if row['node']['id']=='OK')
    assert success['reachable_end_node_ids']==success['reachable_wait_node_ids']==[]
    assert success['first_cross_interaction_edges']==[detail['flow']['edges'][4]]
    assert detail==before
    assert not [issue for issue in detail_issues(dict(id='REQ-test',detail_contract=detail))
                if issue['code'].startswith('DETAIL_FLOW_')]


def test_diagnostics_keep_invalid_terminal_topology_and_do_not_copy_old_evidence():
    detail=flow_fixture();detail['flow']['nodes'][2]['kind']='end'
    detail['flow']['edges'].append(dict(id='BAD',**{'from':'OK','to':'S'},condition='旧候选回到入口'))
    detail['interactions'][0]['evidence']={'success':{'status':'source','sources':[{'source':'S999','locator':'OLD'}]}}
    detail['interactions'][0]['untrusted_metadata']='PRIVATE_TOKEN'
    before=copy.deepcopy(detail);facts=interaction_path_facts(detail)
    serialized=json.dumps(facts)
    assert all(value not in serialized for value in ('S999','OLD','PRIVATE_TOKEN','evidence'))
    codes={issue['code'] for issue in detail_issues(dict(id='REQ-test',detail_contract=detail))}
    # Remove malformed model-only evidence for the independent topology check.
    clean=copy.deepcopy(detail);clean['interactions'][0].pop('evidence');clean['interactions'][0].pop('untrusted_metadata')
    codes={issue['code'] for issue in detail_issues(dict(id='REQ-test',detail_contract=clean))}
    assert {'DETAIL_FLOW_TERMINAL','DETAIL_FLOW_START'}<=codes
    assert detail==before


def test_repeated_branching_is_bounded_by_nodes_instead_of_full_paths():
    detail=flow_fixture()
    nodes=[dict(id=f'N{i}',label=f'节点{i}',kind='action',interaction_id='I1') for i in range(60)]
    nodes[-1]['kind']='end'
    edges=[dict(id=f'E{i}-{j}',**{'from':f'N{i}','to':f'N{j}'})
           for i in range(59) for j in (i+1,i+2) if j<60]
    detail['flow']=dict(nodes=nodes,edges=edges)
    facts=interaction_path_facts(detail)
    rows=facts['interactions'][0]['associated_nodes']
    assert len(rows)==60 and all(row['reachable_end_node_ids']==['N59'] for row in rows)
    assert len(json.dumps(facts))<40000
