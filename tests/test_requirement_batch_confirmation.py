"""Atomic displayed-row review, with isolated synthetic storage-cloud data."""
import copy
import os
from pathlib import Path

os.environ['RA_DATA_DIR'] = str(Path(__file__).resolve().parents[1] / 'evidence/requirement-batch-confirmation-tests')

import pytest

from app.core import Problem
from app.requirement_confirmation import FIELDS, confirmation_current, linked_acceptance
from app.requirement_batch_confirmation import PREVIEW_KIND, RECEIPT_KIND
from app.store import Store
from app.understanding import coverage_complete
from tests.test_decision_review import api
from tests.test_requirement_confirmation import setup as single_setup


def setup(store):
    p = single_setup(store)
    with store.edit(p['id'], p['revision'], '合成三条储能云需求') as (p, _):
        for n, title in ((2, '电价时段查看'), (3, '站点列表查看')):
            requirement = copy.deepcopy(p['items'][0])
            requirement.update(id='REQ-000' + str(n), title=title,
                statement='用户查看' + title + '。', related_refs=['AC-000' + str(n)],
                behavior=dict(actor='用户', flow='打开' + title, result='显示列表'))
            acceptance = copy.deepcopy(p['items'][2])
            acceptance.update(id='AC-000' + str(n), title=title + '验收',
                statement='用户打开' + title + '后可以查看列表。', related_refs=[requirement['id']])
            p['items'].extend([requirement, acceptance])
        p['product_context']['scope_ids'] = ['REQ-0001', 'REQ-0002', 'REQ-0003']
    return store.get(p['id'])


def row(project, iid, **changes):
    requirement = next(item for item in project['items'] if item['id'] == iid)
    body = dict(requirement_id=iid, statement=requirement['statement'],
        behavior={field: requirement.get('behavior', {}).get(field, '') for field in FIELDS},
        acceptance_items=[dict(id=ac['id'], statement=ac['statement']) for ac in linked_acceptance(project, requirement)
                          if ac['selection_status'] in ('selected', 'candidate')],
        confirmed_question_ids=[])
    body.update(changes)
    return body


def preview(client, p, rows, **extra):
    result = client.post(f'/api/projects/{p["id"]}/requirement-confirmation/batch/preview',
                         json=dict(expected_revision=p['revision'], rows=rows, **extra))
    assert result.status_code == 200, result.text
    return result.json()


def commit(client, p, view, key='batch-review-key-0001', **changes):
    body = dict(expected_revision=p['revision'], preview_id=view['preview_id'],
        preview_hash=view['preview_hash'], idempotency_key=key, review_confirmed=True)
    body.update(changes)
    return client.post(f'/api/projects/{p["id"]}/requirement-confirmation/batch/commit', json=body)


def source_files(store):
    folder = store.folder / 'sources'
    return {path.name: path.read_bytes() for path in folder.iterdir()} if folder.exists() else {}


def item(project, iid):
    return next(row for row in project['items'] if row['id'] == iid)


def test_displayed_rows_once_confirmed_blank_optional_fields_and_unselected_untouched(tmp_path):
    store = Store(tmp_path); p = setup(store); files_before = source_files(store)
    rows = [row(p, 'REQ-0001', statement='两名值班人员查看告警及处理记录。'), row(p, 'REQ-0002')]
    with api(store) as client:
        view = preview(client, p, rows)
        assert view['summary']['requirement_count'] == 2
        assert view['summary']['acceptance_count'] == 2
        assert store.get(p['id']) == p and source_files(store) == files_before
        result = commit(client, p, view)
        assert result.status_code == 200, result.text
        saved = result.json()['project']
        assert saved['revision'] == p['revision'] + 1
        for iid in ('REQ-0001', 'REQ-0002'):
            assert confirmation_current(saved, item(saved, iid))
            assert item(saved, iid)['behavior']['permissions'] == ''
            assert item(saved, iid)['selection_status'] == 'selected'
        for iid in ('REQ-0003', 'AC-0003', 'RULE-0001'):
            assert item(saved, iid) == item(p, iid)
        assert item(saved, 'AC-0001')['selection_status'] == item(saved, 'AC-0002')['selection_status'] == 'selected'
        for source in saved['sources'][len(p['sources']):]:
            assert (store.folder/'sources'/source['id']).is_file()
        repeated = commit(client, p, view)
        assert repeated.status_code == 200 and repeated.json()['repeated']
        assert store.get(p['id']) == saved
        conflict = commit(client, p, view, preview_hash='changed')
        assert conflict.status_code == 409 and conflict.json()['code'] == 'IDEMPOTENCY_CONFLICT'


def test_partial_selection_preserves_other_row_confirmation_and_candidate(tmp_path):
    store = Store(tmp_path); p = setup(store)
    with api(store) as client:
        first = preview(client, p, [row(p, 'REQ-0002')])
        response = commit(client, p, first)
        assert response.status_code == 200, response.text
        p = response.json()['project']; previous = copy.deepcopy(item(p, 'REQ-0002'))
        second = preview(client, p, [row(p, 'REQ-0001')])
        response = commit(client, p, second, key='batch-review-key-0002')
        assert response.status_code == 200, response.text
        saved = response.json()['project']
        assert item(saved, 'REQ-0002') == previous and confirmation_current(saved, previous)
        assert item(saved, 'REQ-0003')['selection_status'] == 'candidate'
        assert item(saved, 'AC-0003')['selection_status'] == 'candidate'


def saved_shared_answer(store, client, p):
    with store.edit(p['id'], p['revision'], '合成跨行单一决定') as (p, _):
        p['questions'][0].update(question='两个列表是否仅供值班人员查看？', blocking=True,
                                 related_refs=['REQ-0001', 'REQ-0002'])
    p = store.get(p['id'])
    response = client.post(f'/api/projects/{p["id"]}/decision-review/answer', json=dict(
        expected_revision=p['revision'], question_id='Q-0001', answer='两个列表都仅供值班人员查看。'))
    assert response.status_code == 200, response.text
    return response.json()['project']


def test_shared_answer_confirms_once_only_when_every_target_is_displayed(tmp_path):
    store = Store(tmp_path); p = setup(store)
    with api(store) as client:
        p = saved_shared_answer(store, client, p)
        rows = [row(p, 'REQ-0001', statement='告警协作列表仅供值班人员查看。',
                    confirmed_question_ids=['Q-0001']),
                row(p, 'REQ-0002', statement='电价时段列表仅供值班人员查看。')]
        view = preview(client, p, rows)
        assert len(view['question_results']) == 1
        assert view['question_results'][0]['requirement_ids'] == ['REQ-0001', 'REQ-0002']
        assert view['confirmed_question_ids'] == ['Q-0001']
        result = commit(client, p, view)
        assert result.status_code == 200, result.text
        q = result.json()['project']['questions'][0]
        assert coverage_complete(q) and q['understanding_status'] == 'applied'
        assert set(q['answer_applications']) == {'REQ-0001', 'REQ-0002'}


def test_shared_answer_with_hidden_target_remains_pending_without_blocking_other_confirmation(tmp_path):
    store = Store(tmp_path); p = setup(store)
    with api(store) as client:
        p = saved_shared_answer(store, client, p)
        view = preview(client, p, [row(p, 'REQ-0001', confirmed_question_ids=['Q-0001'])])
        assert not view['question_results'][0]['can_confirm']
        assert '其他条款' in view['question_results'][0]['reason']
        assert view['summary']['pending_question_count'] == 1
        result = commit(client, p, view)
        assert result.status_code == 200, result.text
        saved = result.json()['project']
        assert confirmation_current(saved, item(saved, 'REQ-0001'))
        assert saved['questions'][0]['understanding_status'] == 'pending'
        assert item(saved, 'REQ-0002') == item(p, 'REQ-0002')


def test_displaying_all_targets_without_witness_keeps_saved_answer_pending(tmp_path):
    store = Store(tmp_path); p = setup(store)
    with api(store) as client:
        p = saved_shared_answer(store, client, p)
        view = preview(client, p, [row(p, 'REQ-0001', confirmed_question_ids=['Q-0001']), row(p, 'REQ-0002')])
        assert view['confirmed_question_ids'] == []
        assert view['question_results'][0]['implementation_code'] == 'ANSWER_IMPLEMENTATION_REQUIRED'
        response = commit(client, p, view)
        assert response.status_code == 200, response.text
        final = response.json()['project']; q = final['questions'][0]
        assert q['status'] == 'answered' and q['answer'] == p['questions'][0]['answer']
        assert q['understanding_status'] == 'pending' and not q.get('applied_item_ids')
        assert all(item(final, iid)['statement'] == item(p, iid)['statement'] for iid in ('REQ-0001','REQ-0002'))


def test_explicit_existing_locations_are_bound_in_batch_preview(tmp_path):
    store = Store(tmp_path); p = setup(store)
    with api(store) as client:
        p = saved_shared_answer(store, client, p)
        with store.edit(p['id'], p['revision'], '合成已表达的权限条文') as (p, _):
            for iid in ('REQ-0001','REQ-0002'):
                item(p, iid)['behavior']['permissions'] = '列表仅供值班人员查看。'
        p = store.get(p['id'])
        witnesses = {'Q-0001':[dict(item_id=iid,field='behavior.permissions',quote='列表仅供值班人员查看。')
                               for iid in ('REQ-0001','REQ-0002')]}
        view = preview(client, p, [row(p, 'REQ-0001', confirmed_question_ids=['Q-0001']), row(p, 'REQ-0002')],
                       answer_implementations=witnesses)
        assert view['answer_implementations'] == witnesses
        assert view['confirmed_question_ids'] == ['Q-0001']
        response = commit(client, p, view)
        assert response.status_code == 200, response.text
        q = response.json()['project']['questions'][0]
        assert q['understanding_status'] == 'applied'
        assert all(record['witnesses'] == [dict(field='behavior.permissions',quote='列表仅供值班人员查看。')]
                   for record in q['answer_applications'].values())


def test_unselected_answer_and_unanswered_critical_question_never_auto_confirm(tmp_path):
    store = Store(tmp_path); p = setup(store)
    with api(store) as client:
        p = saved_shared_answer(store, client, p)
        with store.edit(p['id'], p['revision'], '合成关键权限未答') as (p, _):
            q = copy.deepcopy(p['questions'][0])
            q.update(id='Q-unknown', related_refs=['REQ-0002'], status='open', answer=None,
                     question='非值班人员是否允许导出？', answer_source_refs=[])
            p['questions'].append(q)
        p = store.get(p['id'])
        view = preview(client, p, [row(p, 'REQ-0001'), row(p, 'REQ-0002', confirmed_question_ids=['Q-unknown'])])
        assert view['confirmed_question_ids'] == []
        assert view['summary']['pending_question_count'] == 2
        result = commit(client, p, view)
        assert result.status_code == 200, result.text
        saved = result.json()['project']
        assert all(not coverage_complete(q) for q in saved['questions'])
        missing = result.json()['confirmation_state']['rows'][1]['missing_decisions']
        assert {q['question_id'] for q in missing} == {'Q-0001', 'Q-unknown'}


@pytest.mark.parametrize('corruption', ['hash', 'record', 'revision', 'human'])
def test_bad_or_stale_preview_rolls_back_everything(tmp_path, corruption):
    store = Store(tmp_path); p = setup(store)
    with api(store) as client:
        view = preview(client, p, [row(p, 'REQ-0001'), row(p, 'REQ-0002')])
        extra = {}
        if corruption == 'hash': extra['preview_hash'] = 'tampered'
        elif corruption == 'record':
            rows = copy.deepcopy(view['rows']); rows[0]['after']['statement'] = '偷偷改文案'
            store.update_record(p['id'], view['preview_id'], PREVIEW_KIND, rows=rows)
        elif corruption == 'revision':
            with store.edit(p['id'], p['revision'], '新修改') as (draft, _): draft['name'] = '最新名称'
        else: extra['review_confirmed'] = False
        before = store.get(p['id']); files = source_files(store)
        result = commit(client, p, view, **extra)
        assert result.status_code == 409, result.text
        assert store.get(p['id']) == before and source_files(store) == files
        assert store.records(p['id'], RECEIPT_KIND) == []


def test_second_row_source_failure_rolls_back_first_row_and_staged_manual_source(tmp_path):
    store = Store(tmp_path); p = setup(store)
    with store.edit(p['id'], p['revision'], '为第二行建立独立合成来源') as (p, _):
        source = copy.deepcopy(p['sources'][0]); source['id'] = 'SRC-second'
        source['excerpts'][0].update(id='EX-second', source_id='SRC-second')
        p['sources'].append(source)
        item(p, 'REQ-0002')['source_refs'] = [dict(source_id='SRC-second', excerpt_id='EX-second')]
    p = store.get(p['id'])
    with api(store) as client:
        view = preview(client, p, [row(p, 'REQ-0001', statement='值班人员查看本站点告警。'), row(p, 'REQ-0002')])
        # Inject source unavailability without a revision bump to reach the
        # second-row validation after the first row authored a staged source.
        with store.edit(p['id'], p['revision'], '模拟来源不可用', bump=False) as (p, _):
            p['sources'][-1]['excluded'] = True
        before = store.get(p['id']); files = source_files(store)
        result = commit(client, p, view)
        assert result.status_code == 409 and result.json()['code'] == 'REFERENCE_INVALID'
        assert store.get(p['id']) == before and source_files(store) == files
        assert store.records(p['id'], RECEIPT_KIND) == []


def test_late_database_failure_removes_published_source_files(tmp_path, monkeypatch):
    store = Store(tmp_path); p = setup(store)
    with api(store) as client:
        view = preview(client, p, [row(p, 'REQ-0001', statement='值班人员查看本站点告警。'), row(p, 'REQ-0002')])
        original = store.record
        def fail_audit(pid, kind, value, rid=None, db=None):
            if kind == 'audit':
                assert source_files(store), 'failure injected after staged files were published'
                raise Problem('TEST_DB_FAILURE', '模拟事务尾部写入失败', 409)
            return original(pid, kind, value, rid=rid, db=db)
        monkeypatch.setattr(store, 'record', fail_audit)
        files = source_files(store)
        result = commit(client, p, view)
        assert result.status_code == 409 and result.json()['code'] == 'TEST_DB_FAILURE'
        assert store.get(p['id']) == p and source_files(store) == files
        assert store.records(p['id'], RECEIPT_KIND) == []


def test_duplicate_rows_or_unrelated_answer_ids_rejected_before_preview(tmp_path):
    store = Store(tmp_path); p = setup(store)
    with api(store) as client:
        for rows in ([row(p, 'REQ-0001'), row(p, 'REQ-0001')],
                     [row(p, 'REQ-0002', confirmed_question_ids=['Q-0001'])]):
            response = client.post(f'/api/projects/{p["id"]}/requirement-confirmation/batch/preview',
                json=dict(expected_revision=p['revision'], rows=rows))
            assert response.status_code in (400, 409), response.text
        assert store.get(p['id']) == p
        assert store.records(p['id'], PREVIEW_KIND) == []


def test_shared_acceptance_same_display_applied_once_conflicting_display_rejected(tmp_path):
    store = Store(tmp_path); p = setup(store)
    with store.edit(p['id'], p['revision'], '合成共享验收') as (p, _):
        item(p, 'AC-0001')['related_refs'].append('REQ-0002')
    p = store.get(p['id'])
    with api(store) as client:
        view = preview(client, p, [row(p, 'REQ-0001'), row(p, 'REQ-0002')])
        assert view['summary']['acceptance_count'] == 2
        response = commit(client, p, view)
        assert response.status_code == 200, response.text
        saved = response.json()['project']
        assert confirmation_current(saved, item(saved, 'REQ-0001'))
        assert confirmation_current(saved, item(saved, 'REQ-0002'))
        rows = [row(saved, 'REQ-0001'), row(saved, 'REQ-0002')]
        next(ac for ac in rows[1]['acceptance_items'] if ac['id'] == 'AC-0001')['statement'] = '另一个验收描述。'
        response = client.post(f'/api/projects/{p["id"]}/requirement-confirmation/batch/preview',
            json=dict(expected_revision=saved['revision'], rows=rows))
        assert response.status_code == 409 and response.json()['code'] == 'BATCH_CONTENT_CONFLICT'


def test_hidden_revision_candidate_is_never_adopted_and_invalid_row_is_identified(tmp_path):
    store = Store(tmp_path); p = setup(store)
    with store.edit(p['id'], p['revision'], '未展示的AI修订候选') as (p, _):
        candidate = copy.deepcopy(item(p, 'REQ-0002'))
        candidate.update(id='REQ-hidden', target_item_id='REQ-0002', action='revise',
                         statement='建议增加自动导出全部电价。')
        p['items'].append(candidate)
    p = store.get(p['id'])
    with api(store) as client:
        invalid = row(p, 'REQ-0002', acceptance_items=[])
        response = client.post(f'/api/projects/{p["id"]}/requirement-confirmation/batch/preview',
            json=dict(expected_revision=p['revision'], rows=[row(p, 'REQ-0001'), invalid]))
        assert response.status_code == 400
        assert 'REQ-0002' in response.json()['message'] and '电价时段查看' in response.json()['message']
        view = preview(client, p, [row(p, 'REQ-0001'), row(p, 'REQ-0002')])
        response = commit(client, p, view)
        assert response.status_code == 200, response.text
        saved = response.json()['project']
        assert item(saved, 'REQ-hidden') == item(p, 'REQ-hidden')
        assert item(saved, 'REQ-0002')['statement'] == item(p, 'REQ-0002')['statement']
