"""Preparation freshness is separate from model success and human acceptance."""
import copy

import pytest

from app.refinement_preparation import SCHEMA, DRAFT_SCHEMA, draft_coverage, input_identity, view
from app.sources import save_source
from app.store import Store
from tests.test_ui02_actions import case, finished, image, plan, start


@pytest.fixture
def prepared(tmp_path):
    store = Store(tmp_path)
    project = store.create('储能云告警处理')
    with store.edit(project['id'], 0, '添加文字材料') as (project, _):
        project['sources'].append(save_source(store, '储能告警需求.txt',
            '运维人员查看当前电站告警，并记录处理备注。权限边界待产品确认。'.encode(), 'goal'))
        project['product_context_proposal'] = dict(intent='供运维人员查看当前电站告警。')
    identity = input_identity(store, project)
    run_id = store.record(project['id'], 'run', dict(stage='ingest', revision=project['revision'],
        status='awaiting_user', result_applied=True, user_task_id='TASK-PREPARED'))
    store.record(project['id'], 'user_task', dict(action='organize', status='succeeded',
        input_revision=project['revision'], run_ids=[run_id], preparation_schema=SCHEMA, preparation_draft_schema=DRAFT_SCHEMA,
        preparation_input_hash=identity['input_hash'], preparation_source_ids=identity['source_ids']), 'TASK-PREPARED')
    return store, project['id']


def test_status_is_read_only_and_preparation_is_not_confirmation(prepared):
    store, pid = prepared
    before = copy.deepcopy((store.get(pid), store.records(pid, 'run'), store.records(pid, 'user_task')))
    result = view(store, pid)
    assert result['status'] == 'ready_with_limits' and not result['auto_allowed']
    assert not result['needs_organize'] and result['task_id'] == 'TASK-PREPARED'
    assert before == (store.get(pid), store.records(pid, 'run'), store.records(pid, 'user_task'))
    assert store.get(pid)['items'] == []  # Readiness never fabricates confirmed rows.


@pytest.mark.parametrize('reason', [
    '人工一次确认需求与验收', '一次核对并落实独立决定', '人工编辑或采纳条目',
    '人工将参考条款纳入本期并采纳', '人工新增候选需求', '人工新增关联验收候选',
    '人工一次批量确认需求与验收', '人工确认完善需求表格并进入交付',
])
def test_manual_review_sources_and_summary_edits_do_not_reorganize(prepared, reason):
    store, pid = prepared
    before = view(store, pid)
    with store.edit(pid, store.get(pid)['revision'], reason) as (project, _):
        project['sources'].append(save_source(store, '人工需求整行确认.txt', '人工补充本行验收说明。'.encode(), 'goal'))
        project['product_context'] = dict(intent='人工核对告警管理目标', users='运维人员')
        project['context_field_states'] = dict(intent={'status': 'known'})
        project['delivery_scope'] = ['REQ-IGNORED-BY-INPUT-IDENTITY']
    with store.edit(pid, store.get(pid)['revision'], '修改项目信息') as (project, _):
        project['name'] = '储能云告警处理改名'
    after = view(store, pid)
    assert after['status'] == 'ready_with_limits' and after['input_hash'] == before['input_hash']


@pytest.mark.parametrize('change', ['uploaded_same_name', 'interview', 'workspace', 'domain', 'selection', 'answer'])
def test_actual_saved_input_changes_need_new_organize(prepared, change):
    store, pid = prepared
    before = view(store, pid)
    reason = '人工回答问题' if change == 'answer' else '添加文件材料' if change == 'uploaded_same_name' else '保存输入'
    with store.edit(pid, store.get(pid)['revision'], reason) as (project, _):
        if change in ('uploaded_same_name', 'answer'):
            project['sources'].append(save_source(store, '人工需求整行确认.txt', '告警应按电站授权隔离。'.encode(), 'goal'))
        elif change == 'interview':
            project['messages'].append(dict(role='user', stage='interview', text='增加按电站筛选。'))
        elif change == 'workspace':
            project['workspace_draft'] = dict(text='本期增加历史告警查询。', entry_mode='direct')
        elif change == 'domain':
            project['domain_context'] = dict(background='本次是储能运营公司的集中运维云平台。')
        else:
            project['domain_context'] = dict(selected_source_ids=[])
            project['intake'] = dict(text='保留需求目标，排除旧材料。')
    after = view(store, pid)
    assert after['status'] == 'needs_organize' and after['needs_organize'] and after['auto_allowed']
    assert after['input_hash'] != before['input_hash']


def test_vision_metadata_and_model_output_do_not_change_input(prepared):
    store, pid = prepared
    before = view(store, pid)
    with store.edit(pid, store.get(pid)['revision'], '模型保存建议') as (project, _):
        project['sources'][0].update(parse_status='read', vision_run_id='RUN-VISION', failure_reason='')
        project['product_context_proposal'] = dict(intent='仅供核对的 AI 目标建议')
        project['messages'].append(dict(role='assistant', stage='interview', text='这是 AI 的理解。'))
    assert view(store, pid)['input_hash'] == before['input_hash']
    assert view(store, pid)['status'] == 'ready_with_limits'


@pytest.mark.parametrize('status', ['failed', 'cancelled', 'paused_budget'])
def test_same_input_failed_attempt_never_auto_retries(prepared, status):
    store, pid = prepared
    identity = input_identity(store, store.get(pid))
    store.record(pid, 'user_task', dict(action='organize', status=status,
        input_revision=store.get(pid)['revision'], run_ids=[], preparation_schema=SCHEMA,
        preparation_input_hash=identity['input_hash']), 'TASK-FAILED')
    result = view(store, pid)
    assert result['status'] == 'failed' and result['needs_organize']
    assert not result['auto_allowed'] and result['task_id'] == 'TASK-FAILED'
    assert result['retry_requires_explicit']
    with store.edit(pid, store.get(pid)['revision'], '添加文字材料') as (project, _):
        project['sources'].append(save_source(store, '新增要求.txt', '本期仅做查看。'.encode(), 'goal'))
    assert view(store, pid)['auto_allowed']


def test_legacy_task_exact_input_snapshot_still_requires_missing_whole_table_drafts(prepared):
    store, pid = prepared
    with store.connect() as db:
        original = store.records(pid, 'user_task', db)[0]
    legacy = {key: value for key, value in original.items() if not key.startswith('preparation_') and key != 'id'}
    store.record(pid, 'user_task', legacy, 'TASK-LEGACY')
    with store.edit(pid, store.get(pid)['revision'], '修改项目信息') as (project, _):
        project['name'] = '名称不属于新输入'
    result = view(store, pid)
    assert result['status'] == 'needs_organize' and result['task_id'] == 'TASK-LEGACY'
    assert result['auto_allowed'] and result['draft_coverage']['missing_context_fields']


def test_unknown_legacy_revision_and_benchmark_do_not_prove_preparation(tmp_path):
    store = Store(tmp_path)
    project = store.create('储能云需求')
    with store.edit(project['id'], 0, '保存共享工作底稿') as (project, _):
        project['workspace_draft'] = dict(text='运维人员查看电站告警。', entry_mode='direct')
    pid = project['id']
    store.record(pid, 'user_task', dict(action='organize', status='succeeded', input_revision=999, run_ids=[]))
    identity = input_identity(store, project)
    store.record(pid, 'user_task', dict(action='organize', status='succeeded',
        input_revision=project['revision'], benchmark={'target': '竞品参考'}, run_ids=[],
        preparation_schema=SCHEMA, preparation_input_hash=identity['input_hash']))
    result = view(store, pid)
    assert result['status'] == 'needs_organize' and result['auto_allowed']
    assert result['entry_mode'] == 'direct'  # Direct input is allowed to request AI.


@pytest.mark.parametrize('scope', [
    {'generation_target': {'option_id': 'OPTION-1'}},
    {'target': {'kind': 'item', 'id': 'REQ-1'}},
    {'retry_input': {'target': {'kind': 'item', 'id': 'REQ-1'}}},
    {'requirement_recommendation_scope': {'requirement_id': 'REQ-1'}},
])
def test_local_organize_does_not_replace_whole_input_preparation(prepared, scope):
    store, pid = prepared
    original = store.records(pid, 'user_task')[0]
    local = {key: value for key, value in original.items() if key != 'id'}
    store.record(pid, 'user_task', dict(local, **scope), 'TASK-LOCAL')
    assert view(store, pid)['task_id'] == 'TASK-PREPARED'


def test_partial_and_empty_and_busy_states(prepared, tmp_path):
    store, pid = prepared
    store.update_record(pid, 'TASK-PREPARED', 'user_task', status='partial')
    assert view(store, pid)['status'] == 'ready_with_limits'
    store.record(pid, 'user_task', dict(action='interview', status='running'), 'TASK-ACTIVE')
    busy = view(store, pid)
    assert busy['status'] == 'running' and busy['task_id'] == 'TASK-ACTIVE'
    assert not busy['auto_allowed']
    other = store.create('空项目')
    empty = view(store, other['id'])
    assert empty['status'] == 'empty' and not empty['auto_allowed']


def test_real_action_pipeline_stamps_inputs_and_applied_output(case):
    client, app, model, root = case
    image(client, root)
    body, preview = plan(client, root, message='储能云运维人员查看电站告警，权限边界待确认。')
    response = start(client, root, body, preview)
    assert response.status_code == 200, response.text
    task = finished(client, root)
    assert task['status'] in ('succeeded', 'partial')
    assert task['preparation_schema'] == SCHEMA
    assert task['preparation_draft_schema'] == DRAFT_SCHEMA
    assert task['preparation_result']['draft_schema'] == DRAFT_SCHEMA
    assert task['preparation_result']['input_hash'] == task['preparation_input_hash']
    assert task['preparation_result']['run_id'] == task['run_ids'][-1]
    before = client.get(root).json()
    count = len(model['requests'])
    first = client.get(root + '/refinement-preparation').json()
    second = client.get(root + '/refinement-preparation').json()
    assert first == second and first['status'] in ('ready', 'ready_with_limits')
    assert first['input_hash'] == task['preparation_input_hash']
    assert len(model['requests']) == count and client.get(root).json() == before


def test_failed_action_records_attempt_without_result_stamp(case):
    client, app, model, root = case
    model['fail_next'] = True
    body, preview = plan(client, root, message='储能云运维人员查看电站告警。')
    assert start(client, root, body, preview).status_code == 200
    task = finished(client, root)
    assert task['status'] == 'failed' and task.get('preparation_input_hash')
    assert 'preparation_result' not in task
    result = client.get(root + '/refinement-preparation').json()
    assert result['status'] == 'failed' and not result['auto_allowed']



def _legacy_task(store, pid, name='TASK-LEGACY-NEEDS-DRAFT'):
    project = store.get(pid); identity = input_identity(store, project)
    rid = store.record(pid, 'run', dict(stage='ingest', revision=project['revision'],
        status='awaiting_user', result_applied=True, user_task_id=name))
    store.record(pid, 'user_task', dict(action='organize', status='succeeded',
        input_revision=project['revision'], run_ids=[rid], preparation_schema=SCHEMA,
        preparation_input_hash=identity['input_hash']), name)
    return name


def _rows(store, pid, acceptance=True):
    with store.edit(pid, store.get(pid)['revision'], '合成可核对草稿') as (project, _):
        project['product_context_proposal'] = dict(intent='查看储能电站告警', users='运维人员',
                                                  change_scope='新增电站告警查看')
        source = project['sources'][0]
        refs = [dict(source_id=source['id'], excerpt_id=source['excerpts'][0]['id'])]
        project['items'] = [dict(id='REQ-DRAFT', kind='requirement', applies_to='to_be',
            title='储能电站告警查看', statement='运维人员查看当前电站告警。', related_refs=[], source_refs=refs,
            selection_status='candidate')]
        if acceptance:
            project['items'].append(dict(id='AC-DRAFT', kind='acceptance', applies_to='to_be',
                title='告警查看验收', statement='进入当前电站告警列表后，显示该电站告警记录。',
                related_refs=['REQ-DRAFT'], source_refs=refs, selection_status='candidate'))
    return store.get(pid)


def test_legacy_complete_reviewable_draft_is_reused_without_paid_upgrade(prepared):
    store, pid = prepared
    _rows(store, pid); _legacy_task(store, pid)
    before = copy.deepcopy((store.get(pid), store.records(pid, 'run'), store.records(pid, 'user_task')))
    result = view(store, pid)
    assert result['status'] == 'ready' and result['draft_coverage']['complete']
    assert not result['auto_allowed'] and not result['needs_organize']
    assert before == (store.get(pid), store.records(pid, 'run'), store.records(pid, 'user_task'))
    assert all(item['selection_status'] == 'candidate' for item in store.get(pid)['items'])
    assert not store.get(pid).get('requirement_confirmation_mode')


def test_missing_acceptance_in_old_ingest_gets_one_whole_table_draft_attempt(prepared):
    store, pid = prepared
    _rows(store, pid, acceptance=False); _legacy_task(store, pid)
    result = view(store, pid)
    assert result['status'] == 'needs_organize' and result['auto_allowed']
    assert result['draft_coverage']['missing_acceptance_ids'] == ['REQ-DRAFT']
    assert result['draft_coverage']['missing_context_fields'] == []
    # The upgraded task can remain partial; returning to the page cannot spend
    # again merely because the model did not manage to fill every field.
    task = store.get_record(pid, 'TASK-LEGACY-NEEDS-DRAFT', 'user_task')
    store.record(pid, 'user_task', dict(task, status='partial', preparation_draft_schema=DRAFT_SCHEMA,
                                      id='TASK-DRAFT-UPGRADE'), 'TASK-DRAFT-UPGRADE')
    before = copy.deepcopy((store.get(pid), store.records(pid, 'run'), store.records(pid, 'user_task')))
    first = view(store, pid); second = view(store, pid)
    assert first == second and first['status'] == 'ready_with_limits'
    assert not first['auto_allowed'] and not first['needs_organize']
    assert first['draft_coverage']['missing_acceptance_ids'] == ['REQ-DRAFT']
    assert before == (store.get(pid), store.records(pid, 'run'), store.records(pid, 'user_task'))


@pytest.mark.parametrize('status', ['failed', 'cancelled', 'paused_budget'])
def test_upgrade_failure_preserves_existing_draft_and_requires_explicit_retry(prepared, status):
    store, pid = prepared
    p = _rows(store, pid, acceptance=False); _legacy_task(store, pid)
    prior = store.get_record(pid, 'TASK-LEGACY-NEEDS-DRAFT', 'user_task')
    store.record(pid, 'user_task', dict(prior, status=status, run_ids=[], preparation_draft_schema=DRAFT_SCHEMA,
                                      id='TASK-UPGRADE-FAILED'), 'TASK-UPGRADE-FAILED')
    first = view(store, pid); second = view(store, pid)
    assert first == second and first['status'] == 'failed'
    assert first['retry_requires_explicit'] and not first['auto_allowed']
    assert store.get(pid) == p and len(store.records(pid, 'user_task')) == 3


def test_new_drafting_contract_without_any_reviewable_output_is_not_ready(prepared):
    store, pid = prepared
    with store.edit(pid, store.get(pid)['revision'], '合成零整理结果') as (project, _):
        project.pop('product_context_proposal', None)
    first = view(store, pid); second = view(store, pid)
    assert first == second and first['status'] == 'failed'
    assert not first['auto_allowed'] and first['needs_organize']
    assert not first['draft_coverage']['has_usable_draft']
    assert store.get(pid)['sources'] and not store.get(pid)['items']


def test_new_local_failure_does_not_mask_whole_table_preparation(prepared):
    store, pid = prepared
    _rows(store, pid, acceptance=False); _legacy_task(store, pid)
    global_task = store.get_record(pid, 'TASK-LEGACY-NEEDS-DRAFT', 'user_task')
    store.record(pid, 'user_task', dict(global_task, action='clarify', status='failed',
        target=dict(kind='item', id='REQ-DRAFT'), id='TASK-LOCAL-FAILED'), 'TASK-LOCAL-FAILED')
    result = view(store, pid)
    assert result['status'] == 'needs_organize' and result['auto_allowed']
    assert result['task_id'] == 'TASK-LEGACY-NEEDS-DRAFT'


@pytest.mark.parametrize('status', ['rejected', 'deferred', 'excluded'])
def test_unavailable_acceptance_does_not_count_as_a_prepared_draft(prepared, status):
    store, pid = prepared
    project = _rows(store, pid)
    project['items'][-1]['selection_status'] = status
    result = draft_coverage(project)
    assert result['missing_acceptance_ids'] == ['REQ-DRAFT'] and not result['complete']


def test_only_current_scope_and_available_revision_candidates_count(prepared):
    store, pid = prepared
    project = _rows(store, pid)
    original = project['items'][-1]; original['statement'] = ''
    candidate = dict(original, id='AC-REVISED', target_item_id=original['id'], action='revise',
                     statement='进入电站告警列表后，显示该电站告警记录。')
    project['items'].append(candidate)
    extra = dict(project['items'][0], id='REQ-OTHER', title='不在本期的历史查询')
    project['items'].append(extra)
    project['product_context'] = dict(scope_ids=['REQ-DRAFT'])
    assert draft_coverage(project)['complete']
    candidate['superseded_by'] = 'AC-NEWER'
    assert draft_coverage(project)['missing_acceptance_ids'] == ['REQ-DRAFT']


def test_legacy_direct_ingest_without_acceptance_can_upgrade_once(prepared):
    store, pid = prepared
    _rows(store, pid, acceptance=False)
    # Direct legacy runs predate task envelopes. Ignore the other task only to
    # exercise this compatibility path, without modifying any business content.
    with store.connect() as db:
        db.execute("DELETE FROM records WHERE project_id=? AND kind='user_task'", (pid,))
    run_id = store.record(pid, 'run', dict(stage='ingest', revision=store.get(pid)['revision'],
                                          status='awaiting_user', result_applied=True))
    result = view(store, pid)
    assert result['status'] == 'needs_organize' and result['auto_allowed']
    assert result['run_id'] == run_id
    store.update_record(pid, run_id, 'run', preparation_draft_schema=DRAFT_SCHEMA)
    result = view(store, pid)
    assert result['status'] == 'ready_with_limits' and not result['auto_allowed']
