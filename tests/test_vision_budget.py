"""Image transport must not consume the text/repair allowance as base64 prose."""
import copy
import io
import random

import pytest
from PIL import Image

from app.budgets import check_context, stage_config
from app.core import Problem, dumps
from app.provider import DEFAULT
from tests.test_ui02_actions import case, finished, plan, start


def messages(payload='A' * 1200000):
    return [{'role': 'user', 'content': [
        {'type': 'text', 'text': '保留原文与未知。'},
        {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + payload}},
    ]}]


def test_image_transport_is_not_text_and_request_is_never_modified():
    request = messages()
    original = copy.deepcopy(request)
    config = stage_config(dict(DEFAULT, budget_mode='fixed'), 'vision')
    check_context(request, config)
    assert request == original
    assert config['max_tokens'] == DEFAULT['max_tokens']


def test_repair_and_ordinary_text_still_hit_the_capacity_guard():
    request = messages('AA==')
    request.append({'role': 'assistant', 'content': 'x' * 400000})
    with pytest.raises(Problem) as error:
        check_context(request, stage_config(dict(DEFAULT, budget_mode='fixed'), 'vision'))
    assert error.value.code == 'BUDGET_EXHAUSTED'
    assert '切换功能优先' not in str(error.value)
    with pytest.raises(Problem):
        check_context([{'role': 'user', 'content': dumps(messages())}], stage_config(dict(DEFAULT, budget_mode='fixed'), 'vision'))


def test_large_image_batches_reach_provider_then_ingest(case):
    client, app, model, root = case
    # Deterministic incompressible synthetic PNG: a realistic screenshot-sized
    # transport, unlike the tiny solid-colour fixtures that hid this incident.
    pixels = random.Random(23).randbytes(480 * 320 * 3)
    out = io.BytesIO()
    Image.frombytes('RGB', (480, 320), pixels).save(out, format='PNG')
    for n in range(4):
        response = client.post(root + '/sources/file',
            data={'expected_revision': client.get(root).json()['revision'], 'purpose': 'goal'},
            files={'file': (f'synthetic-{n}.png', out.getvalue(), 'image/png')})
        assert response.status_code == 200
    body, preview = plan(client, root, max_calls=8)
    assert preview['stages'] == ['vision', 'vision', 'ingest']
    assert start(client, root, body, preview).status_code == 200
    task = finished(client, root)
    # Earlier vision batches deliberately report omitted later images; preserve
    # that partial status, but every step and actual analysis must complete.
    assert task['status'] == 'partial' and task['completed_steps'] == 3, task
    assert not task.get('error')
    assert any(m['role'] == 'assistant' and m['stage'] == 'ingest'
               for m in client.get(root).json()['messages'])
    assert task['calls'] == 3 and len(model['requests']) == 3
    image_counts = [sum(c['type'] == 'image_url' for c in r['messages'][1]['content']) for r in model['requests']]
    assert image_counts == [3, 1, 0]
    assert all(s.get('vision_run_id') for s in client.get(root).json()['sources'] if s.get('image_mime'))
