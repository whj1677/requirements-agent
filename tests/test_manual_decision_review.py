"""Isolated, no-model checks for editing linked clauses before one confirmation."""
import copy

import pytest

from app.core import digest
from app.decision_review import review_question_hash
from app.requirements import behavior_fingerprint, behavior_review_current
from app.store import Store
from app.understanding import applied_targets, coverage_complete
from tests.helpers import prepared
from tests.test_decision_review import api, commit, setup_group


def save_answer(client, project, question_id='Q-0001', text='相接边界允许保存。'):
    response = client.post(f'/api/projects/{project["id"]}/decision-review/answer', json=dict(
        expected_revision=project['revision'], question_id=question_id, answer=text))
    assert response.status_code == 200, response.text
    return response.json()['project']


def preview(client, project, edits=None, **extra):
    body = dict(expected_revision=project['revision'], question_id='Q-0001',
                edits=edits if edits is not None else [dict(item_id='REQ-0001',
                    statement='管理员能够维护电价时段，相接边界允许保存。')])
    body.update(extra)
    return client.post(f'/api/projects/{project["id"]}/decision-review/manual-preview', json=body)


def test_manual_preview_is_non_mutating_and_commit_preserves_all_other_fields(tmp_path):
    store = Store(tmp_path)
    project = prepared(store)
    with store.edit(project['id'], project['revision'], '冻结非编辑字段') as (p, _):
        item = p['items'][0]
        item.update(behavior={'actor':'电价管理员','data':'电价时段'},
                    change_type='unspecified', scope_evidence=[],
                    classification_reason='当前资料不足', arbitrary_extension={'retained':[1,2]})
    project = store.get(project['id'])
    with api(store) as client:
        project = save_answer(client, project)
        before = copy.deepcopy(project)
        files = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob('*'))
        response = preview(client, project)
        assert response.status_code == 200, response.text
        view = response.json()
        assert store.get(project['id']) == before
        assert sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob('*')) == files
        target = view['groups'][0]['targets'][0]
        assert target['before'] == before['items'][0]
        assert target['after']['statement'] != target['before']['statement']
        for key, value in target['before'].items():
            if key != 'statement':
                assert target['after'][key] == value
        result = commit(client, project, view, ['manual-answer'])
        assert result.status_code == 200, result.text
        saved = result.json()['project']
        item = saved['items'][0]
        for key, value in before['items'][0].items():
            if key not in ('statement','source_refs','content_version'):
                assert item[key] == value
        assert item['source_refs'][:len(before['items'][0]['source_refs'])] == before['items'][0]['source_refs']
        assert len(saved['sources']) == len(before['sources']) + 1
        assert saved['questions'][0]['understanding_status'] == 'applied'
        assert applied_targets(saved, saved['questions'][0]) == {'REQ-0001'}
        repeated = commit(client, project, view, ['manual-answer'])
        assert repeated.status_code == 200 and repeated.json()['repeated'] is True
        assert store.get(project['id']) == saved


def test_manual_confirmation_does_not_approve_old_behavior(tmp_path):
    store = Store(tmp_path)
    project = prepared(store)
    with store.edit(project['id'], project['revision'], '冻结已核对说明') as (p, _):
        item = p['items'][0]
        item.update(behavior={'actor':'管理员','flow':'维护电价时段'}, behavior_review_required=True)
        item['behavior_review'] = dict(content_hash=behavior_fingerprint(item), actor='fixture')
    project = store.get(project['id'])
    with api(store) as client:
        project = save_answer(client, project)
        old_review = copy.deepcopy(project['items'][0]['behavior_review'])
        response = preview(client, project)
        assert response.status_code == 200, response.text
        result = commit(client, project, response.json(), ['manual-answer'])
        assert result.status_code == 200, result.text
        saved = result.json()['project']
        assert saved['items'][0]['behavior_review'] == old_review
        assert not behavior_review_current(saved['items'][0])
        assert saved['questions'][0]['understanding_status'] == 'applied'


def test_explicit_behavior_review_and_patch_merge_keep_other_fields(tmp_path):
    store = Store(tmp_path)
    project = prepared(store)
    with store.edit(project['id'], project['revision'], '冻结说明') as (p, _):
        p['items'][0].update(behavior={'actor':'管理员','flow':'维护时段','data':'站点时段'},
                             behavior_review_required=True)
    with api(store) as client:
        project = save_answer(client, store.get(project['id']))
        response = preview(client, project, edits=[dict(item_id='REQ-0001',
            behavior={'flow':'维护时段，相接边界允许保存。'})], review_behavior_item_ids=['REQ-0001'])
        assert response.status_code == 200, response.text
        result = commit(client, project, response.json(), ['manual-answer'])
        assert result.status_code == 200, result.text
        item = result.json()['project']['items'][0]
        assert item['behavior'] == {'actor':'管理员','flow':'维护时段，相接边界允许保存。','data':'站点时段'}
        assert behavior_review_current(item)


def test_goal_is_an_automatic_direct_target_and_cannot_be_omitted(tmp_path):
    store = Store(tmp_path)
    project = prepared(store)
    with store.edit(project['id'], project['revision'], '合成目标关联') as (p, _):
        p['items'].append(dict(copy.deepcopy(p['items'][0]), id='GOAL-0001', kind='goal',
                               title='电价配置目标', statement='支持站点电价时段配置。'))
        p['questions'][0]['related_refs'].append('GOAL-0001')
    with api(store) as client:
        project = save_answer(client, store.get(project['id']))
        response = preview(client, project, edits=[dict(item_id='GOAL-0001',
            statement='支持站点电价时段配置，相接边界允许保存。'),
            dict(item_id='REQ-0001', statement='管理员维护电价时段，相接边界允许保存。')])
        assert response.status_code == 200, response.text
        view = response.json()
        assert {t['id'] for t in view['groups'][0]['targets']} == {'REQ-0001','GOAL-0001'}
        result = commit(client, project, view, ['manual-answer'])
        assert result.status_code == 200, result.text
        saved = result.json()['project']
        assert applied_targets(saved, saved['questions'][0]) == {'REQ-0001','GOAL-0001'}


@pytest.mark.parametrize('edits', [
    [{'item_id':'RULE-0001','statement':'无关规则'}],
    [{'item_id':'REQ-0001','statement':'新正文','selection_status':'selected'}],
    [{'item_id':'REQ-0001','statement':'新正文','source_refs':[]}],
    [{'item_id':'REQ-0001','behavior':{'unknown':'未知字段'}}],
    [{'item_id':'REQ-0001','statement':'新正文'}, {'item_id':'REQ-0001','statement':'重复'}],
])
def test_invalid_edit_has_no_project_or_source_mutation(tmp_path, edits):
    store = Store(tmp_path)
    with api(store) as client:
        project = save_answer(client, prepared(store))
        response = preview(client, project, edits=edits)
        assert response.status_code in (400,409), response.text
        assert store.get(project['id']) == project


def test_group_requires_explicit_decisions_partial_stays_pending(tmp_path):
    store = Store(tmp_path)
    with api(store) as client:
        project = save_answer(client, setup_group(store), 'QG-price', '新电价只能未来生效。迟到电量处理未定。')
        missing = preview(client, project, edits=[], question_id='QG-price')
        assert missing.status_code == 409 and missing.json()['code'] == 'REVIEW_REQUIRED'
        decisions = [dict(decision_id='Q-part-'+str(n), confirmed=n==0,
                         answer_quote='新电价只能未来生效。' if n==0 else '') for n in range(3)]
        response = preview(client, project, question_id='QG-price', decisions=decisions,
            edits=[dict(item_id='REQ-0001', statement='管理员维护电价时段；新电价只能未来生效。')])
        assert response.status_code == 200, response.text
        view = response.json()
        assert [d['coverage'] for d in view['decisions']] == ['clear','pending','pending']
        result = commit(client, project, view, ['manual-answer'])
        assert result.status_code == 200, result.text
        questions = result.json()['project']['questions']
        assert coverage_complete(questions[0]) and questions[0]['understanding_status'] == 'applied'
        assert all(q['decision_coverage']=='pending' and q['understanding_status']=='pending' for q in questions[1:])


@pytest.mark.parametrize('mutation, expected', [
    ('revision','STALE_REVISION'), ('answer','STALE_ANSWER'),
    ('item','STALE_CONTENT'), ('source','STALE_SOURCE')])
def test_stale_manual_preview_never_overwrites_latest_state(tmp_path, mutation, expected):
    store = Store(tmp_path)
    with api(store) as client:
        project = save_answer(client, prepared(store))
        response = preview(client, project)
        assert response.status_code == 200, response.text
        view = response.json()
        with store.edit(project['id'], project['revision'], '模拟并发修改', bump=mutation=='revision') as (p, _):
            if mutation=='answer':
                p['questions'][0]['answer']='相接边界尚未确定。'
            elif mutation=='item':
                p['items'][0]['statement']='当前另一位编辑者的内容。'
            elif mutation=='source':
                p['sources'][0]['sha256']='changed-source'
        current = store.get(project['id'])
        result = commit(client, project, view, ['manual-answer'])
        assert result.status_code == 409, result.text
        assert result.json()['code'] == expected
        assert store.get(project['id']) == current


def test_no_confirmation_no_edit_and_known_compound_not_flat_confirmed(tmp_path):
    store = Store(tmp_path)
    with api(store) as client:
        project = save_answer(client, prepared(store))
        view = preview(client, project).json()
        result = client.post(f'/api/projects/{project["id"]}/decision-review/commit', json=dict(
            expected_revision=project['revision'], preview_id=view['preview_id'],
            preview_hash=view['preview_hash'], selected_group_ids=['manual-answer'],
            idempotency_key='no-confirm-0001', review_confirmed=False))
        assert result.status_code == 409 and store.get(project['id']) == project
        with store.edit(project['id'], project['revision'], '冻结已识别的复合问题') as (p, _):
            p['questions'][0]['decision_review_proposal'] = dict(
                decisions=[dict(id='a'),dict(id='b')], question_hash=review_question_hash(p,'Q-0001'),
                answer_hash=digest(p['questions'][0]['answer']))
        project = store.get(project['id'])
        result = preview(client, project)
        assert result.status_code == 409 and result.json()['code'] == 'DECISION_MAPPING_REQUIRED'


def test_current_revision_cannot_silently_bind_an_unseen_new_answer(tmp_path):
    store = Store(tmp_path)
    with api(store) as client:
        project = save_answer(client, prepared(store), text='相接边界允许保存。')
        visible_answer = project['questions'][0]['answer']
        project = save_answer(client, project, text='相接边界仍待确认。')
        result = preview(client, project, expected_answer_text=visible_answer)
        assert result.status_code == 409 and result.json()['code'] == 'STALE_ANSWER'
        assert store.get(project['id']) == project
        result = preview(client, project, expected_answer_text=project['questions'][0]['answer'])
        assert result.status_code == 200, result.text
