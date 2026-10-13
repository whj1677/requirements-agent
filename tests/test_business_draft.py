"""Offline business protocol checks; synthetic storage-cloud data, no model calls."""
import copy
import asyncio
import json
import os
from pathlib import Path

os.environ.setdefault('RA_DATA_DIR', str(Path(__file__).resolve().parents[1] / 'evidence' / 'business-draft-tests' / 'isolated'))

import pytest

from app.business_draft import BEHAVIOR, PROTOCOL, SUMMARY_FIELDS, adapt, enabled
from app.core import Problem
from app.provider import DEFAULT, assemble, input_metrics
from app.requirement_recommendation import scope
from app.workflow import apply_response
from tests.test_requirement_recommendation import project, target


def business_value(text='', basis='unknown', sources=None):
    return dict(text=text, basis=basis, sources=(sources if sources is not None else
        [dict(source='S1', locator='L1')] if basis == 'fact' else []))


def draft_row(key='R1'):
    return dict(row_key=key, title='电价时段维护', statement=business_value('管理员能够维护电价时段。', 'fact'),
        behavior={field: business_value('管理员', 'fact') if field == 'actor' else business_value()
                  for field in BEHAVIOR},
        acceptance=[business_value('管理员保存重叠时段时，拒绝保存。', 'fact')], questions=[])


def packet(*rows):
    return dict(summary='模型业务摘要待核对', summary_fields={}, requirements=list(rows), limitations=[])


def setup(tmp_path):
    _, p = project(tmp_path)
    local = scope(p, target())
    cfg = dict(DEFAULT, business_draft_protocol=PROTOCOL, budget_mode='unlimited')
    messages, excerpts, omitted = assemble(p, 'clarify', '', cfg, tmp_path,
        requirement_recommendation_scope=local)
    return p, local, cfg, messages, excerpts, omitted


def test_assembly_business_only_preserves_sources_user_words_and_reading_limits(tmp_path):
    p, local, cfg, _, _, _ = setup(tmp_path)
    p['messages'] += [dict(role='user', text='业主不应该看到别人的电价模板。'),
        dict(role='assistant', text='过时审计摘要', response={'audit_snapshot': '不应重复'})]
    p['questions'][0].update(status='answered', answer='边界允许相接',
        answer_source_refs=copy.deepcopy(p['items'][0]['source_refs']), decision_review_proposal={'huge_audit': '不可传'})
    p.setdefault('product_context', {})['scope_ids'] = ['REQ-0001']
    source = dict(p['sources'][0], id='SRC-pic', image_mime='image/png', title='模板列表截图',
        excerpts=[dict(id='EX-pic', source_id='SRC-pic', text='图片尚未视觉分析', locator='图1')])
    p['sources'].append(source)
    p['messages'].append(dict(role='assistant', stage='vision', response=dict(result=dict(
        observations=[dict(source_ref=dict(source_id='SRC-pic', excerpt_id='EX-pic'), region='顶部',
            observation='归属地区输入框', uncertain=False)], unreadable=[], unobservable=['按钮点击结果未知']),
        limitations=['静态截图不证明交互结果'])))
    before = copy.deepcopy(p)
    messages, ex, omitted = assemble(p, 'clarify', '完善本行', cfg, tmp_path,
        requirement_recommendation_scope=local)
    header = json.loads(messages[0]['content'].split('可信任务头：')[1])
    ctx = json.loads(messages[1]['content'][0]['text'])
    assert header['response_protocol'] == PROTOCOL and header['stage'] == 'clarify'
    assert header['schema']['properties']['requirements']['items']['properties']['row_key'] == {'const': 'R1'}
    assert ctx['current_rows'][0]['questions'][0]['answer'] == '边界允许相接'
    assert ctx['current_rows'][0]['scoped'] is True
    assert '业主不应该' in ctx['recent_messages'][0]['text']
    encoded = json.dumps(ctx, ensure_ascii=False)
    assert 'audit_snapshot' not in encoded and 'huge_audit' not in encoded
    for server_id in ['REQ-0001', 'RULE-0001', 'Q-0001', 'SRC-0001', 'EX-0001', 'SRC-pic']:
        assert server_id not in encoded
    assert ctx['vision_observations'][0]['observations'][0]['text'] == '归属地区输入框'
    assert ctx['source_status'][1]['saved_visual_observations'] is True
    assert ctx['excerpts'][1]['text'] == '图片尚未视觉分析'  # Raw original stays raw.
    assert len(ex) == 2 and omitted == [] and p == before
    assert input_metrics(messages)['raw_materials']['characters'] > 0


@pytest.mark.parametrize('stage,local,benchmark,host,flag,expected', [
    ('ingest', None, None, 'https://api.deepseek.com', PROTOCOL, True),
    ('clarify', {'requirement_id': 'REQ-0001'}, None, 'https://api.deepseek.com', PROTOCOL, True),
    ('clarify', None, None, 'https://api.deepseek.com', PROTOCOL, False),
    ('ingest', None, {'source_ids': ['x']}, 'https://api.deepseek.com', PROTOCOL, False),
    ('interview', None, None, 'https://api.deepseek.com', PROTOCOL, False),
    ('vision', None, None, 'https://api.deepseek.com', PROTOCOL, False),
    ('prd', None, None, 'https://api.deepseek.com', PROTOCOL, False),
    ('ingest', None, None, 'https://proxy.example', PROTOCOL, False),
    ('ingest', None, None, 'https://api.deepseek.com', None, False),
])
def test_protocol_scope_is_explicit(stage, local, benchmark, host, flag, expected):
    assert enabled(dict(base_url=host, business_draft_protocol=flag), stage, benchmark, local) is expected


def test_one_row_becomes_traceable_unconfirmed_requirement_and_acceptance(tmp_path):
    p, local, _, _, ex, _ = setup(tmp_path)
    before = copy.deepcopy(p)
    value, report = adapt(packet(draft_row()), p, ex, 'clarify', local)
    assert report['status'] == 'ready_for_human_review' and report['complete']
    assert report['review_required'] and report['source_validation'] == 'locator_checked_not_semantic_approval'
    assert len(value['proposals']) == 2
    requirement, ac = value['proposals']
    assert requirement['target_item_id'] == 'REQ-0001'
    assert requirement['related_refs'] == p['items'][0]['related_refs']
    assert requirement['behavior']['actor'] == '管理员'
    assert requirement['behavior']['permissions'] == ''
    assert ac['related_refs'] == ['REQ-0001']
    assert all(not item['answer_refs'] for item in value['proposals'])
    assert all(item['source_refs'] == [dict(source_id='SRC-0001', excerpt_id='EX-0001')] for item in value['proposals'])
    assert p == before
    saved = copy.deepcopy(p)
    mapping = apply_response(saved, value)
    assert mapping[requirement['temp_id']].startswith('REQ-')
    assert saved['items'][:len(p['items'])] == p['items']
    assert all(item['selection_status'] == 'candidate' for item in saved['items'][len(p['items']):])
    assert saved['questions'] == p['questions']


def test_invalid_reference_rejects_whole_independent_row_without_losing_good_row(tmp_path):
    p, _, _, _, ex, _ = setup(tmp_path)
    good, bad = draft_row(), draft_row('R2')
    bad['acceptance'][0]['sources'][0]['locator'] = 'L99'
    original = packet(good, bad)
    canonical, report = adapt(original, p, ex, 'ingest')
    assert report['status'] == 'partial' and not report['complete']
    assert len(report['accepted']) == 1 and len(report['rejected']) == 1
    assert report['rejected'][0]['raw'] == bad
    assert report['rejected'][0]['requirement_id'] == 'REQ-other'
    assert report['rejected'][0]['code'] == 'BUSINESS_DRAFT_SOURCE'
    assert [i['kind'] for i in canonical['proposals']] == ['requirement', 'acceptance']
    assert all(i.get('target_item_id') != 'REQ-other' for i in canonical['proposals'])
    assert '未通过' in canonical['summary']


def test_invalid_top_structure_and_legacy_protocol_are_rejected(tmp_path):
    p, _, _, _, ex, _ = setup(tmp_path)
    for value in (dict(schema_version='1.1', proposals=[]),
                  dict(packet(), requirements='not-an-array')):
        with pytest.raises(Problem, match='业务草稿结构'):
            adapt(value, p, ex, 'ingest')


@pytest.mark.parametrize('bad', ['not-an-object', [], None, 42])
def test_scalar_or_array_row_is_isolated_but_original_content_stays_inspectable(tmp_path, bad):
    p, _, _, _, ex, _ = setup(tmp_path)
    canonical, report = adapt(packet(draft_row(), bad), p, ex, 'ingest')
    assert report['status'] == 'partial' and len(report['accepted']) == 1
    assert report['rejected'][0]['raw'] == bad
    assert report['rejected'][0]['row_key'] is None and report['rejected'][0]['requirement_id'] is None
    assert report['rejected'][0]['code'] == 'BUSINESS_DRAFT_INVALID'
    assert len(canonical['proposals']) == 2


def test_unrecognized_model_ids_duplicate_keys_and_out_of_scope_row_never_bind(tmp_path):
    p, local, _, _, ex, _ = setup(tmp_path)
    bad = draft_row(); bad['target_item_id'] = 'REQ-other'
    canonical, report = adapt(packet(bad), p, ex, 'clarify', local)
    assert report['status'] == 'partial'
    assert canonical['proposals'][0]['target_item_id'] == 'REQ-0001'
    assert report['structure_rejected'][0]['raw'] == 'REQ-other'
    assert report['accepted'][0]['raw'] == bad
    _, report = adapt(packet(draft_row(), draft_row()), p, ex, 'ingest')
    assert len(report['rejected']) == 2 and not report['accepted']
    canonical, report = adapt(packet(draft_row('R2')), p, ex, 'clarify', local)
    assert report['rejected'][0]['code'] == 'RECOMMENDATION_SCOPE' and not canonical['proposals']


def test_unknown_not_filled_as_requirement_condition_and_saved_original_stays(tmp_path):
    p, local, _, _, ex, _ = setup(tmp_path)
    p['items'][0]['behavior'] = dict(permissions='管理员维护本人创建的时段。')
    row = draft_row()
    row['behavior']['permissions'] = business_value('其他角色尚不明确')
    row['behavior']['entry'] = business_value('入口与登录条件未知')
    row['acceptance'].append(business_value('尚无数据恢复验收依据'))
    canonical, report = adapt(packet(row), p, ex, 'clarify', local)
    req = canonical['proposals'][0]
    assert req['behavior']['entry'] == ''
    assert req['behavior']['permissions'] == p['items'][0]['behavior']['permissions']
    assert len(canonical['proposals']) == 2
    ev = report['accepted'][0]['field_evidence']
    assert ev['behavior']['permissions']['basis'] == 'unknown'
    assert ev['behavior']['permissions']['retained_original']
    assert ev['acceptance'][1]['text'] == '尚无数据恢复验收依据'


def test_recommendation_keeps_label_and_question_stays_open_with_no_answer_binding(tmp_path):
    p, local, _, _, ex, _ = setup(tmp_path)
    row = draft_row()
    row['behavior']['result'] = business_value('保存成功后刷新列表', 'recommendation', [dict(source='S1', locator='L1')])
    row['questions'] = [dict(text='相接边界是否允许保存？', reason='资料未明确', blocking=True,
        sources=[dict(source='S1', locator='L1')], recommendations=[business_value('允许相接', 'recommendation')])]
    canonical, report = adapt(packet(row), p, ex, 'clarify', local)
    assert report['status'] == 'ready_for_human_review', report
    assert canonical['proposals'][0]['behavior']['result'] == '建议：保存成功后刷新列表'
    assert canonical['questions'][0]['options'] == ['建议：允许相接']
    assert canonical['questions'][0]['related_refs'] == ['REQ-0001']
    saved = copy.deepcopy(p); apply_response(saved, canonical)
    assert saved['questions'][-1]['answer'] is None and saved['questions'][-1]['status'] == 'open'


def test_new_and_existing_rows_and_summary_are_independent_candidates(tmp_path):
    p, _, _, _, ex, _ = setup(tmp_path)
    new = draft_row('new-period-copy'); new['title'] = '复制电价时段'
    new['statement'] = business_value('复制电价时段', 'recommendation', [dict(source='S1', locator='L1')])
    new['acceptance'] = []
    data = packet(draft_row(), draft_row('R2'), new)
    data['summary_fields'] = dict(users=business_value('管理员', 'fact'),
        intent=business_value('维护电价时段', 'fact'), value=business_value('缺少明确价值'))
    canonical, report = adapt(data, p, ex, 'ingest')
    assert len(report['accepted']) == 3 and report['complete'], report
    added = next(i for i in canonical['proposals'] if i['kind'] == 'requirement' and i['action'] == 'add')
    assert added['change_type'] == 'unspecified' and added['scope_evidence'] == []
    assert canonical['product_context_proposal'] == dict.fromkeys(SUMMARY_FIELDS, '') | dict(users='管理员', intent='维护电价时段')
    assert report['summary_fields']['value']['basis'] == 'unknown'


def test_summary_rejection_is_reported_and_does_not_destroy_valid_rows(tmp_path):
    p, local, _, _, ex, _ = setup(tmp_path)
    data = packet(draft_row()); data['summary_fields'] = {'intent': business_value('改写全项目', 'fact')}
    canonical, report = adapt(data, p, ex, 'clarify', local)
    assert len(report['accepted']) == 1 and report['status'] == 'partial'
    assert report['summary_rejected'][0]['code'] == 'RECOMMENDATION_SCOPE'
    assert 'product_context_proposal' not in canonical


@pytest.mark.parametrize('field', ['statement', 'acceptance'])
def test_fact_without_sources_cannot_borrow_context_to_fake_confirmation(tmp_path, field):
    p, local, _, _, ex, _ = setup(tmp_path)
    row = draft_row()
    if field == 'statement': row[field]['sources'] = []
    else: row['acceptance'][0]['sources'] = []
    canonical, report = adapt(packet(row), p, ex, 'clarify', local)
    assert report['status'] == 'rejected' and report['rejected'][0]['code'] == 'BUSINESS_DRAFT_SOURCE'
    assert canonical['proposals'] == []


def test_full_input_budget_failure_does_not_silently_omit_material(tmp_path):
    p, local, cfg, _, _, _ = setup(tmp_path)
    before = copy.deepcopy(p)
    cfg.update(budget_mode='fixed', context_chars=200, max_tokens=10)
    with pytest.raises(Problem) as error:
        assemble(p, 'clarify', '', cfg, tmp_path, requirement_recommendation_scope=local)
    assert error.value.code == 'BUDGET_EXHAUSTED' and p == before


def test_question_marks_do_not_discard_valid_business_draft(tmp_path):
    p, _, _, _, ex, _ = setup(tmp_path)
    good, bad = draft_row(), draft_row('R2')
    bad['questions'] = [dict(text='谁来操作？失败如何处理？', reason='两个问题被错误合并', blocking=True,
                             recommendations=[], sources=[])]
    canonical, report = adapt(packet(good, bad), p, ex, 'ingest')
    assert report['status'] == 'ready_for_human_review'
    assert not report['rejected'] and not report['question_rejected']
    assert canonical['questions'][0]['question'] == bad['questions'][0]['text']
    assert len(canonical['proposals']) == 4


def test_business_protocol_runs_through_actual_action_workflow_as_candidates(tmp_path):
    from app.actions import Actions
    from app.workflow import Workflow
    from tests.test_runtime import FakeProvider

    class BusinessFakeProvider(FakeProvider):
        business_draft_protocol = PROTOCOL

    async def scenario():
        store, initial = project(tmp_path)
        provider = BusinessFakeProvider([packet(draft_row())])
        workflow = Workflow(store, provider)
        actions = Actions(store, workflow)
        body = dict(expected_revision=initial['revision'], action='clarify', message='',
                    document_type='prd', target=target())
        plan = actions.plan(initial['id'], body)
        task = actions.start(initial['id'], body, plan['plan_hash'], 'business-protocol-fixture', False)
        await actions.jobs[task['id']]
        finished = store.get_record(initial['id'], task['id'], 'user_task')
        saved = store.get(initial['id'])
        assert finished['status'] in ('succeeded', 'partial'), finished
        assert finished['calls'] == 1 and len(provider.received) == 1
        header = json.loads(provider.received[0][0]['content'].split('可信任务头：')[1])
        assert header['response_protocol'] == PROTOCOL
        assert saved['items'][:len(initial['items'])] == initial['items']
        assert saved['sources'] == initial['sources'] and saved['questions'] == initial['questions']
        candidates = saved['items'][len(initial['items']):]
        assert [i['kind'] for i in candidates] == ['requirement', 'acceptance']
        assert all(i['selection_status'] == 'candidate' for i in candidates)
        report = saved['business_draft_reviews'][-1]
        assert report['accepted'][0]['candidate_temp_id'] == candidates[0]['id']
        assert report['accepted'][0]['raw']['row_key'] == 'R1'
        assert report['accepted'][0]['field_evidence']['statement']['basis'] == 'fact'
        assert report['raw_response_hash'] and report['run_id']

    asyncio.run(scenario())


def test_duplicate_source_locators_are_deduplicated_without_changing_raw_text(tmp_path):
    p, local, _, _, ex, _ = setup(tmp_path)
    row = draft_row()
    for value in (row['statement'], row['behavior']['actor'], row['acceptance'][0]):
        value['sources'] *= 2
    row['questions'] = [dict(text='相接边界是否允许保存？', reason='材料未明确', blocking=True,
        sources=[dict(source='S1', locator='L1')] * 2, recommendations=[])]
    original = copy.deepcopy(row)
    canonical, report = adapt(packet(row), p, ex, 'clarify', local)
    assert report['status'] == 'ready_for_human_review', report
    assert report['accepted'][0]['raw'] == original == row
    assert canonical['proposals'][0]['statement'] == original['statement']['text']
    assert all(len(item['source_refs']) == 1 for item in canonical['proposals'])
    assert len(canonical['questions'][0]['source_refs']) == 1
    assert len(canonical['used_source_refs']) == 1


@pytest.mark.parametrize('change', [dict(selection_status='rejected'), dict(selection_status='deferred'),
                                   dict(applies_to='as_is'), dict(superseded_by='REQ-replacement')])
def test_historical_roots_cannot_be_rebound_or_turned_into_current_candidates(tmp_path, change):
    from app.business_draft import _input, _roots
    p, _, _, _, ex, _ = setup(tmp_path)
    p['items'][0].update(change)
    before = copy.deepcopy(p)
    roots = _roots(p)
    assert 'R1' not in roots and roots['R2']['id'] == 'REQ-other'
    ctx = _input(p, ex, '', None)
    assert [row['row_key'] for row in ctx['current_rows']] == ['R2']
    if change.get('applies_to') == 'as_is':
        assert ctx['readonly_current_state'][0]['statement'] == p['items'][0]['statement']
        assert '现状参考' in ctx['readonly_current_state'][0]['context_role']
    canonical, report = adapt(packet(draft_row('R1')), p, ex, 'ingest')
    assert not canonical['proposals'] and report['status'] == 'rejected'
    assert report['rejected'][0]['code'] == 'BUSINESS_DRAFT_ROW'
    assert report['rejected'][0]['requirement_id'] == 'REQ-0001'
    assert p == before


def test_old_revisions_and_declined_clauses_are_not_presented_as_current_suggestions(tmp_path):
    from app.business_draft import _input
    p, local, _, _, ex, _ = setup(tmp_path)
    p['items'].append(dict(copy.deepcopy(p['items'][0]), id='REQ-old', target_item_id='REQ-0001',
                           selection_status='deferred', statement='历史已采纳后归档的修订'))
    p['items'].append(dict(copy.deepcopy(p['items'][1]), id='RULE-rejected',
                           selection_status='rejected', statement='明确不要的旧规则'))
    p['items'].append(dict(copy.deepcopy(p['items'][0]), id='REQ-pending', target_item_id='REQ-0001',
                           selection_status='candidate', statement='当前尚待核对的修订'))
    ctx = _input(p, ex, '', local)
    statements = [item['statement'] for item in ctx['current_rows'][0]['related_clauses']]
    assert '历史已采纳后归档的修订' not in statements and '明确不要的旧规则' not in statements
    assert '当前尚待核对的修订' in statements


def test_rejected_acceptance_does_not_suppress_a_new_reviewable_suggestion(tmp_path):
    p, local, _, _, ex, _ = setup(tmp_path)
    row = draft_row()
    p['items'][2].update(statement=row['acceptance'][0]['text'], selection_status='rejected')
    canonical, report = adapt(packet(row), p, ex, 'clarify', local)
    # The legacy local validator still rejects a same-text historical clause;
    # the adapter must not silently say this declined acceptance was reused.
    assert report['status'] == 'rejected' and not canonical['proposals']
    assert '同文条款' in report['rejected'][0]['reason']
