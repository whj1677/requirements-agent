"""GET projections must not alter the canonical document or review hashes."""

import copy

from fastapi.testclient import TestClient

from app.contracts import review_target
from app.main import create_app
from app.product_flow import document_review_current, status
from tests.helpers import prepared
from tests.product_flow_helpers import check_prepared


def client(tmp_path):
    app = create_app(tmp_path, access_token='offline-projection-test', env_path=tmp_path / '.env')
    browser = TestClient(app)
    browser.headers['Origin'] = 'http://testserver'
    browser.headers['X-CSRF-Token'] = browser.post('/api/session/login',
        json={'key':'offline-projection-test'}).json()['csrf']
    return browser, app


def assert_read_only_projection(browser, store, pid):
    canonical = store.get(pid)
    before = copy.deepcopy(canonical)
    with store.connect() as db:
        payload_before = db.execute('SELECT payload FROM projects WHERE id=?', (pid,)).fetchone()[0]
    response = browser.get('/api/projects/' + pid)
    assert response.status_code == 200, response.text
    projected = response.json()
    assert projected['review_status'] == {
        'available': bool(canonical['review']),
        'current': bool(canonical['review'] and canonical['review']['target_hash'] == review_target(canonical)),
    }
    assert projected['product_flow'] == status(canonical)
    for kind in ('mrd', 'prd'):
        assert projected['document_review_status'][kind]['reviewed'] == document_review_current(canonical, kind)
        assert 'reader' in projected['documents'][kind]
        assert 'reader' not in canonical['documents'][kind]
    assert store.get(pid) == before
    with store.connect() as db:
        assert db.execute('SELECT payload FROM projects WHERE id=?', (pid,)).fetchone()[0] == payload_before
    return projected


def test_fresh_and_stale_review_status_survive_project_get(tmp_path):
    browser, app = client(tmp_path)
    project = prepared(app.state.store)
    project = check_prepared(app.state.store, project['id'], through=5)
    fresh = assert_read_only_projection(browser, app.state.store, project['id'])
    assert fresh['review_status']['current'] is True
    assert all(value['reviewed'] for value in fresh['document_review_status'].values())
    assert fresh['product_flow'][4]['complete'] is True

    with app.state.store.edit(project['id'], project['revision'], '合成过期状态') as (draft, _):
        draft['review']['target_hash'] = 'stale-target'
        draft['documents']['prd']['id'] = 'DOC-NEW'
    stale = assert_read_only_projection(browser, app.state.store, project['id'])
    assert stale['review_status']['current'] is False
    assert stale['document_review_status']['mrd']['reviewed'] is True
    assert stale['document_review_status']['prd']['reviewed'] is False
    assert stale['product_flow'][4]['complete'] is False
