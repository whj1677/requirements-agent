"""Offline decision-coverage counterexamples; no model or daily workspace data."""
import copy
import json

import pytest

from app.business_draft import adapt
from app.core import brief_hash
from app.provider import assemble
from app.store import Store
from app.workflow import replace_business_ids
from tests.helpers import prepared
from tests.test_business_draft import business_value, draft_row, packet, setup
from tests.test_business_draft_partial_questions import question


UNKNOWN_ACCEPTANCE = '时段数据为空时，保存后的可观察结果应是什么？'


@pytest.mark.parametrize('source_case', ['own_quote', 'context_only', 'different_quote'])
def test_only_explicit_sourced_unknown_question_is_recovered_without_positive_ac_or_approval(tmp_path, source_case):
    project, local, _, _, excerpts, _ = setup(tmp_path)
    project['questions'] = []
    if source_case != 'different_quote':
        excerpts[0]['text'] += '\n' + UNKNOWN_ACCEPTANCE
    row = draft_row()
    row['acceptance'].append(business_value(UNKNOWN_ACCEPTANCE, sources=(
        [] if source_case == 'context_only' else [dict(source='S1', locator='L1')])))
    original = copy.deepcopy(project)

    canonical, report = adapt(packet(row), project, excerpts, 'clarify', local)

    assert project == original
    assert [proposal['kind'] for proposal in canonical['proposals']] == ['requirement', 'acceptance']
    evidence = report['accepted'][0]['field_evidence']
    if source_case != 'own_quote':
        assert canonical['questions'] == []
        assert evidence['acceptance'][1]['clarification_review_required'] is True
        return
    pending, = canonical['questions']
    assert pending['question'] == UNKNOWN_ACCEPTANCE
    assert pending['related_refs'] == ['REQ-0001']
    assert pending['source_refs'] == [dict(source_id='SRC-0001', excerpt_id='EX-0001')]
    assert pending['blocking'] is False and pending['options'] == []
    assert 'answer' not in pending and project['active_baseline_id'] == original['active_baseline_id']
    assert evidence['acceptance'][1]['question_temp_ids'] == [pending['temp_id']]
    assert evidence['questions'][0]['origin'] == 'unknown_acceptance'
    assert report['accepted'][0]['raw'] == row
    persisted = replace_business_ids(report, {pending['temp_id']: 'Q-stable-unknown'})
    assert persisted['accepted'][0]['field_evidence']['acceptance'][1]['question_temp_ids'] == ['Q-stable-unknown']


@pytest.mark.parametrize('state', ['open', 'answered'])
def test_unknown_acceptance_reuses_same_object_question_and_preserves_saved_answer(tmp_path, state):
    project, local, _, _, excerpts, _ = setup(tmp_path)
    saved = project['questions'][0]
    saved.update(question=UNKNOWN_ACCEPTANCE, status=state,
                 answer='拒绝保存并保留输入。' if state == 'answered' else None,
                 understanding_status='applied' if state == 'answered' else 'pending')
    row = draft_row(); row['acceptance'].append(business_value(UNKNOWN_ACCEPTANCE))
    original = copy.deepcopy(project)

    canonical, report = adapt(packet(row), project, excerpts, 'clarify', local)

    assert canonical['questions'] == [] and project == original
    assert report['accepted'][0]['field_evidence']['acceptance'][1]['existing_question_id'] == saved['id']


def test_existing_same_object_question_with_different_words_is_not_blindly_duplicated_or_closed(tmp_path):
    project, local, _, _, excerpts, _ = setup(tmp_path)
    saved = project['questions'][0]
    saved.update(question='没有时段数据时保存如何处理？', status='answered', answer='拒绝保存。')
    excerpts[0]['text'] += '\n' + UNKNOWN_ACCEPTANCE
    row = draft_row()
    row['acceptance'].append(business_value(UNKNOWN_ACCEPTANCE, sources=[dict(source='S1', locator='L1')]))
    original = copy.deepcopy(project)

    canonical, report = adapt(packet(row), project, excerpts, 'clarify', local)

    assert canonical['questions'] == [] and project == original
    evidence = report['accepted'][0]['field_evidence']['acceptance'][1]
    assert evidence['clarification_review_required'] is True
    assert 'existing_question_id' not in evidence  # Semantic identity is not guessed.


def test_matching_question_on_other_requirement_does_not_hide_current_unknown(tmp_path):
    project, local, _, _, excerpts, _ = setup(tmp_path)
    project['questions'][0].update(question=UNKNOWN_ACCEPTANCE, related_refs=['REQ-other'])
    excerpts[0]['text'] += '\n' + UNKNOWN_ACCEPTANCE
    row = draft_row()
    row['acceptance'].append(business_value(UNKNOWN_ACCEPTANCE, sources=[dict(source='S1', locator='L1')]))

    canonical, _ = adapt(packet(row), project, excerpts, 'clarify', local)

    assert len(canonical['questions']) == 1
    assert canonical['questions'][0]['related_refs'] == ['REQ-0001']


@pytest.mark.parametrize('scope_record', [
    dict(out_of_scope_reason='空数据保存暂不纳入本期。'),
    dict(superseded_by=['Q-split-empty-data']),
])
def test_unknown_acceptance_does_not_reactivate_deferred_or_split_question(tmp_path, scope_record):
    project, local, _, _, excerpts, _ = setup(tmp_path)
    saved = project['questions'][0]
    saved.update(question=UNKNOWN_ACCEPTANCE, **scope_record)
    row = draft_row(); row['acceptance'].append(business_value(UNKNOWN_ACCEPTANCE))
    original = copy.deepcopy(project)

    canonical, report = adapt(packet(row), project, excerpts, 'clarify', local)

    assert canonical['questions'] == [] and project == original
    evidence = report['accepted'][0]['field_evidence']['acceptance'][1]
    assert evidence['existing_question_id'] == saved['id']
    assert any('保留原范围' in note for note in report['accepted'][0]['notes'])


def test_explicit_blocking_question_covers_repeated_unknown_acceptance_without_downgrade(tmp_path):
    project, local, _, _, excerpts, _ = setup(tmp_path)
    row = draft_row()
    row['questions'] = [dict(question(UNKNOWN_ACCEPTANCE), blocking=True)]
    row['acceptance'] += [business_value(UNKNOWN_ACCEPTANCE), business_value(UNKNOWN_ACCEPTANCE)]

    canonical, report = adapt(packet(row), project, excerpts, 'clarify', local)

    pending, = canonical['questions']
    assert pending['blocking'] is True
    evidence = report['accepted'][0]['field_evidence']['acceptance']
    assert evidence[1]['clarification_review_required'] is True
    assert evidence[2]['clarification_review_required'] is True


def test_generic_placeholders_do_not_create_decisions_or_fill_behavior(tmp_path):
    project, local, _, _, excerpts, _ = setup(tmp_path)
    row = draft_row()
    row['acceptance'] += [business_value(text) for text in ('', '资料未说明。', '未知', '待定')]

    canonical, _ = adapt(packet(row), project, excerpts, 'clarify', local)

    assert canonical['questions'] == []
    requirement = next(proposal for proposal in canonical['proposals'] if proposal['kind'] == 'requirement')
    assert requirement['behavior']['entry'] == '' and requirement['behavior']['permissions'] == ''


def test_invalid_explicit_question_keeps_unknown_evidence_without_manufacturing_replacement(tmp_path):
    project, local, _, _, excerpts, _ = setup(tmp_path)
    row = draft_row()
    row['questions'] = [dict(question(), sources=[dict(source='S1', locator='L-missing')])]
    row['acceptance'].append(business_value(UNKNOWN_ACCEPTANCE))

    canonical, report = adapt(packet(row), project, excerpts, 'clarify', local)

    assert report['status'] == 'partial' and len(report['question_rejected']) == 1
    assert canonical['questions'] == []
    assert report['accepted'][0]['field_evidence']['acceptance'][1]['clarification_review_required'] is True
    assert len(canonical['proposals']) == 2


def test_invalid_unknown_acceptance_source_is_not_repaired_by_borrowing_row_source(tmp_path):
    project, local, _, _, excerpts, _ = setup(tmp_path)
    project['questions'] = []
    row = draft_row()
    unknown = business_value(UNKNOWN_ACCEPTANCE, sources=[dict(source='S1', locator='L-missing')])
    row['acceptance'].append(unknown)
    original = copy.deepcopy(project)

    canonical, report = adapt(packet(row), project, excerpts, 'clarify', local)

    assert canonical['questions'] == [] and project == original
    assert report['status'] == 'rejected'
    issue, = report['rejected']
    assert issue['raw'] == row and issue['code'] == 'BUSINESS_DRAFT_SOURCE'
    assert canonical['proposals'] == []


@pytest.mark.parametrize('text', [
    '截图中各指标数值（如累计收益51.43万元等）为样例显示，不作为固定业务条件。',
    '列表分页与每页条数的具体默认值待定（共50条数据/每页10条为样例）。',
    '保存成功后的提示样式截图未展示，待人工核对。',
    '尚无明确验收条件。',
    '空数据是否允许保存？允许保存时谁可以修改？',
])
def test_samples_display_details_parameters_and_nonspecific_unknown_do_not_become_required_questions(tmp_path, text):
    project, local, _, _, excerpts, _ = setup(tmp_path)
    project['questions'] = []
    excerpts[0]['text'] += '\n' + text
    row = draft_row()
    row['acceptance'].append(business_value(text, sources=[dict(source='S1', locator='L1')]))

    canonical, report = adapt(packet(row), project, excerpts, 'clarify', local)

    assert canonical['questions'] == []
    evidence = report['accepted'][0]['field_evidence']['acceptance'][1]
    assert evidence['basis'] == 'unknown' and evidence['text'] == text
    assert evidence['clarification_review_required'] is True


def test_business_and_independent_review_inputs_require_decision_content_check(tmp_path):
    _, _, config, business_messages, _, _ = setup(tmp_path / 'business')
    assert '影响本期实现或验收的真实未知' in business_messages[0]['content']
    assert '适用前提或输入、触发动作和可观察结果' in business_messages[0]['content']
    assert '不重复提问' in business_messages[0]['content']

    project = prepared(Store(tmp_path / 'review'))
    project['questions'][0].update(status='answered', understanding_status='applied',
                                  answer='提交后生成当日报表，并保留生成记录。')
    artifact = project['documents']['prd']
    artifact['question_snapshot'] = copy.deepcopy(project['questions'])
    artifact['item_snapshot'] = copy.deepcopy(project['items'])
    artifact['item_snapshot'][0].setdefault('behavior', {})['flow'] = '报表生成时机资料未说明。'
    artifact['brief_hash'] = brief_hash(project)
    original = copy.deepcopy(project)

    messages, _, _ = assemble(project, 'review', '', config, tmp_path / 'review')

    assert project == original
    assert '当前同一版本' in messages[0]['content']
    assert 'applied 及人工核对记录只证明已记录应用' in messages[0]['content']
    assert '不自动批准、修改条款、创建问题或发起下一轮模型调用' in messages[0]['content']
    context = json.loads(messages[1]['content'][0]['text'])
    assert context['documents']['prd']['current'] is True
    assert '报表生成时机资料未说明' in json.dumps(context['documents']['prd'], ensure_ascii=False)
    assert '提交后生成当日报表' in json.dumps(context['questions'], ensure_ascii=False)
