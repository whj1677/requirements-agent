"""Frozen/suspended synthetic Providers; no HTTP or semantic-quality claims."""
import asyncio
import copy
import json

import pytest

from app.core import digest, dumps
from app.provider import Provider
from app.request_budget import initial_budget
from app.workflow import Workflow
from tests.test_requirement_recommendation import project


class SuspendedProvider(Provider):
    def __init__(self,excerpts,empty=False):
        super().__init__();self.excerpts=excerpts;self.empty=empty;self.received=[]
        self.dispatched=asyncio.Event();self.release=asyncio.Event()
    def key(self,config):return 'offline-fixture'
    async def request(self,config,messages,evidence=None):
        before=digest(messages);self.received.append(copy.deepcopy(messages))
        evidence.before_send(config)
        assert digest(messages)==before
        header=json.loads(messages[0]['content'].split('可信任务头：',1)[1])
        if header.get('response_protocol')=='project-langmem-1':
            value={'tool_calls':[] if self.empty else [dict(name='EvidenceMemory',args=dict(
                source_id=e['source_id'],excerpt_id=e['id'])) for e in self.excerpts]}
        else:
            self.dispatched.set();await self.release.wait()
            value=dict(schema_version='1.1',stage='interview',summary='合成进度检查',proposals=[],questions=[],
                findings=[],used_source_refs=[],limitations=[],result={'can_organize':True})
        meta=dict(finish_reason='stop',usage={'prompt_tokens':1,'completion_tokens':1})
        evidence.response(dumps(value),http_status=200,finish_reason='stop',usage=meta['usage'])
        return value,meta


@pytest.mark.parametrize('empty,cancel',[(False,False),(True,False),(False,True)])
def test_primary_dispatch_replaces_extraction_phase_while_provider_is_pending(tmp_path,empty,cancel):
    async def scenario():
        store,p=project(tmp_path)
        with store.edit(p['id'],p['revision'],'合成启用记忆') as (current,_):current['project_memory_mode']='langmem'
        p=store.get(p['id']);provider=SuspendedProvider([e for s in p['sources'] for e in s['excerpts']],empty)
        workflow=Workflow(store,provider);config=workflow.config('interview');config['max_calls']=2
        tid=store.record(p['id'],'user_task',dict(status='running',calls=0,action='interview',run_ids=[],message='准备',
            authorized_configs={'interview':{k:v for k,v in config.items() if k!='proxy'}},max_calls=2,request_budget=initial_budget(2)))
        run=workflow.start(p['id'],p['revision'],'interview','合成消息',user_task_id=tid,config_override=config)
        job=workflow.tasks[run['id']]
        store.update_record(p['id'],tid,'user_task',run_ids=[run['id']])
        await asyncio.wait_for(provider.dispatched.wait(),5)
        current=store.get_record(p['id'],tid,'user_task');saved=store.get_record(p['id'],run['id'],'run')
        assert current['message']=='正在处理本轮需求（本步骤第 2 次模型请求）'
        assert '提取' not in current['message'] and '找到' not in current['message']
        assert current['request_budget']['used']==saved['calls']==len(provider.received)==2
        assert saved['status']=='running' and saved['memory_trace']['status']=='ready'
        assert bool(saved['memory_trace']['entries']) is not empty
        assert digest(provider.received[1])==saved['input_hash']
        if cancel:
            store.update_record(p['id'],tid,'user_task',status='cancelled',message='用户已取消')
            workflow.cancel(p['id'],run['id'])
            workflow._task_progress(p['id'],run['id'],tid,'错误晚到进度')
        else:provider.release.set()
        await job
        result=store.get_record(p['id'],run['id'],'run')
        assert result['status']==('cancelled' if cancel else 'succeeded'),result
        assert result['calls']==len(provider.received)==2
        if cancel:assert store.get_record(p['id'],tid,'user_task')['message']=='用户已取消'
    asyncio.run(scenario())


def test_exhausted_memory_only_budget_never_claims_primary_dispatch(tmp_path):
    async def scenario():
        store,p=project(tmp_path)
        with store.edit(p['id'],p['revision'],'合成启用记忆') as (current,_):current['project_memory_mode']='langmem'
        p=store.get(p['id']);provider=SuspendedProvider([e for s in p['sources'] for e in s['excerpts']])
        workflow=Workflow(store,provider);config=workflow.config('interview');config['max_calls']=1
        tid=store.record(p['id'],'user_task',dict(status='running',calls=0,action='interview',run_ids=[],message='准备',
            authorized_configs={'interview':{k:v for k,v in config.items() if k!='proxy'}},max_calls=1,request_budget=initial_budget(1)))
        run=workflow.start(p['id'],p['revision'],'interview','合成消息',user_task_id=tid,config_override=config)
        job=workflow.tasks[run['id']];store.update_record(p['id'],tid,'user_task',run_ids=[run['id']]);await job
        result=store.get_record(p['id'],run['id'],'run')
        assert result['status']=='paused_budget' and result['calls']==1
        assert not provider.dispatched.is_set()
        assert '提取可追溯记忆' in store.get_record(p['id'],tid,'user_task')['message']
        assert '正在处理本轮需求' not in store.get_record(p['id'],tid,'user_task')['message']
    asyncio.run(scenario())


@pytest.mark.parametrize('invalid',['terminal_task','unbound_run','other_project','terminal_run','reverse_binding'])
def test_shared_progress_writer_preserves_unrelated_and_terminal_records(tmp_path,invalid):
    store,p=project(tmp_path);workflow=Workflow(store,None)
    rid=store.record(p['id'],'run',dict(status='running'))
    tid=store.record(p['id'],'user_task',dict(status='running',run_ids=[rid],message='原消息'))
    store.update_record(p['id'],rid,'run',user_task_id=tid if invalid!='reverse_binding' else 'OTHER')
    if invalid=='terminal_task':store.update_record(p['id'],tid,'user_task',status='succeeded')
    if invalid=='unbound_run':store.update_record(p['id'],tid,'user_task',run_ids=[])
    if invalid=='terminal_run':store.update_record(p['id'],rid,'run',status='cancelled')
    before=store.get_record(p['id'],tid,'user_task')
    pid=store.create('另一项目')['id'] if invalid=='other_project' else p['id']
    workflow._task_progress(pid,rid,tid,'晚到消息')
    assert store.get_record(p['id'],tid,'user_task')==before


def test_existing_dsh_progress_updates_only_its_bound_running_task(tmp_path,monkeypatch):
    from app import dsh_intake
    from app.core import Problem
    async def scenario():
        store,p=project(tmp_path);provider=SuspendedProvider([]);workflow=Workflow(store,provider)
        config=dict(workflow.config('ingest'),intake_engine='deepseek-harness')
        tid=store.record(p['id'],'user_task',dict(status='running',calls=0,run_ids=[],message='准备',
            authorized_configs={'ingest':{k:v for k,v in config.items() if k!='proxy'}},max_calls=None,request_budget=initial_budget(None)))
        async def request(config,messages,key,evidence,**callbacks):
            callbacks['on_progress']('DSH 正在读取本轮资料')
            assert store.get_record(p['id'],tid,'user_task')['message']=='DSH 正在读取本轮资料'
            store.update_record(p['id'],tid,'user_task',status='cancelled',message='用户已取消')
            callbacks['on_progress']('晚到 DSH 阶段')
            assert store.get_record(p['id'],tid,'user_task')['message']=='用户已取消'
            raise Problem('CANCELLED','合成结束')
        monkeypatch.setattr(dsh_intake,'request',request)
        run=workflow.start(p['id'],p['revision'],'ingest','',user_task_id=tid,config_override=config)
        job=workflow.tasks[run['id']];store.update_record(p['id'],tid,'user_task',run_ids=[run['id']]);await job
        assert store.get_record(p['id'],run['id'],'run')['status']=='cancelled'
        assert provider.received==[]
    asyncio.run(scenario())
