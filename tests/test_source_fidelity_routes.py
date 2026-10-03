"""Public adoption routes exercise isolated stores with no provider calls."""
import copy
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from tests.test_result_conditions import case,NORMAL,SOURCE

@pytest.fixture
def client_case(tmp_path):
    app=create_app(tmp_path,access_token='synthetic-source-fidelity',env_path=tmp_path/'absent.env')
    p=app.state.store.create('Synthetic source-fidelity route')
    old,v,ex=case(NORMAL);i=v['proposals'][0]
    i.update(id='REQ-fidelity',selection_status='candidate')
    with app.state.store.edit(p['id'],p['revision'],'isolated synthetic source fixture') as (p,_):
        p['sources']=[dict(old['sources'][0],title='Synthetic business source',purpose='goal',excluded=False,parse_status='read',failure_reason='',image_mime=None)]
        p['items']=[i]
        p['options']=[dict(id='OPT-fidelity',proposed_item_refs=[i['id']],title='Synthetic option',direction_status='candidate')]
    with TestClient(app) as client:
        csrf=client.post('/api/session/login',json={'key':'synthetic-source-fidelity'}).json()['csrf']
        client.headers.update({'Origin':'http://testserver','X-CSRF-Token':csrf})
        yield client,app,'/api/projects/'+p['id']

@pytest.mark.parametrize('route',['single','bulk'])
def test_public_unsupported_adoption_rejects_without_partial_write(client_case,route):
    c,a,root=client_case;p=c.get(root).json()
    with a.state.store.edit(p['id'],p['revision'],'isolated unsupported rule fixture') as (p,_):
        p['items'][0]['behavior']['permissions']='仅本租户管理员可见反馈。'
    before=copy.deepcopy(a.state.store.get(p['id']))
    if route=='single':r=c.post(root+'/items/REQ-fidelity',json=dict(expected_revision=before['revision'],selection_status='selected'))
    else:r=c.post(root+'/options/OPT-fidelity/items',json=dict(expected_revision=before['revision'],item_ids=['REQ-fidelity']))
    assert r.status_code==409 and r.json()['code']=='SEMANTIC_BLOCKED',r.text
    assert a.state.store.get(p['id'])==before
    assert not a.state.store.records(p['id'],'run')

def test_public_title_edit_is_validated_before_selection(client_case):
    c,a,root=client_case;p=c.get(root).json();before=copy.deepcopy(a.state.store.get(p['id']))
    r=c.post(root+'/items/REQ-fidelity',json=dict(expected_revision=p['revision'],selection_status='selected',title='预算不足时的申请反馈'))
    assert r.status_code==409 and r.json()['code']=='SEMANTIC_BLOCKED',r.text
    assert a.state.store.get(p['id'])==before

def test_public_explicit_manual_statement_preserves_new_decision_source(client_case):
    c,a,root=client_case;p=c.get(root).json()
    statement='资源申请反馈仅本租户管理员可见反馈；这是本期明确新增的权限规则。'
    r=c.post(root+'/items/REQ-fidelity',json=dict(expected_revision=p['revision'],selection_status='selected',statement=statement))
    assert r.status_code==200,r.text
    after=r.json();i=after['items'][0];source=after['sources'][-1]
    assert i['statement']==statement and i['selection_status']=='selected'
    assert source['id']!=p['sources'][0]['id'] and any(e['text']==statement for e in source['excerpts'])
    assert any(ref['source_id']==source['id'] for ref in i['source_refs'])
    assert not after.get('active_baseline_id')
