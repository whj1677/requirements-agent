"""运行时能力标识：启动时固定，会话接口纯附加，不含敏感信息。"""
from fastapi.testclient import TestClient
from app.contracts import API_CAPABILITIES
from app.main import create_app

def client(tmp_path):
    app=create_app(tmp_path,access_token='test-token',env_path=tmp_path/'.env')
    c=TestClient(app)
    login=c.post('/api/session/login',json={'key':'test-token'})
    c.headers.update({'Origin':'http://testserver','X-CSRF-Token':login.json()['csrf']})
    return c,app

def test_session_exposes_backend_capabilities(tmp_path):
    c,app=client(tmp_path)
    with c:
        body=c.get('/api/session').json()
    backend=body['backend']
    assert 'actions' in backend['capabilities']
    assert backend['capabilities']==sorted(backend['capabilities'])
    assert backend==app.state.runtime

def test_runtime_id_fixed_at_startup_not_recomputed_per_request(tmp_path):
    c,app=client(tmp_path)
    with c:
        first=c.get('/api/session').json()['backend']
        second=c.get('/api/session').json()['backend']
    assert first['runtime_id']==second['runtime_id']
    assert first['started_at']==second['started_at']
    # 响应直接来自 app.state：运行标识在启动时固定，不随请求重读磁盘
    app.state.runtime['runtime_id']='mutated-in-memory'
    with c:
        assert c.get('/api/session').json()['backend']['runtime_id']=='mutated-in-memory'

def test_session_response_carries_no_secrets(tmp_path):
    c,app=client(tmp_path)
    with c:
        text=c.get('/api/session').text
    assert 'test-token' not in text
    assert 'ra_session' not in text

def test_capabilities_declared_sorted_tuple():
    assert isinstance(API_CAPABILITIES,tuple)
    assert list(API_CAPABILITIES)==sorted(API_CAPABILITIES)
    assert 'actions' in API_CAPABILITIES
