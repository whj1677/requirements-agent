"""Actual pinned SDK + synthetic HTTP; no paid model or daily data access."""
import asyncio
import copy
import json
import threading
import time

import httpx
import pytest

from app import dsh_intake
from app.core import Problem
from app.model_evidence import ModelCallEvidence
from app.provider import DEFAULT
from tests.test_dsh_intake import inputs


@pytest.mark.parametrize('mode',['normal','json_false','literal_dsml','repeat_incomplete','final_reentry','delayed_final','early_final','cancelled','final_cancel'])
def test_actual_sdk_read_to_candidate_boundary(tmp_path,monkeypatch,mode):
    sent=[];progress=[];queue=[('skill',{'name':'requirements-intake'}),('read_snapshot',{'file_path':'input-index.json'})]
    planned=False
    incoming=[];original_prepare=dsh_intake.IntakeReadPhase.prepare
    def prepare(phase,body):
        incoming.append(copy.deepcopy(body))
        return original_prepare(phase,body)
    monkeypatch.setattr(dsh_intake.IntakeReadPhase,'prepare',prepare)
    dsml='<｜｜DSML｜｜tool_calls><｜｜DSML｜｜invoke name="read_snapshot">not executed</｜｜DSML｜｜invoke></｜｜DSML｜｜tool_calls>'
    class Stream(httpx.SyncByteStream):
        def __init__(self,content):self.content=content
        def __iter__(self):yield self.content
    def response(request):
        nonlocal planned
        body=json.loads(request.content);sent.append(copy.deepcopy(body))
        if mode=='cancelled' or mode=='final_cancel' and body.get('tool_choice')=='none':time.sleep(.2)
        if len(sent)==3 and not planned:
            index=next(json.loads(m['content']) for m in body['messages'] if m.get('role')=='tool'
                and isinstance(m.get('content'),str) and '"file_path":"input-index.json"' in m['content'])
            plan=json.loads(index['content'])['read_plan']
            queue.extend([('read_snapshot',{'file_path':'input-index.json'})] if mode=='repeat_incomplete'
                         else [('read_snapshot',row) for row in plan])
            planned=True
        position=len(sent)-1
        tools=mode!='early_final' and (position<len(queue) or mode=='final_reentry')
        if tools:
            name,args=queue[position] if position<len(queue) else ('read_snapshot',{'file_path':'input-index.json'})
            delta=dict(role='assistant',content='',reasoning_content='',tool_calls=[dict(index=0,id='call_'+str(position),
                type='function',function=dict(name=name,arguments=json.dumps(args)))])
            finish='tool_calls'
        else:
            if mode=='delayed_final':time.sleep(.2)
            delta=dict(role='assistant',content=dsml if mode=='literal_dsml' else '{"summary":"合成候选"}',reasoning_content='');finish='stop'
        chunks=[dict(id='synthetic',object='chat.completion.chunk',model=body['model'],
            choices=[dict(index=0,delta=delta,finish_reason=None)]),
            dict(id='synthetic',object='chat.completion.chunk',model=body['model'],
            choices=[dict(index=0,delta={},finish_reason=finish)],usage=dict(prompt_tokens=2,completion_tokens=1,total_tokens=3))]
        wire=(''.join('data: '+json.dumps(chunk,ensure_ascii=False)+'\n\n' for chunk in chunks)+'data: [DONE]\n\n').encode()
        return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Stream(wire))
    original=httpx.Client
    monkeypatch.setattr(dsh_intake.httpx,'Client',lambda **kwargs:original(transport=httpx.MockTransport(response)))
    config=dict(DEFAULT,model='deepseek-v4-pro',thinking_disabled=False,timeout=30,max_tokens=1000,json_mode=mode!='json_false')
    evidence=ModelCallEvidence(tmp_path,'CALL-synthetic-'+mode,'RUN-synthetic','ingest',config['model'],config['base_url'],None,'synthetic')
    async def run():
        task=asyncio.create_task(dsh_intake.request(config,inputs(),'synthetic',evidence,on_progress=progress.append))
        if mode in ('cancelled','final_cancel'):
            end=time.monotonic()+10
            while (not sent or mode=='final_cancel' and sent[-1].get('tool_choice')!='none') and time.monotonic()<end:await asyncio.sleep(.01)
            assert sent
            task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
            return
        if mode in ('repeat_incomplete','final_reentry','early_final','literal_dsml'):
            code={'repeat_incomplete':'DSH_NO_READING_PROGRESS','final_reentry':'DSH_FINALIZATION_REENTRY','early_final':'DSH_READING_UNVERIFIED','literal_dsml':'SCHEMA_INVALID'}[mode]
            with pytest.raises(Problem) as failure:await task
            assert failure.value.code==code
        else:
            value,meta=await task
            assert value=={'summary':'合成候选'}
            assert meta['outbound_http_requests']==len(sent)
    asyncio.run(run())
    assert evidence.value['outbound_http_requests']==len(sent)
    assert len(evidence.value['dsh_http_requests'])==len(sent)
    if mode in ('normal','json_false','literal_dsml','delayed_final','final_reentry','final_cancel'):
        assert sum(body.get('tool_choice')=='none' for body in sent)==1
        final=sent[-1]
        assert final['tools'] and any(m.get('role')=='tool' for m in final['messages'])
        assert final['max_tokens']==1000 and final['thinking']=={'type':'enabled'}
        import hashlib
        from app.core import digest
        assert all('response_format' not in body for body in incoming), 'SDK must not already inject JSON mode'
        assert all('response_format' not in body for body in sent[:-1])
        assert final.get('response_format')==({'type':'json_object'} if config['json_mode'] else None)
        phase_audit=evidence.value['dsh_read_phase']['finalization']
        assert phase_audit['json_mode']==config['json_mode']
        assert final['messages'][-1]['role']=='system' and '读取阶段已结束' in final['messages'][-1]['content']
        hint=final['messages'][-1]['content']
        assert '单一 JSON 对象' in hint
        assert phase_audit['phase_instruction_sha256']==hashlib.sha256(hint.encode()).hexdigest()
        assert phase_audit['previous_messages_hash']==digest(final['messages'][:-1])
        # Incoming SDK history and tool definitions are preserved exactly.
        original_final=next(body for body in incoming if digest(body['messages'])==phase_audit['previous_messages_hash'])
        assert original_final['messages']==final['messages'][:-1] and original_final['tools']==final['tools']
        expected=copy.deepcopy(original_final);expected['tool_choice']='none'
        expected['messages'].append(final['messages'][-1])
        if config['json_mode']:expected['response_format']={'type':'json_object'}
        assert final==expected
        assert all(row['sha256'] in hint for row in phase_audit['snapshot_identity'])
        assert evidence.value['dsh_http_requests'][-1]['finalization']==phase_audit

        assert evidence.value['dsh_read_phase']['complete'] is True
        assert all(row['complete'] for row in evidence.value['dsh_reading_coverage']['files'])
        assert '资料与输出格式已读完，正在生成需求初稿' in progress
    if mode=='literal_dsml':
        assert evidence.value['final_output']==dsml
        assert len(sent)==len(queue)+1
        assert sum(e['type']=='tool/call' for e in evidence.value['dsh_events'])==len(queue)
    if mode in ('early_final','repeat_incomplete','cancelled'):
        assert not any(body.get('tool_choice')=='none' or body.get('response_format') for body in sent)
    if mode=='repeat_incomplete':
        assert len(sent)==3 and not evidence.value['dsh_read_phase']['complete']
    if mode=='early_final':
        assert len(sent)==1 and evidence.value['final_output']=='{"summary":"合成候选"}'
        assert not evidence.value['dsh_read_phase']['complete']
    assert 'evidence_save_failed' not in evidence.limitations


def test_serialized_save_prevents_same_temp_race(tmp_path,monkeypatch):
    from app import model_evidence
    evidence=ModelCallEvidence(tmp_path,'CALL-save-lock','RUN-synthetic','ingest','synthetic','local',None,'')
    original=model_evidence.os.replace
    lock=threading.Lock();active=0;maximum=0;calls=0
    def delayed_replace(source,destination):
        nonlocal active,maximum,calls
        with lock:active+=1;maximum=max(maximum,active);calls+=1
        try:
            time.sleep(.03)
            return original(source,destination)
        finally:
            with lock:active-=1
    monkeypatch.setattr(model_evidence.os,'replace',delayed_replace)
    threads=[threading.Thread(target=evidence.save) for _ in range(4)]
    for thread in threads:thread.start()
    for thread in threads:thread.join(timeout=3)
    assert calls==4 and maximum==1 and all(not thread.is_alive() for thread in threads)
    assert not evidence.limitations
    assert json.loads(evidence.path.read_text('utf-8'))['call_id']=='CALL-save-lock'

def test_progress_projects_only_exact_running_task(tmp_path,monkeypatch):
    from app.workflow import Workflow
    from tests.test_business_draft_workflow import BusinessProvider
    from tests.test_business_draft import packet,draft_row
    from tests.test_requirement_recommendation import project
    async def scenario():
        store,p=project(tmp_path);workflow=Workflow(store,BusinessProvider([]));raw=packet(draft_row())
        observed=[]
        async def request(config,messages,key,evidence,**callbacks):
            for phase in ['正在读取本轮资料与已答信息','资料与输出格式已读完，正在生成需求初稿']:
                callbacks['on_progress'](phase)
                observed.append(store.get_record(p['id'],'owned','user_task')['message'])
                assert store.get_record(p['id'],'other','user_task')['message']=='untouched'
            evidence.response(json.dumps(raw,ensure_ascii=False),finish_reason='stop')
            return raw,{'finish_reason':'stop','usage':None,'outbound_http_requests':0}
        monkeypatch.setattr(dsh_intake,'request',request)
        run=workflow.start(p['id'],p['revision'],'ingest','',config_override=dict(workflow.config('ingest'),intake_engine='deepseek-harness'))
        store.record(p['id'],'user_task',{'status':'running','run_ids':[run['id']],'message':'initial'},rid='owned')
        store.record(p['id'],'user_task',{'status':'running','run_ids':['another'],'message':'untouched'},rid='other')
        store.update_run(p['id'],run['id'],user_task_id='owned')
        await workflow.tasks[run['id']]
        assert observed==['正在读取本轮资料与已答信息','资料与输出格式已读完，正在生成需求初稿']
    asyncio.run(scenario())
