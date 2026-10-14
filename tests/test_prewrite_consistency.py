"""Pre-writing decision lifecycle with isolated synthetic data and no network.

The provider outputs below are engineering fixtures, never real-model evidence.
"""
import asyncio
import copy

import pytest

from app.actions import Actions
from app.core import Problem
from app.document_reader import export_readiness
from app.decision_review import save_raw_answer
from app.prewrite import (apply_result, document_change_summary, formal_issues,
                          input_hash, normalize_quotes, register, status, validate_result)
from app.provider import DEFAULT, origin
from app.store import Store
from app.workflow import Workflow
from tests.helpers import EXAMPLES
from tests.test_detail_document import compile_fixture, project_fixture
from tests.test_decision_review import api
from tests.test_runtime import FakeProvider


QUESTION = '结果未知时，是否允许再次提交，还是必须先查询原请求？'
CONFLICT = '交互允许结果未知后再次提交，但流程要求先查询原请求。'


def review(project, *, blocker=False, assessment=None):
    value = copy.deepcopy(EXAMPLES['review'])
    value.update(summary='隔离合成成文前检查', proposals=[], questions=[], findings=[])
    value['result'].update(
        assessment=assessment or ('needs_changes' if blocker else 'ready_for_human_review'),
        reviewed_refs=[item['id'] for item in project['items']
                       if item['selection_status'] == 'selected' and item['applies_to'] == 'to_be'],
        required_decisions=[QUESTION] if blocker else [])
    if blocker:
        value['findings'] = [dict(type='contradiction', severity='blocker',
            message=CONFLICT, related_refs=['REQ-0001'], source_refs=[],
            suggested_resolution=QUESTION)]
    return value


def document_plan(project):
    return dict(plan_version='4', sections=[dict(section_key='function', context_refs=[],
        normative_refs=[item['id'] for item in project['items']
                        if item['selection_status'] == 'selected'],
        discussion_refs=[], explanations=[])])


def isolated_store(tmp_path):
    # project_fixture creates its own isolated project, without using daily data.
    project = project_fixture(tmp_path)
    store = Store(tmp_path)
    with store.edit(project['id'], project['revision'], '合成预检输入', bump=False) as (p, _):
        p.update(project)
        p['workflow_mode'] = 'shared-draft'
        p['grants'][origin(DEFAULT)] = dict(source_ids=['SRC-0001'])
    return store, store.get(project['id'])


def test_finding_creates_one_unanswered_decision_without_changing_business_or_artifacts(tmp_path):
    project = project_fixture(tmp_path)
    compile_fixture(project)
    before = copy.deepcopy(project)
    original_hash = input_hash(project)
    response = review(project, blocker=True)
    first = apply_result(project, response, 'RUN-prewrite-1')

    assert status(project)['status'] == 'needs_confirmation'
    assert project['items'] == before['items']
    assert project['documents'] == before['documents']
    assert project['active_baseline_id'] == before['active_baseline_id']
    assert len(project['questions']) == 1
    question = project['questions'][0]
    assert question['status'] == 'open' and not question.get('answer')
    assert question['related_refs'] == ['REQ-0001'] and question['blocking'] is True
    assert first['findings'][0]['question_id'] == question['id']
    # Program-owned unanswered questions must not make their own check stale.
    assert input_hash(project) == original_hash

    apply_result(project, response, 'RUN-prewrite-2')
    assert len(project['questions']) == 1
    assert project['questions'][0]['id'] == question['id']
    assert project['items'] == before['items'] and project['documents'] == before['documents']


def test_duplicate_findings_are_one_business_decision(tmp_path):
    project = project_fixture(tmp_path)
    response = review(project, blocker=True)
    response['findings'].append(copy.deepcopy(response['findings'][0]))
    report = apply_result(project, response, 'RUN-duplicates')
    assert len(report['findings']) == 1
    assert len(project['questions']) == 1


def test_warning_does_not_create_business_question_or_block_generation(tmp_path):
    project = project_fixture(tmp_path)
    response = review(project)
    response['findings'] = [dict(type='other', severity='warning',
        message='可以把章节标题缩短。', related_refs=['REQ-0001'], source_refs=[],
        suggested_resolution='保留现有业务含义。')]
    apply_result(project, response, 'RUN-warning')
    assert project['questions'] == []
    assert status(project)['status'] == 'passed'
    assert formal_issues(project) == []


@pytest.mark.parametrize('dimension', ['statement', 'field', 'interaction', 'flow', 'acceptance'])
def test_every_normative_dimension_invalidates_previous_pass(tmp_path, dimension):
    project = project_fixture(tmp_path)
    apply_result(project, review(project), 'RUN-pass')
    before = input_hash(project)
    assert status(project)['status'] == 'passed'
    req = project['items'][0]
    if dimension == 'statement':
        req['statement'] += '保存后刷新列表。'
    elif dimension == 'field':
        req['detail_contract']['fields'][0]['constraints'] = '1 至 50 个字符'
    elif dimension == 'interaction':
        req['detail_contract']['interactions'][0]['repeat'] = '再次点击可提交新请求。'
    elif dimension == 'flow':
        req['detail_contract']['flow']['edges'][2]['condition'] = '所有校验通过且有权限'
    else:
        project['items'][2]['statement'] += '列表显示新姓名。'
    assert input_hash(project) != before
    assert status(project)['status'] == 'stale'
    assert formal_issues(project)


def test_saved_and_applied_answer_require_a_fresh_check(tmp_path):
    project = project_fixture(tmp_path)
    apply_result(project, review(project, blocker=True), 'RUN-found')
    question = project['questions'][0]
    question.update(status='answered', answer='结果未知时先查询原请求，不能直接重发。',
                    understanding_status='pending')
    assert status(project)['status'] == 'stale'
    assert formal_issues(project)
    # A status flag alone cannot be treated as a completed recheck.
    question.update(understanding_status='applied', applied_item_ids=['REQ-0001'])
    assert status(project)['status'] != 'passed'
    assert formal_issues(project)


@pytest.mark.parametrize('answer_state', ['unanswered', 'saved', 'unverified_application', 'expired_application'])
def test_model_omission_cannot_silently_close_an_unimplemented_business_decision(tmp_path, answer_state):
    project = project_fixture(tmp_path)
    apply_result(project, review(project, blocker=True), 'RUN-found')
    question = project['questions'][0]
    if answer_state != 'unanswered':
        question.update(status='answered', answer='必须查询原请求。', understanding_status='pending')
    if answer_state == 'unverified_application':
        # A stale/imported flag is not evidence that the current clauses implement this answer.
        question.update(understanding_status='applied', applied_item_ids=['REQ-0001'])
    elif answer_state == 'expired_application':
        from app.answer_implementation import implementation_records
        from app.understanding import applied_targets
        req = project['items'][0]
        req['statement'] += '结果未知时必须先查询原请求。'
        question.update(understanding_status='applied', applied_item_ids=['REQ-0001'],
                        answer_implementation_contract=2)
        question['answer_applications'] = implementation_records(project, question,
            {'REQ-0001': [dict(field='statement', quote='结果未知时必须先查询原请求。')]}, method='manual_review')
        assert applied_targets(project, question) == {'REQ-0001'}
        req['statement'] = '结果未知时允许直接再次提交。'
        assert applied_targets(project, question) == set()
    apply_result(project, review(project), 'RUN-omitted')
    assert status(project)['status'] == 'needs_confirmation'
    assert formal_issues(project)


def test_group_answer_saved_only_in_decision_answers_invalidates_review(tmp_path):
    project = project_fixture(tmp_path)
    apply_result(project, review(project, blocker=True), 'RUN-found')
    question = project['questions'][0]
    question['decision_group_id'] = 'QG-prewrite'
    before = input_hash(project)
    save_raw_answer(project, 'QG-prewrite', '必须先查询原请求。')
    assert question.get('answer') is None
    assert project['decision_answers'][-1]['text'] == '必须先查询原请求。'
    assert input_hash(project) != before
    assert status(project)['status'] == 'stale'


@pytest.mark.parametrize('changed', ['answer', 'implemented_clause'])
def test_resolved_decision_reopens_when_its_implementation_is_no_longer_current(tmp_path, changed):
    from app.answer_implementation import implementation_records
    project = project_fixture(tmp_path)
    apply_result(project, review(project, blocker=True), 'RUN-found')
    question = project['questions'][0]
    req = project['items'][0]
    req['statement'] += '结果未知时必须先查询原请求。'
    question.update(status='answered', answer='结果未知时必须先查询原请求。',
        understanding_status='applied', applied_item_ids=['REQ-0001'], answer_implementation_contract=2)
    question['answer_applications'] = implementation_records(project, question,
        {'REQ-0001': [dict(field='statement', quote='结果未知时必须先查询原请求。')]}, method='manual_review')
    apply_result(project, review(project), 'RUN-resolved')
    assert status(project)['status'] == 'passed'
    assert question.get('prewrite_resolution')
    if changed == 'answer':
        save_raw_answer(project, question['id'], '结果未知时允许直接重发。')
    else:
        req['statement'] = '结果未知时允许直接重发。'
    assert status(project)['status'] == 'stale'
    apply_result(project, review(project), 'RUN-omitted-after-change')
    assert status(project)['status'] == 'needs_confirmation'
    assert formal_issues(project)


def dispute_record(project, report):
    project.setdefault('prewrite_responses', []).append(dict(
        finding_id=report['findings'][0]['id'], review_id=report['id'], mode='dispute',
        reason='本次原条款的重试只适用于已明确失败，请复查。', created='synthetic'))


def test_resolved_findings_remain_in_subsequent_review_history(tmp_path):
    project = project_fixture(tmp_path)
    first = apply_result(project, review(project, blocker=True), 'RUN-found')
    dispute_record(project, first)
    second = apply_result(project, review(project), 'RUN-reviewed-dispute')
    assert any(row['id'] == first['findings'][0]['id'] and row['status'] == 'resolved'
               for row in second['findings'])
    third = apply_result(project, review(project), 'RUN-next-check')
    assert any(row['id'] == first['findings'][0]['id'] and row['status'] == 'resolved'
               for row in third['findings'])


def test_old_dispute_does_not_resolve_a_new_similar_finding(tmp_path):
    project = project_fixture(tmp_path)
    first_response = review(project, blocker=True)
    first_response['findings'][0]['topic_key'] = 'unknown-result-retry'
    first = apply_result(project, first_response, 'RUN-first-found')
    dispute_record(project, first)
    apply_result(project, review(project), 'RUN-reviewed-dispute')

    new_response = review(project, blocker=True)
    new_response['findings'][0].update(topic_key='unknown-result-retry',
        message='修改后的交互新增自动重试，与结果未知先查询的流程矛盾。')
    new_response['result']['required_decisions'] = ['结果未知时，新增自动重试是否需要先查询原请求？']
    apply_result(project, new_response, 'RUN-new-finding')
    assert status(project)['status'] == 'needs_confirmation'
    apply_result(project, review(project), 'RUN-omitted-new-finding')
    assert status(project)['status'] == 'needs_confirmation'
    assert formal_issues(project)


def test_reviewer_cannot_quote_nonexistent_field_content(tmp_path):
    project = project_fixture(tmp_path)
    response = review(project, blocker=True)
    response['findings'][0]['evidence_quotes'] = [dict(object_id='REQ-0001',
        path='detail_contract.fields.F-name.constraints', quote='允许无限长度且无需权限')]
    before = copy.deepcopy(project)
    with pytest.raises(Problem) as error:
        apply_result(project, response, 'RUN-false-quote')
    assert error.value.code == 'REFERENCE_INVALID'
    assert project == before


@pytest.mark.parametrize('suffix', ['。', '.'])
def test_prewrite_quote_normalization_only_removes_added_terminal_period_from_full_source(tmp_path, suffix):
    project = project_fixture(tmp_path)
    response = review(project, blocker=True)
    source = project['items'][0]['detail_contract']['fields'][0]['constraints']
    response['findings'][0]['evidence_quotes'] = [dict(object_id='REQ-0001',
        path='detail_contract.fields.F-name.constraints', quote=source + suffix)]
    original = copy.deepcopy(response)
    normalized, audit = normalize_quotes(project, response)
    expected = copy.deepcopy(original)
    expected['findings'][0]['evidence_quotes'][0]['quote'] = source
    assert normalized == expected
    assert response == original
    assert len(audit) == 1 and audit[0]['before'] == source + suffix and audit[0]['after'] == source
    validate_result(project, normalized)


@pytest.mark.parametrize('fault', ['partial_sentence', 'changed_words', 'question_mark',
                                  'exclamation_mark', 'existing_period', 'wrong_property'])
def test_prewrite_quote_normalization_does_not_repair_meaning_or_unbound_quote(tmp_path, fault):
    project = project_fixture(tmp_path)
    response = review(project, blocker=True)
    field = project['items'][0]['detail_contract']['fields'][0]
    source = field['constraints']
    path = 'detail_contract.fields.F-name.constraints'
    quote = source + '。'
    if fault == 'partial_sentence':
        quote = '30 个字符。'
    elif fault == 'changed_words':
        quote = source.replace('30', '50') + '。'
    elif fault == 'question_mark':
        quote = source + '？'
    elif fault == 'exclamation_mark':
        quote = source + '！'
    elif fault == 'existing_period':
        field['constraints'] = source + '。'
        quote = source + '。。'
    else:
        path = 'detail_contract.fields.F-missing.constraints'
    response['findings'][0]['evidence_quotes'] = [dict(object_id='REQ-0001', path=path, quote=quote)]
    original = copy.deepcopy(response)
    normalized, audit = normalize_quotes(project, response)
    assert normalized == response == original and audit == []
    with pytest.raises(Problem) as error:
        validate_result(project, normalized)
    assert error.value.code == 'REFERENCE_INVALID'


def test_known_source_correction_gets_a_reviewable_change_card_without_auto_fixing_business(tmp_path):
    project = project_fixture(tmp_path)
    response = review(project, blocker=True)
    response['result']['required_decisions'] = []
    response['findings'][0]['suggested_resolution'] = '请把重复操作条件恢复为既有规则中的先查询原请求。'
    original = copy.deepcopy(project)
    report = apply_result(project, response, 'RUN-known-correction')
    card, = report['findings']
    question, = project['questions']
    assert card['decision_kind'] == 'correction'
    assert card['question_id'] == question['id'] and question['related_refs'] == ['REQ-0001']
    assert question['status'] == 'open' and not question.get('answer')
    assert project['items'] == original['items'] and project['documents'] == original['documents']
    assert status(project)['status'] == 'needs_confirmation'


def test_generated_document_does_not_stale_its_own_prewrite_input(tmp_path):
    project = project_fixture(tmp_path)
    apply_result(project, review(project), 'RUN-pass')
    before = input_hash(project)
    compile_fixture(project)
    assert input_hash(project) == before
    assert status(project)['status'] == 'passed'


def test_version_summary_uses_frozen_items_without_mutating_either_artifact(tmp_path):
    project = project_fixture(tmp_path)
    compile_fixture(project)
    previous = copy.deepcopy(project['documents']['prd'])
    project['items'][0]['detail_contract']['fields'][0]['constraints'] = '1 至 50 个字符'
    compile_fixture(project)
    current = project['documents']['prd']
    frozen = copy.deepcopy((previous, current))
    summary = document_change_summary(previous, current)
    assert (previous, current) == frozen
    assert previous['id'] != current['id']
    assert previous['item_snapshot'][0]['detail_contract']['fields'][0]['constraints'] == '1 至 30 个字符'
    assert summary['previous_document_id'] == previous['id']
    assert summary['changed_item_ids'] == ['REQ-0001']
    assert summary['affected_sections']


def test_dispute_is_a_recorded_objection_and_cannot_ignore_an_existing_finding(tmp_path):
    store, project = isolated_store(tmp_path)
    with store.edit(project['id'], project['revision'], '合成检查发现', bump=False) as (p, _):
        apply_result(p, review(p, blocker=True), 'RUN-found')
    project = store.get(project['id'])
    report = project['prewrite_review']
    card = report['findings'][0]
    client = api(store)
    register(client.app, store)
    with client:
        body = dict(expected_revision=project['revision'], review_id=report['id'],
                    input_hash=report['input_hash'], mode='dispute',
                    reason='原条款的重试只适用于已明确失败，需要复查适用条件。')
        response = client.post(f'/api/projects/{project["id"]}/prewrite-review/findings/{card["id"]}/response', json=body)
        assert response.status_code == 200, response.text
        current = store.get(project['id'])
        assert current['items'] == project['items'] and current['documents'] == project['documents']
        assert current['questions'] == project['questions']
        assert status(current)['status'] != 'passed' and formal_issues(current)
        assert current['prewrite_responses'][-1]['reason'] == body['reason']
        apply_result(current, review(current, blocker=True), 'RUN-still-found')
        assert status(current)['status'] == 'needs_confirmation'


@pytest.mark.parametrize('fault', ['review_id', 'input_hash', 'unknown_finding', 'empty_reason', 'ignore'])
def test_objection_api_rejects_stale_or_uncontrolled_requests_without_mutation(tmp_path, fault):
    store, project = isolated_store(tmp_path)
    with store.edit(project['id'], project['revision'], '合成检查发现', bump=False) as (p, _):
        apply_result(p, review(p, blocker=True), 'RUN-found')
    project = store.get(project['id'])
    report = project['prewrite_review']
    finding_id = report['findings'][0]['id']
    body = dict(expected_revision=project['revision'], review_id=report['id'],
                input_hash=report['input_hash'], mode='dispute', reason='需要复查原条件。')
    if fault in ('review_id', 'input_hash'):
        body[fault] = 'obsolete'
    elif fault == 'unknown_finding':
        finding_id = 'unknown'
    elif fault == 'empty_reason':
        body['reason'] = ' '
    else:
        body['mode'] = 'ignore'
    client = api(store)
    register(client.app, store)
    with client:
        response = client.post(f'/api/projects/{project["id"]}/prewrite-review/findings/{finding_id}/response', json=body)
        assert response.status_code in (400, 404, 409), response.text
        assert store.get(project['id']) == project


@pytest.mark.parametrize('stale', [False, True])
def test_detail_candidate_preview_commit_and_hash_are_one_reviewed_snapshot(tmp_path, stale):
    store, project = isolated_store(tmp_path)
    with store.edit(project['id'], project['revision'], '合成检查发现', bump=False) as (p, _):
        apply_result(p, review(p, blocker=True), 'RUN-found')
    project = store.get(project['id'])
    qid = project['questions'][0]['id']
    with api(store) as client:
        root = f'/api/projects/{project["id"]}'
        answer = client.post(root + '/decision-review/answer', json=dict(
            expected_revision=project['revision'], question_id=qid,
            answer='结果未知时必须先查询原请求。'))
        assert answer.status_code == 200, answer.text
        project = answer.json()['project']
        with store.edit(project['id'], project['revision'], '合成详细定义修订候选') as (p, _):
            from app.understanding import annotate_candidates
            candidate = copy.deepcopy(p['items'][0])
            candidate.update(id='REQ-detail-candidate', action='revise', target_item_id='REQ-0001',
                selection_status='candidate', answer_refs=[qid])
            candidate.pop('requirement_confirmation', None)
            candidate['detail_contract']['interactions'][0]['repeat'] = '结果未知时必须先查询原请求。'
            p['items'].append(candidate)
            annotate_candidates(p, [candidate])
        project = store.get(project['id'])
        payload = dict(expected_revision=project['revision'], question_id=qid,
            coverage=[dict(decision_id=qid, status='clear', answer_quote='结果未知时必须先查询原请求。')],
            groups=[dict(id='G-detail', title='同步交互重复操作条件', decision_ids=[qid],
                candidate_item_ids=['REQ-detail-candidate'], target_item_ids=['REQ-0001'], depends_on=[])],
            review_behavior_item_ids=['REQ-detail-candidate'],
            answer_implementations={qid: [dict(item_id='REQ-0001',
                field='detail_contract.interactions.I-save.repeat', quote='结果未知时必须先查询原请求。')]})
        response = client.post(root + '/decision-review', json=payload)
        assert response.status_code == 200, response.text
        view = response.json()
        target = view['groups'][0]['targets'][0]
        assert target['before']['detail_contract']['interactions'][0]['repeat'] == '同一请求只执行一次'
        assert target['after']['detail_contract']['interactions'][0]['repeat'] == '结果未知时必须先查询原请求。'
        assert not view['implementation_pending_ids']
        if stale:
            with store.edit(project['id'], project['revision'], '合成预览后候选变化', bump=False) as (p, _):
                next(i for i in p['items'] if i['id'] == 'REQ-detail-candidate')['detail_contract']['interactions'][0]['repeat'] = '允许再次直接提交。'
            before = store.get(project['id'])
        response = client.post(root + '/decision-review/commit', json=dict(
            expected_revision=project['revision'], preview_id=view['preview_id'],
            preview_hash=view['preview_hash'], selected_group_ids=['G-detail'],
            idempotency_key='prewrite-detail-commit', review_confirmed=True))
        if stale:
            assert response.status_code == 409 and response.json()['code'] == 'STALE_CONTENT', response.text
            assert store.get(project['id']) == before
        else:
            assert response.status_code == 200, response.text
            saved = store.get(project['id'])
            req = next(i for i in saved['items'] if i['id'] == 'REQ-0001')
            question = next(q for q in saved['questions'] if q['id'] == qid)
            assert req['detail_contract']['interactions'][0]['repeat'] == '结果未知时必须先查询原请求。'
            assert question['understanding_status'] == 'applied'
            from app.understanding import applied_targets
            assert applied_targets(saved, question) == {'REQ-0001'}
            assert status(saved)['status'] == 'stale'
            assert formal_issues(saved)


def test_manual_detail_decision_changes_only_after_commit_and_resolves_only_after_recheck(tmp_path):
    store, project = isolated_store(tmp_path)
    with store.edit(project['id'], project['revision'], '合成检查发现', bump=False) as (p, _):
        apply_result(p, review(p, blocker=True), 'RUN-found')
    project = store.get(project['id'])
    qid = project['questions'][0]['id']
    with api(store) as client:
        root = f'/api/projects/{project["id"]}'
        answer = client.post(root + '/decision-review/answer', json=dict(
            expected_revision=project['revision'], question_id=qid,
            answer='结果未知时必须先查询原请求。'))
        assert answer.status_code == 200, answer.text
        saved = answer.json()['project']
        detail = copy.deepcopy(saved['items'][0]['detail_contract'])
        detail['interactions'][0]['repeat'] = '结果未知时必须先查询原请求。'
        preview = client.post(root + '/decision-review/manual-preview', json=dict(
            expected_revision=saved['revision'], question_id=qid,
            edits=[dict(item_id='REQ-0001', detail_contract=detail)],
            answer_implementations={qid: [dict(item_id='REQ-0001',
                field='detail_contract.interactions.I-save.repeat', quote='结果未知时必须先查询原请求。')]}))
        assert preview.status_code == 200, preview.text
        view = preview.json()
        assert store.get(project['id']) == saved
        assert not view['implementation_pending_ids']
        committed = client.post(root + '/decision-review/commit', json=dict(
            expected_revision=saved['revision'], preview_id=view['preview_id'],
            preview_hash=view['preview_hash'], selected_group_ids=['manual-answer'],
            idempotency_key='prewrite-manual-detail', review_confirmed=True))
        assert committed.status_code == 200, committed.text
    saved = store.get(project['id'])
    assert saved['items'][0]['detail_contract'] == detail
    assert saved['questions'][0]['understanding_status'] == 'applied'
    assert status(saved)['status'] == 'stale' and formal_issues(saved)
    assert any(source['title'] == '人工核对回答并修订.txt' and
               any('结果未知时必须先查询原请求。' in excerpt['text'] for excerpt in source['excerpts'])
               for source in saved['sources'])
    task, provider, _ = run_action(store, saved, [review(saved)], action='prewrite')
    assert task['status'] == 'succeeded' and task['calls'] == len(provider.received) == 1, task
    final = store.get(project['id'])
    assert status(final)['status'] == 'passed' and formal_issues(final) == []
    assert status(final)['findings'][0]['status'] == 'resolved'
    assert final['items'] == saved['items']


@pytest.mark.parametrize('fault', ['missing_coverage', 'insufficient_context', 'unresolved_decision'])
def test_incomplete_model_review_cannot_report_passed(tmp_path, fault):
    project = project_fixture(tmp_path)
    response = review(project)
    if fault == 'missing_coverage':
        response['result']['reviewed_refs'] = []
    elif fault == 'insufficient_context':
        response['result']['assessment'] = 'insufficient_context'
    else:
        response['result']['required_decisions'] = [QUESTION]
    if fault in ('missing_coverage', 'unresolved_decision'):
        before = copy.deepcopy(project)
        with pytest.raises(Problem) as error:
            apply_result(project, response, 'RUN-incomplete')
        assert error.value.code == ('PREWRITE_COVERAGE_REQUIRED' if fault == 'missing_coverage'
                                    else 'PREWRITE_DECISION_BINDING_REQUIRED')
        assert project == before and status(project)['status'] != 'passed'
        return
    apply_result(project, response, 'RUN-incomplete')
    assert status(project)['status'] != 'passed'
    assert formal_issues(project)


def run_action(store, project, outputs, action='document', **extra):
    async def scenario():
        provider = FakeProvider(outputs)
        workflow = Workflow(store, provider)
        actions = Actions(store, workflow)
        body = dict(expected_revision=project['revision'], action=action, message='',
                    document_type='prd', max_calls=None, option_id=None, **extra)
        plan = actions.plan(project['id'], body)
        assert not plan['missing'], plan['missing']
        key = 'prewrite-offline-action-' + str(len(store.records(project['id'], 'user_task')))
        task = actions.start(project['id'], body, plan['plan_hash'], key, True)
        job = actions.jobs[task['id']]
        await job
        return store.get_record(project['id'], task['id'], 'user_task'), provider, plan
    return asyncio.run(scenario())


@pytest.mark.parametrize('mode', ['shared-draft', None])
def test_direct_workflow_cannot_bypass_prewrite_in_current_or_legacy_project(tmp_path, mode):
    store, project = isolated_store(tmp_path)
    with store.edit(project['id'], project['revision'], '合成运行模式边界', bump=False) as (p, _):
        if mode is None:
            p.pop('workflow_mode', None)
        else:
            p['workflow_mode'] = mode
    project = store.get(project['id'])
    provider = FakeProvider([])
    workflow = Workflow(store, provider)
    with pytest.raises(Problem) as error:
        workflow.start(project['id'], project['revision'], 'prd', '', 'prd')
    assert error.value.code == 'PREWRITE_REQUIRED'
    assert provider.received == [] and store.records(project['id'], 'run') == []
    assert store.get(project['id']) == project


def test_prewrite_runs_before_first_prd_and_pass_is_reused_for_unchanged_document_input(tmp_path):
    store, project = isolated_store(tmp_path)
    assert not project['documents']
    task, provider, _ = run_action(store, project, [review(project)], action='prewrite')
    assert task['status'] == 'succeeded', task
    assert task['calls'] == len(provider.received) == 1
    current = store.get(project['id'])
    assert current['items'] == project['items'] and current['documents'] == project['documents']
    assert status(current)['status'] == 'passed'
    task, provider, plan = run_action(store, current, [document_plan(current)])
    assert task['status'] == 'succeeded', task
    assert plan['stages'] == ['prd']
    assert task['calls'] == len(provider.received) == 1
    written = store.get(project['id'])
    assert status(written)['status'] == 'passed'
    assert written['documents']['prd']['prewrite_snapshot']['input_hash'] == input_hash(written)


def test_changed_requirement_creates_new_prd_with_summary_and_keeps_historical_artifact(tmp_path):
    store, project = isolated_store(tmp_path)
    task, _, _ = run_action(store, project, [review(project), document_plan(project)])
    assert task['status'] == 'succeeded', task
    current = store.get(project['id'])
    first = copy.deepcopy(current['documents']['prd'])
    historical = copy.deepcopy(store.records(project['id'], 'document_artifact'))
    with store.edit(project['id'], current['revision'], '合成已确认长度变更') as (p, _):
        p['items'][0]['detail_contract']['fields'][0]['constraints'] = '1 至 50 个字符'
    changed = store.get(project['id'])
    assert status(changed)['status'] == 'stale'
    assert changed['documents']['prd'] == first
    task, provider, plan = run_action(store, changed, [review(changed), document_plan(changed)])
    assert task['status'] == 'succeeded', task
    assert task['calls'] == len(provider.received) == 2 and plan['stages'] == ['review', 'prd']
    written = store.get(project['id'])
    second = written['documents']['prd']
    assert second['id'] != first['id']
    assert second['document_version'] == first['document_version'] + 1
    assert second['change_summary']['previous_document_id'] == first['id']
    assert second['change_summary']['changed_item_ids'] == ['REQ-0001']
    stored_history = store.records(project['id'], 'document_artifact')
    assert stored_history[:len(historical)] == historical
    assert len(stored_history) == len(historical) + 1


def test_document_action_checks_before_writing_and_pauses_with_old_document_intact(tmp_path):
    store, project = isolated_store(tmp_path)
    with store.edit(project['id'], project['revision'], '合成旧版文档', bump=False) as (p, db):
        compile_fixture(p)
        store.record(p['id'], 'document_artifact', copy.deepcopy(p['documents']['prd']), db=db)
    project = store.get(project['id'])
    original = copy.deepcopy(project['documents'])
    task, provider, plan = run_action(store, project, [review(project, blocker=True)])

    assert plan['stages'] == ['review', 'prd']
    assert task['status'] == 'awaiting_user'
    assert task['calls'] == len(provider.received) == 1
    assert task['step_states'][1]['status'] == 'NOT_RUN'
    assert store.get(project['id'])['documents'] == original
    assert len(store.records(project['id'], 'document_artifact')) == 1


def test_check_failure_cannot_be_skipped_by_review_draft_flag(tmp_path):
    store, project = isolated_store(tmp_path)
    task, provider, _ = run_action(store, project, [Problem('NETWORK_ERROR', '隔离合成网络故障')],
                                   allow_review_draft=True)
    assert task['status'] == 'failed'
    assert task['calls'] == len(provider.received) == 1
    assert task['step_states'][1]['status'] == 'NOT_RUN'
    current = store.get(project['id'])
    assert status(current)['status'] == 'failed'
    assert current['documents'] == project['documents']
    assert formal_issues(current)


def test_broken_flow_reference_cannot_be_skipped_by_review_draft_flag(tmp_path):
    store, project = isolated_store(tmp_path)
    with store.edit(project['id'], project['revision'], '合成悬空流程引用') as (p, _):
        p['items'][0]['detail_contract']['flow']['edges'][0]['to'] = 'N-missing'
    project = store.get(project['id'])
    task, provider, _ = run_action(store, project, [review(project)], allow_review_draft=True)
    assert task['status'] == 'awaiting_user', task
    assert task['calls'] == len(provider.received) == 1
    assert task['step_states'][1]['status'] == 'NOT_RUN'
    current = store.get(project['id'])
    assert current['documents'] == project['documents']
    assert any(row['hard_block'] for row in status(current)['findings'])


def test_explicit_review_draft_preserves_blockers_and_cannot_be_formally_exported(tmp_path):
    store, project = isolated_store(tmp_path)
    task, provider, _ = run_action(store, project,
        [review(project, blocker=True), document_plan(project)], allow_review_draft=True)
    assert task['status'] in ('succeeded', 'partial')
    assert task['calls'] == len(provider.received) == 2
    current = store.get(project['id'])
    assert current['documents'].get('prd')
    assert status(current)['status'] == 'needs_confirmation'
    assert not export_readiness(current, 'prd', 'formal')['ready']
    assert export_readiness(current, 'prd', 'review')['ready']
    assert current['active_baseline_id'] == project['active_baseline_id']
