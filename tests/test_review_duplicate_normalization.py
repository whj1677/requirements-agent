"""Real transport/parser/workflow with synthetic replies; no external model calls."""
import asyncio
import json

import httpx
import pytest

from app.actions import Actions
from app.core import digest
from app.model_evidence import ModelCallEvidence
from app.prewrite import status
from app.provider import DEFAULT, Provider, origin
from app.workflow import Workflow
from tests.test_prewrite_consistency import isolated_store, review
from tests.provider_transport import reply


def _run(tmp_path, monkeypatch, variant):
    store, project = isolated_store(tmp_path)
    value = review(project, blocker=variant == 'blocker')
    if variant == 'missing_coverage':
        value['result']['reviewed_refs'] = []
    if variant == 'redacted':
        value['summary'] = '合成示例 Bearer synthetic-token 的限制说明'
    raw = json.dumps(value, ensure_ascii=False)
    if variant == 'nested_same':
        raw = raw.replace('"required_decisions": []',
                          '"required_decisions": [], "required_decisions": []', 1)
        expected_path = ['result', 'required_decisions']
    elif variant == 'nested_conflict':
        raw = raw.replace('"required_decisions": []',
                          '"required_decisions": [], "required_decisions": ["另一项决定"]', 1)
        expected_path = None
    elif variant == 'typed_conflict':
        raw = raw.replace('"limitations": []', '"limitations": [true], "limitations": [1]', 1)
        expected_path = None
    else:
        first = '"limitations": ' + json.dumps(value['limitations'], ensure_ascii=False)
        repeated = first if variant != 'conflict' else '"limitations": ["不同限制"]'
        raw = raw.replace(first, first + ', ' + repeated, 1)
        expected_path = ['limitations']
    if variant == 'limited':
        response = ModelCallEvidence.response

        def limited_response(self, *args, **kwargs):
            self.final_limit = 20
            return response(self, *args, **kwargs)

        monkeypatch.setattr(ModelCallEvidence, 'response', limited_response)

    requests = []

    def respond(request):
        requests.append(request)
        return reply(request, raw, model='SYNTHETIC_REVIEW', prompt=2, completion=3)

    original = httpx.AsyncClient
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=transport, **kwargs))
    provider = Provider(tmp_path / 'absent.env')
    provider.keys[origin(DEFAULT)] = 'offline-synthetic-key'
    workflow = Workflow(store, provider)
    actions = Actions(store, workflow)

    async def execute():
        body = dict(expected_revision=project['revision'], action='prewrite', message='',
                    document_type='prd', max_calls=None, option_id=None)
        plan = actions.plan(project['id'], body)
        assert not plan['missing'], plan['missing']
        task = actions.start(project['id'], body, plan['plan_hash'], 'synthetic-review', True)
        await actions.jobs[task['id']]
        task = store.get_record(project['id'], task['id'], 'user_task')
        run = store.get_record(project['id'], task['run_ids'][0], 'run')
        return task, run

    task, run = asyncio.run(execute())
    evidence = [json.loads(path.read_text('utf-8'))
                for path in (tmp_path / 'evidence' / 'model-calls').glob('*.json')]
    return project, store.get(project['id']), task, run, requests, evidence, raw, value, expected_path


@pytest.mark.parametrize('variant', ['same', 'nested_same', 'blocker'])
def test_identical_review_members_use_one_call_and_keep_semantic_gates(tmp_path, monkeypatch, variant):
    before, after, task, run, requests, evidence, raw, value, path = _run(tmp_path, monkeypatch, variant)
    assert len(requests) == task['calls'] == run['calls'] == len(evidence) == 1
    normalization = dict(rule='identical-json-members-1', paths=[path],
                         raw_hash=digest(raw), derived_hash=digest(value))
    assert run['attempts'][0]['syntax_normalization'] == normalization
    assert evidence[0]['syntax_normalization'] == normalization
    assert evidence[0]['final_output'] == raw
    assert evidence[0]['evidence_limitations'] == []
    assert evidence[0]['validation_result'] == 'accepted'
    assert 'json_integrity_failure' not in evidence[0]
    assert after['items'] == before['items'] and after['documents'] == before['documents']
    assert after['active_baseline_id'] == before['active_baseline_id']
    if variant == 'blocker':
        assert task['status'] == 'awaiting_user'
        assert status(after)['status'] == 'needs_confirmation'
        assert len(after['questions']) == 1 and after['questions'][0]['status'] == 'open'
        assert not after['questions'][0].get('answer')
    else:
        assert task['status'] == 'succeeded' and status(after)['status'] == 'passed'


@pytest.mark.parametrize('variant', ['conflict', 'typed_conflict', 'nested_conflict', 'limited', 'redacted'])
def test_unsafe_review_duplicates_do_not_apply_or_trigger_paid_repair(tmp_path, monkeypatch, variant):
    before, after, task, run, requests, evidence, raw, _, _ = _run(tmp_path, monkeypatch, variant)
    assert len(requests) == task['calls'] == run['calls'] == len(evidence) == 1
    assert task['status'] == 'failed' and run['error'] == 'SEMANTIC_BLOCKED'
    assert run.get('result_applied') is not True
    assert evidence[0]['json_integrity_failure'] == 'duplicate_member'
    assert evidence[0]['validation_result'] == 'SCHEMA_INVALID'
    assert 'syntax_normalization' not in evidence[0]
    assert status(after)['status'] == 'failed'
    assert after['items'] == before['items'] and after['documents'] == before['documents']
    assert after['questions'] == before['questions']
    if variant in ('limited', 'redacted'):
        expected = 'final_output_truncated' if variant == 'limited' else 'final_output_redacted'
        assert expected in evidence[0]['evidence_limitations']
        assert evidence[0]['final_output'] != raw
    else:
        assert evidence[0]['final_output'] == raw


def test_normalized_review_still_needs_complete_prewrite_coverage(tmp_path, monkeypatch):
    before, after, task, run, requests, evidence, raw, _, _ = _run(tmp_path, monkeypatch, 'missing_coverage')
    assert len(requests) == task['calls'] == run['calls'] == len(evidence) == 1
    assert task['status'] == 'failed' and run['error'] == 'PREWRITE_COVERAGE_REQUIRED'
    assert evidence[0]['syntax_normalization']['paths'] == [['limitations']]
    assert evidence[0]['final_output'] == raw
    assert status(after)['status'] == 'failed'
    assert after['items'] == before['items'] and after['documents'] == before['documents']
