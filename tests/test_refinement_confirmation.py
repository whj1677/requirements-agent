"""One-click displayed-table review; synthetic cloud data and zero model calls."""
import copy
import os
from pathlib import Path

os.environ['RA_DATA_DIR'] = str(Path(__file__).resolve().parents[1] / 'evidence/refinement-confirmation-tests')

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.core import Problem, digest
from app.product_flow import status
from app.refinement_confirmation import RECEIPT_KIND, register
from app.requirement_confirmation import confirmation_current
from app.store import Store
from app.understanding import coverage_complete
from tests.test_requirement_batch_confirmation import setup, row, item, source_files


def api(store, **kwargs):
    app = FastAPI()
    @app.exception_handler(Problem)
    async def problem(_, error):
        return JSONResponse(status_code=error.status, content=dict(code=error.code, message=error.message))
    register(app, store)
    return TestClient(app, **kwargs)


def payload(project, **changes):
    body = dict(expected_revision=project['revision'], idempotency_key='whole-table-review-0001', review_confirmed=True,
        context=dict(values=dict(intent='值班人员协作查看云平台运行信息', users='值班人员', change_scope='新增云平台列表查看'),
                     field_states={field: dict(status='known') for field in ('intent', 'users', 'change_scope')},
                     scope_profile=dict(scenario='new_product', dependencies='unknown'),
                     scope_ids=project['product_context']['scope_ids']),
        rows=[row(project, iid, change_type=item(project, iid).get('change_type', 'new'))
              for iid in project['product_context']['scope_ids']], answers=[])
    body.update(changes)
    return body


def post(client, p, body):
    return client.post('/api/projects/' + p['id'] + '/refinement-confirmation', json=body)


def assert_untouched(store, p, files):
    assert store.get(p['id']) == p
    assert source_files(store) == files
    assert store.records(p['id'], RECEIPT_KIND) == []


def critical(store, p, **changes):
    with store.edit(p['id'], p['revision'], '合成关键权限问题') as (draft, _):
        draft['questions'][0].update(question='两名用户可否修改对方记录？', blocking=True, **changes)
    return store.get(p['id'])


def test_one_atomic_submit_confirms_all_rows_and_first_three_stages_only(tmp_path):
    store = Store(tmp_path); p = setup(store); body = payload(p)
    body['rows'][0]['statement'] = '两名值班人员独立记录告警处理过程，双方可查看。'
    body['rows'][1]['acceptance_items'] = [dict(statement='打开电价时段列表后显示当前可查看的时段。')]
    with api(store) as client:
        response = post(client, p, body)
        assert response.status_code == 200, response.text
        result = response.json(); assert result['confirmed'], result
        saved = store.get(p['id'])
        assert result['project'] == saved and saved['revision'] == p['revision'] + 1
        assert result['destination'] == 'delivery'
        assert all(stage['complete'] for stage in status(saved)[:3])
        assert set(saved['stage_checks']) == {'1', '2', '3'}
        assert saved.get('active_baseline_id') is None
        assert saved['documents'] == p['documents']
        for iid in p['product_context']['scope_ids']:
            assert confirmation_current(saved, item(saved, iid))
            assert item(saved, iid)['behavior']['permissions'] == ''
        assert item(saved, 'REQ-0001')['content_version'] == item(p, 'REQ-0001')['content_version'] + 1
        assert item(saved, 'RULE-0001') == item(p, 'RULE-0001')
        for source in saved['sources'][len(p['sources']):]:
            assert (store.folder / 'sources' / source['id']).is_file()
        again = post(client, p, body)
        assert again.status_code == 200 and again.json()['repeated']
        assert store.get(p['id']) == saved
        body['rows'][0]['statement'] += '修改'
        assert post(client, p, body).json()['code'] == 'IDEMPOTENCY_CONFLICT'


@pytest.mark.parametrize('answer', ['', '待确认', '不确定', '未知', '尚未明确', '待补充', '暂不确定'])
def test_critical_blank_or_unknown_answer_rolls_back_every_field_source_and_row(tmp_path, answer):
    store = Store(tmp_path); p = critical(store, setup(store)); files = source_files(store)
    body = payload(p, answers=[dict(question_id='Q-0001', answer=answer)])
    body['rows'][0]['statement'] = '临时修改，失败时不得保存。'
    with api(store) as client:
        result = post(client, p, body).json()
        assert not result['confirmed']
        assert any(issue.get('question_id') == 'Q-0001' for issue in result['issues'])
    assert_untouched(store, p, files)


@pytest.mark.parametrize('field', ['intent', 'users', 'change_scope'])
def test_required_context_reports_inline_location_and_nothing_saved(tmp_path, field):
    store = Store(tmp_path); p = setup(store); files = source_files(store); body = payload(p)
    body['context']['values'][field] = ''
    with api(store) as client:
        result = post(client, p, body).json()
        assert not result['confirmed']
        assert any(issue.get('field') == field and issue['kind'] == 'context' for issue in result['issues'])
    assert_untouched(store, p, files)


def test_modified_existing_requires_current_state_but_priority_is_optional(tmp_path):
    store = Store(tmp_path); p = setup(store); body = payload(p)
    body['context']['scope_profile']['scenario'] = 'modify_existing'
    with api(store) as client:
        result = post(client, p, body).json()
        assert not result['confirmed']
        assert {row.get('field') for row in result['issues']} == {'current_state', 'preserve_scope'}
        body['context']['values']['current_state'] = '现有云平台仅展示单站信息'
        body['context']['field_states']['preserve_scope'] = dict(status='none')
        assert post(client, p, body).json()['confirmed']


@pytest.mark.parametrize('change', ['missing_row', 'duplicate_row', 'unspecified', 'hidden_change_type', 'no_ac', 'no_review'])
def test_incomplete_or_implicit_review_cannot_confirm(tmp_path, change):
    store = Store(tmp_path); p = setup(store); files = source_files(store); body = payload(p)
    if change == 'missing_row': body['rows'].pop()
    if change == 'duplicate_row': body['rows'].append(body['rows'][0])
    if change == 'unspecified': body['rows'][0]['change_type'] = 'unspecified'
    if change == 'hidden_change_type': body['rows'][0].pop('change_type')
    if change == 'no_ac': body['rows'][0]['acceptance_items'] = []
    if change == 'no_review': body['review_confirmed'] = False
    with api(store) as client:
        response = post(client, p, body)
        assert response.status_code == 409 or response.json()['confirmed'] is False
    assert_untouched(store, p, files)


def test_cross_row_answer_reviewed_once_after_all_changed_targets(tmp_path):
    store = Store(tmp_path); p = critical(store, setup(store), related_refs=['REQ-0001', 'REQ-0002'])
    body = payload(p, answers=[dict(question_id='Q-0001', answer='不能修改对方创建的记录。')])
    for line in body['rows'][:2]:
        line['behavior']['permissions'] = '不能修改对方创建的记录。'
        line['confirmed_question_ids'] = ['Q-0001']
    body['answer_implementations'] = {'Q-0001': [
        dict(item_id=line['requirement_id'], field='behavior.permissions',
             quote=line['behavior']['permissions']) for line in body['rows'][:2]]}
    with api(store) as client:
        result = post(client, p, body).json()
        assert result['confirmed'], result
    saved = store.get(p['id']); q = saved['questions'][0]
    assert coverage_complete(q) and q['understanding_status'] == 'applied'
    assert set(q['answer_applications']) == {'REQ-0001', 'REQ-0002'}
    assert len(saved['decision_answers']) == 1
    assert result['receipt']['confirmed_question_ids'] == ['Q-0001']
    assert all(stage['complete'] for stage in status(saved)[:3])


def grouped(store):
    p = setup(store)
    with store.edit(p['id'], p['revision'], '合成两个相关独立业务决定') as (draft, _):
        q = draft['questions'].pop()
        for n, refs in ((1, ['REQ-0001']), (2, ['REQ-0002'])):
            draft['questions'].append(dict(q, id='Q-leaf-' + str(n), blocking=True,
                question='告警权限？' if n == 1 else '电价权限？', related_refs=refs,
                decision_group_id='QG-rights', decision_group_title='两个云平台模块的权限',
                decision_coverage='pending'))
        draft['workflow_mode'] = 'shared-draft'
    return store.get(p['id'])


def test_group_leaf_answers_are_explicit_and_saved_once_with_direct_bindings(tmp_path,monkeypatch):
    store = Store(tmp_path); p = grouped(store)
    body = payload(p, answers=[dict(question_id='Q-leaf-1', answer='只能查看自己的记录。'),
                              dict(question_id='Q-leaf-2', answer='可查看运维发布的模板。')])
    body['answer_implementations'] = {}
    for n, line in enumerate(body['rows'][:2], 1):
        line['behavior']['permissions'] = body['answers'][n - 1]['answer']
        body['answer_implementations']['Q-leaf-' + str(n)] = [
            dict(item_id=line['requirement_id'], field='behavior.permissions',
                 quote=line['behavior']['permissions'])]
    def unexpected_inference(*args,**kwargs):
        raise AssertionError('Explicitly reviewed witnesses must reach every batch stage without deriving replacements')
    monkeypatch.setattr('app.answer_implementation.derive_changed_witnesses',unexpected_inference)
    with api(store) as client:
        result = post(client, p, body).json()
        assert result['confirmed'], result
    saved = store.get(p['id'])
    assert len(saved['decision_answers']) == 1
    for n, q in enumerate(saved['questions'], 1):
        assert q['answer'] == body['answers'][n - 1]['answer']
        assert q['understanding_status'] == 'applied' and coverage_complete(q)
        assert set(q['answer_applications']) == {'REQ-000' + str(n)}
    assert all(stage['complete'] for stage in status(saved)[:3])


def test_group_leaf_omission_cannot_infer_answer_from_sibling(tmp_path):
    store = Store(tmp_path); p = grouped(store); files = source_files(store)
    body = payload(p, answers=[dict(question_id='Q-leaf-1', answer='都可以查看。')])
    with api(store) as client:
        result = post(client, p, body).json()
        assert not result['confirmed'] and result['issues'][0]['kind'] == 'question'
    assert_untouched(store, p, files)


def test_unshown_direct_clause_is_not_silently_reviewed(tmp_path):
    store = Store(tmp_path); p = critical(store, setup(store), related_refs=['REQ-0001', 'RULE-0001'])
    files = source_files(store)
    with api(store) as client:
        result = post(client, p, payload(p, answers=[dict(question_id='Q-0001', answer='不能修改。')])).json()
        assert not result['confirmed']
        assert item(p, 'RULE-0001')['title'] in result['message'] and 'RULE-0001' not in result['message']
        assert result['issues'][0]['question_id'] == 'Q-0001'
        assert result['issues'][0]['kind'] == 'clause' and result['issues'][0]['item_id'] == 'RULE-0001'
    assert_untouched(store, p, files)


def test_unanswered_critical_omission_does_not_bypass_gate(tmp_path):
    store = Store(tmp_path); p = critical(store, setup(store)); files = source_files(store)
    with api(store) as client:
        result = post(client, p, payload(p)).json()
        assert not result['confirmed'] and any(row.get('question_id') == 'Q-0001' for row in result['issues'])
    assert_untouched(store, p, files)


def test_stale_revision_and_invalid_sources_never_write(tmp_path):
    store = Store(tmp_path); p = setup(store); body = payload(p)
    with store.edit(p['id'], p['revision'], '模拟来源不可用') as (draft, _):
        draft['sources'][0]['excluded'] = True
    latest = store.get(p['id']); files = source_files(store)
    with api(store) as client:
        assert post(client, p, body).json()['code'] == 'STALE_REVISION'
        body['expected_revision'] = latest['revision']
        result = post(client, latest, body).json()
        assert not result['confirmed'] and result['issues'][0]['kind'] == 'source'
    assert_untouched(store, latest, files)


def test_late_database_failure_cleans_all_staged_sources(tmp_path, monkeypatch):
    store = Store(tmp_path); p = critical(store, setup(store)); files = source_files(store)
    original = store.record
    def fail(pid, kind, value, **kwargs):
        if kind == 'audit': raise RuntimeError('offline injected commit failure')
        return original(pid, kind, value, **kwargs)
    monkeypatch.setattr(store, 'record', fail)
    body = payload(p, answers=[dict(question_id='Q-0001', answer='不能修改对方记录。')])
    body['rows'][0]['statement'] += ' 双方可以查看。'
    with api(store, raise_server_exceptions=False) as client:
        assert post(client, p, body).status_code == 500
    assert_untouched(store, p, files)


def test_old_receipt_cannot_confirm_a_later_changed_project(tmp_path):
    store = Store(tmp_path); p = setup(store); body = payload(p)
    with api(store) as client:
        first = post(client, p, body).json(); assert first['confirmed'], first
        with store.edit(p['id'], first['project']['revision'], '确认后另一个窗口修改') as (draft, _):
            draft['items'][0]['statement'] += ' 最新改动待重新核对。'
        latest = store.get(p['id']); files = source_files(store)
        repeated = post(client, p, body)
        assert repeated.status_code == 409 and repeated.json()['code'] == 'STALE_REVISION'
    assert store.get(p['id']) == latest and source_files(store) == files


def test_global_business_answer_requires_explicit_review_without_invented_relation(tmp_path):
    store = Store(tmp_path); p = critical(store, setup(store), related_refs=[])
    body = payload(p, answers=[dict(question_id='Q-0001', answer='本期仅提供网页端。')])
    with api(store) as client:
        result = post(client, p, body).json(); assert result['confirmed'], result
    question = store.get(p['id'])['questions'][0]
    assert question['related_refs'] == [] and question['answer_applications'] == {}
    assert coverage_complete(question)


def test_shared_acceptance_conflict_cannot_choose_one_silently(tmp_path):
    store = Store(tmp_path); p = setup(store)
    with store.edit(p['id'], p['revision'], '合成共享验收') as (draft, _):
        item(draft, 'AC-0001')['related_refs'].append('REQ-0002')
    p = store.get(p['id']); files = source_files(store); body = payload(p)
    shared = next(ac for ac in body['rows'][1]['acceptance_items'] if ac['id'] == 'AC-0001')
    shared['statement'] = '同一验收的另一份不一致内容。'
    with api(store) as client:
        result = post(client, p, body).json()
        assert not result['confirmed'] and result['code'] == 'BATCH_CONTENT_CONFLICT'
    assert_untouched(store, p, files)


def test_group_saved_original_is_reused_and_history_retained(tmp_path):
    from app.decision_review import save_raw_answer
    store = Store(tmp_path); p = grouped(store)
    with store.edit(p['id'], p['revision'], '合成已有原话') as (draft, _):
        saved = save_raw_answer(draft, 'QG-rights', '只能查看自己的记录。可查看运维发布的模板。', store)
    p = store.get(p['id'])
    body = payload(p, answers=[dict(question_id='Q-leaf-1', answer='只能查看自己的记录。'),
                              dict(question_id='Q-leaf-2', answer='可查看运维发布的模板。')])
    with api(store) as client:
        result = post(client, p, body).json(); assert result['confirmed'], result
    final = store.get(p['id'])
    assert final['decision_answers'] == p['decision_answers']
    assert all(q['decision_answer_id'] == saved['id'] for q in final['questions'])


def test_legacy_unsplit_compound_question_is_not_closed_by_nonempty_text(tmp_path):
    from app.understanding import question_fingerprint
    store = Store(tmp_path); p = critical(store, setup(store))
    with store.edit(p['id'], p['revision'], '合成未拆开的旧复合题') as (draft, _):
        q = draft['questions'][0]
        q.update(answer='不能修改，但可以查看。', status='answered')
        q['decision_review_proposal'] = dict(question_hash=digest([question_fingerprint(q)]),
            answer_hash=digest(q['answer']), decisions=[{'question':'能否查看？'}, {'question':'能否修改？'}])
    p = store.get(p['id']); files = source_files(store)
    with api(store) as client:
        result = post(client, p, payload(p, answers=[dict(question_id='Q-0001', answer='不能修改，但可以查看。')])).json()
        assert not result['confirmed'] and result['issues'][0]['question_id'] == 'Q-0001'
    assert_untouched(store, p, files)


def test_main_factory_registers_atomic_endpoint_with_isolated_data(tmp_path, monkeypatch):
    import app.config
    monkeypatch.setattr(app.config, 'dotenv_values', lambda **_: {})
    from app.main import create_app
    app = create_app(tmp_path, access_token=None, env_path=tmp_path / 'absent.env')
    assert any(getattr(route, 'path', None) == '/api/projects/{pid}/refinement-confirmation' for route in app.routes)


def test_excluded_group_leaf_stays_unchanged_and_is_not_required(tmp_path):
    store = Store(tmp_path); p = grouped(store)
    with store.edit(p['id'], p['revision'], '第二子决定明确移出本期') as (draft, _):
        draft['questions'][1]['out_of_scope_reason'] = '本期不处理电价权限改动'
    p = store.get(p['id'])
    with api(store) as client:
        result = post(client, p, payload(p, answers=[dict(question_id='Q-leaf-1', answer='只能查看自己的记录。')])).json()
        assert result['confirmed'], result
    saved = store.get(p['id'])
    assert saved['questions'][1] == p['questions'][1]
    assert saved['decision_answers'][0]['decision_ids'] == ['Q-leaf-1']
    assert coverage_complete(saved['questions'][0])


def restricted_payload(project, scope=('REQ-0001',), **changes):
    body = payload(project, **changes)
    body['context']['scope_ids'] = list(scope)
    body['rows'] = [row(project, iid, change_type=item(project, iid)['change_type']) for iid in scope]
    return body


@pytest.mark.parametrize('refs', [['REQ-0002'], ['AC-0002']])
def test_excluded_requirement_question_is_deferred_without_changing_answer(tmp_path, refs):
    store = Store(tmp_path); p = critical(store, setup(store), related_refs=refs)
    with store.edit(p['id'], p['revision'], '保留尚未完成核对的实际原话') as (draft, _):
        draft['questions'][0].update(answer='不能修改对方记录。', status='answered', understanding_status='pending')
    p = store.get(p['id'])
    with api(store) as client:
        result = post(client, p, restricted_payload(p)).json()
        assert result['confirmed'], result
    saved = store.get(p['id']); q = saved['questions'][0]
    assert q['answer'] == p['questions'][0]['answer'] and q['status'] == 'answered'
    assert q['understanding_status'] == 'pending'
    assert q['scope_exclusion']['requirement_ids'] == ['REQ-0002']
    assert q['out_of_scope_reason'] == q['scope_exclusion']['reason']
    assert item(saved, 'REQ-0002') == item(p, 'REQ-0002')
    assert all(stage['complete'] for stage in status(saved)[:3])


def test_scope_reinclude_requires_review_and_failed_submission_keeps_prior_deferral(tmp_path):
    store = Store(tmp_path); p = critical(store, setup(store), related_refs=['REQ-0002'])
    with api(store) as client:
        first = post(client, p, restricted_payload(p)).json()
        assert first['confirmed'], first
        excluded = store.get(p['id']); files = source_files(store)
        reinclude = restricted_payload(excluded, ('REQ-0001', 'REQ-0002'), idempotency_key='table-reinclude-0001')
        rejected = post(client, excluded, reinclude).json()
        assert not rejected['confirmed'] and any(i.get('question_id') == 'Q-0001' for i in rejected['issues'])
        assert store.get(p['id']) == excluded and source_files(store) == files
        reinclude['answers'] = [dict(question_id='Q-0001', answer='不能修改对方记录。')]
        reinclude['rows'][1]['behavior']['permissions'] = '不能修改对方记录。'
        reinclude['answer_implementations'] = {'Q-0001': [dict(item_id='REQ-0002',
            field='behavior.permissions', quote='不能修改对方记录。')]}
        accepted = post(client, excluded, reinclude).json()
        assert accepted['confirmed'], accepted
    q = store.get(p['id'])['questions'][0]
    assert not q.get('out_of_scope_reason') and 'scope_exclusion' not in q
    assert coverage_complete(q) and q['understanding_status'] == 'applied'


def test_scope_exclusion_is_rolled_back_when_another_row_is_incomplete(tmp_path):
    store = Store(tmp_path); p = critical(store, setup(store), related_refs=['REQ-0002'])
    files = source_files(store); body = restricted_payload(p)
    body['rows'][0]['acceptance_items'] = []
    with api(store) as client:
        result = post(client, p, body).json()
        assert not result['confirmed']
    assert_untouched(store, p, files)


@pytest.mark.parametrize('refs', [[], ['REQ-0001', 'REQ-0002'], ['REQ-0002', 'RULE-0001'], ['REQ-0002', 'missing']])
def test_global_mixed_or_uncertain_question_is_never_implicitly_excluded(tmp_path, refs):
    store = Store(tmp_path); p = critical(store, setup(store), related_refs=refs); files = source_files(store)
    with api(store) as client:
        result = post(client, p, restricted_payload(p)).json()
        assert not result['confirmed'] and any(i.get('question_id') == 'Q-0001' for i in result['issues'])
    assert_untouched(store, p, files)


def test_human_out_of_scope_reason_is_preserved_when_requirement_is_included(tmp_path):
    store = Store(tmp_path); p = critical(store, setup(store), related_refs=['REQ-0002'])
    with store.edit(p['id'], p['revision'], '人工明确暂不解决此业务问题') as (draft, _):
        draft['questions'][0].update(out_of_scope_reason='产品经理明确另一期处理', scope_decided_at='human-choice')
    p = store.get(p['id']); original = copy.deepcopy(p['questions'][0])
    with api(store) as client:
        result = post(client, p, payload(p)).json(); assert result['confirmed'], result
    assert store.get(p['id'])['questions'][0] == original


def test_scope_excludes_only_the_outside_leaf_in_cross_requirement_group(tmp_path):
    store = Store(tmp_path); p = grouped(store)
    with api(store) as client:
        result = post(client, p, restricted_payload(p, answers=[dict(question_id='Q-leaf-1', answer='只能查看自己的记录。')])).json()
        assert result['confirmed'], result
    saved = store.get(p['id'])
    assert coverage_complete(saved['questions'][0])
    assert saved['questions'][1]['status'] == p['questions'][1]['status']
    assert saved['questions'][1]['answer'] == p['questions'][1]['answer']
    assert saved['questions'][1]['scope_exclusion']['requirement_ids'] == ['REQ-0002']


@pytest.mark.parametrize('field,value', [('intent', '未知'), ('users', '待确认'), ('change_scope', '尚未明确')])
def test_known_status_cannot_disguise_unknown_required_context(tmp_path, field, value):
    store = Store(tmp_path); p = setup(store); files = source_files(store); body = payload(p)
    body['context']['values'][field] = value
    body['context']['field_states'][field] = dict(status='known')
    with api(store) as client:
        result = post(client, p, body).json()
        assert not result['confirmed']
        assert any(i.get('field') == field and i['kind'] == 'context' for i in result['issues'])
    assert_untouched(store, p, files)


def test_optional_context_may_stay_explicitly_unknown(tmp_path):
    store = Store(tmp_path); p = setup(store); body = payload(p)
    body['context']['values']['priority'] = '待确认'
    body['context']['field_states']['priority'] = dict(status='unknown')
    with api(store) as client:
        result = post(client, p, body).json(); assert result['confirmed'], result
    final = store.get(p['id'])
    assert final['product_context']['priority'] == '待确认'
    assert final['context_field_states']['priority']['status'] == 'unknown'


def test_previously_confirmed_answer_is_rechecked_after_scope_reinclude(tmp_path):
    store = Store(tmp_path); p = critical(store, setup(store), related_refs=['REQ-0002'])
    with api(store) as client:
        first = post(client, p, payload(p, answers=[dict(question_id='Q-0001', answer='不能修改对方记录。')])).json()
        assert first['confirmed'], first
        confirmed = first['project']
        excluded = post(client, confirmed, restricted_payload(confirmed, idempotency_key='scope-exclusion-again')).json()
        assert excluded['confirmed'], excluded
        saved = excluded['project']; files = source_files(store)
        assert saved['questions'][0]['answer'] == '不能修改对方记录。'
        body = restricted_payload(saved, ('REQ-0001', 'REQ-0002'), idempotency_key='scope-reinclude-again')
        result = post(client, saved, body).json()
        assert not result['confirmed'] and any(i.get('question_id') == 'Q-0001' for i in result['issues'])
        assert store.get(p['id']) == saved and source_files(store) == files


def test_later_human_reason_owns_previous_automatic_scope_marker(tmp_path):
    store = Store(tmp_path); p = critical(store, setup(store), related_refs=['REQ-0002'])
    with api(store) as client:
        first = post(client, p, restricted_payload(p)).json()
        assert first['confirmed'], first
        with store.edit(p['id'], first['project']['revision'], '人工另行决定问题范围') as (draft, _):
            draft['questions'][0].update(out_of_scope_reason='人工决定另一个版本再处理', scope_decided_at='manual-later')
        human = store.get(p['id'])
        body = restricted_payload(human, ('REQ-0001', 'REQ-0002'), idempotency_key='human-scope-preserved')
        result = post(client, human, body).json(); assert result['confirmed'], result
    q = store.get(p['id'])['questions'][0]
    assert q['out_of_scope_reason'] == '人工决定另一个版本再处理'
    assert q['scope_decided_at'] == 'manual-later' and 'scope_exclusion' not in q
    assert q['status'] == human['questions'][0]['status'] and q['answer'] == human['questions'][0]['answer']


def test_whole_table_confirms_displayed_acceptance_revision_without_new_human_source_for_ai_text(tmp_path):
    from tests.test_requirement_confirmation import acceptance_revision
    store = Store(tmp_path); p = acceptance_revision(store, setup(store)); candidate = p['items'][-1]
    body = payload(p)
    body['rows'][0]['acceptance_items'] = [dict(id='AC-0001', statement=candidate['statement'],
                                                candidate_id=candidate['id'])]
    with api(store) as client:
        response = post(client, p, body)
        assert response.status_code == 200, response.text
        result = response.json(); assert result['confirmed'], result
        assert result['destination'] == 'delivery'
        final = store.get(p['id']); ac = item(final, 'AC-0001')
        assert ac['statement'] == candidate['statement']
        assert ac['confirmed_revisions'][0]['candidate_id'] == candidate['id']
        assert item(final, candidate['id'])['selection_status'] == 'deferred'
        assert all(ref in ac['source_refs'] for ref in candidate['source_refs'])
        assert confirmation_current(final, item(final, 'REQ-0001'))
        # Context edits have their own source; none re-label the AI AC as human text.
        for source in final['sources'][len(p['sources']):]:
            assert candidate['statement'] not in (store.folder / 'sources' / source['id']).read_text(encoding='utf-8')



def linked_rule_project(store, *, blocking=True):
    p = setup(store)
    with store.edit(p['id'], p['revision'], '合成跨条款云平台可见范围问题') as (draft, _):
        rule = item(draft, 'RULE-0001')
        rule.update(selection_status='candidate', title='电价模板可见范围',
                    statement='业主查看自己和运维账号创建的电价模板。',
                    behavior={'permissions': '业主查看自己和运维账号创建的电价模板。'},
                    related_refs=['REQ-0001'])
        draft['questions'][0].update(blocking=blocking, related_refs=['REQ-0001', 'RULE-0001'],
                                    question='储能收益页面的账号可见范围是什么？')
    return store.get(p['id'])


def linked_clause(p, iid='RULE-0001', **changes):
    from app.requirement_confirmation import FIELDS
    original = item(p, iid)
    result = dict(item_id=iid, statement=original['statement'],
                  behavior={field: original.get('behavior', {}).get(field, '') for field in FIELDS})
    result.update(changes)
    return result


def linked_payload(p, **changes):
    return payload(p, answers=[dict(question_id='Q-0001', answer='收益页按该账号获准访问的园区查看。')],
                   linked_clauses=[linked_clause(p)], **changes)


def test_explicit_linked_candidate_rule_is_confirmed_without_rewriting_from_answer(tmp_path):
    store = Store(tmp_path); p = linked_rule_project(store)
    with store.edit(p['id'], p['revision'], '原规则已表达的模板查看范围决定') as (draft, _):
        draft['questions'][0]['question'] = '业主可以查看哪些电价模板？'
    p = store.get(p['id']); original = copy.deepcopy(item(p, 'RULE-0001'))
    body = linked_payload(p)
    body['answers'][0]['answer'] = original['statement']
    body['rows'][0]['statement'] = original['statement']
    body['answer_implementations'] = {'Q-0001': [
        dict(item_id='REQ-0001', field='statement', quote=original['statement']),
        dict(item_id='RULE-0001', field='statement', quote=original['statement'])]}
    with api(store) as client:
        result = post(client, p, body).json()
        assert result['confirmed'], result
        saved = store.get(p['id']); rule = item(saved, 'RULE-0001')
        assert rule['selection_status'] == 'selected'
        for key in ('statement', 'behavior', 'source_refs', 'content_version', 'related_refs'):
            assert rule.get(key) == original.get(key), key
        assert result['receipt']['confirmed_clause_ids'] == ['RULE-0001']
        q = saved['questions'][0]
        assert q['understanding_status'] == 'applied' and coverage_complete(q)
        assert set(q['answer_applications']) == {'REQ-0001', 'RULE-0001'}
        assert q['answer_applications']['RULE-0001']['witnesses'] == [
            dict(field='statement', quote=original['statement'])]
        assert all(stage['complete'] for stage in status(saved)[:3])
        assert not any(s['title'] == '人工关联条款确认.txt' for s in saved['sources'])
        assert post(client, p, body).json()['repeated']
        assert store.get(p['id']) == saved


def test_linked_manual_clause_edits_have_sources_and_current_answer_hashes(tmp_path):
    from app.understanding import application_hash
    store = Store(tmp_path); p = linked_rule_project(store)
    with store.edit(p['id'], p['revision'], '模板查看范围待决定') as (draft, _):
        draft['questions'][0]['question'] = '业主可以查看哪些园区的电价模板？'
    p = store.get(p['id'])
    body = linked_payload(p); clause = body['linked_clauses'][0]
    clause['statement'] = '业主查看归属其管理园区的电价模板。'
    clause['behavior']['permissions'] = '业主仅查看获授权园区。'
    body['answers'][0]['answer'] = clause['statement']
    body['rows'][0]['statement'] = clause['statement']
    body['answer_implementations'] = {'Q-0001': [
        dict(item_id='REQ-0001', field='statement', quote=clause['statement']),
        dict(item_id='RULE-0001', field='statement', quote=clause['statement'])]}
    with api(store) as client:
        result = post(client, p, body).json(); assert result['confirmed'], result
    saved = store.get(p['id']); rule = item(saved, 'RULE-0001')
    assert rule['statement'] == clause['statement'] and rule['behavior'] == clause['behavior']
    original = item(p, 'RULE-0001')
    assert rule['content_version'] == original['content_version'] + 1
    assert rule['source_refs'][:len(original['source_refs'])] == original['source_refs']
    authored = [s for s in saved['sources'] if s['title'] == '人工关联条款确认.txt']
    assert len(authored) == 1
    text = (store.folder / 'sources' / authored[0]['id']).read_text(encoding='utf-8')
    assert clause['statement'] in text and clause['behavior']['permissions'] in text
    assert saved['questions'][0]['answer_applications']['RULE-0001']['clause_hash'] == application_hash(rule)
    assert saved['questions'][0]['answer_applications']['RULE-0001']['witnesses'] == [
        dict(field='statement', quote=clause['statement'])]


@pytest.mark.parametrize('change', ['missing', 'unknown', 'unrelated', 'duplicate', 'requirement', 'acceptance',
                                     'deferred', 'rejected', 'revision', 'superseded', 'as_is',
                                     'empty_statement', 'partial_behavior', 'invalid_source', 'extra_field'])
def test_linked_clause_invalid_or_hidden_content_rolls_back_atomically(tmp_path, change):
    store = Store(tmp_path); p = linked_rule_project(store)
    if change in ('unrelated', 'deferred', 'rejected', 'revision', 'superseded', 'as_is', 'invalid_source'):
        with store.edit(p['id'], p['revision'], '合成不可采纳关联规则') as (draft, _):
            rule = item(draft, 'RULE-0001')
            if change == 'unrelated': draft['questions'][0]['related_refs'] = ['REQ-0001']
            if change in ('deferred', 'rejected'): rule['selection_status'] = change
            if change == 'revision': rule['target_item_id'] = 'REQ-0001'
            if change == 'superseded': rule['superseded_by'] = 'RULE-newer'
            if change == 'as_is': rule['applies_to'] = 'as_is'
            if change == 'invalid_source': rule['source_refs'] = [dict(source_id='missing', excerpt_id='missing')]
        p = store.get(p['id'])
    body = linked_payload(p); files = source_files(store)
    body['rows'][0]['statement'] = '失败时不得持久化此条人工需求修改。'
    if change == 'missing': body['linked_clauses'] = []
    if change == 'unknown': body['linked_clauses'][0]['item_id'] = 'RULE-unknown'
    if change == 'duplicate': body['linked_clauses'].append(copy.deepcopy(body['linked_clauses'][0]))
    if change == 'requirement': body['linked_clauses'] = [linked_clause(p, 'REQ-0001')]
    if change == 'acceptance': body['linked_clauses'] = [linked_clause(p, 'AC-0001')]
    if change == 'empty_statement': body['linked_clauses'][0]['statement'] = ' '
    if change == 'partial_behavior': body['linked_clauses'][0]['behavior'].pop('entry')
    if change == 'extra_field': body['linked_clauses'][0]['selection_status'] = 'selected'
    with api(store) as client:
        result = post(client, p, body).json()
        assert not result['confirmed'], result
        assert result['issues'][0]['kind'] == 'clause'
    assert_untouched(store, p, files)


def test_linked_rule_success_still_rolls_back_on_later_required_field_failure(tmp_path):
    store = Store(tmp_path); p = linked_rule_project(store); files = source_files(store)
    body = linked_payload(p); body['linked_clauses'][0]['statement'] += '人工修订内容。'
    body['context']['values']['intent'] = ''
    with api(store) as client:
        result = post(client, p, body).json()
        assert not result['confirmed'] and any(row.get('field') == 'intent' for row in result['issues'])
    assert_untouched(store, p, files)


def test_linked_rule_stale_revision_cannot_select_or_write_sources(tmp_path):
    store = Store(tmp_path); p = linked_rule_project(store); body = linked_payload(p)
    with store.edit(p['id'], p['revision'], '其他人修改规则') as (draft, _):
        item(draft, 'RULE-0001')['statement'] += '仅限本期。'
    current = store.get(p['id']); files = source_files(store)
    with api(store) as client:
        result = post(client, p, body)
        assert result.status_code == 409 and result.json()['code'] == 'STALE_REVISION'
    assert_untouched(store, current, files)


def test_two_questions_share_one_explicit_rule_without_duplicate_review(tmp_path):
    store = Store(tmp_path); p = linked_rule_project(store)
    with store.edit(p['id'], p['revision'], '第二项直接关联决定') as (draft, _):
        draft['questions'][0]['question'] = '电价模板的账号可见范围是什么？'
        draft['questions'].append(dict(copy.deepcopy(draft['questions'][0]), id='Q-0002',
                                      question='电价模板是否按园区显示？', related_refs=['REQ-0002', 'RULE-0001']))
    p = store.get(p['id']); body = linked_payload(p)
    permission = '仅查看账号获授权园区的电价模板。'
    grouping = '电价模板按园区显示。'
    body['answers'][0]['answer'] = permission
    body['answers'].append(dict(question_id='Q-0002', answer=grouping))
    body['rows'][0]['behavior']['permissions'] = permission
    body['rows'][1]['behavior']['data'] = grouping
    clause = body['linked_clauses'][0]
    clause['statement'] = permission + grouping
    clause['behavior']['permissions'] = permission
    clause['behavior']['data'] = grouping
    body['answer_implementations'] = {
        'Q-0001': [dict(item_id=iid, field='behavior.permissions', quote=permission)
                   for iid in ('REQ-0001', 'RULE-0001')],
        'Q-0002': [dict(item_id=iid, field='behavior.data', quote=grouping)
                   for iid in ('REQ-0002', 'RULE-0001')]}
    with api(store) as client:
        result = post(client, p, body).json(); assert result['confirmed'], result
    saved = store.get(p['id'])
    assert result['receipt']['confirmed_clause_ids'] == ['RULE-0001']
    assert set(saved['questions'][0]['answer_applications']) == {'REQ-0001', 'RULE-0001'}
    assert set(saved['questions'][1]['answer_applications']) == {'REQ-0002', 'RULE-0001'}


@pytest.mark.parametrize('answer', [None, '', '待确认'])
def test_optional_unanswered_rule_is_not_silently_selected(tmp_path, answer):
    store = Store(tmp_path); p = linked_rule_project(store, blocking=False)
    body = payload(p, answers=[] if answer is None else [dict(question_id='Q-0001', answer=answer)])
    with api(store) as client:
        result = post(client, p, body).json(); assert result['confirmed'], result
    assert item(store.get(p['id']), 'RULE-0001') == item(p, 'RULE-0001')
    assert result['receipt']['confirmed_clause_ids'] == []


def test_optional_empty_answer_cannot_authorize_submitted_rule_adoption(tmp_path):
    store = Store(tmp_path); p = linked_rule_project(store, blocking=False); files = source_files(store)
    body = linked_payload(p); body['answers'][0]['answer'] = ''
    with api(store) as client:
        result = post(client, p, body).json(); assert not result['confirmed']
    assert_untouched(store, p, files)
