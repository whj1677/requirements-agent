"""日常本机入口无需令牌，但写操作仍需同源和 CSRF 校验。"""
from fastapi.testclient import TestClient
from app.main import create_app

def off_client(tmp_path, monkeypatch):
    monkeypatch.delenv('RA_ACCESS_TOKEN',raising=False)
    app=create_app(tmp_path/'data',env_path=tmp_path/'.env')
    return TestClient(app),app

def test_local_session_without_login(tmp_path,monkeypatch):
    c,app=off_client(tmp_path,monkeypatch)
    with c:
        body=c.get('/api/session').json()
    assert body['backend']['runtime_id']==app.state.runtime['runtime_id']
    assert 'actions' in body['backend']['capabilities']
    assert body['csrf']

def test_local_write_requires_csrf_without_login(tmp_path,monkeypatch):
    c,app=off_client(tmp_path,monkeypatch)
    with c:
        csrf=c.get('/api/session').json()['csrf']
        assert c.post('/api/projects',json={'name':'无令牌模式验证'},headers={'Origin':'http://testserver'}).status_code==403
        assert c.post('/api/projects',json={'name':'无令牌模式验证'},headers={'X-CSRF-Token':csrf}).status_code==403
        res=c.post('/api/projects',json={'name':'无令牌模式验证'},headers={'Origin':'http://testserver','X-CSRF-Token':csrf})
    assert res.status_code==200

def test_local_mode_keeps_host_and_origin_guards(tmp_path,monkeypatch):
    c,app=off_client(tmp_path,monkeypatch)
    with c:
        assert c.get('/api/session',headers={'Origin':'http://evil.example'}).status_code==403

def test_local_factory_ignores_inherited_access_token(tmp_path,monkeypatch):
    monkeypatch.setenv('RA_ACCESS_TOKEN','stale-local-token')
    app=create_app(tmp_path/'data',env_path=tmp_path/'.env')
    with TestClient(app) as c:
        assert c.get('/api/session').status_code==200
        assert c.get('/api/session').json()['csrf']

def test_explicit_token_mode_still_requires_login(tmp_path,monkeypatch):
    monkeypatch.delenv('RA_ACCESS_TOKEN',raising=False)
    app=create_app(tmp_path/'data',access_token='test-token',env_path=tmp_path/'.env')
    c=TestClient(app)
    with c:
        assert c.get('/api/session').status_code==401
        assert c.post('/api/session/login',json={'key':'wrong'}).status_code==401
