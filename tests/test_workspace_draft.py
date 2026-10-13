"""Real application routes for working drafts, distinct from formal approval."""
import copy

from fastapi.testclient import TestClient

from app.core import execution_hash
from app.domain_context import active_source_ids, update
from app.main import create_app
from app.product_flow import execution_issues
from app.provider import DEFAULT, assemble
from app.store import Store
from app.workspace_draft import discussion


def client_project(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    csrf = client.get('/api/session').json()['csrf']
    headers = {'origin': 'http://testserver', 'x-csrf-token': csrf}
    p = app.state.store.create('合成储能云平台')
    return client, app.state.store, headers, p


def test_direct_draft_can_be_saved_before_interview_but_not_formally_confirmed(tmp_path):
    client, store, headers, p = client_project(tmp_path)
    root = '/api/projects/' + p['id']
    response = client.post(root+'/workspace-draft', headers=headers,
        json=dict(expected_revision=0, text='只修改日报标题；其它待确定。', entry_mode='direct'))
    assert response.status_code == 200, response.text
    current = store.get(p['id'])
    assert current['workflow_mode']=='shared-draft'
    assert client.get(root+'/workspace-draft').json()['text']=='只修改日报标题；其它待确定。'
    assert execution_issues(current,'clarify') == []
    projected = client.get(root).json()
    assert projected['confirmation_issues']
    assert not current['active_baseline_id']
    exported = client.get(root+'/discussion-draft').json()
    assert exported['formal'] is False and '非正式' in exported['text']
    assert '只修改日报标题；其它待确定。' in exported['text']
    blocked = client.post(root+'/confirmations', headers=headers, json=dict(
        expected_revision=current['revision'], expected_hashes=projected['hashes'],
        scope_ids=projected['confirmation_scope_ids'], idempotency_key='draft-not-final'))
    assert blocked.status_code == 400 and blocked.json()['code']=='SEMANTIC_BLOCKED'
    assert not store.get(p['id'])['active_baseline_id']


def test_stale_save_keeps_current_text_and_history(tmp_path):
    client, store, headers, p = client_project(tmp_path)
    path='/api/projects/'+p['id']+'/workspace-draft'
    for revision,text in [(0,'第一版'),(1,'新改动')]:
        saved=client.post(path,headers=headers,json=dict(expected_revision=revision,text=text,entry_mode='chat'))
        assert saved.status_code==200,saved.text
    snapshot=store.get(p['id'])
    stale=client.post(path,headers=headers,json=dict(expected_revision=1,text='冲突文本',entry_mode='direct'))
    assert stale.status_code==409
    assert store.get(p['id'])==snapshot
    assert len(snapshot['sources'])==2 and snapshot['sources'][0]['excluded']
    assert snapshot['sources'][0]['superseded_by']==snapshot['sources'][1]['id']
    assert client.get(path).json()['text']=='新改动'


def test_selected_references_keep_current_draft_and_change_execution_identity(tmp_path):
    client, store, headers, p = client_project(tmp_path)
    path='/api/projects/'+p['id']+'/workspace-draft'
    assert client.post(path,headers=headers,json=dict(expected_revision=0,text='本次只改标题',entry_mode='direct')).status_code==200
    p=store.get(p['id'])
    old_hash=execution_hash(p)
    update(p,background='大型储能集成商云平台',selected_source_ids=[])
    assert execution_hash(p)!=old_hash
    sid=p['workspace_draft']['source_id']
    assert active_source_ids(p)=={sid}
    messages,excerpts,_=assemble(p,'ingest','',DEFAULT,store.folder)
    assert {e['source_id'] for e in excerpts}=={sid}
    assert '只辅助理解' in str(messages) and '本次只改标题' in str(messages)


def test_discussion_export_keeps_partial_decisions_visible(tmp_path):
    p=Store(tmp_path).create('合成日报')
    p['questions']=[dict(id='Q-version',question='日报使用哪个数据版本？',status='open',
                        answer=None,decision_coverage='pending',blocking=True)]
    raw=copy.deepcopy(p)
    text=discussion(p)
    assert '日报使用哪个数据版本？（仍待决定）' in text
    assert p==raw


def test_chat_source_projection_matches_selected_references_and_bound_draft(tmp_path):
    client, store, headers, p = client_project(tmp_path)
    root = '/api/projects/' + p['id']
    assert client.post(root+'/workspace-draft', headers=headers, json=dict(
        expected_revision=p['revision'], text='只讨论日报标题', entry_mode='chat')).status_code == 200
    p = store.get(p['id'])
    with store.edit(p['id'], p['revision'], '合成被取消选择的资料') as (current, _):
        extra = copy.deepcopy(current['sources'][0])
        extra['id'] = 'SRC-not-selected'
        current['sources'].append(extra)
        update(current, selected_source_ids=[])
    before = store.get(p['id'])
    projected = client.get(root).json()
    context = client.get(root+'/domain-context').json()
    expected = [before['workspace_draft']['source_id']]
    assert projected['active_source_ids'] == context['effective_source_ids'] == expected
    assert store.get(p['id']) == before
