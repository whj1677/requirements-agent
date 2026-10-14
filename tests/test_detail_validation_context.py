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
