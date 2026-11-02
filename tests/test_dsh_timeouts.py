"""Short, synthetic HTTP streams and owned fake harness; zero external model calls."""
import asyncio
import http.client
import json
import threading
import time
from types import SimpleNamespace
import httpx
import pytest
from app import dsh_intake as d
from app.core import Problem
from app.model_evidence import ModelCallEvidence
from app.provider import DEFAULT
from tests.test_dsh_intake import inputs

@pytest.mark.parametrize('mode',['normal','heartbeat_stall','cancel','idle'])
def test_each_http_gets_own_deadline_and_owned_runtime_closes(tmp_path,monkeypatch,mode):
    stopped=threading.Event();entered=threading.Event();sent=[];clients=[]
    cfg=dict(DEFAULT,timeout=.3,max_tokens=100)
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            entered.set()
            if mode=='normal':
                time.sleep(.16);yield b'data: [DONE]\n\n'
            else:
                while not stopped.wait(.02):yield b': heartbeat\n\n'
        def close(self):pass
    original=httpx.Client
    def client(**kwargs):
        clients.append(kwargs)
        return original(transport=httpx.MockTransport(lambda request:httpx.Response(200,
            headers={'content-type':'text/event-stream'},stream=Stream())),**kwargs)
    monkeypatch.setattr(d.httpx,'Client',client)
    class Harness:
        def __init__(self,bridge):self.bridge=bridge
        def run(self,*args,**kwargs):
            if mode=='idle':stopped.wait(3)
            else:
                for number in range(3):
                    if stopped.is_set():break
                    url=self.bridge.relay_url.split(':')[-1]
                    conn=http.client.HTTPConnection('127.0.0.1',int(url),timeout=2)
                    try:
                        conn.request('POST','/chat/completions',json.dumps({'model':cfg['model'],'stream':True}),
                            {'Authorization':'Bearer '+self.bridge.relay_token})
                        response=conn.getresponse();response.read()
                    finally:conn.close()
            return SimpleNamespace(final_response='{"summary":"synthetic"}',finish_reason='stop')
        def close(self):stopped.set()
    monkeypatch.setattr(d,'runtime_status',lambda:{'ready':True})
    monkeypatch.setattr(d.IntakeBridge,'_harness',lambda self:Harness(self))
    # This test isolates scheduling. Real reading/candidate guards remain covered by existing tests.
    monkeypatch.setattr(d.IntakeReadPhase,'prepare',lambda self,body:'reading')
    monkeypatch.setattr(d.ReadingCoverage,'complete',lambda self:True)
    evidence=ModelCallEvidence(tmp_path,'CALLshort','RUNshort','ingest',cfg['model'],cfg['base_url'],None,'fixture-secret')
    async def scenario():
        task=asyncio.create_task(d.request(cfg,inputs(),'fixture-secret',evidence,on_dispatch=sent.append))
        if mode=='cancel':
            assert await asyncio.to_thread(entered.wait,2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
        elif mode in ('heartbeat_stall','idle'):
            with pytest.raises(Problem) as failure:await task
            assert failure.value.code=='TIMEOUT'
            assert evidence.value['dsh_timeout']['scope']==('runtime_idle' if mode=='idle' else 'http_request')
        else:
            value,meta=await task
            assert value=={'summary':'synthetic'} and meta['outbound_http_requests']==3
            assert meta['elapsed_seconds']>.3
            assert meta['usage'] is None
    asyncio.run(scenario())
    assert stopped.is_set()
    assert sent==list(range(1,(3 if mode=='normal' else 0 if mode=='idle' else 1)+1))
    assert all(c['timeout']==.3 for c in clients)
    assert (tmp_path/'evidence/dsh-intake/CALLshort/snapshot/current-workspace.json').is_file()


def test_later_dispatch_or_completion_cannot_erase_expired_request(monkeypatch):
    clock=[0.];monkeypatch.setattr(d.time,'monotonic',lambda:clock[0])
    deadlines=d.RequestDeadlines(3);deadlines.begin(1)
    clock[0]=2;deadlines.begin(2)
    assert deadlines.remaining()==(1,1)
    clock[0]=3.1;deadlines.end(1)
    assert deadlines.remaining()[0]<0 and deadlines.remaining()[1]==1


def test_sdk_session_rpc_has_no_single_http_total_deadline(tmp_path,monkeypatch):
    import deepseek_harness
    captured={}
    monkeypatch.setattr(deepseek_harness,'DeepSeekHarness',lambda **kwargs:captured.update(kwargs))
    monkeypatch.setattr(d,'runtime_status',lambda:{'ready':True})
    monkeypatch.setattr(d.IntakeBridge,'prepare',lambda self,probe=False:tmp_path/'patch.json')
    workspace=tmp_path/'workspace';workspace.mkdir();skills=tmp_path/'skills';skills.mkdir()
    bridge=d.IntakeBridge(relay_url='http://127.0.0.1:1',relay_token='synthetic',config=dict(DEFAULT),
        workspace=workspace,skill_root=skills,dsh_home=tmp_path/'runtime',evidence_dir=tmp_path/'evidence',timeout_seconds=300)
    bridge._harness()
    assert captured['request_timeout_seconds'] is None
    assert captured['initialize_timeout_seconds']==45 and captured['shutdown_timeout_seconds']==5


def test_workflow_dsh_does_not_apply_single_http_timeout_to_whole_loop(tmp_path,monkeypatch):
    from app.workflow import Workflow
    from tests.test_business_draft_workflow import BusinessProvider
    from tests.test_requirement_recommendation import project
    async def scenario():
        store,p=project(tmp_path);workflow=Workflow(store,BusinessProvider([]))
        async def slow_loop(*args,**kwargs):
            await asyncio.sleep(.12)
            raise Problem('SYNTHETIC_END','bounded fixture finished')
        monkeypatch.setattr(d,'request',slow_loop)
        config=dict(workflow.config('ingest'),intake_engine='deepseek-harness',timeout=.05)
        run=workflow.start(p['id'],p['revision'],'ingest','',config_override=config)
        await workflow.tasks[run['id']]
        assert store.get_record(p['id'],run['id'],'run')['error']=='SYNTHETIC_END'
    asyncio.run(scenario())


def test_real_local_socket_read_timeout_is_still_enforced(tmp_path,monkeypatch):
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    class Stalled(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):time.sleep(.4)  # Deliberately no response headers.
    upstream=ThreadingHTTPServer(('127.0.0.1',0),Stalled)
    thread=threading.Thread(target=upstream.serve_forever,daemon=True);thread.start()
    original=httpx.Client
    class LocalClient(original):
        def stream(self,method,url,**kwargs):
            return super().stream(method,'http://127.0.0.1:'+str(upstream.server_port),**kwargs)
    monkeypatch.setattr(d.httpx,'Client',LocalClient)
    cfg=dict(DEFAULT,timeout=.1)
    relay=d.Relay(cfg,'fixture-secret',None);relay.start()
    try:
        conn=http.client.HTTPConnection('127.0.0.1',relay.server.server_port,timeout=2)
        conn.request('POST','/chat/completions',json.dumps({'model':cfg['model'],'stream':True}),
            {'Authorization':'Bearer '+relay.token})
        response=conn.getresponse();response.read();conn.close()
        assert response.status==502 and relay.error.code=='DSH_TRANSPORT_ERROR'
        assert len(relay.requests)==1 and relay.cancelled.is_set()
    finally:
        relay.close();upstream.shutdown();upstream.server_close();thread.join(2)


def test_expired_step_cannot_dispatch_later_http():
    cfg=dict(DEFAULT,timeout=1);deadlines=d.RequestDeadlines(.01)
    deadlines.begin(1);time.sleep(.02);deadlines.end(1)
    relay=d.Relay(cfg,'fixture-secret',None,deadlines=deadlines);relay.start()
    try:
        conn=http.client.HTTPConnection('127.0.0.1',relay.server.server_port,timeout=2)
        conn.request('POST','/chat/completions',json.dumps({'model':cfg['model'],'stream':True}),
            {'Authorization':'Bearer '+relay.token})
        response=conn.getresponse();response.read();conn.close()
        assert response.status==502 and relay.error.code=='TIMEOUT'
        assert relay.requests==[]
    finally:relay.close()
