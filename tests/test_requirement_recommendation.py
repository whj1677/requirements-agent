"""Synthetic, offline checks for row-local drafting; not business acceptance."""
import asyncio
import copy
import json

import pytest
import jsonschema

from app.actions import Actions
from app.core import Problem, digest
from app.decision_semantics import build_request
from app.provider import DEFAULT, assemble, origin
from app.requirement_recommendation import annotate, scope, validate_response
from app.store import Store
from app.workflow import Workflow, apply_response
from tests.helpers import EXAMPLES, prepared
from tests.test_runtime import FakeProvider


def project(tmp_path):
    store = Store(tmp_path)
    p = prepared(store)
    with store.edit(p['id'], p['revision'], '合成需求行边界') as (p, db):
        p['workflow_mode'] = 'shared-draft'
        p['items'].append(dict(p['items'][0], id='REQ-other', title='云平台另一功能',
                               statement='仅作无关需求的隔离对照', related_refs=[]))
        p['items'].append(dict(p['items'][1], id='RULE-shared', title='共享约束',
                               related_refs=['REQ-0001', 'REQ-other']))
        p['questions'].append(dict(p['questions'][0], id='Q-other', topic_key='other',
            question='另一功能的独立问题？', related_refs=['REQ-other']))
        p['grants'][origin(DEFAULT)] = {'source_ids':['SRC-0001']}
    return store, store.get(p['id'])


def target():
    return dict(kind='item', id='REQ-0001')


def response(p):
    value = copy.deepcopy(EXAMPLES['clarify'])
    value['questions'] = []
    value['result'] = dict(scope_summary='仅整理本条需求建议', outstanding_decisions=[], next_focus='人工核对')
    original = p['items'][0]
    value['proposals'] = [dict(
        temp_id='TMP-local', action='revise', target_item_id=original['id'], kind='requirement',
        title=original['title'], statement=original['statement'], applies_to='to_be',
        epistemic_status='reported', source_refs=copy.deepcopy(original['source_refs']),
        related_refs=copy.deepcopy(original['related_refs']), change_type='unspecified',
        behavior=dict(actor='管理员', entry='', data='电价时段', flow='维护电价时段',
                      permissions='', exceptions='重叠时拒绝保存', result='电价时段得以维护'))]
    return value


def test_row_scope_excludes_other_requirement_and_shared_clause(tmp_path):
    store, p = project(tmp_path)
    local = scope(p, target())
    assert local['writable_item_ids'] == ['AC-0001', 'REQ-0001', 'RULE-0001']
    assert local['shared_readonly_item_ids'] == ['RULE-shared']
    assert local['question_ids'] == ['Q-0001']
    assert scope(p, dict(kind='question', id='Q-0001')) is None
    assert scope(p, target(), 'change') is None
    assert scope(p, None) is None
    assert store.get(p['id']) == p


def test_context_contains_saved_answers_and_explicit_candidate_only_contract(tmp_path):
    _, p = project(tmp_path)
    p['questions'][0].update(status='answered', answer='允许相接', answer_source_refs=p['items'][0]['source_refs'])
    before = copy.deepcopy(p)
    local = scope(p, target())
    messages, _, _ = assemble(p, 'clarify', '根据原话补充本行', DEFAULT, tmp_path,
                              requirement_recommendation_scope=local)
    header = json.loads(messages[0]['content'].split('可信任务头：')[1])
    context = json.loads(messages[1]['content'][0]['text'])
    contract = header['requirement_recommendation_contract']
    assert contract['candidate_only'] and contract['requirement_id'] == 'REQ-0001'
    assert 'product_context_proposal' not in header['schema']['properties']
    assert context['requirement_recommendation']['questions'][0]['answer'] == '允许相接'
    assert '不能再让用户重复回答' in contract['drafting']
    assert '不适用' in contract['applicability'] and '建议：' in contract['recommended_answers']
    assert p == before


def test_local_revision_and_acceptance_remain_candidates_and_originals_unchanged(tmp_path):
    _, p = project(tmp_path)
    value = response(p)
    original = copy.deepcopy(p['items'])
    value['proposals'].append(dict(temp_id='TMP-ac', action='add', target_item_id=None,
        kind='acceptance', title='维护电价时段验收', statement='管理员可维护电价时段。',
        applies_to='to_be', epistemic_status='reported', source_refs=p['items'][0]['source_refs'],
        related_refs=['REQ-0001']))
    local = scope(p, target())
    validate_response(value, p, local)
    apply_response(p, value)
    annotate(p, {i['id'] for i in original}, local)
    assert p['items'][:len(original)] == original
    assert all(i['selection_status'] == 'candidate' for i in p['items'][len(original):])
    assert all(i['recommendation_requirement_id'] == 'REQ-0001' for i in p['items'][len(original):])
    assert p['active_baseline_id'] is None


@pytest.mark.parametrize('escape', ['another_row', 'shared', 'add_requirement', 'global_context',
                                  'question_elsewhere', 'repeat_answer', 'unmarked_suggestion'])
def test_scope_expansion_and_misleading_answer_rejected_without_repair(tmp_path, escape):
    _, p = project(tmp_path)
    value = response(p)
    if escape == 'another_row':
        value['proposals'][0]['target_item_id'] = 'REQ-other'
    elif escape == 'shared':
        value['proposals'][0].update(target_item_id='RULE-shared', kind='rule')
    elif escape == 'add_requirement':
        value['proposals'][0].update(action='add', target_item_id=None, related_refs=['REQ-0001'])
    elif escape == 'global_context':
        value['product_context_proposal'] = {'current_state':'AI 顺便改了现状'}
    else:
        q = dict(temp_id='QTMP-one', topic_key='touching-boundary', question='相接边界如何处理？',
                 why='待确认建议', related_refs=['REQ-0001'], source_refs=[], options=[], blocking=True)
        value['questions'] = [q]
        if escape == 'question_elsewhere':
            q['related_refs'] = ['REQ-other']
        elif escape == 'repeat_answer':
            p['questions'][0].update(status='answered', answer='允许相接')
        else:
            q['options'] = ['默认允许相接']
    snapshot = copy.deepcopy(p)
    with pytest.raises(Problem) as error:
        validate_response(value, p, scope(p, target()))
    assert error.value.code == 'RECOMMENDATION_SCOPE'
    assert error.value.model_repair_allowed is False
    assert p == snapshot


def test_explicit_ai_recommended_answer_stays_unanswered(tmp_path):
    _, p = project(tmp_path)
    value = response(p)
    value['questions'] = [dict(temp_id='QTMP-new', topic_key='true-new-decision',
        question='是否允许编辑草稿？', why='影响编辑范围', related_refs=['REQ-0001'],
        source_refs=[], options=['建议：允许编辑未保存草稿（待人工确认）'], blocking=False)]
    validate_response(value, p, scope(p, target()))
    apply_response(p, value)
    created = next(q for q in p['questions'] if q['topic_key'] == 'true-new-decision')
    assert created['status'] == 'open' and created['answer'] is None
    assert created['options'] == value['questions'][0]['options']


def test_semantic_review_request_never_expands_to_unrelated_pending_answers(tmp_path):
    _, p = project(tmp_path)
    for q in p['questions']:
        q.update(status='answered', answer='仅合成回答', decision_coverage='pending',
                 answer_source_refs=p['items'][0]['source_refs'])
    excerpts = p['sources'][0]['excerpts']
    global_request = build_request(p, copy.deepcopy(p), excerpts)
    local_request = build_request(p, copy.deepcopy(p), excerpts, question_ids=['Q-0001'])
    assert {t['question_id'] for t in global_request['targets']} == {'Q-0001', 'Q-other'}
    assert {t['question_id'] for t in local_request['targets']} == {'Q-0001'}


def test_row_local_review_keeps_cross_row_decision_groups_for_explicit_review(tmp_path):
    _, p = project(tmp_path)
    for q in p['questions']:
        q.update(decision_group_id='QG-shared', decision_answer_id='DA-shared',
                 decision_coverage='pending')
    p['decision_answers']=[dict(id='DA-shared',question_id='QG-shared',
        decision_ids=['Q-0001','Q-other'],text='两件事的当前合成原话',
        source_refs=p['items'][0]['source_refs'])]
    excerpts=p['sources'][0]['excerpts']
    assert build_request(p,copy.deepcopy(p),excerpts) is not None
    assert build_request(p,copy.deepcopy(p),excerpts,question_ids=['Q-0001']) is None


def test_existing_action_plan_and_worker_carry_scope_without_automatic_confirmation(tmp_path):
    async def run():
        store, p = project(tmp_path)
        provider = FakeProvider([response(p)])
        workflow = Workflow(store, provider)
        actions = Actions(store, workflow)
        body = dict(expected_revision=p['revision'], action='clarify', message='',
                    document_type='prd', target=target())
        plan = actions.plan(p['id'], body)
        assert plan['label'] == '完善这条需求建议'
        assert not provider.received and not plan['missing']
        task = actions.start(p['id'], body, plan['plan_hash'], 'local-offline-task', False)
        await actions.jobs[task['id']]
        finished = store.get_record(p['id'], task['id'], 'user_task')
        result = store.get(p['id'])
        run = store.get_record(p['id'], finished['run_ids'][0], 'run')
        assert finished['status'] == 'succeeded', finished
        assert finished['calls'] == 1 and len(provider.received) == 1
        assert run['requirement_recommendation_scope']['requirement_id'] == 'REQ-0001'
        assert result['items'][:len(p['items'])] == p['items']
        assert result['items'][-1]['selection_status'] == 'candidate'
        assert result['items'][-1]['recommendation_requirement_id'] == 'REQ-0001'
        assert result['questions'] == p['questions']
        assert result['active_baseline_id'] is None
    asyncio.run(run())


def test_global_clarify_keeps_existing_contract(tmp_path):
    _, p = project(tmp_path)
    messages, _, _ = assemble(p, 'clarify', '', DEFAULT, tmp_path)
    header = json.loads(messages[0]['content'].split('可信任务头：')[1])
    assert 'requirement_recommendation_contract' not in header
    assert 'product_context_proposal' in header['schema']['properties']
    value = response(p)
    value['proposals'][0]['target_item_id'] = 'REQ-other'
    validate_response(value, p, None)


def test_local_provider_schema_forbids_invented_future_acceptance_id(tmp_path):
    _, p = project(tmp_path)
    messages, _, _ = assemble(p, 'clarify', '', DEFAULT, tmp_path,
                              requirement_recommendation_scope=scope(p,target()))
    schema=json.loads(messages[0]['content'].split('可信任务头：')[1])['schema']
    validator=jsonschema.Draft202012Validator(schema)
    valid=response(p)
    assert validator.is_valid(valid)
    invalid=copy.deepcopy(valid)
    invalid['proposals'][0]['related_refs']=['AC-FUTURE-INVENTED']
    errors=list(validator.iter_errors(invalid))
    assert errors and any('related_refs' in list(error.absolute_path) for error in errors)
    extra=copy.deepcopy(valid)
    extra['proposals'].append(dict(temp_id='TMP-nongoal',action='add',target_item_id=None,
        kind='non_goal',title='异常策略未规定',statement='材料未规定异常策略。',
        applies_to='to_be',epistemic_status='reported',source_refs=p['items'][0]['source_refs'],
        related_refs=['REQ-0001']))
    assert not validator.is_valid(extra)
    # The shared public contract must remain unchanged for ordinary tasks.
    global_messages,_,_=assemble(p,'clarify','',DEFAULT,tmp_path)
    global_schema=json.loads(global_messages[0]['content'].split('可信任务头：')[1])['schema']
    assert 'enum' not in global_schema['$defs']['Proposal']['properties']['related_refs']['items']


def local_header(p, folder):
    messages, _, _ = assemble(p, 'clarify', '', DEFAULT, folder,
                              requirement_recommendation_scope=scope(p, target()))
    return json.loads(messages[0]['content'].split('可信任务头：')[1])


def test_unanswered_historical_words_never_become_answer_refs(tmp_path):
    _, p = project(tmp_path)
    p['messages'].append(dict(role='user', stage='interview', text='按照文档来', source_id='SRC-0001'))
    header = local_header(p, tmp_path)
    binding = header['requirement_recommendation_contract']['saved_answer_binding']
    assert binding['allowed_question_ids'] == []
    assert '历史聊天原话' in binding['instruction'] and 'source_refs' in binding['instruction']
    assert header['schema']['$defs']['Proposal']['properties']['answer_refs']['maxItems'] == 0
    value = response(p)
    value['proposals'][0]['answer_refs'] = ['Q-0001']
    before = copy.deepcopy(p)
    validator = jsonschema.Draft202012Validator(header['schema'])
    assert any('answer_refs' in list(e.absolute_path) for e in validator.iter_errors(value))
    with pytest.raises(Problem) as error:
        validate_response(value, p, scope(p, target()))
    assert error.value.code == 'RECOMMENDATION_SCOPE' and not error.value.model_repair_allowed
    # The underlying evidence validator remains strict and unchanged.
    from app.understanding import validate
    with pytest.raises(Problem) as error:
        validate(value, p, p['sources'][0]['excerpts'])
    assert error.value.code == 'REFERENCE_INVALID'
    assert p == before


@pytest.mark.parametrize('mode', ['single', 'saved_group'])
def test_schema_lists_only_current_local_bindable_answers(tmp_path, mode):
    _, p = project(tmp_path)
    q = p['questions'][0]
    if mode == 'single':
        q.update(status='answered', answer='允许相接', answer_source_refs=p['items'][0]['source_refs'])
    else:
        q.update(decision_group_id='QG-one', decision_answer_id='DA-one', decision_coverage='pending')
        p['decision_answers'] = [dict(id='DA-one', question_id='QG-one', decision_ids=[q['id']],
            text='允许相接', answer_hash=digest('允许相接'), source_refs=p['items'][0]['source_refs'])]
    p['questions'][-1].update(status='answered', answer='另一个范围的回答')
    header = local_header(p, tmp_path)
    answer_schema = header['schema']['$defs']['Proposal']['properties']['answer_refs']
    assert answer_schema['items']['enum'] == ['Q-0001']
    assert header['requirement_recommendation_contract']['saved_answer_binding']['allowed_question_ids'] == ['Q-0001']
    value = response(p)
    value['proposals'][0]['answer_refs'] = ['Q-0001']
    validator = jsonschema.Draft202012Validator(header['schema'])
    assert validator.is_valid(value)
    validate_response(value, p, scope(p, target()))
    value['proposals'][0]['answer_refs'] = ['Q-other']
    assert not validator.is_valid(value)
    with pytest.raises(Problem):
        validate_response(value, p, scope(p, target()))


def test_new_acceptance_may_cite_answer_source_but_not_bind_answer_id(tmp_path):
    _, p = project(tmp_path)
    p['questions'][0].update(status='answered', answer='允许相接')
    value = response(p)
    value['proposals'].append(dict(temp_id='TMP-ac', action='add', target_item_id=None,
        kind='acceptance', title='相接验收建议', statement='管理员保存相接时段后可以查看该时段。',
        applies_to='to_be', epistemic_status='reported', source_refs=p['items'][0]['source_refs'],
        related_refs=['REQ-0001'], answer_refs=['Q-0001']))
    header = local_header(p, tmp_path)
    assert not jsonschema.Draft202012Validator(header['schema']).is_valid(value)
    with pytest.raises(Problem):
        validate_response(value, p, scope(p, target()))


@pytest.mark.parametrize('refs', [[], ['RULE-0001'], ['AC-0001', 'RULE-0001'],
                                  ['RULE-0001', 'AC-0001', 'Q-0001']])
def test_original_requirement_relation_const_is_enforced_at_runtime(tmp_path, refs):
    _, p = project(tmp_path)
    value = response(p)
    value['proposals'][0]['related_refs'] = refs
    before = copy.deepcopy(p)
    header = local_header(p, tmp_path)
    assert not jsonschema.Draft202012Validator(header['schema']).is_valid(value)
    with pytest.raises(Problem) as error:
        validate_response(value, p, scope(p, target()))
    assert error.value.code == 'RECOMMENDATION_SCOPE'
    assert p == before


def test_independently_sourced_candidate_keeps_unanswered_question_open(tmp_path):
    _, p = project(tmp_path)
    source = copy.deepcopy(p['sources'][0])
    source.update(id='SRC-HISTORY', title='已有访谈原话')
    source['excerpts'] = [dict(source['excerpts'][0], id='EX-HISTORY', source_id='SRC-HISTORY',
                               text='本期电价时段由管理员维护。相接边界尚未决定。')]
    p['sources'].append(source)
    p['messages'].append(dict(role='user', stage='interview', text=source['excerpts'][0]['text'], source_id='SRC-HISTORY'))
    value = response(p)
    value['proposals'][0].update(source_refs=[dict(source_id='SRC-HISTORY', excerpt_id='EX-HISTORY')], answer_refs=[])
    value['proposals'].append(dict(temp_id='TMP-ac', action='add', target_item_id=None,
        kind='acceptance', title='管理员维护入口验收', statement='管理员进入电价时段页面，可以查看时段维护内容。',
        applies_to='to_be', epistemic_status='reported', source_refs=p['items'][0]['source_refs'], related_refs=['REQ-0001']))
    original = copy.deepcopy(p)
    header = local_header(p, tmp_path)
    assert jsonschema.Draft202012Validator(header['schema']).is_valid(value)
    from app.contracts import validate_response as validate_general
    excerpts = [excerpt for source in p['sources'] for excerpt in source['excerpts']]
    validate_general(value, 'clarify', p, excerpts)
    validate_response(value, p, scope(p, target()))
    apply_response(p, value)
    assert p['items'][:len(original['items'])] == original['items']
    assert all(item['selection_status'] == 'candidate' for item in p['items'][len(original['items']):])
    assert p['items'][len(original['items'])]['source_refs'] == value['proposals'][0]['source_refs']
    assert p['questions'] == original['questions']
    assert p['questions'][0]['status'] == 'open' and p['questions'][0]['answer'] is None
    assert not p['items'][len(original['items'])].get('answer_versions')
    assert p['sources'] == original['sources']
