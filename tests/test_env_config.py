"""Synthetic credentials and endpoints only; no paid provider calls."""
import asyncio
import hashlib
import os
from pathlib import Path
import subprocess
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core import Problem
from app.main import create_app
from app.provider import DEFAULT, Provider
from app.store import Store
from app.exports import document_files
from tests.helpers import prepared


def model_response():
    return {'model':'synthetic-model','choices':[{'message':{'content':'{"ok":true}'},'finish_reason':'stop'}]}


def test_file_key_reaches_only_bound_synthetic_endpoint(tmp_path,monkeypatch):
    monkeypatch.delenv('RA_SYNTHETIC_API_KEY',raising=False)
    path=tmp_path/'.env'
    path.write_text('RA_SYNTHETIC_API_KEY=synthetic-file-credential\n',encoding='utf8')
    provider=Provider(env_path=path)
    config=dict(DEFAULT,base_url='http://127.0.0.1:18998',local_allowed=True,key_env='RA_SYNTHETIC_API_KEY')
    provider.env_origins['RA_SYNTHETIC_API_KEY']='http://127.0.0.1:18998'
    requests=[]
    original=httpx.AsyncClient
    def respond(request):
        requests.append(request)
        return httpx.Response(200,json=model_response())
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(respond),**kw))
    value,_=asyncio.run(provider.request(config,[{'role':'user','content':'synthetic only'}]))
    assert value=={'ok':True}
    assert len(requests)==1
    assert requests[0].headers['Authorization']=='Bearer synthetic-file-credential'
    assert requests[0].url.host=='127.0.0.1'
    assert provider.key_status(config)['key_source']=='.env'
    assert provider.key(dict(config,base_url='http://127.0.0.1:18999'))==''


def test_precedence_status_and_session_key_are_not_persisted(tmp_path,monkeypatch):
    path=tmp_path/'.env'
    path.write_text('RA_DEEPSEEK_API_KEY=synthetic-file-credential\n',encoding='utf8')
    monkeypatch.setenv('RA_DEEPSEEK_API_KEY','synthetic-process-credential')
    app=create_app(tmp_path/'db',access_token='local-login',env_path=path)
    client=TestClient(app)
    login=client.post('/api/session/login',json={'key':'local-login'}).json()
    client.headers.update({'Origin':'http://testserver','X-CSRF-Token':login['csrf']})
    status=client.get('/api/models').json()['model']
    assert status['key_configured'] and status['key_source']=='process_env'
    assert status['key_env']=='RA_DEEPSEEK_API_KEY' and status['bound_origin']=='https://api.deepseek.com'
    assert 'synthetic-process-credential' not in client.get('/api/models').text
    client.put('/api/models/model/key',json={'key':'synthetic-session-credential'})
    assert client.get('/api/models').json()['model']['key_source']=='session'
    client.put('/api/models/model/key',json={'key':''})
    assert client.get('/api/models').json()['model']['key_source']=='process_env'
    assert 'synthetic-session-credential' not in (tmp_path/'db'/'requirements.sqlite3').read_bytes().decode('utf8',errors='ignore')
    assert 'synthetic-session-credential' not in path.read_text(encoding='utf8')
    restarted=create_app(tmp_path/'db',access_token='other-login',env_path=path)
    assert restarted.state.provider.key_status(DEFAULT)['key_source']=='process_env'


def test_new_process_reads_explicit_root_not_working_or_parent_dir(tmp_path,monkeypatch):
    root=tmp_path/'project';root.mkdir()
    outside=tmp_path/'elsewhere';outside.mkdir()
    (tmp_path/'.env').write_text('RA_DEEPSEEK_API_KEY=synthetic-parent-decoy\n',encoding='utf8')
    (outside/'.env').write_text('RA_DEEPSEEK_API_KEY=synthetic-cwd-decoy\n',encoding='utf8')
    path=root/'.env'
    monkeypatch.delenv('RA_DEEPSEEK_API_KEY',raising=False)
    command=[sys.executable,'-c',
             'import app.config as c; c.ROOT=__import__("pathlib").Path(__import__("sys").argv[1]); '
             'from app.provider import Provider,DEFAULT; p=Provider(); print(p.key(DEFAULT))']
    env=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parent.parent),PYTHONUTF8='1')
    env.pop('RA_DEEPSEEK_API_KEY',None)
    for value in ('synthetic-first-credential','synthetic-second-credential'):
        path.write_text('RA_DEEPSEEK_API_KEY='+value+'\n',encoding='utf8')
        result=subprocess.run(command+[str(root)],cwd=outside,env=env,capture_output=True,text=True,check=True)
        assert result.stdout.strip()==value


@pytest.mark.parametrize('value',['','YOUR_API_KEY','<your-key>','sk-xxx','placeholder'])
def test_empty_or_placeholder_never_sends_request(tmp_path,monkeypatch,value):
    monkeypatch.delenv('RA_DEEPSEEK_API_KEY',raising=False)
    path=tmp_path/'.env'
    path.write_text('RA_DEEPSEEK_API_KEY='+value+'\n',encoding='utf8')
    provider=Provider(env_path=path)
    assert provider.key_status(DEFAULT)['key_source']=='none'
    calls=[]
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:calls.append(kw))
    with pytest.raises(Problem) as error:
        asyncio.run(provider.request(DEFAULT,[]))
    assert error.value.code=='CONFIG_MISSING' and not calls


def test_saved_provider_binding_recovers_without_cross_service_key(tmp_path,monkeypatch):
    monkeypatch.delenv('RA_OTHER_API_KEY',raising=False)
    path=tmp_path/'.env'
    path.write_text('RA_DEEPSEEK_API_KEY=synthetic-deepseek-credential\nRA_OTHER_API_KEY=synthetic-other-credential\n',encoding='utf8')
    store=Store(tmp_path/'db')
    other=dict(DEFAULT,base_url='http://127.0.0.1:18888',local_allowed=True,key_env='RA_OTHER_API_KEY',model='other-model')
    store.setting('model',other)
    store.setting('vision',dict(other,model='other-vision'))
    app=create_app(tmp_path/'db',env_path=path)
    provider=app.state.provider
    assert provider.key(other)=='synthetic-other-credential'
    assert provider.key_status(other)['key_source']=='.env'
    assert provider.key(dict(other,key_env='RA_DEEPSEEK_API_KEY'))==''
    assert provider.key(dict(DEFAULT,model='another-deepseek-model'))=='synthetic-deepseek-credential'


def test_missing_project_env_does_not_read_parent(tmp_path,monkeypatch):
    monkeypatch.delenv('RA_DEEPSEEK_API_KEY',raising=False)
    (tmp_path/'.env').write_text('RA_DEEPSEEK_API_KEY=synthetic-parent-decoy\n',encoding='utf8')
    assert Provider(env_path=tmp_path/'child'/'.env').key(DEFAULT)==''


def test_key_stays_out_of_document_export_and_data_backup(tmp_path,monkeypatch):
    monkeypatch.delenv('RA_DEEPSEEK_API_KEY',raising=False)
    secret='synthetic-export-backup-credential'
    path=tmp_path/'.env'
    path.write_text('RA_DEEPSEEK_API_KEY='+secret+'\n',encoding='utf8')
    data=tmp_path/'db'
    app=create_app(data,env_path=path)
    project=prepared(app.state.store)
    # This backup test needs a real synthetic source, while the shared prepared()
    # fixture intentionally uses a lightweight placeholder for other tests.
    source=project['sources'][0]
    raw=source['excerpts'][0]['text'].encode('utf-8')
    original=data/'sources'/source['id']
    original.parent.mkdir(parents=True,exist_ok=True)
    original.write_bytes(raw)
    source_hash=hashlib.sha256(raw).hexdigest()
    with app.state.store.edit(project['id'],project['revision'],'合成备份来源校准',bump=False) as (working,_):
        working['sources'][0]['sha256']=source_hash
        working['sources'][0]['excerpts'][0]['source_hash']=source_hash
    project=app.state.store.get(project['id'])
    assert app.state.provider.key_status(DEFAULT)['key_source']=='.env'
    for kind in ('prd','mrd'):
        files=asyncio.run(document_files(project,kind))
        assert all(secret.encode() not in body for body in files.values())
    env=dict(os.environ,RA_DATA_DIR=str(data),PYTHONUTF8='1')
    result=subprocess.run([sys.executable,'scripts/backup.py'],cwd=Path(__file__).resolve().parent.parent,env=env,capture_output=True,text=True,check=True)
    backup=Path(result.stdout.strip())
    assert backup.is_dir() and not (backup/'.env').exists()
    assert secret.encode() not in (backup/'requirements.sqlite3').read_bytes()
    assert secret.encode() not in (data/'requirements.sqlite3').read_bytes()
