"""Partial business drafts preserve good clauses and never approve suggestions."""
import asyncio
import copy

import pytest

from app.business_draft import _input, adapt
from app.requirement_recommendation import scope
from app.workflow import Workflow, apply_response
from tests.test_business_draft import business_value, draft_row, packet, setup
from tests.test_business_draft_workflow import BusinessProvider
from tests.test_requirement_recommendation import project, target


def question(text='保存失败后是否保留本次输入？'):
    return dict(text=text, reason='需要决定的保存失败体验', blocking=False,
                sources=[dict(source='S1', locator='L1')], recommendations=[])


@pytest.mark.parametrize('defect,code', [('extra_field', 'BUSINESS_DRAFT_INVALID'),
    ('source', 'BUSINESS_DRAFT_SOURCE'), ('recommendation_source', 'BUSINESS_DRAFT_SOURCE'),
    ('unknown_option', 'BUSINESS_DRAFT_QUESTION'), ('long_option', 'BUSINESS_DRAFT_QUESTION')])
def test_bad_question_keeps_valid_requirement_acceptance_and_other_question(tmp_path, defect, code):
    p, local, _, _, excerpts, _ = setup(tmp_path)
    row = draft_row()
    bad = question()
    if defect == 'extra_field':
        bad['questions_note'] = None  # Actual DSH envelope failure shape.
    elif defect == 'source':
        bad['sources'][0]['locator'] = 'L999'
    elif defect == 'recommendation_source':
        bad['recommendations'] = [business_value('保留输入', 'fact', [dict(source='S9', locator='L1')])]
    elif defect == 'unknown_option':
        bad['recommendations'] = [business_value('不知道')]
    else:
        bad['recommendations'] = [business_value('建议内容' * 300, 'recommendation')]
    good = question('录入时区采用什么口径？')
    row['questions'] = [bad, good]
    raw, before = copy.deepcopy(row), copy.deepcopy(p)
    canonical, report = adapt(packet(row), p, excerpts, 'clarify', local)
    assert report['status'] == 'partial' and report['complete'] is False
    assert len(report['accepted']) == 1 and not report['rejected']
    assert [proposal['kind'] for proposal in canonical['proposals']] == ['requirement', 'acceptance']
    assert [q['question'] for q in canonical['questions']] == [good['text']]
    issue = report['question_rejected'][0]
    assert issue == report['accepted'][0]['question_rejected'][0]
    assert issue['row_key'] == 'R1' and issue['requirement_id'] == 'REQ-0001'
    assert issue['question_index'] == 1 and issue['code'] == code and issue['raw'] == bad
    assert report['accepted'][0]['raw'] == row == raw and p == before
    assert len(report['accepted'][0]['field_evidence']['questions']) == 1
    assert '问题未载入' in canonical['limitations'][-1]


@pytest.mark.parametrize('bad', [None, [], 'bad-question', 7])
def test_malformed_question_element_is_preserved_without_discarding_row(tmp_path, bad):
    p, local, _, _, excerpts, _ = setup(tmp_path)
    row = draft_row(); row['questions'] = [bad]
    canonical, report = adapt(packet(row), p, excerpts, 'clarify', local)
    assert report['question_rejected'][0]['raw'] == bad
    assert not report['rejected'] and len(canonical['proposals']) == 2
    assert canonical['questions'] == []


def test_legacy_question_gate_still_applies_but_cannot_discard_good_clauses(tmp_path):
    p, local, _, _, excerpts, _ = setup(tmp_path)
    p.pop('workflow_mode')
    row = draft_row(); row['questions'] = [question('由谁操作？失败时如何处理？')]
    canonical, report = adapt(packet(row), p, excerpts, 'clarify', local)
    assert report['status'] == 'partial' and not report['rejected']
    assert report['question_rejected'][0]['code'] == 'SCHEMA_INVALID'
    assert len(canonical['proposals']) == 2 and canonical['questions'] == []


def test_repeated_existing_rule_answer_is_in_context_and_not_asked_again(tmp_path):
    p, _, _, _, excerpts, _ = setup(tmp_path)
    p['questions'][0].update(question='相接边界是否允许保存？', related_refs=['RULE-0001'],
        status='answered', answer='允许相接', answer_source_refs=copy.deepcopy(p['items'][0]['source_refs']))
    local = scope(p, target())
    original = copy.deepcopy(p)
    context = _input(p, excerpts, '', local)
    assert context['current_rows'][0]['questions'][0]['answer'] == '允许相接'
    row = draft_row(); row['questions'] = [question('相接边界是否允许保存?')]
    canonical, report = adapt(packet(row), p, excerpts, 'clarify', local)
    assert not canonical['questions'] and not report['question_rejected']
    assert report['accepted'][0]['field_evidence']['questions'][0]['existing_question_id'] == 'Q-0001'
    assert p == original


def test_repeated_pending_draft_and_acceptance_are_reused_without_new_items(tmp_path):
    from app.domain_context import source_snapshot
    p, local, _, _, excerpts, _ = setup(tmp_path)
    row = draft_row()
    canonical, _ = adapt(packet(row), p, excerpts, 'clarify', local)
    mapping = apply_response(p, canonical)
    candidate = next(i for i in p['items'] if i['id'] == mapping['TMP-business-1'])
    candidate['generation_evidence'] = source_snapshot(p, excerpts)
    before = copy.deepcopy(p)
    repeated, report = adapt(packet(row), p, excerpts, 'clarify', local)
    assert report['status'] == 'ready_for_human_review'
    assert repeated['proposals'] == [] and repeated['questions'] == []
    assert report['accepted'][0]['candidate_temp_id'] == mapping['TMP-business-1']
    assert report['accepted'][0]['field_evidence']['acceptance'][0]['existing_item_id'] == mapping['TMP-business-1-ac-1']
    assert p == before


@pytest.mark.parametrize('stale', ['unversioned', 'source_version', 'excerpt_text', 'answer_binding', 'extra_reference'])
def test_matching_old_candidate_is_not_reused_with_stale_or_unverified_evidence(tmp_path, stale):
    from app.domain_context import source_snapshot
    p, local, _, _, excerpts, _ = setup(tmp_path)
    row = draft_row()
    canonical, _ = adapt(packet(row), p, excerpts, 'clarify', local)
    mapping = apply_response(p, canonical)
    candidate = next(i for i in p['items'] if i['id'] == mapping['TMP-business-1'])
    if stale != 'unversioned':
        candidate['generation_evidence'] = source_snapshot(p, excerpts)
    if stale == 'source_version':
        p['sources'][0]['version'] = (p['sources'][0].get('version') or 1) + 1
    elif stale == 'excerpt_text':
        excerpts[0]['text'] += ' 新的明确补充。'
        p['sources'][0]['excerpts'][0]['text'] = excerpts[0]['text']
    elif stale == 'answer_binding':
        candidate['answer_refs'] = ['Q-0001']
    elif stale == 'extra_reference':
        candidate['source_refs'].append(dict(source_id='SRC-old', excerpt_id='EX-old'))
    before = copy.deepcopy(p)
    repeated, report = adapt(packet(row), p, excerpts, 'clarify', local)
    assert [i['kind'] for i in repeated['proposals']] == ['requirement']
    assert report['accepted'][0]['candidate_temp_id'] == 'TMP-business-1'
    assert p == before


def test_same_response_repeated_questions_and_acceptance_only_create_one_each(tmp_path):
    p, local, _, _, excerpts, _ = setup(tmp_path)
    row = draft_row()
    row['questions'] = [question(), question()]
    row['acceptance'] *= 2
    canonical, report = adapt(packet(row), p, excerpts, 'clarify', local)
    assert len(canonical['proposals']) == 2 and len(canonical['questions']) == 1
    assert not report['question_rejected']
    assert len(report['accepted'][0]['raw']['questions']) == 2


def test_recommended_behavior_and_acceptance_do_not_answer_critical_decision(tmp_path):
    p, local, _, _, excerpts, _ = setup(tmp_path)
    row = draft_row()
    for key, text in [('entry', '从所选园区进入维护页面'), ('flow', '修改后先显示预览再保存'),
                      ('exceptions', '保存失败时保留本次输入并提示重试')]:
        row['behavior'][key] = business_value(text, 'recommendation')
    row['acceptance'] = [business_value('保存失败时输入仍可编辑', 'recommendation', [dict(source='S1', locator='L1')])]
    critical = question('业主是否允许修改其他业主创建的时段？')
    critical.update(blocking=True, recommendations=[business_value('仅可修改自己创建的时段', 'recommendation')])
    row['questions'] = [critical]
    before = copy.deepcopy(p)
    canonical, report = adapt(packet(row), p, excerpts, 'clarify', local)
    assert report['status'] == 'ready_for_human_review'
    req, acceptance = canonical['proposals']
    assert req['epistemic_status'] == acceptance['epistemic_status'] == 'proposed'
    assert req['behavior']['entry'].startswith('建议：') and req['behavior']['exceptions'].startswith('建议：')
    assert req['behavior']['permissions'] == before['items'][0].get('behavior', {}).get('permissions', '')
    assert not req['answer_refs'] and not acceptance['answer_refs']
    apply_response(p, canonical)
    assert p['items'][:len(before['items'])] == before['items']
    assert all(item['selection_status'] == 'candidate' for item in p['items'][len(before['items']):])
    assert p['questions'][-1]['blocking'] is True and p['questions'][-1]['answer'] is None
    assert p['questions'][-1]['status'] == 'open' and p['active_baseline_id'] == before['active_baseline_id']


def test_question_partial_workflow_uses_one_frozen_call_and_preserves_originals(tmp_path):
    async def scenario():
        store, before = project(tmp_path)
        row = draft_row(); bad = question(); bad['questions_note'] = None
        row['questions'] = [bad, question('导出文件使用什么时区？')]
        provider = BusinessProvider([packet(row)])
        workflow = Workflow(store, provider)
        run = workflow.start(before['id'], before['revision'], 'clarify', '', discussion_target=target())
        await workflow.tasks[run['id']]
        finished = store.get_record(before['id'], run['id'], 'run')
        assert finished['status'] == 'partial', finished
        assert finished['calls'] == 1 and finished['repair_count'] == 0 and len(provider.received) == 1
        after = store.get(before['id'])
        assert after['items'][:len(before['items'])] == before['items']
        assert after['sources'] == before['sources'] and after.get('stage_checks') == before.get('stage_checks')
        assert after['questions'][:len(before['questions'])] == before['questions']
        assert after['questions'][-1]['answer'] is None and after['questions'][-1]['status'] == 'open'
        assert len(after['items']) - len(before['items']) == 2
        saved = after['business_draft_reviews'][-1]
        assert saved['question_rejected'][0]['raw'] == bad
        assert saved['question_rejected'][0]['requirement_id'] == 'REQ-0001'
        assert saved['accepted'][0]['candidate_temp_id'].startswith('REQ-')
        assert saved['raw_response_hash'] and saved['status'] == 'partial'
    asyncio.run(scenario())
