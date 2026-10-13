"""Extra fields must not erase independently valid content or invent ownership."""
import copy
import asyncio
import json
from pathlib import Path

import pytest

from app.business_draft import adapt, assemble
from app.core import Problem
from app.provider import DEFAULT
from tests.test_business_draft import draft_row, packet, setup
from tests.test_business_draft_partial_questions import question


@pytest.mark.parametrize('blocking', [True, False, None])
def test_nonempty_misplaced_questions_reject_without_fabricated_binding(tmp_path, blocking):
    project, local, _, _, excerpts, _ = setup(tmp_path)
    raw = packet(draft_row())
    unknown = question('删除电价模板是否影响已引用的园区？')
    if blocking is None: del unknown['blocking']
    else: unknown['blocking'] = blocking
    raw['questions'] = [unknown]
    before = copy.deepcopy(raw)
    with pytest.raises(Problem) as error:
        adapt(raw, project, excerpts, 'clarify', local)
    assert raw == before
    assert error.value.code == 'BUSINESS_DRAFT_UNBOUND_QUESTION'
    assert error.value.model_repair_allowed is False


def test_misplaced_question_with_invalid_reference_cannot_bypass_rejection(tmp_path):
    project, local, _, _, excerpts, _ = setup(tmp_path)
    raw = packet(draft_row())
    bad = question(); bad['sources'] = [dict(source='S404', locator='L1')]
    raw['questions'] = [bad]
    with pytest.raises(Problem) as error:
        adapt(raw, project, excerpts, 'clarify', local)
    assert error.value.code == 'BUSINESS_DRAFT_UNBOUND_QUESTION'
    assert raw['questions'] == [bad]


@pytest.mark.parametrize('misplaced', [None, '', '请确认权限范围', {}, {'text':'缺少所属需求'}])
def test_malformed_root_questions_cannot_be_treated_as_empty(tmp_path, misplaced):
    project, local, _, _, excerpts, _ = setup(tmp_path)
    raw = packet(draft_row()); raw['questions'] = misplaced
    with pytest.raises(Problem) as error:
        adapt(raw, project, excerpts, 'clarify', local)
    assert error.value.code == 'BUSINESS_DRAFT_UNBOUND_QUESTION'


def test_only_empty_root_questions_is_neutral_visible_metadata(tmp_path):
    project, local, _, _, excerpts, _ = setup(tmp_path)
    raw = packet(draft_row()); raw['questions'] = []
    canonical, report = adapt(raw, project, excerpts, 'clarify', local)
    assert len(report['accepted']) == 1 and report['status'] == 'partial'
    assert report['structure_rejected'][0]['raw'] == []
    assert canonical['questions'] == []
    assert report['question_rejected'] == []


@pytest.mark.parametrize('stage', ['ingest', 'clarify'])
def test_unbound_question_fails_workflow_but_raw_output_and_originals_remain(tmp_path, stage):
    from app.workflow import Workflow
    from tests.test_requirement_recommendation import project, target
    from tests.test_business_draft_workflow import BusinessProvider

    async def scenario():
        store, before = project(tmp_path)
        raw = packet(draft_row())
        unbound = question('删除后已经配置的园区如何处理？'); unbound['blocking'] = True
        raw['questions'] = [unbound]
        provider = BusinessProvider([raw])
        workflow = Workflow(store, provider)
        run = workflow.start(before['id'], before['revision'], stage, '',
            discussion_target=target() if stage == 'clarify' else None)
        await workflow.tasks[run['id']]
        finished = store.get_record(before['id'], run['id'], 'run')
        assert finished['status'] == 'failed' and finished['error'] == 'BUSINESS_DRAFT_UNBOUND_QUESTION'
        assert finished['repair_count'] == 0 and len(provider.received) == 1
        assert store.get(before['id']) == before
        call = finished['attempts'][0]['call_id']
        evidence = json.loads((store.folder / 'evidence/model-calls' / (call + '.json')).read_text('utf-8'))
        assert json.loads(evidence['final_output']) == raw
        assert evidence['validation_result'] == 'BUSINESS_DRAFT_UNBOUND_QUESTION'
    asyncio.run(scenario())


def test_unknown_root_and_row_fields_preserve_raw_but_cannot_change_known_fields(tmp_path):
    project, local, _, _, excerpts, _ = setup(tmp_path)
    row = draft_row(); row['statement_note'] = '未经确认的补充解释'; row['selection_status'] = 'selected'
    raw = packet(row); raw['approved'] = True
    canonical, report = adapt(raw, project, excerpts, 'clarify', local)

    assert len(report['accepted']) == 1 and not report['rejected']
    assert report['accepted'][0]['raw'] == row
    assert {tuple(d['path']) for d in report['structure_rejected']} == {
        ('approved',), ('requirements', 0, 'statement_note'), ('requirements', 0, 'selection_status')}
    assert all('selection_status' not in item for item in canonical['proposals'])
    assert all(d['requirement_id'] == project['items'][0]['id']
               for d in report['structure_rejected'] if d['path'][0] == 'requirements')


@pytest.mark.parametrize('field', ['summary', 'summary_fields', 'requirements', 'limitations'])
@pytest.mark.parametrize('mode', ['missing', 'wrong_type'])
def test_required_root_contract_remains_strict(tmp_path, field, mode):
    project, local, _, _, excerpts, _ = setup(tmp_path)
    raw = packet(draft_row()); raw['questions'] = []
    if mode == 'missing': del raw[field]
    else: raw[field] = 42
    with pytest.raises(Problem) as error:
        adapt(raw, project, excerpts, 'clarify', local)
    assert error.value.code == 'BUSINESS_DRAFT_INVALID'


def test_extra_row_field_does_not_hide_malformed_known_statement(tmp_path):
    project, local, _, _, excerpts, _ = setup(tmp_path)
    bad = draft_row(); bad['statement_note'] = ''; bad['statement'] = {'not':'a value'}
    canonical, report = adapt(packet(bad), project, excerpts, 'clarify', local)
    assert report['status'] == 'partial' and not report['rejected']
    assert report['accepted'][0]['raw'] == bad
    assert report['field_rejected'][0]['raw'] == bad['statement']
    assert canonical['proposals'][0]['statement'] == project['items'][0]['statement']
    assert any(item['kind'] == 'acceptance' for item in canonical['proposals'])


LIVE = Path(__file__).resolve().parents[1] / 'evidence/tariff-entry-20261010/draft-workload-v1/live'


@pytest.mark.skipif(not (LIVE / 'provider-response.json').is_file(), reason='Local real-model evidence is not committed')
def test_actual_failed_response_preserves_ten_valid_rows_offline():
    raw = json.loads((LIVE / 'provider-response.json').read_text('utf-8'))['value']
    project = json.loads((LIVE / 'project-before.json').read_text('utf-8'))
    snapshot, original = copy.deepcopy(project), copy.deepcopy(raw)
    assert raw['questions'] == [] and len(raw['requirements']) == 10
    _, excerpts, _ = assemble(project, 'ingest', '', dict(DEFAULT, budget_mode='unlimited'))
    canonical, report = adapt(raw, project, excerpts, 'ingest')

    assert project == snapshot and raw == original
    assert len(report['accepted']) == 10 and not report['rejected']
    assert report['status'] == 'partial' and not report['question_rejected']
    assert {tuple(d['path']) for d in report['structure_rejected']} == {
        ('questions',), ('requirements', 9, 'statement_note')}
    assert len(canonical['proposals']) == 37
    assert len(canonical['questions']) == 3
    assert report['accepted'][-1]['field_evidence']['questions'][0]['existing_question_id'] == 'Q-57b03ae3314048c5'
