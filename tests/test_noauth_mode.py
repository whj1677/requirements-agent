"""临时无令牌模式：RA_ACCESS_TOKEN=off 时会话与登录放行，其余安全校验保留。"""
from fastapi.testclient import TestClient
from app.main import create_app

def off_client(tmp_path, monkeypatch):
    monkeypatch.setenv('RA_ACCESS_TOKEN','off')
    app=create_app(tmp_path/'data',env_path=tmp_path/'.env')
    return TestClient(app),app

def test_off_mode_session_without_login(tmp_path,monkeypatch):
    c,app=off_client(tmp_path,monkeypatch)
    with c:
        body=c.get('/api/session').json()
    assert body['backend']['runtime_id']==app.state.runtime['runtime_id']
    assert 'actions' in body['backend']['capabilities']

def test_off_mode_login_accepts_any_key_and_writes_pass(tmp_path,monkeypatch):
    c,app=off_client(tmp_path,monkeypatch)
    with c:
        assert c.post('/api/session/login',json={'key':'anything'}).status_code==200
        res=c.post('/api/projects',json={'name':'无令牌模式验证'},headers={'Origin':'http://testserver'})
    assert res.status_code==200

def test_off_mode_keeps_host_and_origin_guards(tmp_path,monkeypatch):
    c,app=off_client(tmp_path,monkeypatch)
    with c:
        assert c.get('/api/session',headers={'Origin':'http://evil.example'}).status_code==403

def test_token_mode_unaffected_by_off_default(tmp_path,monkeypatch):
    monkeypatch.delenv('RA_ACCESS_TOKEN',raising=False)
    app=create_app(tmp_path/'data',access_token='test-token',env_path=tmp_path/'.env')
    c=TestClient(app)
    with c:
        assert c.get('/api/session').status_code==401
        assert c.post('/api/session/login',json={'key':'wrong'}).status_code==401
