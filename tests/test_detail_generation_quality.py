"""Generation instructions narrow output without repairing business decisions."""
import copy

import jsonschema
import pytest

from app.detail_draft import canonical_detail, input_detail, model_schema
from app.requirement_details import DETAIL_SCHEMA, DetailProblem, detail_issues, require_complete
from tests.test_detail_business_draft import sample_detail


def test_generation_requires_an_explicit_node_kind_without_migrating_drafts():
    before = copy.deepcopy(DETAIL_SCHEMA)
    generation = model_schema(generation=True)
    node_schema = generation['properties']['flow']['properties']['nodes']['items']
    node = dict(id='N1', label='核对原批次结果', actor='云平台',
                evidence={prop: dict(status='proposal', reason='供产品经理核对的定义')
                          for prop in ('label', 'actor')})
    for kind in ('start', 'action', 'decision', 'wait', 'end'):
        jsonschema.validate(dict(node, kind=kind), node_schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(dict(node, kind=''), node_schema)

    # Incomplete saved drafts remain editable; generation does not change the
    # persisted contract or implicitly approve one of the valid enum values.
    draft = sample_detail()
    draft['flow']['nodes'][0]['kind'] = ''
    jsonschema.validate(draft, model_schema())
    assert DETAIL_SCHEMA == before


def test_generation_accepts_evidence_for_each_objects_own_properties():
    raw = sample_detail()
    raw['applicability']['state_transitions'] = dict(applicable=True, reason='')
    raw['state_transitions'] = [dict(id='T1', from_state='未修改', to_state='已保存',
        trigger='保存成功', condition='姓名校验通过', prohibited_actions='保存中禁止重复提交')]
    for node in raw['flow']['nodes']:
        node.setdefault('actor', '云平台')
    for collection in (raw['fields'], raw['interactions'], raw['state_transitions'],
                       raw['flow']['nodes'], raw['flow']['edges']):
        for row in collection:
            row['evidence'] = {prop: dict(status='proposal', reason='测试候选，等待人工核对')
                               for prop in row if prop not in ('id', 'evidence')}
    schema = model_schema(generation=True)
    jsonschema.validate(raw, schema)
    canonical = canonical_detail(raw, {}, {})
    assert canonical == raw
    assert not detail_issues(dict(id='REQ-example', detail_contract=canonical))
    # A field cannot borrow an edge-only evidence key even if the graph has
    # valid condition evidence. Each collection has its own attribution.
    raw['fields'][0]['evidence']['condition'] = dict(status='proposal')
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(raw, schema)


@pytest.mark.parametrize('defect,code,node_id', [
    ('one_branch', 'DETAIL_FLOW_BRANCH', 'N2'),
    ('outgoing_terminal', 'DETAIL_FLOW_TERMINAL', 'N4'),
])
def test_generated_graph_defects_stay_reviewable_but_cannot_be_confirmed(defect, code, node_id):
    raw = sample_detail()
    if defect == 'one_branch':
        raw['flow']['nodes'][1]['kind'] = 'decision'
    else:
        raw['flow']['edges'].append(dict(id='E5', **{'from': 'N4', 'to': 'N2'},
                                        condition='继续核对结果'))
    before = copy.deepcopy(raw)
    candidate = canonical_detail(raw, {}, {})
    item = dict(id='REQ-review', detail_contract=candidate)
    assert raw == before == candidate
    assert 'requirement_confirmation' not in item
    with pytest.raises(DetailProblem) as error:
        require_complete(item)
    assert any(issue['code'] == code and issue['path'] == 'detail_contract.flow.nodes.' + node_id
               for issue in error.value.issues)


def test_unknown_identifier_generation_keeps_known_meaning_and_precise_gap():
    raw = sample_detail()
    raw['fields'][0].update(name='批次标识', data_type='id',
        meaning='定位原提交批次并查询该批次结果', source='待确认：标识的产生方式',
        required='查询时必须携带原批次标识', default_value='无默认值',
        constraints='沿用原提交的标识', visibility='有原批次查看权限时展示',
        editability='只读，不允许改写原批次标识', evidence={
            'meaning': dict(status='source', sources=[dict(source='S1', locator='L1')]),
            'source': dict(status='unknown', reason='用途已经明确，资料未说明如何产生标识')})
    before = copy.deepcopy(raw)
    canonical = canonical_detail(raw, {('S1', 'L1'): dict(source_id='SRC-brief', excerpt_id='EX-1')}, {})
    field = canonical['fields'][0]
    assert field['meaning'] == before['fields'][0]['meaning']
    assert field['evidence']['meaning']['status'] == 'source'
    assert field['evidence']['meaning']['source_refs'] == [dict(source_id='SRC-brief', excerpt_id='EX-1')]
    assert input_detail(canonical, {('SRC-brief', 'EX-1'): dict(source='S1', locator='L1')}, {}) == before
    unknown_paths = {issue['path'] for issue in detail_issues(dict(id='REQ-query', detail_contract=canonical))
                     if issue['code'] == 'DETAIL_PROPERTY_UNKNOWN'}
    assert unknown_paths == {'detail_contract.fields.F1.source'}
    assert raw == before
