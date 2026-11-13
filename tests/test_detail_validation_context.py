"""Revision input carries exact deterministic gaps, without approving or editing."""
import copy
import json

from app.business_draft import assemble
from tests.test_business_draft import setup
from tests.test_detail_business_draft import sample_detail


def context(project, config, folder, local=None):
    messages, _, _ = assemble(project, 'clarify' if local else 'ingest',
                              '核对详细定义', config, folder, local)
    return json.loads(messages[-1]['content'][0]['text'])


def test_revision_context_locates_graph_errors_and_keeps_unknown_attribute(tmp_path):
    project, local, config, _, _, _ = setup(tmp_path)
    detail = sample_detail()
    detail['flow']['edges'] = [e for e in detail['flow']['edges'] if e['id'] != 'E4']
    detail['flow']['edges'].append(dict(id='E5', **{'from':'N4', 'to':'N2'}, condition='重新编辑'))
    detail['fields'][0]['constraints'] = '待确认：允许的姓名长度'
    detail['fields'][0]['evidence'] = {'constraints': {'status':'unknown', 'reason':'长度尚未决定'}}
    project['items'][0]['detail_contract'] = detail
    before = copy.deepcopy(project)
    row = context(project, config, tmp_path, local)['current_rows'][0]
    issues = {(i['code'], i['path']) for i in row['detail_validation_issues']}
    assert ('DETAIL_FLOW_BRANCH', 'detail_contract.flow.nodes.N3') in issues
    assert ('DETAIL_FLOW_TERMINAL', 'detail_contract.flow.nodes.N4') in issues
    assert ('DETAIL_PROPERTY_UNKNOWN', 'detail_contract.fields.F1.constraints') in issues
    assert not any(path.endswith('.meaning') for _, path in issues)
    assert row['detail_contract']['fields'][0]['meaning'] == detail['fields'][0]['meaning']
    assert row['detail_contract']['fields'][0]['evidence']['constraints']['status'] == 'unknown'
    assert all('item_id' not in issue for issue in row['detail_validation_issues'])
    assert project == before


def test_local_revision_excludes_other_rows_diagnostics(tmp_path):
    project, local, config, _, _, _ = setup(tmp_path)
    project['items'][0]['detail_contract'] = sample_detail()
    other = copy.deepcopy(project['items'][0])
    other.update(id='REQ-OTHER', title='另一功能', detail_contract=sample_detail())
    other['detail_contract']['flow']['nodes'][2]['kind'] = ''
    project['items'].append(other)
    before = copy.deepcopy(project)
    rows = context(project, config, tmp_path, local)['current_rows']
    assert len(rows) == 1 and rows[0]['detail_validation_issues'] == []
    assert project == before


def test_missing_required_definition_is_not_reported_as_complete(tmp_path):
    project, local, config, _, _, _ = setup(tmp_path)
    project['items'][0]['detail_required'] = True
    row = context(project, config, tmp_path, local)['current_rows'][0]
    assert [i['code'] for i in row['detail_validation_issues']] == ['DETAIL_REQUIRED']
    assert 'detail_contract' not in row


def test_generation_and_review_share_literal_interaction_path_projection(tmp_path):
    from tests.test_interaction_path_facts import flow_fixture
    from app.requirement_details import interaction_path_facts
    from app.request_context import assemble as review_assemble
    from app.provider import DEFAULT
    from tests.test_detail_document import project_fixture
    project,local,config,_,_,_=setup(tmp_path)
    detail=flow_fixture();project['items'][0]['detail_contract']=detail
    row=context(project,config,tmp_path,local)['current_rows'][0]
    assert row['interaction_path_facts']==interaction_path_facts(detail)
    reviewed=project_fixture(tmp_path/'review')
    reviewed['items'][0]['detail_contract']=detail
    before=copy.deepcopy(reviewed)
    messages,_,_=review_assemble(reviewed,'review','核对流程',DEFAULT,tmp_path)
    value=json.loads(messages[1]['content'][0]['text'])
    projected=next(row for row in value['interaction_path_facts'] if row['item_id']==reviewed['items'][0]['id'])
    assert {k:v for k,v in projected.items() if k!='item_id'}==interaction_path_facts(detail)
    header=json.loads(messages[0]['content'].split('可信任务头：',1)[1])
    policy=header['semantic_quality_contract']['interaction_path_review_policy']
    assert all(text in policy for text in ('每个interaction_id','共享等待','本次请求','已答规则','finding','required_decisions'))
    assert reviewed==before
