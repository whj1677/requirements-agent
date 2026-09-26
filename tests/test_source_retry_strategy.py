"""Synthetic HTTP extractions; no public-site requests or daily data access."""
from unittest.mock import AsyncMock
from tests.test_runtime import client


def test_dynamic_retry_keeps_strategy_partial_status_and_prior_snapshot(tmp_path, monkeypatch):
    fetch = AsyncMock(return_value=(b'<html>synthetic</html>', '合成网页正文', 'controlled-browser;partial-resources', 'https://example.com/material'))
    monkeypatch.setattr('app.main.webpage', fetch)
    c, app = client(tmp_path)
    with c:
        p = c.post('/api/projects', json={'name': '动态重读合成项目'}).json()
        base = '/api/projects/' + p['id']
        r = c.post(base + '/sources/url', json=dict(expected_revision=0,url='https://example.com/material',dynamic=True,authorized_public=True))
        assert r.status_code == 200, r.text
        old = r.json()
        assert old['dynamic'] is True and old['parse_status'] == 'partial'
        r = c.post(base + '/sources/' + old['id'] + '/retry', json={'expected_revision': 1})
        assert r.status_code == 200, r.text
        new = r.json()
        assert new['dynamic'] is True and new['parse_status'] == 'partial'
        assert new['parent_source_id'] == old['id'] and new['version'] == 2
        assert fetch.await_args.args == ('https://example.com/material', True)
        assert app.state.store.get(p['id'])['sources'][0] == old


def test_failed_dynamic_source_retains_strategy_for_retry(tmp_path, monkeypatch):
    fetch = AsyncMock(side_effect=RuntimeError('synthetic failure'))
    monkeypatch.setattr('app.main.webpage', fetch)
    c, app = client(tmp_path)
    with c:
        p = c.post('/api/projects', json={'name': '失败后重读'}).json()
        base = '/api/projects/' + p['id']
        r = c.post(base + '/sources/url', json=dict(expected_revision=0,url='https://example.com/material',dynamic=True,authorized_public=True))
        old = r.json()
        assert old['dynamic'] is True and old['parse_status'] == 'failed'
        fetch.side_effect = None
        fetch.return_value = (b'<html>synthetic</html>', '合成重新读取正文', 'controlled-browser', old['uri'])
        r = c.post(base + '/sources/' + old['id'] + '/retry', json={'expected_revision': 1})
        assert r.status_code == 200 and r.json()['parse_status'] == 'read'
        assert fetch.await_args.args[1] is True


def test_legacy_unknown_strategy_cannot_silently_downgrade(tmp_path, monkeypatch):
    fetch = AsyncMock(side_effect=RuntimeError('synthetic failure'))
    monkeypatch.setattr('app.main.webpage', fetch)
    c, app = client(tmp_path)
    with c:
        p = c.post('/api/projects', json={'name': '旧失败来源'}).json()
        base = '/api/projects/' + p['id']
        old = c.post(base + '/sources/url', json=dict(expected_revision=0,url='https://example.com/material',dynamic=True,authorized_public=True)).json()
        with app.state.store.edit(p['id'], 1, 'synthetic legacy record') as (draft, _):
            draft['sources'][0].pop('dynamic')
        fetch.reset_mock()
        r = c.post(base + '/sources/' + old['id'] + '/retry', json={'expected_revision': 2})
        assert r.status_code != 200 and '读取策略' in r.json()['message']
        fetch.assert_not_awaited()
        assert len(app.state.store.get(p['id'])['sources']) == 1
