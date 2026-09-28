"""No application spending quotas; source/cancel/provider limits still apply."""
import asyncio
import copy

import httpx
import pytest

from app.budgets import check_context, input_allowance
from app.core import Problem
from app.provider import DEFAULT, Provider
from tests.test_ui02_actions import case, finished, image, plan, start


def test_old_quota_configuration_and_old_clients_cannot_pause_new_tasks(case):
    client, app, model, root = case
    legacy = dict(model['config'], budget_mode='fixed', max_calls=1, context_chars=10000, action_seconds=5)
    for slot in ('model', 'vision'):
        # Simulate an existing installation without mutating its stored settings.
        app.state.store.setting(slot, legacy)
    assert all(c['budget_mode'] == 'unlimited' and c['max_calls'] is None for c in client.get('/api/models').json().values())
    for _ in range(25):
        image(client, root)
    body, preview = plan(client, root, max_calls=1)
    assert preview['max_calls'] is None and len(preview['stages']) == 10
    assert all(p['mode'] == 'unlimited' and p['input_allowance'] is None for p in preview['budget_policies'].values())
    assert start(client, root, body, preview).status_code == 200
    task = finished(client, root)
    assert task['status'] in ('succeeded', 'partial') and task['completed_steps'] == 10, task
    assert task['calls'] == len(model['requests']) == 10 and task['max_calls'] is None
    assert app.state.store.setting('vision') == legacy


def test_unlimited_preflight_does_not_trim_or_reject_large_text():
    text = '明确业务原文。' * 200000
    messages = [{'role': 'user', 'content': text}]
    original = copy.deepcopy(messages)
    config = dict(DEFAULT, max_calls=1, context_chars=1, action_seconds=1)
    assert input_allowance(config) is None
    check_context(messages, config)
    assert messages == original


def test_provider_physical_context_failure_is_visible_without_echoing_input(monkeypatch):
    provider = Provider()
    monkeypatch.setattr(provider, 'key', lambda c: 'synthetic-key')
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(400, json={
        'error': {'code': 'context_length_exceeded', 'message': 'maximum context length; PRIVATE_INPUT'}}))
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real_client(transport=transport, **kwargs))
    with pytest.raises(Problem) as caught:
        asyncio.run(provider.request(DEFAULT, [{'role': 'user', 'content': 'synthetic'}]))
    assert caught.value.code == 'MODEL_CONTEXT_LIMIT'
    assert '分段' in str(caught.value) and 'PRIVATE_INPUT' not in str(caught.value)
