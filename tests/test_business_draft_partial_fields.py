"""Keep valid draft fields without laundering malformed content or bad sources."""
import copy
import json
from pathlib import Path

import pytest

from app.business_draft import BEHAVIOR, DRAFT_SCHEMA, adapt, assemble
from app.provider import DEFAULT
from app.workflow import apply_response
from tests.test_business_draft import business_value, draft_row, packet, setup


def test_bad_existing_statement_keeps_original_and_six_valid_acceptance_candidates(tmp_path):
    project, local, _, _, excerpts, _ = setup(tmp_path)
    row = draft_row()
    row['statement'] = '格式错误的正文，不能猜成资料事实'
    row['acceptance'] = [business_value(f'结果 {index} 可观察。', 'recommendation') for index in range(6)]
    before, original = copy.deepcopy(project), copy.deepcopy(row)
    canonical, report = adapt(packet(row), project, excerpts, 'clarify', local)
    assert report['status'] == 'partial' and not report['complete']
    assert not report['rejected'] and len(report['field_rejected']) == 1
    diagnostic = report['field_rejected'][0]
    assert diagnostic['path'] == ['requirements', 0, 'statement']
    assert diagnostic['raw'] == row['statement'] and diagnostic['reason']
    assert report['accepted'][0]['raw'] == original
    requirement = next(item for item in canonical['proposals'] if item['kind'] == 'requirement')
    assert requirement['statement'] == project['items'][0]['statement']
    acceptance = [item for item in canonical['proposals'] if item['kind'] == 'acceptance']
    assert len(acceptance) == 6 and all(item['epistemic_status'] == 'proposed' for item in acceptance)
    assert all(item['statement'].startswith('建议：') for item in acceptance)
    assert project == before and row == original
    saved = copy.deepcopy(project)
    apply_response(saved, canonical)
    assert saved['items'][:len(project['items'])] == project['items']
    assert all(item['selection_status'] == 'candidate' for item in saved['items'][len(project['items']):])
    assert '格式错误' in report['field_rejected'][0]['raw']


@pytest.mark.parametrize('bad', ['字符串', {'not': 'a value'}, 42, None])
def test_bad_behavior_dimension_preserves_original_without_discarding_valid_dimensions(tmp_path, bad):
    project, local, _, _, excerpts, _ = setup(tmp_path)
    project['items'][0]['behavior'] = {'entry': '从已确认的电价管理入口进入'}
    row = draft_row(); row['behavior']['entry'] = bad
    canonical, report = adapt(packet(row), project, excerpts, 'clarify', local)
    req = canonical['proposals'][0]
    assert req['behavior']['entry'] == project['items'][0]['behavior']['entry']
    assert req['behavior']['actor'] == '管理员'
    assert len([item for item in canonical['proposals'] if item['kind'] == 'acceptance']) == 1
    assert report['field_rejected'][0]['path'] == ['requirements', 0, 'behavior', 'entry']
    assert report['field_rejected'][0]['raw'] == bad


def test_missing_known_value_preserves_original_and_reports_missing(tmp_path):
    project, local, _, _, excerpts, _ = setup(tmp_path)
    row = draft_row(); del row['statement']; del row['behavior']['actor']
    canonical, report = adapt(packet(row), project, excerpts, 'clarify', local)
    assert all(item['kind'] == 'acceptance' for item in canonical['proposals'])
    assert report['accepted'][0]['field_evidence']['statement']['retained_original']
    assert len(report['field_rejected']) == 2
    assert all(item['missing'] for item in report['field_rejected'])
    assert report['accepted'][0]['raw'] == row


@pytest.mark.parametrize('key', ['new-module', 'R404'])
def test_invalid_statement_cannot_supply_identity_or_body_for_new_or_unknown_row(tmp_path, key):
    project, _, _, _, excerpts, _ = setup(tmp_path)
    row = draft_row(key); row['statement'] = '不能猜测的新需求正文'
    canonical, report = adapt(packet(row), project, excerpts, 'ingest')
    assert not canonical['proposals'] and report['status'] == 'rejected'
    assert report['rejected'][0]['raw'] == row


@pytest.mark.parametrize('bad', ['不是对象', None, {'text': 42, 'basis': 'recommendation', 'sources': []}])
def test_bad_acceptance_shape_does_not_discard_good_acceptance(tmp_path, bad):
    project, local, _, _, excerpts, _ = setup(tmp_path)
    row = draft_row(); row['acceptance'].insert(0, bad)
    canonical, report = adapt(packet(row), project, excerpts, 'clarify', local)
    assert len([item for item in canonical['proposals'] if item['kind'] == 'acceptance']) == 1
    assert report['field_rejected'][0]['path'] == ['requirements', 0, 'acceptance', 0]
    assert report['field_rejected'][0]['raw'] == bad
    assert report['accepted'][0]['raw'] == row and report['status'] == 'partial'


@pytest.mark.parametrize('field', ['statement', 'behavior', 'acceptance'])
@pytest.mark.parametrize('sources', [[{'source': 'S404', 'locator': 'L1'}], 'malformed', []])
def test_structural_error_cannot_hide_bad_or_missing_fact_sources(tmp_path, field, sources):
    project, local, _, _, excerpts, _ = setup(tmp_path)
    row = draft_row()
    bad = {'text': 42, 'basis': 'fact', 'sources': sources}
    if field == 'behavior': row['behavior']['entry'] = bad
    elif field == 'acceptance': row['acceptance'].append(bad)
    else: row[field] = bad
    canonical, report = adapt(packet(row), project, excerpts, 'clarify', local)
    assert not canonical['proposals'] and report['status'] == 'rejected'
    assert report['rejected'][0]['raw'] == row
    assert report['rejected'][0]['code'] in ('BUSINESS_DRAFT_SOURCE', 'BUSINESS_DRAFT_INVALID')


def test_acceptance_container_limit_cannot_be_bypassed_by_discarding_bad_elements(tmp_path):
    project, local, _, _, excerpts, _ = setup(tmp_path)
    row = draft_row(); row['acceptance'] += ['bad'] * 30
    canonical, report = adapt(packet(row), project, excerpts, 'clarify', local)
    assert not canonical['proposals'] and report['status'] == 'rejected'


def test_global_contract_exposes_all_required_rows_and_reports_omissions(tmp_path):
    project, _, cfg, _, excerpts, _ = setup(tmp_path)
    messages, _, _ = assemble(project, 'ingest', '整理整表', cfg)
    header = json.loads(messages[0]['content'].split('可信任务头：')[1])
    context = json.loads(messages[1]['content'][0]['text'])
    assert header['draft_contract'] == context['draft_contract'] == DRAFT_SCHEMA == 2
    assert header['required_row_keys'] == context['required_row_keys'] == ['R1', 'R2']
    assert '不能直接写字符串' in messages[0]['content']
    canonical, report = adapt(packet(draft_row()), project, excerpts, 'ingest')
    assert report['required_row_keys'] == ['R1', 'R2'] and report['missing_row_keys'] == ['R2']
    assert report['status'] == 'partial' and not report['complete']
    assert len(canonical['proposals']) == 2 and len(report['accepted']) == 1
    assert any('R2' in text and '未返回' in text for text in canonical['limitations'])
    assert report['limitations'] == canonical['limitations']


def test_local_contract_requires_only_target_and_all_rows_can_be_unchanged(tmp_path):
    project, local, cfg, _, excerpts, _ = setup(tmp_path)
    messages, _, _ = assemble(project, 'clarify', '', cfg, local_scope=local)
    context = json.loads(messages[1]['content'][0]['text'])
    assert context['required_row_keys'] == ['R1']
    row = draft_row(); row['title'] = project['items'][0]['title']
    row['statement'] = business_value()
    row['behavior'] = {key: business_value() for key in BEHAVIOR}
    canonical, report = adapt(packet(row), project, excerpts, 'clarify', local)
    assert not report['missing_row_keys'] and report['complete']
    assert all(item['kind'] == 'acceptance' for item in canonical['proposals'])


EVIDENCE = Path(__file__).resolve().parents[1] / 'evidence/refinement-autofill-20261011'


@pytest.mark.skipif(not (EVIDENCE / 'RUN-54cab27372db4041.json').is_file(),
                    reason='Local frozen model evidence is not committed')
def test_frozen_tariff_failure_recovers_six_acceptance_candidates_without_model():
    project = json.loads((EVIDENCE / 'project-before.json').read_text('utf-8'))
    run = json.loads((EVIDENCE / 'RUN-54cab27372db4041.json').read_text('utf-8'))
    row = run['business_draft_review']['rejected'][0]['raw']
    original, before = copy.deepcopy(row), copy.deepcopy(project)
    local = run['requirement_recommendation_scope']
    _, excerpts, _ = assemble(project, 'clarify', '', dict(DEFAULT, budget_mode='unlimited'), local_scope=local)
    canonical, report = adapt(packet(row), project, excerpts, 'clarify', local)
    assert project == before and row == original
    assert len(row['acceptance']) == 6 and isinstance(row['statement'], str)
    assert not report['rejected'], report
    assert report['status'] == 'partial' and len(report['accepted']) == 1
    assert len(report['field_rejected']) == 1
    assert report['field_rejected'][0]['path'] == ['requirements', 0, 'statement']
    acceptance = [item for item in canonical['proposals'] if item['kind'] == 'acceptance']
    assert len(acceptance) == 6
    assert all(item['related_refs'] == [local['requirement_id']] for item in acceptance)
    root = next(item for item in project['items'] if item['id'] == local['requirement_id'])
    assert next(item for item in canonical['proposals'] if item['kind'] == 'requirement')['statement'] == root['statement']
