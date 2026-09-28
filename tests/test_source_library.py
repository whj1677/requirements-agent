"""Source library purpose and original attachment routes (synthetic inputs only)."""
import copy

from fastapi.testclient import TestClient
from PIL import Image
import io

from app.sources import save_source
from tests.test_runtime import client


def _png_bytes():
    buffer = io.BytesIO()
    Image.new('RGB', (3, 2), (12, 34, 56)).save(buffer, format='PNG')
    return buffer.getvalue()


def _project_with_sources(tmp_path):
    c, app = client(tmp_path)
    project = c.post('/api/projects', json={'name': '合成材料库'}).json()
    parent = save_source(app.state.store, '合成需求说明.txt', '合成原文'.encode('utf-8'), 'current')
    parent['parse_status'] = 'partial'
    child = save_source(app.state.store, '合成需求说明 · 图片.png', _png_bytes(), 'current')
    child.update(container_source_id=parent['id'], container_hash=parent['sha256'], container_locator='图片1')
    with app.state.store.edit(project['id'], project['revision'], '合成父子材料') as (draft, _):
        draft['sources'].extend([parent, child])
    return c, app, project['id'], parent, child


def test_source_file_route_returns_exact_stored_bytes_as_attachment_and_is_scoped(tmp_path):
    c, app, pid, parent, child = _project_with_sources(tmp_path)
    root = f'/api/projects/{pid}/sources'
    for source in (parent, child):
        response = c.get(f'{root}/{source["id"]}/file')
        assert response.status_code == 200
        assert response.content == (tmp_path / 'sources' / source['id']).read_bytes()
        assert response.headers['content-type'] == 'application/octet-stream'
        assert response.headers['content-disposition'].startswith('attachment;')

    other = c.post('/api/projects', json={'name': '另一合成项目'}).json()
    assert c.get(f'/api/projects/{other["id"]}/sources/{parent["id"]}/file').status_code == 404
    assert c.get(f'/api/projects/{pid}/sources/unknown-source/file').status_code == 404

    anonymous = TestClient(app)
    denied = anonymous.get(f'{root}/{parent["id"]}/file')
    assert denied.status_code == 401


def test_source_purpose_updates_container_children_without_changing_evidence_or_items(tmp_path):
    c, app, pid, parent, child = _project_with_sources(tmp_path)
    root = f'/api/projects/{pid}'
    before = app.state.store.get(pid)
    old_sources = {row['id']: copy.deepcopy(row) for row in before['sources']}
    old_items = copy.deepcopy(before['items'])

    response = c.post(f'{root}/sources/{parent["id"]}/purpose',
                      json={'expected_revision': before['revision'], 'purpose': 'goal'})
    assert response.status_code == 200
    after = app.state.store.get(pid)
    assert {row['id']: row['purpose'] for row in after['sources']} == {
        parent['id']: 'goal', child['id']: 'goal'}
    assert after['items'] == old_items
    for row in after['sources']:
        old = old_sources[row['id']]
        for key in ('id', 'sha256', 'excerpts', 'container_source_id', 'container_hash'):
            assert row.get(key) == old.get(key)
    assert c.post(f'{root}/sources/{parent["id"]}/purpose',
                  json={'expected_revision': before['revision'], 'purpose': 'reference'}).status_code == 409
    assert c.post(f'{root}/sources/{parent["id"]}/purpose',
                  json={'expected_revision': after['revision'], 'purpose': 'invalid'}).status_code == 422

    # A child can be used independently, but only its own descendants (if any) follow it.
    response = c.post(f'{root}/sources/{child["id"]}/purpose',
                      json={'expected_revision': after['revision'], 'purpose': 'reference'})
    assert response.status_code == 200
    final = app.state.store.get(pid)
    assert next(row for row in final['sources'] if row['id'] == parent['id'])['purpose'] == 'goal'
    assert next(row for row in final['sources'] if row['id'] == child['id'])['purpose'] == 'reference'
