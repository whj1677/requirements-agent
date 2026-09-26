import asyncio
import copy

import pytest
from fastapi.testclient import TestClient

from app.contracts import gate
from app.core import Problem, brief_hash, digest, ui_view_hash
from app.exports import document_files
from app.main import create_app
from app.workflow import apply_response
from tests.helpers import prepared


def test_same_revision_document_artifacts_have_distinct_snapshots(tmp_path):
    app = create_app(tmp_path, access_token='synthetic-login', env_path=tmp_path/'.env')
    store = app.state.store
    p = prepared(store)
    original = copy.deepcopy(p['documents']['prd'])
    original['id'] = 'DOC-A'
    original['content']['title'] = 'PRD A 专属正文'
    updated = copy.deepcopy(original)
    updated['id'] = 'DOC-B'
    updated['content']['title'] = 'PRD B 专属正文'
    with store.edit(p['id'], p['revision'], '同底稿两份文档', bump=False) as (state, db):
        store.record(p['id'], 'document_artifact', original, db=db)
        store.record(p['id'], 'document_artifact', updated, db=db)
        state['documents']['prd'] = updated
    assert original['draft_revision'] == updated['draft_revision']
    with TestClient(app) as client:
        csrf = client.post('/api/session/login', json={'key':'synthetic-login'}).json()['csrf']
        client.headers.update({'Origin':'http://testserver', 'X-CSRF-Token':csrf})
        artifacts = client.get('/api/projects/'+p['id']+'/artifacts').json()['document_artifact']
        assert {d['content']['title'] for d in artifacts} >= {'PRD A 专属正文', 'PRD B 专属正文'}
        current = client.get('/api/projects/'+p['id']).json()['documents']['prd']
        assert current['id'] == 'DOC-B'
        assert current['content']['title'] == 'PRD B 专属正文'


def test_ui_candidate_makes_old_document_export_unavailable(tmp_path):
    app = create_app(tmp_path, access_token='synthetic-login', env_path=tmp_path/'.env')
    store = app.state.store
    p = prepared(store)
    next_spec = copy.deepcopy(p['ui']['spec'])
    next_spec['pages'][0]['regions'][0]['components'][0]['label'] = '新版页面布局'
    with store.edit(p['id'], p['revision'], '合成页面候选', bump=False) as (state, _):
        apply_response(state, dict(stage='ui', result={'spec':next_spec}, proposals=[], questions=[], summary='合成候选', limitations=[]))
    candidate = store.get(p['id'])['ui_candidates'][0]
    with TestClient(app) as client:
        csrf = client.post('/api/session/login', json={'key':'synthetic-login'}).json()['csrf']
        client.headers.update({'Origin':'http://testserver', 'X-CSRF-Token':csrf})
        root = '/api/projects/'+p['id']
        assert client.post(root+'/ui-candidates/'+candidate['id']+'/activate', json={'expected_revision':p['revision']}).status_code == 200
        state = client.get(root).json()
        assert state['documents']['prd']['brief_hash'] == brief_hash(state)
        assert state['document_update_needed']
        assert client.get(root+'/documents/prd/md').status_code == 409
        with pytest.raises(Problem) as error:
            asyncio.run(document_files(state, 'prd'))
        assert error.value.status == 409


def test_same_view_regeneration_rebinds_current_brief_without_visual_version(tmp_path):
    app = create_app(tmp_path, access_token='synthetic-login', env_path=tmp_path/'.env')
    store = app.state.store
    p = prepared(store)
    old_spec = copy.deepcopy(p['ui']['spec'])
    old_hash = digest(old_spec)
    with store.edit(p['id'], p['revision'], '底稿内容改变') as (state, _):
        state['items'][0]['statement'] = '管理员能够维护电价时段并查看保存反馈。'
    p = store.get(p['id'])
    assert p['ui']['brief_hash'] != brief_hash(p)
    generated = copy.deepcopy(old_spec)
    generated['draft_revision'] = p['revision']
    assert ui_view_hash(generated) == ui_view_hash(old_spec)
    with store.edit(p['id'], p['revision'], '相同页面重新生成', bump=False) as (state, _):
        apply_response(state, dict(stage='ui', result={'spec':generated}, proposals=[], questions=[], summary='新底稿核验同页', limitations=[]))
    current = store.get(p['id'])
    assert not current.get('ui_candidates')
    assert current['ui']['brief_hash'] == brief_hash(current)
    assert digest(current['ui']['spec']) == old_hash
    assert current['ui']['verification']['generated_spec_hash'] == digest(generated)
    assert not any('线框对应旧底稿' in issue for issue in gate(current))
