import copy

from fastapi.testclient import TestClient

from app.core import ui_view_hash
from app.main import create_app
from app.workflow import apply_response
from tests.helpers import prepared


def response(spec):
    return dict(stage='ui',result={'spec':spec},proposals=[],questions=[],summary='合成页面候选',limitations=[])


def test_layout_candidate_requires_real_diff_and_service_activation(tmp_path):
    app=create_app(tmp_path,access_token='synthetic-login',env_path=tmp_path/'.env')
    store=app.state.store
    p=prepared(store)
    base_spec=copy.deepcopy(p['ui']['spec'])
    prose_only=copy.deepcopy(base_spec)
    prose_only['design_intent']='改写说明，不改变画面或交互'
    with store.edit(p['id'],p['revision'],'合成无差异候选',bump=False) as (state,_):
        apply_response(state,response(prose_only))
        assert not state.get('ui_candidates')
    p=store.get(p['id'])
    next_spec=copy.deepcopy(base_spec)
    next_spec['pages'][0]['regions'][0]['components'][0]['label']='调整后的列表布局'
    assert ui_view_hash(next_spec)!=ui_view_hash(base_spec)
    with store.edit(p['id'],p['revision'],'合成布局候选',bump=False) as (state,_):
        apply_response(state,response(next_spec))
    p=store.get(p['id'])
    assert p['ui']['spec']==base_spec
    candidate=p['ui_candidates'][0]
    with TestClient(app) as client:
        csrf=client.post('/api/session/login',json={'key':'synthetic-login'}).json()['csrf']
        client.headers.update({'Origin':'http://testserver','X-CSRF-Token':csrf})
        root='/api/projects/'+p['id']
        assert client.get(root+'/ui-candidates/'+candidate['id']+'/prototype').status_code==200
        assert client.post(root+'/ui-candidates/'+candidate['id']+'/activate',json={'expected_revision':p['revision']+1}).status_code==409
        assert client.post(root+'/ui-candidates/'+candidate['id']+'/activate',json={'expected_revision':p['revision']}).status_code==200
        after=client.get(root).json()
        assert after['revision']==p['revision']+1
        assert after['ui']['spec']==next_spec
        assert after['document_update_needed'] is True
        assert after['review'] is None
        assert after['documents']['prd']['content']==p['documents']['prd']['content']
        assert client.post(root+'/ui-candidates/'+candidate['id']+'/activate',json={'expected_revision':after['revision']}).status_code==404
        rejected_spec=copy.deepcopy(next_spec)
        rejected_spec['pages'][0]['regions'][0]['components'][0]['label']='未采纳的第三版布局'
        with store.edit(p['id'],after['revision'],'合成拒绝候选',bump=False) as (state,_):
            apply_response(state,response(rejected_spec))
        reject_id=store.get(p['id'])['ui_candidates'][-1]['id']
        assert client.post(root+'/ui-candidates/'+reject_id+'/reject',json={'expected_revision':after['revision']}).status_code==200
        after_reject=client.get(root).json()
        assert after_reject['revision']==after['revision']
        assert after_reject['ui']['spec']==next_spec
        assert after_reject['ui_candidates'][-1]['status']=='rejected'
        original=next(i for i in after['documents']['prd']['item_snapshot'] if i['id']=='REQ-0001')['statement']
        assert client.post(root+'/items/REQ-0001',json={'expected_revision':after['revision'],'selection_status':'selected','statement':'新版合成需求内容'}).status_code==200
        updated=client.get(root).json()
        assert next(i for i in updated['items'] if i['id']=='REQ-0001')['statement']=='新版合成需求内容'
        assert next(i for i in updated['documents']['prd']['item_snapshot'] if i['id']=='REQ-0001')['statement']==original
