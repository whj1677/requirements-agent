"""Recommendations may borrow a real row's context, never its factual authority."""
import copy

import pytest

from app.business_draft import BEHAVIOR, adapt
from app.workflow import apply_response
from tests.test_business_draft import business_value, draft_row, packet, setup


def test_unsourced_acceptance_uses_existing_row_context_and_remains_proposed(tmp_path):
    project, local, _, _, excerpts, _ = setup(tmp_path)
    before = copy.deepcopy(project)
    row = draft_row()
    row['acceptance'] = [business_value('保存失败时保留已填写内容，允许继续修改。', 'recommendation')]
    canonical, report = adapt(packet(row), project, excerpts, 'clarify', local)

    assert report['complete'] and not report['rejected']
    evidence = report['accepted'][0]['field_evidence']['acceptance'][0]
    assert evidence == dict(basis='recommendation', text=row['acceptance'][0]['text'],
        source_refs=before['items'][0]['source_refs'], source_relationship='context_only')
    assert report['accepted'][0]['raw']['acceptance'][0]['sources'] == []
    acceptance = next(p for p in canonical['proposals'] if p['kind'] == 'acceptance')
    assert acceptance['statement'].startswith('建议：')
    assert acceptance['epistemic_status'] == 'proposed' and acceptance['answer_refs'] == []
    assert acceptance['source_refs'] == before['items'][0]['source_refs']
    assert project == before

    apply_response(project, canonical)
    assert project['items'][:len(before['items'])] == before['items']
    assert project['questions'] == before['questions']
    assert all(i['selection_status'] == 'candidate' for i in project['items'][len(before['items']):])


def test_existing_requirement_all_recommended_fields_use_context_without_becoming_facts(tmp_path):
    project, local, _, _, excerpts, _ = setup(tmp_path)
    row = draft_row()
    row['statement'] = business_value('维护电价时段时提供字段提示。', 'recommendation')
    row['behavior'] = {field: business_value('本行待核对的' + field + '建议', 'recommendation')
                       for field in BEHAVIOR}
    row['acceptance'] = []
    canonical, report = adapt(packet(row), project, excerpts, 'clarify', local)

    assert report['complete'] and len(canonical['proposals']) == 1
    proposed = canonical['proposals'][0]
    assert proposed['action'] == 'revise' and proposed['target_item_id'] == project['items'][0]['id']
    assert proposed['epistemic_status'] == 'proposed'
    assert proposed['statement'].startswith('建议：')
    assert all(text.startswith('建议：') for text in proposed['behavior'].values())
    evidence = report['accepted'][0]['field_evidence']
    assert all(part['basis'] == 'recommendation' and part['source_relationship'] == 'context_only'
               for part in [evidence['statement'], *evidence['behavior'].values()])


@pytest.mark.parametrize('field', ['statement', 'behavior', 'acceptance'])
def test_incorrect_model_reference_is_not_replaced_with_valid_context(tmp_path, field):
    project, local, _, _, excerpts, _ = setup(tmp_path)
    row = draft_row()
    bad = business_value('仅供核对的建议', 'recommendation', [dict(source='S404', locator='L1')])
    if field == 'statement': row['statement'] = bad
    elif field == 'behavior': row['behavior']['entry'] = bad
    else: row['acceptance'] = [bad]
    canonical, report = adapt(packet(row), project, excerpts, 'clarify', local)

    assert canonical['proposals'] == []
    assert report['rejected'][0]['code'] == 'BUSINESS_DRAFT_SOURCE'
    assert report['rejected'][0]['raw'] == row


@pytest.mark.parametrize('missing', ['root_refs', 'not_in_current_input'])
def test_recommendation_without_current_original_context_is_not_adopted(tmp_path, missing):
    project, local, _, _, excerpts, _ = setup(tmp_path)
    if missing == 'root_refs': project['items'][0]['source_refs'] = []
    else: project['items'][0]['source_refs'] = [dict(source_id='SRC-unselected', excerpt_id='EX-old')]
    row = draft_row()
    row['acceptance'] = [business_value('保存失败时保留已填写内容。', 'recommendation')]
    canonical, report = adapt(packet(row), project, excerpts, 'clarify', local)

    assert canonical['proposals'] == []
    assert report['rejected'][0]['code'] == 'BUSINESS_DRAFT_SOURCE'


def test_new_requirement_cannot_borrow_an_unrelated_rows_context(tmp_path):
    project, _, _, _, excerpts, _ = setup(tmp_path)
    row = draft_row('new-1')
    row['statement'] = business_value('新增一个自动控制功能。', 'recommendation')
    row['behavior'] = {field: business_value() for field in BEHAVIOR}
    row['acceptance'] = []
    canonical, report = adapt(packet(row), project, excerpts, 'ingest')

    assert canonical['proposals'] == []
    assert report['rejected'][0]['code'] == 'BUSINESS_DRAFT_SOURCE'


def test_supplied_valid_recommendation_reference_is_preserved(tmp_path):
    project, local, _, _, excerpts, _ = setup(tmp_path)
    row = draft_row()
    row['acceptance'] = [business_value('保存失败时保留已填写内容。', 'recommendation',
                                       [dict(source='S1', locator='L1')])]
    canonical, report = adapt(packet(row), project, excerpts, 'clarify', local)

    assert report['complete'] and canonical['proposals']
    evidence = report['accepted'][0]['field_evidence']['acceptance'][0]
    assert evidence['source_refs'] == project['items'][0]['source_refs']
    assert 'source_relationship' not in evidence
