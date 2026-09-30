"""Offline behavior checks for authoritative project summary projections."""
import copy

from fastapi.testclient import TestClient

from app.core import hashes
from app.main import create_app
from app.product_flow import current_stage, status
from tests.helpers import prepared
from tests.product_flow_helpers import check_prepared


def client(tmp_path):
    app = create_app(tmp_path, access_token='offline-summary-test', env_path=tmp_path / '.env')
    browser = TestClient(app)
    browser.headers['Origin'] = 'http://testserver'
    browser.headers['X-CSRF-Token'] = browser.post('/api/session/login',
        json={'key': 'offline-summary-test'}).json()['csrf']
    return browser, app


def assert_project_summary(browser, app, project):
    before = copy.deepcopy(app.state.store.get(project['id']))
    with app.state.store.connect() as db:
        payload_before = db.execute('SELECT payload FROM projects WHERE id=?', (project['id'],)).fetchone()[0]
        updated_at = db.execute('SELECT MAX(created) FROM revisions WHERE project_id=?',
                                 (project['id'],)).fetchone()[0]
    listing = browser.get('/api/projects').json()
    listed = next(row for row in listing if row['id'] == project['id'])
    response = browser.get('/api/projects/' + project['id'])
    assert response.status_code == 200, response.text
    projected = response.json()
    expected_stage = current_stage(before)
    assert listed['flow_current_title'] == expected_stage['title']
    assert projected['flow_current_title'] == listed['flow_current_title']
    assert projected['updated_at'] == listed['updated_at'] == updated_at
    assert projected['revision'] == listed['revision'] == before['revision']
    assert app.state.store.get(project['id']) == before
    with app.state.store.connect() as db:
        assert db.execute('SELECT payload FROM projects WHERE id=?', (project['id'],)).fetchone()[0] == payload_before
    return listed, projected


def test_saved_intake_stays_at_stage_one_until_analysis_then_advances(tmp_path):
    browser, app = client(tmp_path)
    project = browser.post('/api/projects', json={'name': '仅保存诉求的合成项目'}).json()
    saved = browser.post('/api/projects/' + project['id'] + '/intake', json={
        'expected_revision': project['revision'], 'text': '保存但尚未分析的合成诉求。',
        'platform': '', 'preserved': '',
    })
    assert saved.status_code == 200, saved.text
    project = app.state.store.get(project['id'])
    assert project['stage_checks']['1']['meaning'] == '输入已保存，尚非分析结论核对'
    assert not any(m.get('role') == 'assistant' and m.get('stage') == 'ingest' for m in project['messages'])
    assert not project['items'] and not project['questions']
    listed, projected = assert_project_summary(browser, app, project)
    assert listed['flow_current_title'] == '提供现状与诉求'
    assert projected['product_flow'][0]['complete'] is True

    with app.state.store.edit(project['id'], project['revision'], '合成分析结果') as (draft, _):
        draft['messages'].append({'role': 'assistant', 'stage': 'ingest', 'text': '合成分析结果'})
    analyzed = app.state.store.get(project['id'])
    listed, _ = assert_project_summary(browser, app, analyzed)
    assert listed['flow_current_title'] == '核对现状、价值与改动范围'


def test_stale_prior_checkpoint_remains_the_current_business_stage(tmp_path):
    browser, app = client(tmp_path)
    project = prepared(app.state.store)
    project = check_prepared(app.state.store, project['id'], through=2)
    with app.state.store.edit(project['id'], project['revision'], '使前序诉求核对过期') as (draft, _):
        draft['product_context']['intent'] = '发生变化，前序核对应失效'
    changed = app.state.store.get(project['id'])
    assert status(changed)[0]['complete'] is False
    listed, _ = assert_project_summary(browser, app, changed)
    assert listed['flow_current_title'] == '提供现状与诉求'


def test_confirmed_project_summary_stays_at_handoff_and_get_is_read_only(tmp_path):
    browser, app = client(tmp_path)
    project = prepared(app.state.store)
    project = check_prepared(app.state.store, project['id'], through=5)
    current = browser.get('/api/projects/' + project['id']).json()
    response = browser.post('/api/projects/' + project['id'] + '/confirmations', json={
        'expected_revision': current['revision'], 'expected_hashes': current['hashes'],
        'scope_ids': [item['id'] for item in current['items'] if item['selection_status'] == 'selected'],
        'idempotency_key': 'summary-confirm-test',
    })
    assert response.status_code == 200, response.text
    confirmed = app.state.store.get(project['id'])
    assert status(confirmed)[5]['complete'] is True
    listed, projected = assert_project_summary(browser, app, confirmed)
    assert listed['flow_current_title'] == '确认与交接'
    assert projected['product_flow'][5]['complete'] is True
