"""Offline decision/action boundaries; all identities and answers are synthetic."""
import asyncio
import copy
import json

import jsonschema
import pytest

from app.contracts import validate_response
from app.core import Problem, digest
from app.provider import DEFAULT, assemble, origin, request_schema
from app.understanding import accept_answer_update
from app.workflow import Workflow, apply_response
from tests.test_answer_binding_repair import project_with_answer, acceptance_revision
from tests.test_runtime import FakeProvider, execute_run


def response(project, *, split=False):
    value = acceptance_revision(project, valid_binding=False)
    value['proposals'] = []
    question = dict(temp_id='QTMP-parent', topic_key='decision-recheck',
        question='已有决定是否需要重新确认？', why='合成动作边界', options=[],
        blocking=False, related_refs=['REQ-0001'], source_refs=[],
        target_question_id='Q-0001', decision_points=[])
    if split:
        question['decision_points'] = [dict(question, temp_id='QTMP-'+key,
            topic_key=key, question=text)
            for key, text in [('a', '相接边界是否允许？'), ('b', '边界如何提示？')]]
        for child in question['decision_points']:
            child.pop('target_question_id'); child.pop('decision_points')
    value['questions'] = [question]
    return value


@pytest.mark.parametrize('state', ['answered', 'superseded', 'deferred'])
@pytest.mark.parametrize('split', [False, True])
def test_current_or_inactive_decision_cannot_be_reasked_as_split(tmp_path, state, split):
    _, p = project_with_answer(tmp_path, acceptance_related=True)
    if state == 'superseded': p['questions'][0]['superseded_by'] = ['Q-successor']
    if state == 'deferred': p['questions'][0]['out_of_scope_reason'] = '合成范围决定'
    value = response(p, split=split); before = copy.deepcopy((p, value))
    with pytest.raises(Problem) as caught:
        validate_response(value, 'clarify', p, p['sources'][0]['excerpts'])
    assert caught.value.code == 'REFERENCE_INVALID'
    assert caught.value.model_repair_allowed is False
    assert (p, value) == before


def test_unanswered_split_and_independent_new_question_preserve_saved_answer(tmp_path):
    _, p = project_with_answer(tmp_path, acceptance_related=True)
    old = copy.deepcopy(p['questions'][0])
    independent = response(p)
    independent['questions'][0].update(target_question_id=None, topic_key='separate-decision',
        question='拒绝提示应如何展示？')
    validate_response(independent, 'clarify', p, p['sources'][0]['excerpts'])
    apply_response(p, independent)
    assert p['questions'][0] == old and len(p['questions']) == 2
    p['questions'][1].update(id='Q-open', status='open', answer=None)
    split = response(p, split=True); split['questions'][0]['target_question_id'] = 'Q-open'
    validate_response(split, 'clarify', p, p['sources'][0]['excerpts'])
    apply_response(p, split)
    assert p['questions'][0] == old
    assert len(p['questions'][1]['superseded_by']) == 2


@pytest.mark.parametrize('inactive', [None, 'superseded_by', 'out_of_scope_reason'])
def test_answer_application_only_uses_current_answer_and_original_target(tmp_path, inactive):
    _, p = project_with_answer(tmp_path, acceptance_related=True)
    value = acceptance_revision(p, valid_binding=True)
    if inactive: p['questions'][0][inactive] = ['Q-successor'] if inactive == 'superseded_by' else '合成排除'
    before = copy.deepcopy(p)
    if inactive:
        with pytest.raises(Problem) as caught:
            validate_response(value, 'clarify', p, p['sources'][0]['excerpts'])
        assert caught.value.code == 'REFERENCE_INVALID' and not caught.value.model_repair_allowed
        candidate = dict(value['proposals'][0], answer_versions={'Q-0001': digest(p['questions'][0]['answer'])})
        with pytest.raises(Problem): accept_answer_update(p, candidate)
        assert p == before
    else:
        validate_response(value, 'clarify', p, p['sources'][0]['excerpts'])
        apply_response(p, value)
        candidate = next(i for i in p['items'] if i.get('target_item_id') == 'AC-0001')
        assert p['questions'][0]['answer'] == before['questions'][0]['answer']
        assert p['questions'][0]['understanding_status'] != 'applied'
        accept_answer_update(p, candidate)
        assert not p['questions'][0]['applied_item_ids']
        assert p['questions'][0]['understanding_status']=='candidate_ready'


@pytest.mark.parametrize('stage', ['ingest', 'clarify', 'change'])
def test_model_task_schema_distinguishes_decisions_and_splittable_questions(tmp_path, stage):
    _, p = project_with_answer(tmp_path, acceptance_related=True)
    p['questions'].append(dict(p['questions'][0], id='Q-open', status='open', answer=None))
    p['questions'].append(dict(p['questions'][0], id='Q-inactive', out_of_scope_reason='合成排除'))
    original = copy.deepcopy(request_schema(stage))
    messages, _, _ = assemble(p, stage, '仅合成动作边界', DEFAULT, tmp_path)
    header = json.loads(messages[0]['content'].split('可信任务头：', 1)[1])
    policy = header['question_action_contract']
    assert policy['answered_decision_ids'] == ['Q-0001']
    assert policy['splittable_question_ids'] == ['Q-open']
    assert policy['inactive_question_ids'] == ['Q-inactive']
    schema = header['schema']['$defs']['Question']['properties']['target_question_id']
    validator = jsonschema.Draft202012Validator(schema)
    assert validator.is_valid(None) and validator.is_valid('Q-open')
    assert not validator.is_valid('Q-0001') and not validator.is_valid('Q-inactive')
    assert request_schema(stage) == original
    context = json.loads(messages[1]['content'][0]['text'])
    assert context['questions'] == p['questions']  # no filtering or lost answers


def test_answered_split_fails_once_without_partial_application_or_repair(tmp_path):
    async def scenario():
        store, p = project_with_answer(tmp_path, acceptance_related=True)
        with store.edit(p['id'], p['revision'], '合成授权', bump=False) as (draft, _):
            draft['grants'][origin(DEFAULT)] = {'source_ids': [s['id'] for s in draft['sources']]}
        before = store.get(p['id'])
        bad = response(p)
        bad['proposals'] = acceptance_revision(p, valid_binding=True)['proposals']
        provider = FakeProvider([bad, bad, bad]); workflow = Workflow(store, provider)
        run = workflow.start(p['id'], p['revision'], 'clarify', '仅合成已答问题动作')
        await workflow.tasks[run['id']]
        return before, store.get(p['id']), store.get_record(p['id'], run['id'], 'run'), provider
    before, after, run, provider = asyncio.run(scenario())
    assert run['status'] == 'failed' and run['error'] == 'REFERENCE_INVALID'
    assert run['calls'] == 1 and run['repair_count'] == 0 and len(provider.received) == 1
    assert not run.get('result_applied')
    for key in ('revision', 'questions', 'items', 'messages', 'documents'):
        assert after[key] == before[key]


def test_identical_complete_output_and_same_error_cannot_trigger_second_paid_repair(tmp_path):
    run, p, provider = execute_run(tmp_path, [{'confirmed': True}] * 3)
    assert run['calls'] == 2 and run['repair_count'] == 1
    assert len(provider.received) == 2 and run['error'] == 'SCHEMA_INVALID'
    assert not run.get('result_applied') and not p['messages']


def test_unanswered_question_cannot_be_used_as_an_answer_binding(tmp_path):
    _, p = project_with_answer(tmp_path, acceptance_related=True)
    p['questions'][0].update(status='open', answer=None)
    value = acceptance_revision(p, valid_binding=True); before = copy.deepcopy(p)
    with pytest.raises(Problem) as caught:
        validate_response(value, 'clarify', p, p['sources'][0]['excerpts'])
    assert caught.value.code == 'REFERENCE_INVALID' and not caught.value.model_repair_allowed
    assert p == before


def test_identical_full_raw_parse_failures_stop_before_third_request(tmp_path, monkeypatch):
    class RawFailure(FakeProvider):
        async def request(self, config, messages, evidence=None):
            self.received.append(messages)
            evidence.response('{"summary":"原字面内容", "proposals":[], "questions":[]',
                              finish_reason='stop', usage={'total_tokens': 100})
            raise Problem('SCHEMA_INVALID', '同一原始JSON未闭合')
    monkeypatch.setattr('tests.test_runtime.FakeProvider', RawFailure)
    run, p, provider = execute_run(tmp_path, [None] * 3)
    assert run['calls'] == 2 and run['repair_count'] == 1 and len(provider.received) == 2
    assert run['error'] == 'SCHEMA_INVALID' and not p['messages']
