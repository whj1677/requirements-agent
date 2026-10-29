"""Synthetic recovery examples and counterexamples; no live materials or calls."""
import asyncio
import copy
import json

import pytest

from app.core import Problem
from app.contracts import validate_response
from app.response_normalization import normalize
from app.model_json import schema_closers
from app.provider import DEFAULT, Provider, assemble, origin
from app.sources import save_source
from app.workflow import Workflow
from tests.test_answer_binding_repair import project_with_answer, acceptance_revision
from tests.test_deepseek_responses import mock_transport, completed, provider, evidence, SCHEMA
from tests.test_runtime import FakeProvider


def scenario(tmp_path):
    store, p = project_with_answer(tmp_path, acceptance_related=False)
    answer = p['questions'][0]['answer']
    source = save_source(store, '合成回答.txt', answer.encode(), 'goal')
    with store.edit(p['id'], p['revision'], '合成回答来源') as (draft, _):
        draft['sources'].append(source)
        draft['questions'][0]['answer_source_refs'] = [dict(source_id=source['id'], excerpt_id=source['excerpts'][0]['id'])]
        draft['grants'][origin(DEFAULT)] = {'source_ids': [s['id'] for s in draft['sources']]}
    p = store.get(p['id'])
    value = acceptance_revision(p, valid_binding=True)
    value['proposals'][0]['source_refs'] += p['questions'][0]['answer_source_refs']
    value['used_source_refs'] = copy.deepcopy(value['proposals'][0]['source_refs'])
    ex = [e for s in p['sources'] for e in s['excerpts']]
    return store, p, value, ex


def test_combined_temporary_id_source_pair_and_answer_binding_need_no_model_repair(tmp_path):
    store, p, value, ex = scenario(tmp_path)
    row = value['proposals'][0]
    row['temp_id'] = 'TMP-验收 条款'
    value['questions'] = [dict(temp_id='QTMP-待确认', topic_key='boundary', question='边界如何处理？',
        why='缺少边界说明', options=[], blocking=True, related_refs=[row['temp_id']], source_refs=[])]
    row['source_refs'][0]['source_id'] = 'SRC-wrong'
    original = copy.deepcopy(value)
    result, changes = normalize(value, p, ex, 'clarify')
    assert value == original
    assert result['proposals'][0]['answer_refs'] == []
    assert result['proposals'][0]['source_refs'][-1] == p['questions'][0]['answer_source_refs'][0]
    assert result['questions'][0]['related_refs'] == [result['proposals'][0]['temp_id']]
    assert result['proposals'][0]['statement'] == row['statement']
    validate_response(result, 'clarify', p, ex)
    assert {c['rule'] for c in changes} == {'temporary-id-syntax-1', 'unique-sent-source-pair-1', 'indirect-answer-source-only-1'}
    assert normalize(result, p, ex, 'clarify') == (result, [])


@pytest.mark.parametrize('fault', ['missing_answer_source', 'source_not_sent', 'foreign_answer', 'stale_answer',
                                  'unknown_question', 'wrong_kind', 'wrong_target', 'add', 'answer_not_cited'])
def test_invalid_answer_cannot_be_silently_detached_without_actual_evidence(tmp_path, fault):
    _, p, value, ex = scenario(tmp_path)
    row, q = value['proposals'][0], p['questions'][0]
    if fault == 'missing_answer_source': q.pop('answer_source_refs')
    elif fault == 'source_not_sent': ex = ex[:1]
    elif fault == 'foreign_answer': q['answer'] = '资料里不存在的决定'
    elif fault == 'stale_answer': q['status'] = 'open'
    elif fault == 'unknown_question': row['answer_refs'] = ['Q-absent']
    elif fault == 'wrong_kind': row['kind'] = 'rule'
    elif fault == 'wrong_target': row['target_item_id'] = 'AC-absent'
    elif fault == 'add': row.update(action='add', target_item_id=None)
    else: row['source_refs'] = row['source_refs'][:1]
    result, _ = normalize(value, p, ex, 'clarify')
    assert result['proposals'][0]['answer_refs'] == row['answer_refs']
    with pytest.raises(Problem): validate_response(result, 'clarify', p, ex)


def test_valid_direct_answer_binding_and_stable_ids_remain_unchanged(tmp_path):
    _, p, value, ex = scenario(tmp_path)
    p['questions'][0]['related_refs'].append('AC-0001')
    result, changes = normalize(value, p, ex, 'clarify')
    assert result == value and changes == []


def test_workflow_combined_faults_save_only_candidate_with_one_call(tmp_path):
    async def run():
        store, p, value, _ = scenario(tmp_path)
        value['proposals'][0]['temp_id'] = 'TMP-验收'
        original = copy.deepcopy(value)
        before = copy.deepcopy(p)
        provider = FakeProvider([value])
        w = Workflow(store, provider)
        r = w.start(p['id'], p['revision'], 'clarify', '合成引用恢复验证')
        await w.tasks[r['id']]
        return store.get_record(p['id'], r['id']), store.get(p['id']), before, value, original
    run, after, before, value, original = asyncio.run(run())
    assert run['error'] is None and run['calls'] == 1 and run['repair_count'] == 0
    assert run['result_applied'] and value == original
    assert after['items'][:len(before['items'])] == before['items']
    assert after['questions'] == before['questions']
    assert after['active_baseline_id'] is None
    assert after['items'][-1]['selection_status'] == 'candidate'
    assert after['items'][-1]['statement'] == value['proposals'][0]['statement']
    assert run['attempts'][0]['response_normalizations']


@pytest.mark.parametrize('raw', ['{"draft":"原文"', '{"draft":"原文" }'])
def test_schema_closers_preserve_literals_or_leave_valid_json_alone(raw):
    result = schema_closers(raw, SCHEMA)
    if raw.endswith('}'):
        assert result is None
    else:
        assert result[0] == {'draft': '原文'}


@pytest.mark.parametrize('raw', ['{"draft":', '{"draft":"半句话', '{"draft":"a",',
    '{"draft":"a","draft":"b"', '{"wrong":"a"', '{"draft":NaN', '{"draft":"a"}{}',
    '{"draft":"a"}}', '{"draft":"a" "other":"b"'])
def test_missing_values_conflicts_and_extra_data_are_not_recovered(raw):
    assert schema_closers(raw, SCHEMA) is None


def test_runtime_header_uses_structured_output_and_keeps_original_evidence(monkeypatch, tmp_path):
    requests = mock_transport(monkeypatch, completed('{"draft":"原文"'))
    messages = [dict(role='system', content='可信任务头：'+json.dumps(dict(
        transport_protocol='responses-json-schema-v1', schema=SCHEMA))), dict(role='user', content='合成资料')]
    proof = evidence(tmp_path)
    result, meta = asyncio.run(provider(tmp_path).request(DEFAULT, messages, proof))
    assert result == {'draft': '原文'} and len(requests) == 1
    assert requests[0]['url'].endswith('/responses')
    assert requests[0]['body']['text']['format']['name'] == 'requirements_runtime_v1'
    assert proof.value['final_output'] == '{"draft":"原文"'
    assert meta['syntax_normalization']['rule'] == 'schema-forced-container-closers-1'


@pytest.mark.parametrize('stage', ['interview', 'clarify', 'change', 'review', 'prd'])
def test_official_runtime_stages_advertise_structured_transport(tmp_path, stage):
    store, p, _, _ = scenario(tmp_path)
    w = Workflow(store, Provider(tmp_path/'absent.env'))
    config = w.config(stage)
    messages, _, _ = assemble(p, stage, '合成测试', config, store.folder)
    header = json.loads(messages[0]['content'].split('可信任务头：', 1)[1])
    assert header['transport_protocol'] == 'responses-json-schema-v1'


def seed_failed_runtime(tmp_path):
    from app.core import digest, execution_hash, now
    from app.model_evidence import ModelCallEvidence
    from tests.test_runtime import client
    c, app = client(tmp_path)
    store, p, value, _ = scenario(tmp_path)
    value['proposals'][0]['temp_id'] = 'TMP-验收 条款'
    messages, ex, omitted = assemble(p, 'clarify', '合成单题修订', DEFAULT, tmp_path)
    proof = ModelCallEvidence(tmp_path, 'CALL-original', 'RUN-history', 'clarify', 'synthetic',
        origin(DEFAULT), None, 'synthetic-key')
    proof.request_context(DEFAULT, messages, {}, {})
    proof.response(json.dumps(value, ensure_ascii=False), http_status=200, finish_reason='stop')
    proof.finish('SCHEMA_INVALID')
    store.record(p['id'], 'run', dict(stage='clarify', status='failed', error='SEMANTIC_BLOCKED',
        document_type='prd', user_task_id='TASK-history', result_applied=False, calls=2,
        input_state_hash=execution_hash(p), sent_excerpt_ids=[e['id'] for e in ex],
        attempts=[dict(call_id='CALL-original', actual_input_hash=digest(messages), evidence_limitations=[]),
                  dict(call_id='CALL-bad-repair', repair_of='CALL-original')]), 'RUN-history')
    store.record(p['id'], 'user_task', dict(action='clarify', label='根据回答更新理解', status='failed',
        message='格式修复改变了原文', error='SEMANTIC_BLOCKED', calls=2, run_ids=['RUN-history'],
        stages=['clarify'], completed_steps=0, source_ids=[], max_calls=None, cost=None, created=now(),
        retry_input=dict(action='clarify', target=dict(kind='question', id='Q-0001'))), 'TASK-history')
    return c, app, p, value, proof


def test_historical_runtime_recovers_first_output_without_reusing_bad_repair_or_paid_call(tmp_path, monkeypatch):
    c, app, p, value, proof = seed_failed_runtime(tmp_path)
    async def forbidden(*a, **kw): raise AssertionError('local recovery cannot request a provider')
    monkeypatch.setattr(app.state.provider, 'request', forbidden)
    root = '/api/projects/'+p['id']
    raw = proof.path.read_bytes()
    original_run = app.state.store.get_record(p['id'], 'RUN-history')
    assert c.get(root+'/actions').json()[0]['local_recovery_available'] is True
    response = c.post(root+'/actions/TASK-history/recover-draft', json={'expected_revision': p['revision']})
    assert response.status_code == 200, response.text
    assert response.json()['external_model_calls'] == 0
    after = app.state.store.get(p['id'])
    assert after['items'][:len(p['items'])] == p['items']
    assert after['items'][-1]['statement'] == value['proposals'][0]['statement']
    assert after['items'][-1]['selection_status'] == 'candidate'
    assert after['questions'] == p['questions'] and after['active_baseline_id'] is None
    assert app.state.store.get_record(p['id'], 'RUN-history') == original_run
    assert proof.path.read_bytes() == raw
    assert c.post(root+'/actions/TASK-history/recover-draft', json={'expected_revision': p['revision']}).json()['id'] == response.json()['id']


@pytest.mark.parametrize('fault', ['stale', 'truncated', 'redacted', 'hash', 'foreign_source', 'active', 'transport_rejected'])
def test_historical_runtime_recovery_rejects_unsafe_evidence_without_writes(tmp_path, fault):
    c, app, p, _, proof = seed_failed_runtime(tmp_path)
    store = app.state.store
    if fault == 'stale':
        with store.edit(p['id'], p['revision'], '新输入') as (draft, _):
            draft['messages'].append(dict(role='user', text='新决定'))
    elif fault == 'active':
        store.record(p['id'], 'user_task', dict(status='running'), 'TASK-other')
    else:
        e = json.loads(proof.path.read_text('utf-8'))
        if fault == 'truncated': e['finish_reason'] = 'length'
        elif fault == 'redacted': e['evidence_limitations'] = ['request_input_redacted']
        elif fault == 'transport_rejected': e['transport_rejection'] = 'PARAMETER_UNSUPPORTED'
        elif fault == 'hash': e['actual_input_hash'] = 'changed'
        else:
            v = json.loads(e['final_output']);v['proposals'][0]['source_refs'] = [dict(source_id='SRC-absent', excerpt_id='EX-absent')]
            e['final_output'] = json.dumps(v)
        proof.path.write_text(json.dumps(e), 'utf-8')
    before = store.get(p['id'])
    response = c.post('/api/projects/'+p['id']+'/actions/TASK-history/recover-draft', json={'expected_revision': before['revision']})
    assert response.status_code != 200
    assert store.get(p['id']) == before
