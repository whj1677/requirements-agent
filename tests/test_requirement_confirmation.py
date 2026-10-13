"""Synthetic cloud requirement packages; no real model or daily data writes."""
import copy

import pytest

from app.core import digest
from app.product_flow import blockers
from app.requirement_confirmation import FIELDS, confirmation_current
from app.store import Store
from app.understanding import coverage_complete
from tests.helpers import prepared
from tests.test_decision_review import api


def setup(store):
    project = prepared(store)
    with store.edit(project['id'], project['revision'], '合成储能云单项确认') as (p, _):
        req = p['items'][0]
        req.update(title='储能云告警协作', statement='两名值班人员协作处理告警。',
                   selection_status='candidate', behavior_review_required=True, change_type='new',
                   behavior={'actor':'值班人员', 'flow':'查看并处理告警', 'result':'处理记录可查看'})
        p['items'][2]['selection_status'] = 'candidate'
        p['product_context'] = {'scope_ids':['REQ-0001']}
    return store.get(project['id'])


def payload(project, **extra):
    req = project['items'][0]
    value = dict(expected_revision=project['revision'], requirement_id=req['id'],
                 statement=req['statement'],
                 behavior={field:req.get('behavior', {}).get(field, '') for field in FIELDS},
                 acceptance_items=[dict(id='AC-0001', statement=project['items'][2]['statement'])])
    value.update(extra)
    return value


def preview(client, p, **extra):
    response = client.post(f'/api/projects/{p["id"]}/requirement-confirmation/preview', json=payload(p, **extra))
    assert response.status_code == 200, response.text
    return response.json()


def commit(client, p, view, **extra):
    body = dict(expected_revision=p['revision'], preview_id=view['preview_id'],
                preview_hash=view['preview_hash'], idempotency_key='row-review-key-0001', review_confirmed=True)
    body.update(extra)
    return client.post(f'/api/projects/{p["id"]}/requirement-confirmation/commit', json=body)


def test_one_explicit_confirmation_selects_requirement_and_acceptance_with_blank_optional_fields(tmp_path):
    store = Store(tmp_path)
    p = setup(store)
    with api(store) as client:
        view = preview(client, p, statement='两名值班人员独立记录处理过程，双方可查看。',
                       acceptance_items=[dict(id='AC-0001', statement='用户A写入处理记录后，用户B可以查看该记录。')])
        assert store.get(p['id']) == p
        saved = commit(client, p, view)
        assert saved.status_code == 200, saved.text
        final = saved.json()['project']
        req, ac = final['items'][0], final['items'][2]
        assert req['selection_status'] == ac['selection_status'] == 'selected'
        assert req['behavior']['entry'] == req['behavior']['permissions'] == ''
        assert req['statement'] == view['after']['statement']
        assert confirmation_current(final, req)
        assert not {'behavior_fields_missing', 'behavior_review_pending', 'acceptance_missing'} & {
            row['code'] for row in blockers(final, 3)}
        state = client.get(f'/api/projects/{p["id"]}/requirement-confirmation/state').json()
        assert state['rows'][0]['confirmed'] is True
        again = commit(client, p, view)
        assert again.status_code == 200 and again.json()['repeated'] is True
        assert store.get(p['id']) == final


def test_new_manual_acceptance_has_original_requirement_link_and_actual_source(tmp_path):
    store = Store(tmp_path)
    p = setup(store)
    with api(store) as client:
        view = preview(client, p, acceptance_items=[dict(statement='用户A处理告警后，处理记录显示在告警详情。')])
        assert view['acceptance_items'][0]['new']
        response = commit(client, p, view)
        assert response.status_code == 200, response.text
        final = response.json()['project']
        ac = next(item for item in final['items'] if item['id'] == view['acceptance_items'][0]['id'])
        assert ac['related_refs'] == ['REQ-0001'] and ac['source_refs']
        assert ac['selection_status'] == 'selected'
        assert confirmation_current(final, final['items'][0])


def test_open_critical_business_decision_remains_blocking_and_locatable(tmp_path):
    store = Store(tmp_path)
    p = setup(store)
    with store.edit(p['id'], p['revision'], '关键权限决定待答') as (p, _):
        p['questions'][0].update(blocking=True, question='另一名值班人员能否修改我的处理记录？')
    p = store.get(p['id'])
    with api(store) as client:
        view = preview(client, p, confirmed_question_ids=['Q-0001'])
        result = view['question_results'][0]
        assert not result['can_confirm'] and not result['confirm'] and result['reason']
        response = commit(client, p, view)
        assert response.status_code == 200, response.text
        final = response.json()['project']
        question = final['questions'][0]
        assert question['status'] == 'open' and not coverage_complete(question)
        block = next(row for row in blockers(final, 3) if row['code'] == 'question_open')
        assert block['actions'][0]['question_id'] == 'Q-0001'
        state = client.get(f'/api/projects/{p["id"]}/requirement-confirmation/state').json()
        assert state['rows'][0]['missing_decisions'][0]['question_id'] == 'Q-0001'


def test_saved_single_answer_is_confirmed_only_when_explicitly_included(tmp_path):
    store = Store(tmp_path)
    p = setup(store)
    with api(store) as client:
        answer = client.post(f'/api/projects/{p["id"]}/decision-review/answer', json=dict(
            expected_revision=p['revision'], question_id='Q-0001', answer='不能修改其他人的记录，但可以查看。'))
        assert answer.status_code == 200, answer.text
        p = answer.json()['project']
        view = preview(client, p, statement='两人可查看双方记录，仅能修改自己的记录。',
                       confirmed_question_ids=['Q-0001'])
        assert view['question_results'][0]['confirm']
        assert not coverage_complete(store.get(p['id'])['questions'][0])
        response = commit(client, p, view)
        assert response.status_code == 200, response.text
        final = response.json()['project']
        q = final['questions'][0]
        assert coverage_complete(q) and q['understanding_status'] == 'applied'
        assert q['answer_applications']['REQ-0001']['method'] == 'manual_review'


def test_saved_answer_without_requested_confirmation_stays_pending(tmp_path):
    store = Store(tmp_path)
    p = setup(store)
    with api(store) as client:
        answer = client.post(f'/api/projects/{p["id"]}/decision-review/answer', json=dict(
            expected_revision=p['revision'], question_id='Q-0001', answer='相接边界允许保存。'))
        p = answer.json()['project']
        view = preview(client, p)
        response = commit(client, p, view)
        assert response.status_code == 200, response.text
        q = response.json()['project']['questions'][0]
        assert q['understanding_status'] == 'pending' and not coverage_complete(q)


def test_existing_text_requires_location_then_can_confirm_without_rewording(tmp_path):
    store = Store(tmp_path); p = setup(store)
    with api(store) as client:
        answer = client.post(f'/api/projects/{p["id"]}/decision-review/answer', json=dict(
            expected_revision=p['revision'], question_id='Q-0001', answer='两名值班人员协作处理告警。'))
        p = answer.json()['project']; original = p['items'][0]['statement']
        view = preview(client, p, confirmed_question_ids=['Q-0001'])
        assert not view['question_results'][0]['confirm']
        assert view['question_results'][0]['implementation_code'] == 'ANSWER_IMPLEMENTATION_REQUIRED'
        response = commit(client, p, view)
        assert response.status_code == 200, response.text
        saved = response.json()['project']; q = saved['questions'][0]
        assert q['status'] == 'answered' and q['understanding_status'] == 'pending'
        assert saved['items'][0]['statement'] == original
        view = preview(client, saved, confirmed_question_ids=['Q-0001'], answer_implementations={
            'Q-0001':[dict(item_id='REQ-0001', field='statement', quote=original)]})
        assert view['question_results'][0]['confirm']
        response = commit(client, saved, view, idempotency_key='row-review-key-0002')
        assert response.status_code == 200, response.text
        final = response.json()['project']
        assert final['items'][0]['statement'] == original
        assert final['questions'][0]['answer_applications']['REQ-0001']['contract_version'] == 2


def test_uncertain_answer_never_becomes_a_clear_decision(tmp_path):
    store = Store(tmp_path)
    p = setup(store)
    with api(store) as client:
        response = client.post(f'/api/projects/{p["id"]}/decision-review/answer', json=dict(
            expected_revision=p['revision'], question_id='Q-0001', answer='暂不确定'))
        p = response.json()['project']
        view = preview(client, p, confirmed_question_ids=['Q-0001'])
        assert view['question_results'][0]['confirm'] is False
        assert view['question_results'][0]['reason']
        response = commit(client, p, view)
        assert response.status_code == 200, response.text
        assert not coverage_complete(response.json()['project']['questions'][0])


def test_full_legacy_row_still_requires_explicit_package_confirmation_after_opt_in(tmp_path):
    store = Store(tmp_path)
    p = setup(store)
    with store.edit(p['id'], p['revision'], '合成第二条旧需求') as (p, _):
        second = copy.deepcopy(p['items'][0])
        second.update(id='REQ-OTHER', behavior={field:'既有明确说明' for field in FIELDS},
                      selection_status='selected', behavior_review_required=False,
                      related_refs=['AC-OTHER'])
        other_ac = dict(copy.deepcopy(p['items'][2]), id='AC-OTHER', related_refs=['REQ-OTHER'],
                        selection_status='selected')
        p['items'].extend([second, other_ac])
        p['product_context']['scope_ids'].append('REQ-OTHER')
    p = store.get(p['id'])
    with api(store) as client:
        response = commit(client, p, preview(client, p))
        assert response.status_code == 200, response.text
        final = response.json()['project']
        pending = [row for row in blockers(final, 3) if row['code'] == 'requirement_confirmation_stale']
        assert pending and pending[0]['actions'][0]['item_id'] == 'REQ-OTHER'
        assert confirmation_current(final, final['items'][0])
        assert not confirmation_current(final, final['items'][3])


def test_multi_target_answer_is_not_falsely_closed_by_one_row(tmp_path):
    store = Store(tmp_path)
    p = setup(store)
    with store.edit(p['id'], p['revision'], '问题也涉及规则') as (p, _):
        p['questions'][0]['related_refs'].append('RULE-0001')
    p = store.get(p['id'])
    with api(store) as client:
        answer = client.post(f'/api/projects/{p["id"]}/decision-review/answer', json=dict(
            expected_revision=p['revision'], question_id='Q-0001', answer='相接边界允许保存。'))
        p = answer.json()['project']
        view = preview(client, p, confirmed_question_ids=['Q-0001'])
        assert view['question_results'][0]['can_confirm'] is False
        assert '其他条款' in view['question_results'][0]['reason']
        response = commit(client, p, view)
        assert response.status_code == 200, response.text
        assert not coverage_complete(response.json()['project']['questions'][0])


@pytest.mark.parametrize('overrides,code', [
    ({'review_confirmed':False}, 'REVIEW_REQUIRED'),
    ({'preview_hash':'wrong'}, 'SNAPSHOT_INVALID'),
])
def test_invalid_commit_never_writes_items_or_sources(tmp_path, overrides, code):
    store = Store(tmp_path)
    p = setup(store)
    with api(store) as client:
        view = preview(client, p)
        response = commit(client, p, view, **overrides)
        assert response.status_code == 409 and response.json()['code'] == code
        assert store.get(p['id']) == p


def test_concurrent_revision_rejects_old_preview(tmp_path):
    store = Store(tmp_path)
    p = setup(store)
    with api(store) as client:
        view = preview(client, p)
        with store.edit(p['id'], p['revision'], '另一个页面修改') as (fresh, _):
            fresh['name'] = '最新名称'
        before = store.get(p['id'])
        response = commit(client, p, view)
        assert response.status_code == 409 and response.json()['code'] == 'STALE_REVISION'
        assert store.get(p['id']) == before


def test_no_legacy_auto_confirmation_and_unrelated_change_does_not_revoke_signed_row(tmp_path):
    store = Store(tmp_path)
    p = setup(store)
    assert not confirmation_current(p, p['items'][0])
    with api(store) as client:
        view = preview(client, p)
        response = commit(client, p, view)
        p = response.json()['project']
        with store.edit(p['id'], p['revision'], '其他独立条款修改') as (p, _):
            p['items'][1]['statement'] = '另一个条款的新内容。'
        p = store.get(p['id'])
        assert confirmation_current(p, p['items'][0])
        with store.edit(p['id'], p['revision'], '本条验收修改') as (p, _):
            p['items'][2]['statement'] = '新增验收断言。'
        p = store.get(p['id'])
        assert not confirmation_current(p, p['items'][0])
        assert 'requirement_confirmation_stale' in {row['code'] for row in blockers(p, 3)}


@pytest.mark.parametrize('mutation', ['exclude', 'missing', 'superseded'])
def test_invalid_sources_cannot_be_approved_by_row_checkbox(tmp_path, mutation):
    store = Store(tmp_path)
    p = setup(store)
    if mutation == 'exclude':
        p['sources'][0]['excluded'] = True
    elif mutation == 'superseded':
        p['sources'][0]['superseded_by'] = 'newer'
    else:
        p['items'][0]['source_refs'] = []
    from app.requirement_confirmation import build_preview
    from app.core import Problem
    with pytest.raises(Problem) as error:
        build_preview(p, payload(p))
    assert error.value.code == 'REFERENCE_INVALID'


def test_existing_selected_acceptance_cannot_be_hidden_from_confirmation(tmp_path):
    store = Store(tmp_path)
    p = setup(store)
    with store.edit(p['id'], p['revision'], '已有采纳验收') as (p, _):
        p['items'][2]['selection_status'] = 'selected'
    p = store.get(p['id'])
    with api(store) as client:
        response = client.post(f'/api/projects/{p["id"]}/requirement-confirmation/preview',
                               json=payload(p, acceptance_items=[dict(statement='其他新验收。')]))
        assert response.status_code == 409 and response.json()['code'] == 'REVIEW_INCOMPLETE'
        assert store.get(p['id']) == p


def test_ai_revision_preserves_ai_sources_and_is_never_relabelled_as_human_authored(tmp_path):
    store = Store(tmp_path)
    p = setup(store)
    with store.edit(p['id'], p['revision'], '合成AI修订候选') as (p, _):
        candidate = copy.deepcopy(p['items'][0])
        candidate.update(id='REQ-CANDIDATE', target_item_id='REQ-0001', action='revise',
                         statement='两名值班人员可查看双方记录，仅能修改自己的记录。')
        candidate['behavior']['permissions'] = '可查看双方记录，仅能修改自己的记录。'
        p['items'].append(candidate)
    p = store.get(p['id'])
    candidate = p['items'][-1]
    with api(store) as client:
        view = preview(client, p, candidate_id=candidate['id'], statement=candidate['statement'],
                       behavior={field:candidate.get('behavior', {}).get(field, '') for field in FIELDS})
        response = commit(client, p, view)
        assert response.status_code == 200, response.text
        final = response.json()['project']
        assert len(final['sources']) == len(p['sources'])
        assert final['items'][0]['source_refs'] == p['items'][0]['source_refs']
        assert final['items'][-1]['selection_status'] == 'deferred'
        assert final['items'][0]['confirmed_revisions'][0]['candidate_id'] == candidate['id']
        assert confirmation_current(final, final['items'][0])


def test_stale_answer_bound_ai_candidate_is_rejected(tmp_path):
    store = Store(tmp_path)
    p = setup(store)
    with store.edit(p['id'], p['revision'], '陈旧AI回答绑定') as (p, _):
        candidate = copy.deepcopy(p['items'][0])
        candidate.update(id='REQ-CANDIDATE', target_item_id='REQ-0001', action='revise',
                         answer_refs=['Q-0001'], answer_versions={'Q-0001':digest('旧回答')})
        p['items'].append(candidate)
        p['questions'][0].update(answer='新回答', status='answered')
    p = store.get(p['id'])
    with api(store) as client:
        response = client.post(f'/api/projects/{p["id"]}/requirement-confirmation/preview',
                               json=payload(p, candidate_id='REQ-CANDIDATE'))
        assert response.status_code == 409 and response.json()['code'] == 'STALE_ANSWER'
        assert store.get(p['id']) == p


def setup_single_group(store, members=1):
    p = setup(store)
    with store.edit(p['id'], p['revision'], '合成决定组原话') as (p, _):
        p['workflow_mode'] = 'shared-draft'
        question = p['questions'][0]
        question.update(decision_group_id='QG-one', decision_coverage='pending', blocking=True)
        for index in range(1, members):
            p['questions'].append(dict(copy.deepcopy(question), id='Q-extra-'+str(index),
                                       question='另一个独立业务决定？'))
    return store.get(p['id'])


def test_single_member_group_raw_answer_can_be_confirmed_once_with_preserved_provenance(tmp_path):
    store = Store(tmp_path)
    p = setup_single_group(store)
    with api(store) as client:
        saved = client.post(f'/api/projects/{p["id"]}/decision-review/answer', json=dict(
            expected_revision=p['revision'], question_id='QG-one', answer='双方可查看，只能修改自己的记录。'))
        assert saved.status_code == 200, saved.text
        p = saved.json()['project']
        assert p['questions'][0]['answer'] is None
        original = copy.deepcopy(p)
        view = preview(client, p, statement='双方可查看，只能修改自己的记录。',
                       confirmed_question_ids=['Q-0001'])
        row = view['question_results'][0]
        assert row['answer'] == p['decision_answers'][0]['text']
        assert row['confirm'] and row['can_confirm']
        assert store.get(p['id']) == original
        response = commit(client, p, view)
        assert response.status_code == 200, response.text
        final = response.json()['project']
        q = final['questions'][0]
        assert q['answer'] == p['decision_answers'][0]['text']
        assert q['answer_source_refs'] == p['decision_answers'][0]['source_refs']
        assert q['decision_answer_id'] == p['questions'][0]['decision_answer_id']
        assert final['decision_answers'] == p['decision_answers']
        assert coverage_complete(q) and q['understanding_status'] == 'applied'
        assert not [row for row in blockers(final, 3) if row['code'] in ('question_open', 'answer_review_pending')]


def test_single_member_group_with_existing_explicit_leaf_answer_is_not_compound(tmp_path):
    store = Store(tmp_path)
    p = setup_single_group(store)
    with api(store) as client:
        saved = client.post(f'/api/projects/{p["id"]}/decision-review/answer', json=dict(
            expected_revision=p['revision'], question_id='Q-0001', answer='相接边界允许保存。'))
        p = saved.json()['project']
        view = preview(client, p, statement='管理员能够维护电价时段，相接边界允许保存。',
                       confirmed_question_ids=['Q-0001'])
        assert view['question_results'][0]['confirm'] is True
        response = commit(client, p, view)
        assert response.status_code == 200, response.text
        assert coverage_complete(response.json()['project']['questions'][0])


@pytest.mark.parametrize('case', ['unanswered', 'unknown', 'stale_record', 'multiple_members', 'multiple_points', 'compound_proposal'])
def test_group_without_a_single_known_answer_never_becomes_clear(tmp_path, case):
    store = Store(tmp_path)
    p = setup_single_group(store, members=2 if case == 'multiple_members' else 1)
    with api(store) as client:
        if case != 'unanswered':
            response = client.post(f'/api/projects/{p["id"]}/decision-review/answer', json=dict(
                expected_revision=p['revision'], question_id='QG-one',
                answer='暂不确定' if case == 'unknown' else '双方可查看，只能修改自己的记录。'))
            p = response.json()['project']
        if case in ('stale_record', 'multiple_points', 'compound_proposal'):
            with store.edit(p['id'], p['revision'], '合成待核对结构') as (p, _):
                q = p['questions'][0]
                if case == 'stale_record':
                    q['decision_answer_id'] = 'DA-unavailable'
                elif case == 'multiple_points':
                    q['decision_points'] = [{'id':'d1'}, {'id':'d2'}]
                else:
                    from app.understanding import question_fingerprint
                    q['decision_review_proposal'] = dict(decisions=[{'id':'d1'}, {'id':'d2'}],
                        question_hash=digest([question_fingerprint(q)]),
                        answer_hash=digest(p['decision_answers'][0]['text']))
            p = store.get(p['id'])
        view = preview(client, p, confirmed_question_ids=['Q-0001'])
        row = view['question_results'][0]
        assert not row['confirm'] and not row['can_confirm'] and row['reason']
        response = commit(client, p, view)
        assert response.status_code == 200, response.text
        final = response.json()['project']
        assert final['questions'][0]['answer'] is None
        assert not coverage_complete(final['questions'][0])
        assert any(row['code'] == 'question_open' for row in blockers(final, 3))


def acceptance_revision(store, project):
    """Synthetic AC revision with a distinct material source, never a model call."""
    from app.sources import save_source
    text = '用户打开电价模板列表后，可按模板名称筛选并看到匹配结果。'
    with store.edit(project['id'], project['revision'], '合成验收修订候选') as (project, _):
        original = next(item for item in project['items'] if item['id'] == 'AC-0001')
        original['selection_status'] = 'selected'
        source = save_source(store, '合成验收依据.txt', text.encode('utf-8'), 'goal')
        project['sources'].append(source)
        candidate = copy.deepcopy(original)
        candidate.update(id='AC-REVISION', target_item_id=original['id'], action='revise',
            statement=text, selection_status='candidate', recommendation_requirement_id='REQ-0001',
            source_refs=[dict(source_id=source['id'], excerpt_id=excerpt['id']) for excerpt in source['excerpts']])
        project['items'].append(candidate)
    return store.get(project['id'])


def test_acceptance_revision_is_displayed_then_explicitly_confirmed_with_original_identity(tmp_path):
    store = Store(tmp_path); p = acceptance_revision(store, setup(store)); candidate = p['items'][-1]
    with api(store) as client:
        view = preview(client, p, acceptance_items=[dict(id='AC-0001', candidate_id=candidate['id'],
                                                       statement=candidate['statement'])])
        assert view['acceptance_items'][0]['candidate'] == candidate
        assert view['acceptance_items'][0]['before']['statement'] == p['items'][2]['statement']
        assert store.get(p['id']) == p
        response = commit(client, p, view)
        assert response.status_code == 200, response.text
        final = response.json()['project']; ac = final['items'][2]
        assert ac['id'] == 'AC-0001' and ac['statement'] == candidate['statement']
        assert ac['selection_status'] == 'selected'
        assert len(final['sources']) == len(p['sources'])  # Not re-authored by a human.
        assert ac['source_refs'] == p['items'][2]['source_refs'] + candidate['source_refs']
        assert ac['confirmed_revisions'][0]['candidate_id'] == candidate['id']
        assert ac['confirmed_revisions'][0]['source_refs'] == candidate['source_refs']
        assert dict(final['items'][-1], selection_status='candidate') == candidate
        assert final['items'][-1]['selection_status'] == 'deferred'
        assert confirmation_current(final, final['items'][0])


def test_edited_acceptance_revision_records_only_explicit_human_edit(tmp_path):
    store = Store(tmp_path); p = acceptance_revision(store, setup(store)); candidate = p['items'][-1]
    edited = candidate['statement'] + '清空名称后显示全部可查看模板。'
    with api(store) as client:
        view = preview(client, p, acceptance_items=[dict(id='AC-0001', candidate_id=candidate['id'], statement=edited)])
        response = commit(client, p, view)
        assert response.status_code == 200, response.text
        final = response.json()['project']
        assert len(final['sources']) == len(p['sources']) + 1
        source = final['sources'][-1]
        assert edited in (store.folder / 'sources' / source['id']).read_text(encoding='utf-8')
        assert all(ref in final['items'][2]['source_refs'] for ref in candidate['source_refs'])
        assert final['items'][2]['confirmed_revisions'][0]['candidate_id'] == candidate['id']


@pytest.mark.parametrize('field,value,code', [
    ('kind', 'rule', 'REFERENCE_INVALID'),
    ('target_item_id', 'RULE-0001', 'REFERENCE_INVALID'),
    ('selection_status', 'deferred', 'REFERENCE_INVALID'),
    ('superseded_by', ['AC-NEWER'], 'REFERENCE_INVALID'),
    ('applies_to', 'reference', 'REFERENCE_INVALID'),
    ('action', 'add', 'REFERENCE_INVALID'),
    ('recommendation_requirement_id', 'REQ-OTHER', 'SCOPE_INVALID'),
    ('source_refs', [], 'REFERENCE_INVALID'),
])
def test_invalid_acceptance_revision_cannot_be_confirmed(tmp_path, field, value, code):
    from app.core import Problem
    from app.requirement_confirmation import build_preview
    store = Store(tmp_path); p = acceptance_revision(store, setup(store)); p['items'][-1][field] = value
    with pytest.raises(Problem) as error:
        build_preview(p, payload(p, acceptance_items=[dict(id='AC-0001', candidate_id='AC-REVISION',
                                                           statement=p['items'][-1]['statement'])]))
    assert error.value.code == code


def test_acceptance_revision_requires_original_id_and_cannot_hide_selected_acceptance(tmp_path):
    store = Store(tmp_path); p = acceptance_revision(store, setup(store)); candidate = p['items'][-1]
    with api(store) as client:
        for rows in ([dict(statement=candidate['statement'], candidate_id=candidate['id'])],
                     [dict(id=candidate['id'], statement=candidate['statement'])]):
            response = client.post(f'/api/projects/{p["id"]}/requirement-confirmation/preview',
                                   json=payload(p, acceptance_items=rows))
            assert response.status_code == 409 and response.json()['code'] == 'REFERENCE_INVALID'
        response = client.post(f'/api/projects/{p["id"]}/requirement-confirmation/preview',
                               json=payload(p, acceptance_items=[dict(statement='另一条验收')]))
        assert response.status_code == 409 and response.json()['code'] == 'REVIEW_INCOMPLETE'
        assert store.get(p['id']) == p


@pytest.mark.parametrize('state,code', [('old_answer', 'STALE_ANSWER'), ('unrelated_answer', 'REFERENCE_INVALID')])
def test_acceptance_revision_checks_current_directly_related_answer(tmp_path, state, code):
    from app.core import Problem
    from app.requirement_confirmation import build_preview
    store = Store(tmp_path); p = acceptance_revision(store, setup(store)); candidate = p['items'][-1]
    q = p['questions'][0]; q.update(answer='允许相接时段保存', status='answered')
    candidate.update(answer_refs=[q['id']], answer_versions={q['id']: digest(q['answer'])})
    if state == 'old_answer':
        q['related_refs'].append('AC-0001'); candidate['answer_versions'][q['id']] = digest('旧回答')
    with pytest.raises(Problem) as error:
        build_preview(p, payload(p, acceptance_items=[dict(id='AC-0001', candidate_id=candidate['id'],
                                                           statement=candidate['statement'])]))
    assert error.value.code == code


@pytest.mark.parametrize('changed', ['original', 'candidate', 'source', 'answer'])
def test_acceptance_revision_preview_rejects_changed_evidence_before_apply(tmp_path, changed):
    from app.core import Problem
    from app.requirement_confirmation import build_preview, apply_preview
    store = Store(tmp_path); p = acceptance_revision(store, setup(store)); candidate = p['items'][-1]
    q = p['questions'][0]; q.update(answer='允许相接时段保存', status='answered')
    q['related_refs'].append('AC-0001')
    candidate.update(answer_refs=[q['id']], answer_versions={q['id']:digest(q['answer'])})
    view = build_preview(p, payload(p, acceptance_items=[dict(id='AC-0001', candidate_id=candidate['id'],
                                                              statement=candidate['statement'])]))
    if changed == 'original': p['items'][2]['statement'] += '已被另一编辑更新'
    if changed == 'candidate': candidate['statement'] += '已被另一编辑更新'
    if changed == 'source': p['sources'][-1]['excerpts'][0]['text'] += '来源已更新'
    if changed == 'answer': q['answer'] = '不允许相接时段保存'
    before = copy.deepcopy(p)
    with pytest.raises(Problem) as error:
        apply_preview(p, view, store)
    assert error.value.code in ('STALE_REVISION', 'STALE_SOURCE', 'STALE_ANSWER')
    assert p == before

