"""Current human review survives explicit empty values without becoming saved state."""
import copy
import pytest
from fastapi.testclient import TestClient
from app.core import digest, brief_hash, execution_hash
from app.main import create_app
from app.scope_context import FIELDS, context_review_statuses


def reviewed_empty():
    state={'status':'unknown','note':'本期暂不设置优先级'}
    return dict(product_context={'priority':''},context_field_states={'priority':state},
        product_context_proposal={'priority':'P1'},context_confirmations={'priority':dict(
            actor='authenticated_local_user',content_hash=digest(dict(text='',state=state)))})


@pytest.mark.parametrize('change',['none','text','status','note','actor','hash'])
def test_status_requires_exact_current_value_and_complete_state(change):
    p=reviewed_empty()
    if change=='text':p['product_context']['priority']='P2'
    if change=='status':p['context_field_states']['priority']['status']='known'
    if change=='note':p['context_field_states']['priority']['note']='已改说明'
    if change=='actor':p['context_confirmations']['priority']['actor']='model'
    if change=='hash':p['context_confirmations']['priority']['content_hash']='old'
    before=copy.deepcopy(p)
    result=context_review_statuses(p)
    assert set(result)==set(FIELDS) and all(type(v) is bool for v in result.values())
    assert result['priority'] is (change=='none')
    assert not any(result[field] for field in FIELDS if field!='priority')
    assert p==before


def test_get_projects_review_flags_are_response_only_and_keep_hashes(tmp_path):
    app=create_app(tmp_path,access_token='test-review',env_path=tmp_path/'absent.env')
    store=app.state.store;p=store.create('合成明确留空')
    with store.edit(p['id'],p['revision'],'合成人工留空') as (p,_):p.update(reviewed_empty())
    before=store.get(p['id']);before_brief=brief_hash(before);before_execution=execution_hash(before)
    with TestClient(app) as client:
        client.post('/api/session/login',json={'key':'test-review'})
        response=client.get('/api/projects/'+p['id'])
    assert response.status_code==200
    projected=response.json()
    assert projected['context_review_statuses']['priority'] is True
    assert projected['product_context']['priority']==''
    assert projected['product_context_proposal']['priority']=='P1'
    assert brief_hash(projected)==before_brief
    assert store.get(p['id'])==before and 'context_review_statuses' not in before
    assert execution_hash(store.get(p['id']))==before_execution
