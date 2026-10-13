"""Chapter closure stays in one explicit, isolated no-model decision commit."""
import copy

import pytest

from app.core import Problem
from app.document_discussion import section_question_ids
from app.product_flow import BEHAVIOR, INTAKE, SCOPE, checkpoint, fingerprint, status
from app.store import Store
from tests.helpers import prepared
from tests.test_decision_review import api


def fixture(tmp_path):
    store = Store(tmp_path)
    project = prepared(store)
    with store.edit(project['id'], project['revision'], '合成已核对范围', bump=False) as (p, _):
        p['product_context'] = {key: '合成：' + label for key, label in {**INTAKE, **SCOPE}.items()}
        p['product_context']['scope_ids'] = ['REQ-0001']
        p['items'][0].update(change_type='modified', behavior={key: '合成：' + label for key, label in BEHAVIOR.items()})
        p['questions'][0].update(blocking=True, blocking_stage=3, related_refs=['RULE-0001'])
        for step in (1, 2):
            checkpoint(p, step, fingerprint(p, step))
    project = store.get(project['id'])
    section_id = project['documents']['prd']['content']['sections'][0]['section_id']
    scope = dict(document_id='DOC-prd', section_id=section_id)
    store.record(project['id'], 'run', dict(stage='change', status='awaiting_user',
        revision=project['revision'], result_applied=True,
        discussion_target=dict(kind='document', id=scope['document_id'], section_id=section_id),
        discussion_result=dict(question_ids=['Q-0001'], item_ids=[])))
    return store, project, scope


def preview(client, project, edits=None, **extra):
    saved = client.post(f'/api/projects/{project["id"]}/decision-review/answer', json=dict(
        expected_revision=project['revision'], question_id='Q-0001', answer='相接边界允许保存。'))
    assert saved.status_code == 200, saved.text
    project = saved.json()['project']
    response = client.post(f'/api/projects/{project["id"]}/decision-review/manual-preview', json=dict(
        expected_revision=project['revision'], question_id='Q-0001', edits=edits or [], **extra))
    assert response.status_code == 200, response.text
    view = response.json()
    body = dict(expected_revision=project['revision'], preview_id=view['preview_id'],
                preview_hash=view['preview_hash'], selected_group_ids=['manual-answer'],
                idempotency_key='chapter-confirmation-0001', review_confirmed=True)
    return project, body


def post(client, project, body):
    return client.post(f'/api/projects/{project["id"]}/decision-review/commit', json=body)


def test_chapter_commit_closes_finalized_revision_and_repeat_is_idempotent(tmp_path):
    store, project, scope = fixture(tmp_path)
    with api(store) as client:
        project, body = preview(client, project, [dict(item_id='RULE-0001',
            statement='保存时段重叠时拒绝保存，原数据不变，编辑内容保留。相接边界允许保存。')])
        before = copy.deepcopy(project)
        body['document_discussion'] = scope
        response = post(client, project, body)
        assert response.status_code == 200, response.text
        result = response.json()
        saved = store.get(project['id'])
        assert saved['revision'] == before['revision'] + 1
        assert result['receipt']['document_discussion'] == dict(scope,
            checkpoint_saved=True, complete=True, pending_codes=[])
        assert status(saved)[2]['complete']
        assert saved['stage_checks']['3']['content_hash'] == fingerprint(saved, 3)
        assert saved['stage_checks']['3']['draft_revision'] == saved['revision']
        assert saved['items'][1]['content_version'] == before['items'][1]['content_version'] + 1
        assert saved['documents'] == before['documents']
        assert saved['stage_checks']['1'] == before['stage_checks']['1']
        assert saved['stage_checks']['2'] == before['stage_checks']['2']
        repeated = post(client, project, body)
        assert repeated.status_code == 200 and repeated.json()['repeated'] is True
        assert repeated.json()['receipt'] == result['receipt']
        assert store.get(project['id']) == saved
        without_scope = {key:value for key,value in body.items() if key != 'document_discussion'}
        conflict = post(client, project, without_scope)
        assert conflict.status_code == 409 and conflict.json()['code'] == 'IDEMPOTENCY_CONFLICT'


@pytest.mark.parametrize('gap', ['unrelated_question', 'prior_checkpoint', 'unreviewed_behavior'])
def test_remaining_gaps_save_review_without_confirming_stage(tmp_path, gap):
    store, project, scope = fixture(tmp_path)
    with store.edit(project['id'], project['revision'], '合成其它未闭环事项', bump=False) as (p, _):
        # This decision is already in the original rule. The separate gap must
        # not be hidden by selecting an unrelated, merely nonempty rule.
        p['items'][1]['statement'] += '相接边界允许保存。'
        p['sources'][0]['excerpts'][0]['text'] += '\n相接边界允许保存。'
        if gap == 'unrelated_question':
            p['questions'].append(dict(p['questions'][0], id='Q-other', question='另一个需求的权限是什么？'))
        elif gap == 'prior_checkpoint':
            p['product_context']['users'] = '修改后的使用者，范围尚未重新核对'
        else:
            p['items'][0]['behavior_review_required'] = True
    with api(store) as client:
        project, body = preview(client, store.get(project['id']), answer_implementations={
            'Q-0001': [dict(item_id='RULE-0001', field='statement', quote='相接边界允许保存。')]})
        before = copy.deepcopy(project)
        body['document_discussion'] = scope
        response = post(client, project, body)
        assert response.status_code == 200, response.text
        saved = store.get(project['id'])
        receipt = response.json()['receipt']['document_discussion']
        assert not receipt['checkpoint_saved'] and not receipt['complete'] and receipt['pending_codes']
        assert not status(saved)[2]['complete']
        assert saved.get('stage_checks', {}).get('3') == before.get('stage_checks', {}).get('3')
        assert saved['questions'][0]['understanding_status'] == 'applied'
        assert saved['items'][1] == before['items'][1]
        assert saved['questions'][0]['answer_applications']['RULE-0001']['witnesses'] == [
            dict(field='statement', quote='相接边界允许保存。')]
        if gap == 'unrelated_question':
            assert saved['questions'][1] == before['questions'][1]
        if gap == 'prior_checkpoint':
            assert 'previous_checkpoint_pending' in receipt['pending_codes']


@pytest.mark.parametrize('scope_change', ['wrong_section', 'stale_document', 'unrelated_question', 'unrelated_audit'])
def test_invalid_chapter_ownership_rejects_before_mutation(tmp_path, scope_change):
    store, project, scope = fixture(tmp_path)
    if scope_change == 'unrelated_question':
        with store.connect() as db:
            db.execute("DELETE FROM records WHERE project_id=? AND kind='run'", (project['id'],))
    elif scope_change == 'unrelated_audit':
        with store.connect() as db:
            db.execute("DELETE FROM records WHERE project_id=? AND kind='run'", (project['id'],))
        store.record(project['id'], 'run', dict(stage='change', status='succeeded',
            revision=project['revision'], result_applied=True,
            discussion_target=dict(kind='document', id='DOC-prd', section_id=scope['section_id']),
            discussion_result=dict(question_ids=[], item_ids=[]),
            decision_review=dict(reviewed_question_ids=['Q-0001'])))
    with api(store) as client:
        project, body = preview(client, project)
        before = store.get(project['id'])
        body['document_discussion'] = dict(scope)
        if scope_change == 'wrong_section':
            body['document_discussion']['section_id'] = 'missing'
        elif scope_change == 'stale_document':
            body['document_discussion']['document_id'] = 'DOC-old'
        response = post(client, project, body)
        assert response.status_code == 409, response.text
        assert response.json()['code'] in ('STALE_DOCUMENT', 'DOCUMENT_DISCUSSION_SCOPE')
        assert store.get(project['id']) == before
        assert store.records(project['id'], 'decision_receipt') == []


def test_chapter_checkpoint_and_changes_roll_back_with_failed_receipt(tmp_path, monkeypatch):
    store, project, scope = fixture(tmp_path)
    with api(store) as client:
        project, body = preview(client, project)
        body['document_discussion'] = scope
        before = store.get(project['id'])
        original = store.record

        def fail_receipt(pid, kind, value, rid=None, db=None):
            if kind == 'decision_receipt':
                raise Problem('SYNTHETIC_FAILURE', '合成事务中止', 409)
            return original(pid, kind, value, rid, db)

        monkeypatch.setattr(store, 'record', fail_receipt)
        response = post(client, project, body)
        assert response.status_code == 409 and response.json()['code'] == 'SYNTHETIC_FAILURE'
        assert store.get(project['id']) == before
        assert store.records(project['id'], 'decision_receipt') == []


def test_normal_commit_does_not_gain_implicit_checkpoint(tmp_path):
    store, project, _ = fixture(tmp_path)
    with api(store) as client:
        project, body = preview(client, project)
        response = post(client, project, body)
        assert response.status_code == 200, response.text
        assert 'document_discussion' not in response.json()['receipt']
        assert '3' not in store.get(project['id']).get('stage_checks', {})


def test_stale_preview_rejects_without_checkpoint(tmp_path):
    store, project, scope = fixture(tmp_path)
    with api(store) as client:
        project, body = preview(client, project)
        body['document_discussion'] = scope
        with store.edit(project['id'], project['revision'], '合成提交前并发变更') as (p, _):
            p['name'] = '并发修改后的项目名'
        before = store.get(project['id'])
        response = post(client, project, body)
        assert response.status_code == 409 and response.json()['code'] == 'STALE_REVISION'
        assert store.get(project['id']) == before
        assert store.records(project['id'], 'decision_receipt') == []


@pytest.mark.parametrize('explicit_result', [False, True])
def test_exact_result_does_not_borrow_legacy_audit_ids_but_real_input_fallback_stays_available(tmp_path, explicit_result):
    store, project, scope = fixture(tmp_path)
    input_revision = project['revision']
    with store.edit(project['id'], project['revision'], '合成历史章节产生的新问题', bump=False) as (p, _):
        p['questions'].append(dict(p['questions'][0], id='Q-legacy-new'))
    with store.connect() as db:
        db.execute("DELETE FROM records WHERE project_id=? AND kind='run'", (project['id'],))
    run = dict(stage='change', status='awaiting_user', revision=input_revision, result_applied=True,
               decision_review=dict(reviewed_question_ids=['Q-0001', 'Q-legacy-new']))
    if explicit_result:
        run['discussion_result'] = dict(question_ids=['Q-0001'], item_ids=[])
    rid = store.record(project['id'], 'run', run)
    store.record(project['id'], 'user_task', dict(retry_input=dict(target=dict(kind='document',
        id=scope['document_id'], section_id=scope['section_id'])), run_ids=[rid]))
    with store.connect() as db:
        owned = section_question_ids(store.get(project['id'], db), store, db, scope)
    assert owned == ({'Q-0001'} if explicit_result else {'Q-legacy-new'})
