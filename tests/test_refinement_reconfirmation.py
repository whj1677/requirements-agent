"""A repeated table review preserves valid document inputs, never stale proof."""
import copy

import pytest

from app.core import brief_hash
from app.requirement_confirmation import confirmation_current
from app.store import Store
from tests.test_refinement_confirmation import api, grouped, payload, post, critical
from tests.test_requirement_batch_confirmation import setup, item, source_files


def reviewed_payload(project, answers, **changes):
    body = payload(project, answers=answers, **changes)
    rows = {row['requirement_id']: row for row in body['rows']}
    questions = {q['id']: q for q in project['questions']}
    body['answer_implementations'] = {answer['question_id']: [
        dict(item_id=iid, field='behavior.permissions', quote=rows[iid]['behavior']['permissions'])
        for iid in questions[answer['question_id']]['related_refs'] if iid in rows]
        for answer in answers}
    return body


def confirmed_project(store, *, group=False):
    project = grouped(store) if group else critical(store, setup(store))
    answers = ([dict(question_id='Q-leaf-1', answer='只可查看自己的记录。'),
                dict(question_id='Q-leaf-2', answer='只可查看运维发布的模板。')] if group else
               [dict(question_id='Q-0001', answer='不可修改其他用户的记录。')])
    body = payload(project, answers=answers)
    questions = {q['id']: q for q in project['questions']}
    body['answer_implementations'] = {}
    for answer in answers:
        refs = questions[answer['question_id']]['related_refs']
        witnesses = []
        for row in body['rows']:
            if row['requirement_id'] not in refs:
                continue
            row['behavior']['permissions'] = answer['answer']
            witnesses.append(dict(item_id=row['requirement_id'], field='behavior.permissions',
                                  quote=answer['answer']))
        body['answer_implementations'][answer['question_id']] = witnesses
    with api(store) as client:
        result = post(client, project, body).json()
    assert result['confirmed'], result
    assert result['destination'] == 'delivery' and not result['implementation_pending_ids']
    project = store.get(project['id'])
    with store.edit(project['id'], project['revision'], '合成基于已确认输入的PRD') as (draft, _):
        draft['documents']['prd'] = dict(id='DOC-confirmed', brief_hash=brief_hash(draft),
            draft_revision=draft['revision'], content=dict(sections=[]))
    return store.get(project['id']), answers


def reconfirm(store, project, answers, **overrides):
    body = reviewed_payload(project, answers, idempotency_key='repeat-whole-table-review-0002')
    body.update(overrides)
    with api(store) as client:
        result = post(client, project, body).json()
    assert result['confirmed'], result
    return store.get(project['id'])


@pytest.mark.parametrize('group', [False, True])
def test_repeat_confirmation_preserves_existing_prd_inputs_and_original_reviews(tmp_path, group):
    store = Store(tmp_path)
    project, answers = confirmed_project(store, group=group)
    files = source_files(store)
    result = reconfirm(store, project, answers)
    assert result['revision'] == project['revision'] + 1
    assert result['items'] == project['items']
    assert result['questions'] == project['questions']
    assert result['documents'] == project['documents']
    assert result['decision_answers'] == project['decision_answers']
    assert source_files(store) == files
    assert brief_hash(result) == project['documents']['prd']['brief_hash']


@pytest.mark.parametrize('field', ['statement', 'acceptance', 'answer', 'context', 'new_acceptance'])
def test_real_input_changes_still_expire_prd_and_keep_unaffected_row_reviews(tmp_path, field):
    store = Store(tmp_path)
    project, answers = confirmed_project(store)
    body = reviewed_payload(project, copy.deepcopy(answers),
                            idempotency_key='changed-whole-table-review-0002')
    if field == 'statement':
        body['rows'][0]['statement'] += '允许按站点筛选。'
    elif field == 'acceptance':
        body['rows'][0]['acceptance_items'][0]['statement'] += '按站点筛选后显示对应记录。'
    elif field == 'answer':
        body['answers'][0]['answer'] = '可以修改其他用户的记录。'
        body['rows'][0]['behavior']['permissions'] = body['answers'][0]['answer']
        body['answer_implementations']['Q-0001'][0]['quote'] = body['answers'][0]['answer']
    elif field == 'context':
        body['context']['values']['users'] = '云平台运维人员'
    elif field == 'new_acceptance':
        body['rows'][0]['acceptance_items'].append(dict(statement='筛选后显示对应站点记录。'))
    with api(store) as client:
        result = post(client, project, body).json()
    assert result['confirmed'], result
    saved = store.get(project['id'])
    assert brief_hash(saved) != project['documents']['prd']['brief_hash']
    assert saved['documents'] == project['documents']
    assert item(saved, 'REQ-0002') == item(project, 'REQ-0002')
    if field in ('statement', 'acceptance', 'new_acceptance'):
        assert item(saved, 'REQ-0001')['requirement_confirmation'] != item(project, 'REQ-0001')['requirement_confirmation']
    if field == 'statement':
        assert item(saved, 'REQ-0001')['content_version'] == item(project, 'REQ-0001')['content_version'] + 1
    if field == 'answer':
        assert saved['questions'][0]['decision_answer_id'] != project['questions'][0]['decision_answer_id']


def test_changed_source_version_requires_new_row_confirmation(tmp_path):
    store = Store(tmp_path)
    project, answers = confirmed_project(store)
    old_review = copy.deepcopy(item(project, 'REQ-0001')['requirement_confirmation'])
    with store.edit(project['id'], project['revision'], '合成来源版本更新') as (draft, _):
        draft['sources'][0]['version'] = draft['sources'][0].get('version', 1) + 1
    project = store.get(project['id'])
    assert not confirmation_current(project, item(project, 'REQ-0001'))
    result = reconfirm(store, project, answers)
    assert confirmation_current(result, item(result, 'REQ-0001'))
    assert item(result, 'REQ-0001')['requirement_confirmation'] != old_review
    assert brief_hash(result) != project['documents']['prd']['brief_hash']


def test_stale_review_hashes_are_replaced_only_after_current_explicit_confirmation(tmp_path):
    store = Store(tmp_path)
    project, answers = confirmed_project(store)
    with store.edit(project['id'], project['revision'], '合成旧核对凭据失效') as (draft, _):
        item(draft, 'REQ-0001')['requirement_confirmation']['content_hash'] = 'expired'
        item(draft, 'REQ-0001')['behavior_review']['content_hash'] = 'expired'
        draft['questions'][0]['decision_coverage_review']['question_hash'] = 'expired'
        draft['questions'][0]['answer_applications']['REQ-0001']['clause_hash'] = 'expired'
    project = store.get(project['id'])
    assert not confirmation_current(project, item(project, 'REQ-0001'))
    result = reconfirm(store, project, answers)
    assert confirmation_current(result, item(result, 'REQ-0001'))
    assert item(result, 'REQ-0001')['requirement_confirmation']['content_hash'] != 'expired'
    assert item(result, 'REQ-0001')['behavior_review']['content_hash'] != 'expired'
    assert result['questions'][0]['decision_coverage_review']['question_hash'] != 'expired'
    assert result['questions'][0]['answer_applications']['REQ-0001']['clause_hash'] != 'expired'


def test_excluded_source_still_blocks_repeat_confirmation_without_writes(tmp_path):
    store = Store(tmp_path)
    project, answers = confirmed_project(store)
    with store.edit(project['id'], project['revision'], '合成来源已撤回') as (draft, _):
        draft['sources'][0]['excluded'] = True
    project = store.get(project['id'])
    files = source_files(store)
    with api(store) as client:
        result = post(client, project, payload(project, answers=answers,
            idempotency_key='blocked-whole-table-review-0002')).json()
    assert not result['confirmed'], result
    assert store.get(project['id']) == project
    assert source_files(store) == files
