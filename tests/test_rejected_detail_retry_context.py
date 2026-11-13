"""Rejected model graphs are scoped diagnostics, never source or saved rules."""
import copy
import json
import pytest
from app.business_draft import _input
from tests.test_business_draft import setup
from tests.test_detail_business_draft import sample_detail


def rejected(pid):
    detail=sample_detail()
    detail['flow']['nodes'][-1]['kind']='action'
    detail['flow']['edges'][0]['evidence']={'condition':{
        'status':'source','sources':[{'source':'S999','locator':'OLD'}]}}
    return dict(requirement_id=pid,reason='旧候选缺少后续去向',raw={'detail_contract':detail})


def test_retry_contains_only_own_latest_rejected_graph_and_computed_issues(tmp_path):
    p,local,_,_,excerpts,_=setup(tmp_path);rid=local['requirement_id']
    p['business_draft_reviews']=[dict(input_revision=p['revision'],accepted=[],rejected=[
        rejected(rid),dict(requirement_id='REQ-other',reason='PRIVATE_OTHER_ROW',raw={})])]
    before=copy.deepcopy(p)
    context=_input(p,excerpts,'只重新整理这条需求',local)
    assert len(context['current_rows'])==1
    candidate=context['current_rows'][0]['rejected_detail_candidate']
    assert candidate['status']=='unaccepted_ai_candidate'
    assert candidate['rejection_reason']=='旧候选缺少后续去向'
    assert any(issue['code'].startswith('DETAIL_FLOW_') and issue['object_id']=='N5'
               for issue in candidate['topology_issues'])
    encoded=json.dumps(candidate)
    assert all(token not in encoded for token in ('evidence','sources','S999','OLD','PRIVATE_OTHER_ROW'))
    assert candidate['flow']['nodes'][-1]['kind']=='action'
    assert p==before


def test_rejected_candidate_retains_interaction_and_state_semantics_without_source_aliases(tmp_path):
    from tests.test_interaction_path_facts import flow_fixture
    from app.requirement_details import interaction_path_facts
    p,local,_,_,excerpts,_=setup(tmp_path);rid=local['requirement_id']
    detail=flow_fixture()
    detail['flow']['nodes'][2]['kind']='end'
    detail['state_transitions'][0]['evidence']={'condition':{'status':'source','sources':[{'source':'S999','locator':'OLD'}]}}
    p['business_draft_reviews']=[dict(accepted=[],rejected=[dict(requirement_id=rid,
        reason='结束节点仍有出边',raw=dict(detail_contract=detail))])]
    before=copy.deepcopy(p)
    candidate=_input(p,excerpts,'核对原候选',local)['current_rows'][0]['rejected_detail_candidate']
    assert candidate['flow']['nodes'][1]['interaction_id']=='I1'
    assert candidate['interactions']==detail['interactions']
    assert candidate['state_transitions'][0]['condition']==detail['state_transitions'][0]['condition']
    assert candidate['interaction_path_facts']==interaction_path_facts(detail)
    assert any(issue['code']=='DETAIL_FLOW_TERMINAL' for issue in candidate['topology_issues'])
    assert all(token not in json.dumps(candidate) for token in ('S999','OLD','evidence'))
    assert candidate['status']=='unaccepted_ai_candidate' and p==before


@pytest.mark.parametrize('latest',['accepted','rejected_without_flow','malformed_flow'])
def test_newest_row_outcome_supersedes_old_failure_without_guessing(tmp_path,latest):
    p,local,_,_,excerpts,_=setup(tmp_path);rid=local['requirement_id']
    p['business_draft_reviews']=[dict(accepted=[],rejected=[rejected(rid)])]
    newer=dict(accepted=[],rejected=[])
    if latest=='accepted':newer['accepted']=[dict(requirement_id=rid)]
    else:
        row=dict(requirement_id=rid,reason='最新拒收',raw={})
        if latest=='malformed_flow':row['raw']={'detail_contract':{'flow':{'nodes':[None],'edges':[]}}}
        newer['rejected']=[row]
    p['business_draft_reviews'].append(newer)
    before=copy.deepcopy(p)
    row=_input(p,excerpts,'',local)['current_rows'][0]
    if latest=='accepted':assert 'rejected_detail_candidate' not in row
    else:
        candidate=row['rejected_detail_candidate']
        assert candidate['rejection_reason']=='最新拒收' and candidate['diagnostic_reason']
        assert not candidate['topology_issues'] and 'flow' not in candidate
    assert p==before


@pytest.mark.parametrize('location',['report','accepted_metadata'])
def test_accepted_body_retains_latest_rejected_detail_and_schema_diagnosis(tmp_path,location):
    p,local,_,_,excerpts,_=setup(tmp_path);rid=local['requirement_id']
    detail=sample_detail()
    detail['flow']['edges'][0]['evidence']={'condition':{'source':'S999','locator':'OLD'}}
    diagnostic=dict(detail="'status' is a required property",
        path=['flow','edges',0,'evidence','condition'],
        schema_path=['properties','flow','required'],validator='required',
        raw_source={'source':'S999','locator':'OLD'})
    field=dict(requirement_id=rid,path=['requirements',0,'detail_contract'],raw=detail,
        code='BUSINESS_DRAFT_INVALID',reason='最新详细定义结构错误',diagnostic=diagnostic)
    accepted=dict(requirement_id=rid)
    newest=dict(accepted=[accepted],rejected=[],field_rejected=[])
    (newest if location=='report' else accepted)['field_rejected']=[field]
    p['business_draft_reviews']=[dict(accepted=[],rejected=[rejected(rid)]),newest]
    before=copy.deepcopy(p)
    candidate=_input(p,excerpts,'',local)['current_rows'][0]['rejected_detail_candidate']
    assert candidate['status']=='unaccepted_ai_candidate'
    assert candidate['rejection_reason']=='最新详细定义结构错误'
    assert candidate['rejection_code']=='BUSINESS_DRAFT_INVALID'
    assert candidate['schema_diagnostic']=={k:v for k,v in diagnostic.items() if k!='raw_source'}
    assert candidate['flow']['nodes']==detail['flow']['nodes']
    assert all(token not in json.dumps(candidate) for token in ('S999','OLD','sources','raw_source'))
    assert p==before


@pytest.mark.parametrize('kind',['other_requirement','other_field'])
def test_accepted_detail_does_not_revive_unrelated_field_failure(tmp_path,kind):
    p,local,_,_,excerpts,_=setup(tmp_path);rid=local['requirement_id']
    field=dict(requirement_id='REQ-other' if kind=='other_requirement' else rid,
        path=['requirements',0,'detail_contract' if kind=='other_requirement' else 'acceptance'],
        raw=sample_detail(),reason='UNRELATED')
    p['business_draft_reviews']=[dict(accepted=[],rejected=[rejected(rid)]),
        dict(accepted=[dict(requirement_id=rid)],rejected=[],field_rejected=[field])]
    before=copy.deepcopy(p)
    assert 'rejected_detail_candidate' not in _input(p,excerpts,'',local)['current_rows'][0]
    assert p==before
