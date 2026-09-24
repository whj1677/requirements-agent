import asyncio
import copy
import io
import json
import time
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from app.core import Problem
from app.main import create_app
from app.provider import Provider, DEFAULT, assemble, origin
from app.sources import public_target, parse_bytes, save_source
from app.workflow import Workflow, apply_response
from app.store import Store
from tests.helpers import EXAMPLES, prepared

def client(tmp_path):
    app=create_app(tmp_path,access_token='test-token',env_path=tmp_path/'.env')
    c=TestClient(app)
    login=c.post('/api/session/login',json={'key':'test-token'})
    c.headers.update({'Origin':'http://testserver','X-CSRF-Token':login.json()['csrf']})
    return c,app

def test_missing_key_http_and_persistence(tmp_path):
    c,app=client(tmp_path)
    with c:
        p=c.post('/api/projects',json={'name':'无 Key 项目'}).json()
        run=c.post(f'/api/projects/{p["id"]}/runs',json={'expected_revision':0,'stage':'ingest'}).json()
        time.sleep(.1)
        runs=c.get(f'/api/projects/{p["id"]}/runs').json()
        assert runs[-1]['error']=='CONFIG_MISSING'
        assert app.state.store.get(p['id'])['messages']==[]
    assert Store(tmp_path).get(p['id'])['name']=='无 Key 项目'

def test_csrf_origin_host_and_secret_mask(tmp_path):
    c,app=client(tmp_path)
    assert c.post('/api/projects',json={'name':'x'},headers={'Origin':'https://evil.example'}).status_code==403
    assert c.post('/api/projects',json={'name':'x'},headers={'X-CSRF-Token':'wrong'}).status_code==403
    assert c.get('/api/projects',headers={'Host':'evil.example'}).status_code==403
    assert c.put('/api/models/model/key',json={'key':'secret-engineering-only'}).status_code==200
    result=c.get('/api/models').text
    assert 'secret-engineering-only' not in result
    assert 'secret-engineering-only' not in (tmp_path/'requirements.sqlite3').read_bytes().decode('utf8',errors='ignore')

@pytest.mark.parametrize('url',['http://127.0.0.1','http://169.254.169.254','http://192.168.1.1','http://[::1]','file:///etc/passwd','http://user:pass@example.com','http://example.com:8765'])
def test_ssrf_rejection(url):
    with pytest.raises(Problem):public_target(url)

def test_dns_mixed_private_refused(monkeypatch):
    import socket
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(None,None,None,None,('8.8.8.8',80)),(None,None,None,None,('127.0.0.1',80))])
    with pytest.raises(Problem):public_target('https://public-looking.example')

def test_bad_file_is_visible_and_good_file_survives(tmp_path):
    store=Store(tmp_path)
    bad=save_source(store,'bad.pdf',b'not a PDF')
    good=save_source(store,'good.txt','正常材料'.encode())
    assert bad['parse_status']=='failed' and good['parse_status']=='read'

def test_real_image_block_and_capability(tmp_path):
    store=Store(tmp_path);p=store.create('图像验证')
    out=io.BytesIO();Image.new('RGB',(80,60),'white').save(out,format='PNG')
    p['sources']=[save_source(store,'pixels.png',out.getvalue())]
    config=dict(DEFAULT)
    messages,excerpts,omitted=assemble(p,'vision','查看图片',config,tmp_path)
    assert messages[1]['content'][1]['image_url']['url'].startswith('data:image/png;base64,')
    config['vision']='unsupported'
    with pytest.raises(Problem,match='视觉'):assemble(p,'vision','',config,tmp_path)

def test_context_preserves_rules_or_refuses(tmp_path):
    store=Store(tmp_path);p=prepared(store);config=dict(DEFAULT,context_chars=100)
    with pytest.raises(Problem,match='关键底稿'):assemble(p,'clarify','',config,tmp_path)

def test_questions_not_repeated_and_candidate_not_auto_approved(tmp_path):
    store=Store(tmp_path);p=prepared(store)
    r=copy.deepcopy(EXAMPLES['clarify'])
    r['questions'][0]['topic_key']=p['questions'][0]['topic_key']
    before=len(p['questions']);apply_response(p,r)
    assert len(p['questions'])==before
    assert p['active_baseline_id'] is None

class FakeProvider(Provider):
    def __init__(self,results,delay=0):super().__init__();self.results=iter(results);self.delay=delay;self.received=[]
    def key(self,c):return 'offline-fixture'
    async def request(self,c,m,evidence=None):
        self.received.append(m);await asyncio.sleep(self.delay)
        r=next(self.results)
        if isinstance(r,Exception):raise r
        return copy.deepcopy(r),dict(request_model=c['model'],response_model='OFFLINE_FIXTURE',usage={'total_tokens':100},origin=origin(c),finish_reason='stop')

def execute_run(tmp_path,results,max_calls=8,cancel=False):
    async def scenario():
        store=Store(tmp_path);p=store.create('离线故障注入')
        with store.edit(p['id'],0,'仅合成授权',bump=False) as (p,db):p['grants'][origin(DEFAULT)]={'source_ids':[]}
        provider=FakeProvider(results,.02 if cancel else 0);w=Workflow(store,provider)
        store.setting('model',dict(DEFAULT,max_calls=max_calls))
        r=w.start(p['id'],0,'ingest','合成测试')
        if cancel:
            await asyncio.sleep(.01);store.update_run(p['id'],r['id'],status='cancelled')
        await w.tasks[r['id']]
        return store.get_record(p['id'],r['id']),store.get(p['id']),provider
    return asyncio.run(scenario())

def empty_ingest():
    return dict(schema_version='1.1',stage='ingest',summary='离线合成响应',proposals=[],questions=[],findings=[],used_source_refs=[],limitations=[],result={'understanding':'合成验证','material_limits':[]})

def test_bounded_retry_then_success(tmp_path):
    r,p,provider=execute_run(tmp_path,[Problem('RATE_LIMITED','429'),empty_ingest()])
    assert r['calls']==2 and r['status']=='succeeded'

def test_auth_failure_not_retried(tmp_path):
    r,p,provider=execute_run(tmp_path,[Problem('AUTH_FAILED','401')])
    assert r['calls']==1 and r['error']=='AUTH_FAILED'

def test_budget_all_attempts_count(tmp_path):
    r,p,provider=execute_run(tmp_path,[Problem('SCHEMA_INVALID','bad')]*4,max_calls=2)
    assert r['calls']==2 and r['status']=='paused_budget' and not p['messages']

def test_cancel_prevents_commit_and_new_request(tmp_path):
    r,p,provider=execute_run(tmp_path,[empty_ingest()],cancel=True)
    assert r['status']=='cancelled' and len(provider.received)==1 and not p['messages']

def test_schema_failure_no_draft_write(tmp_path):
    r,p,provider=execute_run(tmp_path,[{'confirmed':True}]*3)
    assert r['calls']==3 and r['status']=='failed' and not p['messages']
