"""Manual acceptance is source-backed, linked, and never adopted implicitly."""
from fastapi.testclient import TestClient

from app.main import create_app
from app.product_flow import BEHAVIOR, INTAKE, SCOPE
from tests.helpers import prepared


def ready_without_acceptance(store):
    project = prepared(store)
    with store.edit(project['id'], project['revision'], '合成缺验收条件') as (draft, _):
        draft['items'] = [item for item in draft['items'] if item['kind'] != 'acceptance']
        draft['items'][0]['related_refs'] = ['RULE-0001']
        draft['items'][0]['behavior'] = {key: '已明确：' + label for key, label in BEHAVIOR.items()}
        draft['product_context'] = {key: '合成核对：' + label for key, label in {**INTAKE, **SCOPE}.items()}
        draft['product_context']['scope_ids'] = ['REQ-0001']
    return store.get(project['id'])


def test_manual_acceptance_candidate_requires_explicit_adoption(tmp_path):
    app = create_app(tmp_path / 'data', access_token='synthetic-login', env_path=tmp_path / 'absent.env')
    project = ready_without_acceptance(app.state.store)
    root = '/api/projects/' + project['id']
    with TestClient(app) as client:
        csrf = client.post('/api/session/login', json={'key': 'synthetic-login'}).json()['csrf']
        client.headers.update({'Origin': 'http://testserver', 'X-CSRF-Token': csrf})
        for step in (1, 2):
            project = client.get(root).json()
            response = client.post(root + '/stage-checks/' + str(step), json={
                'expected_revision': project['revision'],
                'expected_hash': project['product_flow'][step - 1]['content_hash'],
            })
            assert response.status_code == 200, response.text
        project = client.get(root).json()
        assert any('缺少已采纳的关联验收条件' in issue for issue in project['product_flow'][2]['missing'])
        prior_sources = len(project['sources'])
        body = {'expected_revision': project['revision'], 'title': '重叠输入验收',
                'statement': '管理员输入重叠时段并保存，应看到拒绝提示，原数据保持不变。'}
        response = client.post(root + '/items/REQ-0001/acceptance', json=body)
        assert response.status_code == 200, response.text
        project = client.get(root).json()
        candidates = [i for i in project['items'] if i['kind'] == 'acceptance']
        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate['selection_status'] == 'candidate'
        assert candidate['related_refs'] == ['REQ-0001']
        assert candidate['statement'] == body['statement']
        assert len(project['sources']) == prior_sources + 1
        source = next(s for s in project['sources'] if s['id'] == candidate['source_refs'][0]['source_id'])
        assert any(body['statement'] in excerpt['text'] for excerpt in source['excerpts'])
        assert any('缺少已采纳的关联验收条件' in issue for issue in project['product_flow'][2]['missing'])
        blocked = client.post(root + '/stage-checks/3', json={
            'expected_revision': project['revision'], 'expected_hash': project['product_flow'][2]['content_hash']})
        assert blocked.status_code == 409
        chosen = client.post(root + '/items/' + candidate['id'], json={
            'expected_revision': project['revision'], 'selection_status': 'selected'})
        assert chosen.status_code == 200, chosen.text
        project = client.get(root).json()
        assert not any('缺少已采纳的关联验收条件' in issue for issue in project['product_flow'][2]['missing'])
        checked = client.post(root + '/stage-checks/3', json={
            'expected_revision': project['revision'], 'expected_hash': project['product_flow'][2]['content_hash']})
        assert checked.status_code == 200, checked.text


def test_manual_acceptance_rejects_invalid_reference_and_preserves_old_candidate_api(tmp_path):
    app = create_app(tmp_path / 'data', access_token='synthetic-login', env_path=tmp_path / 'absent.env')
    project = ready_without_acceptance(app.state.store)
    root = '/api/projects/' + project['id']
    with TestClient(app) as client:
        csrf = client.post('/api/session/login', json={'key': 'synthetic-login'}).json()['csrf']
        client.headers.update({'Origin': 'http://testserver', 'X-CSRF-Token': csrf})
        body = {'expected_revision': project['revision'], 'title': '人工验收', 'statement': '明确的测试预期。'}
        assert client.post(root + '/items/RULE-0001/acceptance', json=body).status_code == 400
        assert client.post(root + '/items/REQ-NOT-FOUND/acceptance', json=body).status_code == 400
        assert client.post(root + '/items/REQ-0001/acceptance', json={**body, 'statement': '   '}).status_code == 400
        assert client.get(root).json()['revision'] == project['revision']
        old = client.post(root + '/items', json={**body, 'change_type': 'new'})
        assert old.status_code == 200, old.text
        latest = client.get(root).json()
        assert any(item['kind'] == 'requirement' and item['title'] == '人工验收' for item in latest['items'])
        assert client.post(root + '/items/REQ-0001/acceptance', json=body).status_code == 409
