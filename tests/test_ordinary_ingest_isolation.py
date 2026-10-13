"""Ordinary organization must not inherit the optional benchmark output contract."""
import copy
import json

import jsonschema
import pytest

from app.benchmark import ordinary_response
from app.contracts import SCHEMA
from app.core import Problem, digest
from app.provider import DEFAULT, assemble
from app.store import Store
from tests.helpers import prepared
from tests.test_benchmark_flow import material
from tests.test_ui02_actions import case, plan, start, finished

EMPTY = {'summary': '', 'observations': [], 'limitations': []}


@pytest.mark.parametrize('stage', ['ingest', 'clarify', 'change', 'brainstorm', 'ui', 'review', 'prd'])
def test_normal_generation_does_not_advertise_benchmark_fields(tmp_path, stage):
    store = Store(tmp_path)
    p = prepared(store)
    p['reference_mode'] = 'builtin'
    before = copy.deepcopy(SCHEMA)
    messages, _, _ = assemble(p, stage, '', DEFAULT, tmp_path)
    header = json.loads(messages[0]['content'].split('可信任务头：', 1)[1])
    assert 'benchmark_analysis' not in header['schema']['properties']
    assert 'benchmark_analysis' not in header['response_boundary']['object_keys']['$']
    assert 'benchmark_contract' not in header
    if stage == 'ingest':
        assert '没有对标任务' in header['benchmark_output_policy']
        jsonschema.Draft202012Validator(header['schema']).validate(header['ingest_contract']['example'])
    assert SCHEMA == before, 'one request must not change the shared validator'


@pytest.mark.parametrize('extra_error', [False, True])
def test_exact_empty_template_is_audited_without_business_changes_or_benchmark_record(case, extra_error):
    c, app, model, root = case
    material(c, root, 'goal')
    originals = []
    def transform(value, body, context):
        if len(model['requests']) == 1:
            value['benchmark_analysis'] = copy.deepcopy(EMPTY)
            if extra_error:
                value['findings_extra_placeholder'] = []
        originals.append(copy.deepcopy(value))
        return value
    model['transform'] = transform
    body, preview = plan(c, root, 'organize', '')
    assert start(c, root, body, preview).status_code == 200
    task = finished(c, root)
    assert task['status'] == 'succeeded', task
    assert task['calls'] == 1 + int(extra_error)
    p = c.get(root).json()
    assert 'benchmark_analysis' not in p
    assert all(i['selection_status'] == 'candidate' for i in p['items'])
    assert [i['statement'] for i in p['items']] == [i['statement'] for i in originals[0]['proposals']]
    assert not p['baselines'] and not p['documents']
    run = app.state.store.get_record(p['id'], task['run_ids'][0], 'run')
    attempt = run['attempts'][0]
    evidence = json.loads((app.state.store.folder / 'evidence/model-calls' / (attempt['call_id'] + '.json')).read_text('utf-8'))
    raw = json.loads(evidence['final_output'])
    assert raw == originals[0] and raw['benchmark_analysis'] == EMPTY
    normalized, changed = ordinary_response(raw)
    assert changed
    assert evidence['response_normalizations'] == attempt['response_normalizations']
    audit = evidence['response_normalizations'][0]
    assert audit['input_hash'] == digest(raw) and audit['output_hash'] == digest(normalized)
    for request in model['requests']:
        header = json.loads(request['messages'][0]['content'].split('可信任务头：', 1)[1])
        assert 'benchmark_analysis' not in header['schema']['properties']
    if extra_error:
        assert 'findings_extra_placeholder' in model['requests'][1]['messages'][-1]['content']
        assert '不要补写' in model['requests'][1]['messages'][-1]['content']


@pytest.mark.parametrize('value', [None, {}, {**EMPTY, 'summary': '没有竞品材料'},
    {**EMPTY, 'limitations': ['没有参考资料']}, {**EMPTY, 'observations': [{'title': '实际观察'}]},
    {**EMPTY, 'other': ''}, {**EMPTY, 'summary': ' '}])
def test_nonexact_or_meaningful_unrequested_benchmark_is_rejected_once(case, value):
    c, app, model, root = case
    material(c, root, 'goal')
    def transform(response, body, context):
        response['benchmark_analysis'] = copy.deepcopy(value)
        return response
    model['transform'] = transform
    body, preview = plan(c, root, 'organize', '')
    assert start(c, root, body, preview).status_code == 200
    task = finished(c, root)
    assert task['status'] == 'failed' and task['error'] == 'SCHEMA_INVALID'
    assert '无需补充竞品资料' in task['message']
    assert task['calls'] == len(model['requests']) == 1
    p = c.get(root).json()
    assert not p['items'] and 'benchmark_analysis' not in p
    assert not any(m['role'] == 'assistant' for m in p['messages'])
    assert not p['baselines']


def test_empty_template_normalization_cannot_hide_repair_business_changes(case):
    c, app, model, root = case
    material(c, root, 'goal')
    def transform(value, body, context):
        if len(model['requests']) == 1:
            value['benchmark_analysis'] = copy.deepcopy(EMPTY)
            value['findings_extra_placeholder'] = []
        else:
            value['proposals'][0]['statement'] = '擅自更改的规则。'
        return value
    model['transform'] = transform
    body, preview = plan(c, root, 'organize', '')
    assert start(c, root, body, preview).status_code == 200
    task = finished(c, root)
    assert task['status'] == 'failed' and task['error'] == 'SEMANTIC_BLOCKED'
    assert task['calls'] == 2 and not c.get(root).json()['items']


def test_normalizing_empty_template_never_mutates_original():
    original = {'proposals': [{'statement': '需保留的原文'}], 'benchmark_analysis': copy.deepcopy(EMPTY)}
    before = copy.deepcopy(original)
    result, changed = ordinary_response(original)
    assert changed and original == before
    assert result == {'proposals': original['proposals']}
    with pytest.raises(Problem) as error:
        ordinary_response({**original, 'benchmark_analysis': {**EMPTY, 'summary': '未授权结论'}})
    assert not error.value.model_repair_allowed
