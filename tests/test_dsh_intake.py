"""Isolated intake boundary tests; no paid model or daily data access."""
import asyncio
import json
from pathlib import Path

import httpx
import pytest

from app import dsh_intake
from app.core import digest
from app.model_evidence import ModelCallEvidence
from app.provider import DEFAULT
from app.skill_guidance import apply, load


def inputs():
    header = dict(stage='ingest', initial_intake=True, response_protocol='business-draft-v1', detail_required=False,
                  schema={'type':'object','properties':{'summary':{'type':'string'}},'required':['summary']})
    system = apply(header, '仅整理当前资料，不替人确认。', load({}, 'ingest'))
    return [dict(role='system',content=system+'\n可信任务头：'+json.dumps(header,ensure_ascii=False)),
            dict(role='user',content=[dict(type='text',text=json.dumps({'material':'管理员只能查询告警。','answers':['不允许远程控制。']},ensure_ascii=False))])]


def test_scope_excludes_detail_and_other_stages():
    header = dict(stage='ingest', initial_intake=True, response_protocol='business-draft-v1')
    assert dsh_intake.eligible({'stage':'ingest'}, header)
    for key in ('draft_request','benchmark','discussion_target','generation_target','requirement_recommendation_scope'):
        assert not dsh_intake.eligible({'stage':'ingest',key:{'id':'x'}},header)
    assert not dsh_intake.eligible({'stage':'ingest'},dict(header,detail_required=True))
    assert not dsh_intake.eligible({'stage':'prd'},header)


def test_skill_discovery_has_only_stage_wrapper(tmp_path):
    from app.dsh_skills import export
    report = export(inputs()[0]['content'], tmp_path/'skills')
    assert [p.relative_to(tmp_path/'skills').as_posix() for p in (tmp_path/'skills').rglob('SKILL.md')] == ['requirements-intake/SKILL.md']
    assert report['selected']['selected'][0]['id'] == 'anthropic-write-spec'
    for relative, sha in report['original_hashes'].items():
        import hashlib
        assert hashlib.sha256((tmp_path/'skill-provenance'/relative).read_bytes()).hexdigest() == sha
    assert list((tmp_path/'skill-provenance').rglob('LICENSE'))
    local=report['local_skill']
    assert [section['heading'] for section in local['sections']]==['## 先整理已知，再提出剩余决定','## 输出']
    assert hashlib.sha256((tmp_path/'skill-provenance'/local['path']).read_bytes()).hexdigest()==local['sha256']
    skill=(tmp_path/'skills/requirements-intake/SKILL.md').read_text(encoding='utf-8')
    assert 'Word 资料优先调用' not in skill
    from scripts.package_local import source_paths
    from scripts.build_windows import builtin_skill_assets
    from app.core import ROOT
    assert Path(local['path']) in source_paths(ROOT)
    assert ROOT/local['path'] in dict(builtin_skill_assets(ROOT))


@pytest.mark.skipif(not dsh_intake.runtime_status()['ready'], reason='pinned SDK not installed')
@pytest.mark.parametrize('encoding',['identity','gzip','truncated','gap','fenced_workflow'])
def test_real_sdk_tools_and_counted_mock_transport(tmp_path, monkeypatch,encoding):
    """Actual SDK subprocess and read/skill tools, synthetic SSE only."""
    sent = []
    final = json.dumps({'summary':'合成草稿'},ensure_ascii=False)
    if encoding=='fenced_workflow':
        from tests.test_business_draft import packet,draft_row,business_value
        raw=packet(draft_row())
        raw['summary_fields']={'intent':business_value('仍未知'),'users':business_value('管理员','fact')}
        final='```json\n'+json.dumps(raw,ensure_ascii=False)+'\n```'
    calls = [('skill', {'name':'requirements-intake'}), ('read_snapshot', {'file_path':'input-index.json'})]
    planned=False
    index_parts={};seen_index_offsets=set()
    messages=inputs()
    context=json.loads(messages[1]['content'][0]['text'])
    context['long_line']='中'*4233+'😀'
    messages[1]['content'][0]['text']=json.dumps(context,ensure_ascii=False)
    class Stream(httpx.SyncByteStream):
        def __init__(self, value): self.value = value
        def __iter__(self):
            for start in range(0,len(self.value),17):yield self.value[start:start+17]
    def response(req):
        nonlocal planned
        body = json.loads(req.content)
        sent.append(body)
        if not planned:
            for message in body['messages']:
                if message.get('role')!='tool':continue
                try:part=json.loads(message['content'])
                except (ValueError,TypeError):continue
                if isinstance(part,dict) and part.get('file_path')=='input-index.json' and part['offset'] not in seen_index_offsets:
                    seen_index_offsets.add(part['offset']);index_parts[part['offset']]=part['content']
                    if not part['complete']:
                        calls.append(('read_snapshot',dict(file_path='input-index.json',offset=part['next_offset'])))
                        continue
                    plan=json.loads(''.join(index_parts[offset] for offset in sorted(index_parts)))['read_plan']
                    if encoding=='gap':
                        first=next(row for row in plan if row['file_path']=='current-workspace.json' and row['offset']==0)
                        first.update(offset=1,limit=4095)
                    calls.extend(('read_snapshot',entry) for entry in plan)
                    planned=True
        number = len(sent)-1
        if number < len(calls):
            name, args = calls[number]
            delta = {'role':'assistant','content':'','reasoning_content':'', 'tool_calls':[{'index':0,'id':'call_'+str(number),'type':'function',
                     'function':{'name':name,'arguments':json.dumps(args)}}]}
            finish = 'tool_calls'
        else:
            delta={'role':'assistant','content':final,'reasoning_content':''}; finish='stop'
        chunks=[{'id':'synthetic','object':'chat.completion.chunk','model':body['model'],
                 'choices':[{'index':0,'delta':delta,'finish_reason':None}]},
                {'id':'synthetic','object':'chat.completion.chunk','model':body['model'],
                 'choices':[{'index':0,'delta':{},'finish_reason':finish}],
                 'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}}]
        wire=''.join('data: '+json.dumps(c,ensure_ascii=False)+'\n\n' for c in chunks)+'data: [DONE]\n\n'
        if encoding=='truncated':wire=wire.replace('data: [DONE]\n\n','')
        data=wire.encode()
        headers={'content-type':'text/event-stream'}
        if encoding=='gzip':
            import gzip
            data=gzip.compress(data)
            headers['content-encoding']='gzip'
        return httpx.Response(200,headers=headers,stream=Stream(data))
    client = httpx.Client
    monkeypatch.setattr(dsh_intake.httpx,'Client',lambda **kwargs:client(transport=httpx.MockTransport(response)))
    cfg=dict(DEFAULT,model='deepseek-v4-pro',thinking_disabled=False,timeout=60,max_tokens=1000)
    evidence=ModelCallEvidence(tmp_path,'CALLsynthetic','RUNsynthetic','ingest',cfg['model'],cfg['base_url'],None,'synthetic-key')
    if encoding=='fenced_workflow':
        from app.workflow import Workflow
        from tests.test_business_draft_workflow import BusinessProvider
        from tests.test_requirement_recommendation import project
        async def scenario():
            store,p=project(tmp_path/'workflow')
            provider=BusinessProvider([]);workflow=Workflow(store,provider)
            config=dict(workflow.config('ingest'),intake_engine='deepseek-harness',timeout=60)
            run=workflow.start(p['id'],p['revision'],'ingest','',config_override=config)
            await workflow.tasks[run['id']]
            saved=store.get_record(p['id'],run['id'],'run')
            assert saved['status'] in ('succeeded','partial'),saved.get('error')
            assert saved['calls']==len(sent)+2 and len(provider.received)==2
            assert saved['intake_grounding']['status']=='passed'
            after=store.get(p['id'])
            assert after['sources']==p['sources'] and after['questions']==p['questions']
            assert after['business_draft_reviews'][-1]['accepted']
            assert all(row.get('selection_status')=='candidate' for row in after['items'][len(p['items']):])
            call_id=saved['attempts'][0]['call_id']
            proof=json.loads((store.folder/'evidence/model-calls'/f'{call_id}.json').read_text('utf-8'))
            assert proof['final_output']==final
            assert proof['dsh_transport_normalization']['version']=='dsh-single-json-fence-1'
            assert all(row['complete'] for row in proof['dsh_reading_coverage']['files'])
        asyncio.run(scenario())
        return
    if encoding in ('truncated','gap'):
        from app.core import Problem
        with pytest.raises(Problem) as error:
            asyncio.run(dsh_intake.request(cfg,messages,'synthetic-key',evidence))
        if encoding=='truncated':
            assert error.value.code=='DSH_STREAM_CLOSED'
            assert evidence.value['dsh_runtime_error']['code']=='STREAM_CLOSED'
            assert evidence.value['dsh_http_requests'][0]['sse_done'] is False
            assert evidence.value['final_output'] is None
        else:
            assert error.value.code=='DSH_READING_UNVERIFIED'
            row=next(row for row in evidence.value['dsh_reading_coverage']['files'] if row['path']=='current-workspace.json')
            assert row['missing_ranges']==[[0,1]]
            assert evidence.value['final_output']==final
        return
    value,meta=asyncio.run(dsh_intake.request(cfg,messages,'synthetic-key',evidence))
    assert value=={'summary':'合成草稿'}
    assert len(sent)==meta['outbound_http_requests']==len(calls)+1
    assert all(body['model']=='deepseek-v4-pro' and body['thinking']=={'type':'enabled'} for body in sent)
    assert all(body['max_tokens']==1000 for body in sent)
    assert all(body['stream'] is True for body in sent)
    for record in evidence.value['dsh_http_requests']:
        assert record['content_encoding']==encoding and record['sse_done']
        assert len(record['raw_response_sha256'])==len(record['decoded_response_sha256'])==64
        assert record['raw_response_bytes']>0 and record['decoded_response_bytes']>0
    # Tool responses are the actual runtime results, not mocked tools.
    tool_messages=[m for m in sent[-1]['messages'] if m.get('role')=='tool']
    assert len(tool_messages)==len(calls)
    assert '不允许远程控制' in json.dumps(tool_messages,ensure_ascii=False)
    assert 'Error' not in json.dumps(tool_messages,ensure_ascii=False)
    assert evidence.value['outbound_http_requests']==len(calls)+1
    chunks=[json.loads(m['content']) for m in tool_messages[1:]]
    workspace_chunks=sorted((row for row in chunks if row['file_path']=='current-workspace.json'),key=lambda row:row['offset'])
    assert len(workspace_chunks)>=2
    assert json.loads(''.join(row['content'] for row in workspace_chunks))==context
    assert not any('synthetic-key' in p.read_text(encoding='utf-8') for p in tmp_path.rglob('*.json'))
    assert evidence.value['dsh_reading_coverage']['skill_loaded']
    assert all(row['complete'] for row in evidence.value['dsh_reading_coverage']['files'])


def test_partial_failed_and_invented_reads_are_not_coverage(tmp_path):
    import hashlib
    text='甲😀乙丙丁'
    (tmp_path/'input.json').write_text(text,encoding='utf-8')
    coverage=dsh_intake.ReadingCoverage(tmp_path)
    def result(start,end,failed=False,content=None,declared=None):
        call={'type':'tool/call','data':{'callId':'1','name':'read_snapshot','arguments':{'file_path':'input.json','offset':start}}}
        coverage.observe(call,call)
        payload=dict(file_path='input.json',offset=start if declared is None else declared,next_offset=end,total_chars=len(text),
            sha256=hashlib.sha256(text.encode()).hexdigest(),complete=end==len(text),content=text[start:end] if content is None else content)
        raw={'type':'tool/result','data':{'callId':'1','isError':failed,'content':[{'type':'text','text':json.dumps(payload,ensure_ascii=False)}]}}
        coverage.observe(raw,raw)
    result(0,5,True)
    result(0,5,content='伪造')
    result(1,5,declared=0)
    assert coverage.report()['files'][0]['delivered_chars']==0
    result(0,2);result(3,5)
    assert coverage.report()['files'][0]['missing_ranges']==[[2,3]]
    assert not coverage.complete()
    result(2,3)
    assert coverage.report()['files'][0]['complete']
    assert not coverage.complete()  # No successful skill load.


def test_sdk_usage_includes_cache_reads_in_prompt_total():
    usage=dsh_intake.aggregate_usage([dict(inputTokens=34758,cacheReadTokens=74752,outputTokens=12616,totalTokens=122126)])
    assert usage==dict(prompt_tokens=109510,completion_tokens=12616,total_tokens=122126,
                      prompt_tokens_details=dict(cached_tokens=74752))


@pytest.mark.parametrize('text',['```json\n{"a":1}\n```',' \n```\n{"a":1}\n```\n '])
def test_single_fence_is_transport_only(text):
    import hashlib
    value,audit=dsh_intake.decode_final(text)
    assert value=={'a':1}
    assert audit['raw_sha256']==hashlib.sha256(text.encode()).hexdigest()
    assert audit['normalized_sha256']==hashlib.sha256(b'{"a":1}').hexdigest()


@pytest.mark.parametrize('text',[
    'explanation\n```json\n{}\n```','```json\n{}\n```\nexplanation',
    '```python\n{}\n```','```json\n{}\n```\n```json\n{}\n```',
    '```json\n{} {}\n```','```json\n{} garbage\n```','```json\n{"a":}\n```',
    '```json\n{"a":1,"a":2}\n```','```json\n[]\n```','```json\n{}',
])
def test_fence_does_not_repair_or_accept_other_framing(text):
    with pytest.raises(ValueError):dsh_intake.decode_final(text)


@pytest.mark.skipif(not dsh_intake.runtime_status()['ready'], reason='pinned SDK not installed')
def test_snapshot_tool_runtime_boundary_probe(tmp_path):
    workspace=tmp_path/'snapshot';workspace.mkdir()
    (workspace/'input-index.json').write_text('{}',encoding='utf-8')
    skill=tmp_path/'skills/requirements-intake';skill.mkdir(parents=True)
    (skill/'SKILL.md').write_text('---\nname: requirements-intake\ndescription: synthetic probe\n---\nRead the authorized snapshot.',encoding='utf-8')
    bridge=dsh_intake.IntakeBridge(relay_url='http://127.0.0.1:1',relay_token='probe-no-credential',
        config=dict(DEFAULT),workspace=workspace,skill_root=skill.parent,dsh_home=tmp_path/'runtime',evidence_dir=tmp_path/'evidence')
    report=bridge.probe()
    assert report['model_requests']==0
    assert report['allowed_tools']==['read_snapshot','skill']
    assert all(report['policy'][key] for key in ('outsideReadDenied','writeDenied','textReadOk'))


def test_relay_rejects_wrong_auth_path_model_and_stopped_task(tmp_path):
    import http.client
    cfg=dict(DEFAULT,model='deepseek-v4-pro')
    relay=dsh_intake.Relay(cfg,'secret',None)
    url=relay.start()
    def post(path,token,model):
        connection=http.client.HTTPConnection('127.0.0.1',relay.server.server_port,timeout=2)
        connection.request('POST',path,json.dumps({'model':model,'stream':True}),{'Authorization':'Bearer '+token})
        response=connection.getresponse(); status=response.status;response.read();connection.close()
        return status
    try:
        assert post('/chat/completions','wrong',cfg['model'])==403
        assert post('/anywhere',relay.token,cfg['model'])==403
        assert post('/chat/completions',relay.token,'other-model')==502
        relay.cancelled.set()
        assert post('/chat/completions',relay.token,cfg['model'])==502
        assert relay.requests==[]
    finally: relay.close()
    assert not relay.thread.is_alive()


@pytest.mark.parametrize('stop_mode',['cancel','timeout'])
def test_cancel_stops_owned_runtime_and_preserves_snapshot(tmp_path,monkeypatch,stop_mode):
    import threading
    entered=threading.Event();closed=threading.Event()
    class Harness:
        def run(self,*args,**kwargs):
            entered.set();closed.wait(5)
            raise RuntimeError('owned runtime stopped')
        def close(self): closed.set()
    monkeypatch.setattr(dsh_intake,'runtime_status',lambda:{'ready':True})
    monkeypatch.setattr(dsh_intake.IntakeBridge,'_harness',lambda self:Harness())
    cfg=dict(DEFAULT,timeout=30)
    evidence=ModelCallEvidence(tmp_path,'CALLcancel','RUNcancel','ingest',cfg['model'],cfg['base_url'],None,'synthetic')
    async def scenario():
        task=asyncio.create_task(dsh_intake.request(cfg,inputs(),'synthetic',evidence))
        assert await asyncio.to_thread(entered.wait,3)
        if stop_mode=='cancel':
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await task
        else:
            with pytest.raises(asyncio.TimeoutError): await asyncio.wait_for(task,0.05)
    asyncio.run(scenario())
    assert closed.is_set()
    assert (tmp_path/'evidence/dsh-intake/CALLcancel/snapshot/current-workspace.json').is_file()
    assert evidence.value['outbound_http_requests']==0


def test_budget_rejection_happens_before_any_network_dispatch(tmp_path):
    import http.client
    from app.core import Problem
    class Budget:
        def reserve(self,*args): raise Problem('BUDGET_EXHAUSTED','synthetic exhausted budget')
    cfg=dict(DEFAULT,model='deepseek-v4-pro')
    evidence=ModelCallEvidence(tmp_path,'CALLlimit','RUNlimit','ingest',cfg['model'],cfg['base_url'],None,'synthetic',request_budget=Budget())
    relay=dsh_intake.Relay(cfg,'synthetic',evidence)
    relay.start()
    try:
        connection=http.client.HTTPConnection('127.0.0.1',relay.server.server_port,timeout=2)
        connection.request('POST','/chat/completions',json.dumps({'model':cfg['model'],'stream':True}),{'Authorization':'Bearer '+relay.token})
        response=connection.getresponse();assert response.status==502;response.read();connection.close()
        assert relay.requests==[] and relay.error.code=='BUDGET_EXHAUSTED'
    finally: relay.close()


def test_workflow_routes_only_intake_and_keeps_candidate_guards(tmp_path,monkeypatch):
    from app.workflow import Workflow
    from tests.test_business_draft_workflow import BusinessProvider
    from tests.test_business_draft import packet,draft_row,business_value
    from tests.test_requirement_recommendation import project
    async def scenario():
        store,p=project(tmp_path)
        provider=BusinessProvider([])
        workflow=Workflow(store,provider)
        raw=packet(draft_row())
        raw['summary_fields']={'intent':business_value('仍未知'),'users':business_value('管理员','fact')}
        async def request(config,messages,key,evidence,**callbacks):
            callbacks['on_dispatch'](1);callbacks['on_dispatch'](2)
            evidence.response(json.dumps(raw,ensure_ascii=False),finish_reason='stop')
            return raw,{'finish_reason':'stop','usage':None,'outbound_http_requests':2}
        monkeypatch.setattr(dsh_intake,'request',request)
        config=dict(workflow.config('ingest'),intake_engine='deepseek-harness')
        run=workflow.start(p['id'],p['revision'],'ingest','',config_override=config)
        await workflow.tasks[run['id']]
        saved=store.get_record(p['id'],run['id'],'run')
        assert saved['intake_engine']=='deepseek-harness'
        assert saved['calls']==4 and len(provider.received)==2
        assert saved['intake_grounding']['status']=='passed'
        after=store.get(p['id'])
        assert after['sources']==p['sources'] and after['questions']==p['questions']
        assert after['items'][:len(p['items'])]==p['items']
        assert after['business_draft_reviews'][-1]['accepted']
        assert all(item.get('selection_status')=='candidate' for item in after['items'][len(p['items']):])
    asyncio.run(scenario())
