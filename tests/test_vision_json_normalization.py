"""Synthetic vision JSON recovery through real Provider and Workflow, without network."""
import asyncio
import io
import json

import httpx
import pytest
from PIL import Image

from app.provider import DEFAULT, Provider, origin
from app.sources import save_source
from app.store import Store
from app.workflow import Workflow


def _vision_reply(source, *, summary='合成图片观察'):
    ref = {'source_id': source['id'], 'excerpt_id': source['excerpts'][0]['id']}
    return {
        'schema_version': '1.1', 'stage': 'vision', 'summary': summary,
        'proposals': [], 'questions': [], 'findings': [], 'used_source_refs': [ref],
        'limitations': [],
        'result': {'observations': [{'source_ref': ref, 'region': '页面中央',
                                    'observation': '合成图中有一个矩形', 'uncertain': False}],
                   'unobservable': [], 'unreadable': []},
    }


def _raw_with_duplicate(value, kind):
    raw = json.dumps(value, ensure_ascii=False)
    if kind in ('top_same', 'top_conflict', 'top_typed', 'limited'):
        duplicate = ('"questions": []' if kind != 'top_conflict' else
                     '"questions": ["另一项"]')
        if kind == 'top_typed':
            raw = raw.replace('"uncertain": false', '"uncertain": true, "uncertain": 1', 1)
        else:
            raw = raw.replace('"questions": []', '"questions": [], ' + duplicate, 1)
    elif kind == 'nested_same':
        raw = raw.replace('"unreadable": []', '"unreadable": [], "unreadable": []', 1)
    elif kind == 'nested_conflict':
        raw = raw.replace('"unreadable": []', '"unreadable": [], "unreadable": ["不同"]', 1)
    else:
        raise AssertionError(kind)
    return raw


def _run_case(tmp_path, monkeypatch, kind):
    store = Store(tmp_path)
    project = store.create('合成视觉 JSON 检查')
    image = io.BytesIO()
    Image.new('RGB', (12, 12), 'white').save(image, format='PNG')
    source = save_source(store, 'synthetic.png', image.getvalue(), purpose='goal')
    with store.edit(project['id'], project['revision'], '合成图片与授权') as (project, _):
        project['sources'].append(source)
        project['grants'][origin(DEFAULT)] = {'source_ids': [source['id']]}
    project = store.get(project['id'])
    value = _vision_reply(source, summary='X' * 200000 if kind == 'limited' else '合成图片观察')
    raw = _raw_with_duplicate(value, kind)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={
            'model': 'SYNTHETIC_VISION',
            'choices': [{'message': {'content': raw}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 2, 'completion_tokens': 3, 'total_tokens': 5},
        })

    original = httpx.AsyncClient
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=transport, **kwargs))
    provider = Provider(tmp_path / 'absent.env')
    provider.keys[origin(DEFAULT)] = 'offline-test-key'
    workflow = Workflow(store, provider)

    async def execute():
        run = workflow.start(project['id'], project['revision'], 'vision', '',
                             config_override=DEFAULT)
        await workflow.tasks[run['id']]
        return store.get_record(project['id'], run['id'], 'run')

    run = asyncio.run(execute())
    evidence = [json.loads(path.read_text(encoding='utf-8'))
                for path in (tmp_path / 'evidence' / 'model-calls').glob('*.json')]
    return run, store.get(project['id']), source['id'], requests, evidence, raw


@pytest.mark.parametrize('kind,path', [
    ('top_same', ['questions']), ('nested_same', ['result', 'unreadable']),
])
def test_identical_vision_members_normalize_once_and_keep_exact_raw(tmp_path, monkeypatch, kind, path):
    run, project, source_id, requests, evidence, raw = _run_case(tmp_path, monkeypatch, kind)
    assert len(requests) == run['calls'] == len(run['attempts']) == len(evidence) == 1
    assert run['attempts'][0]['validation_result'] == 'accepted'
    assert run['attempts'][0]['syntax_normalization']['paths'] == [path]
    assert evidence[0]['syntax_normalization']['paths'] == [path]
    assert evidence[0]['final_output'] == raw
    assert evidence[0]['evidence_limitations'] == []
    assert evidence[0]['validation_result'] == 'accepted'
    assert run['result_applied'] is True and run['status'] in ('succeeded', 'partial')
    assert any(s['id'] == source_id and s.get('vision_run_id') == run['id']
               for s in project['sources'])


@pytest.mark.parametrize('kind', ['top_conflict', 'top_typed', 'nested_conflict', 'limited'])
def test_unsafe_vision_duplicates_stop_without_repair_or_fake_vision(tmp_path, monkeypatch, kind):
    run, project, source_id, requests, evidence, raw = _run_case(tmp_path, monkeypatch, kind)
    assert len(requests) == run['calls'] == len(run['attempts']) == len(evidence) == 1
    assert run['status'] == 'failed' and run['error'] == 'SEMANTIC_BLOCKED'
    assert run.get('result_applied') is not True and run['attempts'][0]['validation_result'] == 'SCHEMA_INVALID'
    assert evidence[0]['json_integrity_failure'] == 'duplicate_member'
    assert 'syntax_normalization' not in evidence[0]
    if kind == 'limited':
        assert 'final_output_truncated' in evidence[0]['evidence_limitations']
        assert evidence[0]['final_output'] != raw
    else:
        assert evidence[0]['final_output'] == raw
    assert any(s['id'] == source_id and s['parse_status'] == 'awaiting_vision'
               and not s.get('vision_run_id') for s in project['sources'])
    assert not any(m.get('stage') == 'vision' and m.get('role') == 'assistant'
                   for m in project['messages'])
