import io
import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import create_app
from app.store import Store
from tests.ui02_fixture import model_server


@pytest.fixture
def case(tmp_path):
    with model_server() as model:
        app=create_app(tmp_path,access_token='ui02-login',env_path=tmp_path/'absent.env')
        with TestClient(app) as client:
            csrf=client.post('/api/session/login',json={'key':'ui02-login'}).json()['csrf']
            client.headers.update({'Origin':'http://testserver','X-CSRF-Token':csrf})
            for slot in ('model','vision'):
                assert client.put('/api/models/'+slot,json=model['config']).status_code==200
                assert client.put('/api/models/'+slot+'/key',json={'key':'ui02-synthetic-key'}).status_code==200
            p=client.post('/api/projects',json={'name':'UI02 隔离测试'}).json()
            yield client,app,model,'/api/projects/'+p['id']


def plan(c,root,action='organize',message='维护联系人列表，只读角色不能修改。',max_calls=8,**kwargs):
    p=c.get(root).json()
    body=dict(expected_revision=p['revision'],action=action,message=message,document_type='prd',max_calls=max_calls,option_id=None,**kwargs)
    response=c.post(root+'/actions/plan',json=body)
    assert response.status_code==200,response.text
    return body,response.json()


def start(c,root,body,preview,authorize=True,key=None):
    return c.post(root+'/actions',json=dict(**body,plan_hash=preview['plan_hash'],idempotency_key=key or uuid.uuid4().hex,authorize=authorize))


def finished(c,root):
    for _ in range(250):
        task=c.get(root+'/actions').json()[-1]
        if task['status'] not in ('queued','running'):return task
        time.sleep(.02)
    raise AssertionError('user task did not terminate')


def image(c,root):
    data=io.BytesIO();Image.new('RGB',(30,30),'white').save(data,format='PNG')
    assert c.post(root+'/sources/file',data={'expected_revision':c.get(root).json()['revision'],'purpose':'goal'},files={'file':('synthetic.png',data.getvalue(),'image/png')}).status_code==200


def test_authorization_budget_idempotency_and_vision_context(case):
    c,app,model,root=case
    image(c,root)
    body,preview=plan(c,root,max_calls=3)
    assert preview['stages']==['vision','ingest'] and not model['requests']
    assert start(c,root,body,preview,False).status_code==403 and not model['requests']
    model['invalid_next']=True
    key=uuid.uuid4().hex
    response=start(c,root,body,preview,key=key)
    assert response.status_code==200,response.text
    duplicate=start(c,root,body,preview,key=key)
    assert duplicate.json()['id']==response.json()['id']
    task=finished(c,root)
    assert task['status']=='succeeded' and task['calls']==3 and len(model['requests'])==3
    assert len(task['run_ids'])==2
    ctx=json.loads(model['requests'][-1]['messages'][1]['content'][0]['text'])
    assert ctx['vision_observations'][0]['result']['observations'][0]['observation']=='合成图中可见列表和新增按钮'
    assert any(s['title']=='对话输入.txt' for s in c.get(root).json()['sources'])
    from tests.product_flow_helpers import http_check
    http_check(c,root,through=1)
    body,preview=plan(c,root,'explore','')
    assert not any(r['needs_authorization'] for r in preview['recipients'])
    assert start(c,root,body,preview,False).status_code==200
    assert finished(c,root)['status']=='succeeded'


def test_total_budget_does_not_reset_for_next_stage(case):
    c,app,model,root=case
    image(c,root)
    body,preview=plan(c,root,max_calls=1)
    assert start(c,root,body,preview).status_code==200
    task=finished(c,root)
    assert task['status']=='paused_budget' and task['calls']==1 and len(model['requests'])==1
    assert len(task['run_ids'])==1
    assert c.get(root).json()['sources'][0]['parse_status']=='read'


def test_image_batches_share_budget_and_do_not_resend_previous_batch(case):
    c,app,model,root=case
    for _ in range(4):image(c,root)
    body,preview=plan(c,root,max_calls=2)
    assert preview['stages']==['vision','vision','ingest']
    assert start(c,root,body,preview).status_code==200
    task=finished(c,root)
    assert task['status']=='paused_budget' and task['calls']==2 and len(model['requests'])==2
    contexts=[json.loads(r['messages'][1]['content'][0]['text']) for r in model['requests']]
    batches=[{e['source_id'] for e in ctx['excerpts']} for ctx in contexts]
    # The saved task message is text; compare only image identities.
    image_ids={s['id'] for s in c.get(root).json()['sources'] if s.get('image_mime')}
    batches=[b & image_ids for b in batches]
    assert len(batches[0])==3 and len(batches[1])==1 and not batches[0]&batches[1]
    assert all(s.get('vision_run_id') for s in c.get(root).json()['sources'] if s.get('image_mime'))


def test_cancel_external_change_and_restart_do_not_replay(case):
    c,app,model,root=case
    image(c,root);model['delay']=.15
    body,preview=plan(c,root)
    response=start(c,root,body,preview).json()
    time.sleep(.04)
    assert c.post(root+'/actions/'+response['id']+'/cancel').status_code==200
    time.sleep(.3)
    assert len(model['requests'])<=1 and c.get(root).json()['items']==[]
    assert c.get(root).json()['sources'][0]['parse_status']=='awaiting_vision'
    before=len(model['requests'])
    Store(app.state.store.folder)
    assert len(model['requests'])==before


def test_external_edit_stops_pipeline_and_keeps_call_evidence(case):
    c,app,model,root=case
    image(c,root);model['delay']=.2
    body,preview=plan(c,root)
    assert start(c,root,body,preview).status_code==200
    time.sleep(.06)
    p=c.get(root).json()
    assert c.patch(root,json={'expected_revision':p['revision'],'name':'外部修改'}).status_code==200
    task=finished(c,root)
    assert task['error']=='STALE_REVISION' and task['calls']==1
    assert c.get(root).json()['items']==[]
    assert list((app.state.store.folder/'evidence/model-calls').glob('*.json'))
    run=app.state.store.get_record(root.rsplit('/',1)[-1],task['run_ids'][0],'run')
    assert run['response']['stage']=='vision' and run['result_applied'] is False


@pytest.mark.parametrize('topic',['联系人','电价时段'])
def test_draft_with_unknowns_direct_document_and_option_identity(case,topic):
    c,app,model,root=case
    # Legacy candidate-only engine behavior remains supported below PRODUCT-01 navigation.
    # It is no longer an executable normal-workbench shortcut; see product_flow_http.
    from tests.product_flow_helpers import isolate_legacy_engine
    isolate_legacy_engine(app)
    body,preview=plan(c,root,message=f'维护{topic}列表，只读角色不能修改。')
    assert start(c,root,body,preview).status_code==200
    assert finished(c,root)['status']=='succeeded'
    for action in ('explore','document'):
        body,preview=plan(c,root,action,'')
        assert start(c,root,body,preview,False).status_code==200
        assert finished(c,root)['status']=='succeeded'
    p=c.get(root).json()
    assert p['documents']['prd']['content']['title']==p['name']
    readable='\n'.join(b['text'] for s in p['documents']['prd']['reader']['sections'] for b in s['blocks'])
    assert f'管理员维护{topic}列表。' in readable
    assert any(b['kind']=='open_question' for s in p['documents']['prd']['content']['sections'] for b in s['blocks'])
    assert all(i['selection_status']=='candidate' for i in p['items'])
    assert p['confirmation_issues'] and not p['active_baseline_id']
    before=p['documents']['prd']
    model['fail_next']=True
    body,preview=plan(c,root,'document','')
    assert start(c,root,body,preview,False).status_code==200
    assert finished(c,root)['status']=='failed'
    assert c.get(root).json()['documents']['prd']==before
    p=c.get(root).json();oid=p['options'][0]['id']
    body,preview=plan(c,root,'prototype','')
    body['option_id']=oid
    preview=c.post(root+'/actions/plan',json=body).json()
    assert preview['missing'] and start(c,root,body,preview,False).status_code==409
    assert c.post(root+'/options/'+oid+'/direction',json={'expected_revision':p['revision'],'direction_status':'selected'}).status_code==200
    body,preview=plan(c,root,'prototype','')
    assert start(c,root,body,preview,False).status_code==200
    assert finished(c,root)['status']=='succeeded'
    assert c.get(root).json()['ui']['generation_target']['option_id']==oid


def test_changed_plan_and_new_source_require_fresh_review(case):
    c,app,model,root=case
    body,preview=plan(c,root)
    changed=dict(model['config'],model='changed-synthetic-model')
    assert c.put('/api/models/model',json=changed).status_code==200
    assert start(c,root,body,preview).status_code==409 and not model['requests']
    body,preview=plan(c,root)
    assert start(c,root,body,preview).status_code==200
    finished(c,root)
    image(c,root)
    body,preview=plan(c,root,message='')
    assert any(r['needs_authorization'] for r in preview['recipients'])
    count=len(model['requests'])
    assert start(c,root,body,preview,False).status_code==403
    assert len(model['requests'])==count


def test_active_task_serialization_and_child_resume_cannot_reset_budget(case):
    c,app,model,root=case
    image(c,root);model['delay']=.2
    body,preview=plan(c,root,max_calls=1)
    assert start(c,root,body,preview).status_code==200
    p=c.get(root).json()
    legacy=c.post(root+'/runs',json=dict(expected_revision=p['revision'],stage='ingest',message='',document_type='prd'))
    assert legacy.status_code==409
    task=finished(c,root)
    assert task['status']=='paused_budget' and len(model['requests'])==1
    assert c.post(root+'/runs/'+task['run_ids'][0]+'/resume',json={'expected_revision':c.get(root).json()['revision']}).status_code==409
    # Explicit recovery skips the already-understood image and uses a new reviewed budget.
    body,preview=plan(c,root,message='',max_calls=1)
    assert preview['stages']==['ingest']
    assert start(c,root,body,preview,False).status_code==200
    assert finished(c,root)['status']=='succeeded' and len(model['requests'])==2


def test_restart_pauses_unfinished_records_without_network(tmp_path):
    store=Store(tmp_path)
    p=store.create('restart only synthetic')
    for kind in ('run','user_task'):
        store.record(p['id'],kind,dict(status='running',calls=1))
    reopened=Store(tmp_path)
    for kind in ('run','user_task'):
        record=reopened.records(p['id'],kind)[0]
        assert record['status']=='paused_budget' and record['error']=='RESTARTED' and record['calls']==1


@pytest.mark.parametrize('purpose',['current','goal','reference','template'])
def test_url_source_preserves_selected_purpose_even_on_failure(case,purpose):
    c,app,model,root=case
    p=c.get(root).json()
    result=c.post(root+'/sources/url',json={
        'expected_revision':p['revision'],'url':'http://127.0.0.1:1/blocked',
        'dynamic':False,'authorized_public':True,'purpose':purpose,
    })
    assert result.status_code==200,result.text
    source=result.json()
    assert source['parse_status']=='failed'
    assert source['purpose']==purpose


def test_url_source_legacy_request_defaults_to_reference(case):
    c,app,model,root=case
    p=c.get(root).json()
    result=c.post(root+'/sources/url',json={
        'expected_revision':p['revision'],'url':'http://127.0.0.1:1/blocked',
        'dynamic':False,'authorized_public':True,
    })
    assert result.status_code==200,result.text
    assert result.json()['purpose']=='reference'
