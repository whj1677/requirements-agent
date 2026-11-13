"""Installed LangMem + synthetic model outputs; no real-model semantic claim."""
import asyncio
import copy
import json

import pytest

from app import project_memory as memory
from app.core import Problem, digest, dumps, execution_hash
from app.store import Store
from tests.test_domain_context import source


@pytest.fixture(autouse=True)
def no_external_http(monkeypatch):
    import httpx
    import requests
    def blocked(*args, **kwargs):
        raise AssertionError('Offline regression cannot send HTTP')
    monkeypatch.setattr(httpx.Client, 'send', blocked)
    monkeypatch.setattr(httpx.AsyncClient, 'send', blocked)
    monkeypatch.setattr(requests.Session, 'send', blocked)


def fixture(tmp_path):
    store = Store(tmp_path)
    p = store.create('合成储能云平台告警认领')
    p['sources'] = [source('S1', '当前仅值班人员可认领；不新增远程控制。'),
                    source('S2', '历史方案允许所有人认领；是否仍有效待确认。')]
    p['project_memory_mode'] = 'langmem'
    return store, p, [e for s in p['sources'] for e in s['excerpts']]


def callback_for(excerpts, calls):
    async def callback(messages, phase, protocol, parent):
        calls.append(copy.deepcopy(messages))
        assert protocol == memory.VERSION
        assert 'not_confirmed' not in messages[0]['content']  # schema is extraction-only
        return dict(tool_calls=[dict(name='EvidenceMemory', args=dict(source_id=e['source_id'],
            excerpt_id=e['id'])) for e in excerpts]), 'CALL-TEST'
    return callback


def test_real_langmem_extract_manage_search_cache_and_isolation(tmp_path):
    async def scenario():
        store, p, excerpts = fixture(tmp_path)
        before = copy.deepcopy(p)
        calls, traces = [], []
        trace = await memory.prepare(store,p,excerpts,'interview',callback_for(excerpts,calls),{},traces.append)
        assert len(calls) == 1 and len(trace['entries']) == 2
        assert trace['extraction_call_ids'] == ['CALL-TEST'] and not trace['consumed']
        assert trace['status'] == 'ready' and trace['library_version'] == '0.0.30'
        assert {r['statement'] for r in trace['entries']} == {e['text'] for e in excerpts}
        assert all(r['confirmation']=='not_confirmed' for r in trace['entries'])
        assert p == before and len(store.records(p['id'],'project_memory')) == 1
        # Reload SQLite through a fresh Store, not an in-memory cache.
        trace2 = await memory.prepare(Store(tmp_path),p,excerpts,'prd',callback_for(excerpts,calls),{},traces.append)
        assert trace2['reused'] and trace2['snapshot_id'] == trace['snapshot_id'] and len(calls)==1
        assert trace2['memory_ids'] == trace['memory_ids']
        other=copy.deepcopy(p);other['id']=store.create('另一个项目')['id']
        trace3=await memory.prepare(store,other,excerpts,'interview',callback_for(excerpts,calls),{},traces.append)
        assert not trace3['reused'] and trace3['snapshot_id']!=trace['snapshot_id'] and len(calls)==2
    asyncio.run(scenario())


def test_changed_source_answer_and_exclusion_do_not_recall_old_snapshot(tmp_path):
    async def scenario():
        store,p,excerpts=fixture(tmp_path);calls=[]
        first=await memory.prepare(store,p,excerpts,'interview',callback_for(excerpts,calls),{},lambda _:None)
        p['sources'][0]['excerpts'][0]['text']='本期仅站长可认领；不新增远程控制。'
        second=await memory.prepare(store,p,excerpts,'prd',callback_for(excerpts,calls),{},lambda _:None)
        assert first['input_hash']!=second['input_hash'] and not second['reused']
        p['sources'][1]['excluded']=True
        third=await memory.prepare(store,p,excerpts,'prd',callback_for(excerpts[:1],calls),{},lambda _:None)
        assert len(third['entries'])==1 and third['entries'][0]['source_refs'][0]['source_id']=='S1'
        p['questions']=[dict(id='Q1',status='answered',answer='按已保存回答执行')]
        fourth=await memory.prepare(store,p,excerpts,'prd',callback_for(excerpts[:1],calls),{},lambda _:None)
        assert fourth['input_hash']!=third['input_hash']
        assert len(store.records(p['id'],'project_memory'))==4
    asyncio.run(scenario())


@pytest.mark.parametrize('bad', ['foreign', 'invented', 'schema'])
def test_bad_extraction_fails_without_snapshot_or_hidden_retry(tmp_path,bad):
    async def scenario():
        store,p,excerpts=fixture(tmp_path);calls=[];traces=[]
        async def callback(*args):
            calls.append(1)
            row=dict(source_id='S1',excerpt_id='EX-S1')
            if bad=='foreign':row['source_id']='OTHER'
            if bad=='invented':row['quote']='允许远程控制'
            if bad=='schema':row.pop('excerpt_id')
            return {'tool_calls':[{'name':'EvidenceMemory','args':row}]},'CALL-X'
        with pytest.raises(Problem):
            await memory.prepare(store,p,excerpts,'ingest',callback,{},traces.append)
        assert calls==[1] and not store.records(p['id'],'project_memory')
        assert traces[-1]['status']=='failed' and not traces[-1]['consumed']
    asyncio.run(scenario())


def test_no_ai_summary_as_fact_and_off_by_default(tmp_path):
    store,p,excerpts=fixture(tmp_path)
    p.pop('project_memory_mode')
    assert memory.plan(p,['interview']) is None and not memory.enabled(p,'prd')
    before=execution_hash(p);p['project_memory_mode']='langmem'
    assert execution_hash(p)!=before
    p['product_context_proposal']={'current_state':'AI猜测已有远程控制'}
    p['sources'][1]['origin']='model'
    payload=memory._inputs(p,excerpts)
    assert 'AI猜测' not in dumps(payload) and len(payload['sources'])==1


def test_attach_is_in_actual_dsh_json_and_prd_projection_remains_reversible(tmp_path):
    from app.context_projection import project, materialize
    store,p,excerpts=fixture(tmp_path)
    context={'excerpts':excerpts,'A_normative':{'requirement':[]}}
    projected=project(context,[])
    messages=[dict(role='system',content='trusted'),dict(role='user',content=[dict(type='text',text=dumps(projected))])]
    trace=dict(snapshot_id='SNAP',input_hash='hash',entries=[dict(id='M1',statement=excerpts[0]['text'],
        source_refs=[dict(source_id='S1',excerpt_id='EX-S1')])])
    attached=memory.attach(messages,trace,'prd',excerpts)
    actual=materialize(json.loads(attached[1]['content'][0]['text']))
    assert actual['project_memory']['entries'][0]['source_refs']==[dict(source_id='S1',excerpt_id='EX-S1')]
    assert memory.present_in(attached,trace) and not memory.present_in(messages,trace)
    assert actual['A_normative']==context['A_normative']
    actual.pop('project_memory');assert actual==context
    assert json.loads(messages[1]['content'][0]['text'])==projected


def test_project_setting_existing_auth_revision_and_busy_guards(tmp_path):
    from tests.test_runtime import client
    c,app=client(tmp_path)
    p=c.post('/api/projects',json={'name':'隔离记忆设置'}).json();pid=p['id']
    assert 'project_memory_mode' not in p
    path=f'/api/projects/{pid}'
    changed=c.patch(path,json=dict(expected_revision=0,project_memory_mode='langmem'))
    assert changed.status_code==200 and changed.json()['revision']==1
    assert c.patch(path,json=dict(expected_revision=0,project_memory_mode='off')).status_code==409
    store=app.state.store
    rid=store.record(pid,'run',dict(status='running'))
    assert c.patch(path,json=dict(expected_revision=1,project_memory_mode='off')).status_code==409
    store.update_record(pid,rid,'run',status='completed')
    snapshot=store.record(pid,'project_memory',{'kept':True})
    assert c.patch(path,json=dict(expected_revision=1,project_memory_mode='off')).status_code==200
    assert store.get_record(pid,snapshot,'project_memory')['kept'] is True
    assert store.setting('model') is None


@pytest.mark.parametrize('limit', [1, 4])
def test_workflow_provider_evidence_budget_and_consumption(tmp_path, limit):
    from app.workflow import Workflow
    from tests.test_business_draft_workflow import BusinessProvider
    from tests.test_business_draft import packet, draft_row
    from tests.test_requirement_recommendation import project

    class MemoryProvider(BusinessProvider):
        async def request(self,config,messages,evidence=None):
            if evidence:evidence.before_send(config)
            header=json.loads(messages[0]['content'].split('可信任务头：',1)[1])
            if header.get('response_protocol')==memory.VERSION:
                self.received.append(copy.deepcopy(messages))
                payload={'sources':[dict(source_id=e['source_id'],excerpt_id=e['id'],text=e['text']) for e in self.excerpts]}
                result={'tool_calls':[dict(name='EvidenceMemory',args=dict(source_id=e['source_id'],
                    excerpt_id=e['excerpt_id'])) for e in payload['sources']]}
                meta=dict(usage={'prompt_tokens':1,'completion_tokens':1},finish_reason='stop')
                if evidence:evidence.response(dumps(result),http_status=200,finish_reason='stop',usage=meta['usage'])
                return result,meta
            return await super().request(config,messages,evidence)

    async def scenario():
        store,p=project(tmp_path)
        with store.edit(p['id'],p['revision'],'启用隔离测试记忆') as (edited,_):
            edited['project_memory_mode']='langmem'
        p=store.get(p['id']);provider=MemoryProvider([packet(draft_row())]);provider.excerpts=[e for s in p['sources'] for e in s['excerpts']];w=Workflow(store,provider)
        config=w.config('ingest');config['max_calls']=limit
        run=w.start(p['id'],p['revision'],'ingest','',config_override=config)
        await w.tasks[run['id']]
        result=store.get_record(p['id'],run['id'],'run')
        if limit==1:
            assert result['status']=='paused_budget' and result['error']=='BUDGET_EXHAUSTED'
            assert result['calls']==len(provider.received)==1
            assert result['memory_trace']['consumed'] is False
            assert store.get(p['id'])['sources']==p['sources']
            return
        assert result['status'] in ('succeeded','partial'),result
        assert result['calls']==4 and len(result['attempts'])==4
        trace=result['memory_trace']
        assert trace['consumed'] and trace['consumed_stages']==['ingest']
        assert trace['extraction_call_ids']==[result['attempts'][0]['call_id']]
        assert result['attempts'][0]['protocol']==memory.VERSION
        context=json.loads(provider.received[1][1]['content'][0]['text'])
        assert context['project_memory']['snapshot_id']==trace['snapshot_id']
        assert {a.get('protocol') for a in result['attempts']} >= {memory.VERSION,'intake-grounding-coverage-1','intake-grounding-challenge-2'}
    asyncio.run(scenario())


def test_cancelled_extraction_keeps_trace_and_no_snapshot(tmp_path):
    async def scenario():
        store,p,excerpts=fixture(tmp_path);traces=[]
        async def cancelled(*args):raise asyncio.CancelledError()
        with pytest.raises(Problem,match='取消'):
            await memory.prepare(store,p,excerpts,'interview',cancelled,{},traces.append)
        assert traces[-1]['status']=='failed' and not traces[-1]['consumed']
        assert store.records(p['id'],'project_memory')==[]
    asyncio.run(scenario())


def test_attach_with_real_registered_deltas_preserves_every_original_value():
    from tests.test_context_projection import fixture as projection_fixture
    from app.context_projection import materialize, project
    original=projection_fixture();encoded=project(original,['REQ-now'])
    assert 'context_projection' in encoded
    messages=[dict(role='system',content='trusted'),dict(role='user',content=[dict(type='text',text=dumps(encoded))])]
    trace=dict(snapshot_id='SNAP',input_hash='hash',entries=[])
    attached=memory.attach(messages,trace,'prd')
    restored=materialize(json.loads(attached[1]['content'][0]['text']))
    assert restored.pop('project_memory')['snapshot_id']=='SNAP'
    assert restored==original


def test_adapter_cannot_retry_after_request_exception():
    from app.langmem_provider import WorkflowMemoryModel
    async def scenario():
        calls=[]
        async def failed(*args):
            calls.append(1)
            raise Problem('TIMEOUT','合成超时')
        model=WorkflowMemoryModel(request_callback=failed,call_ids=[])
        bound=model.bind_tools([memory.EvidenceMemory])
        with pytest.raises(Problem,match='超时'):
            await bound.ainvoke('synthetic')
        with pytest.raises(Problem,match='未自动追加'):
            await bound.ainvoke('synthetic')
        assert len(calls)==1
    asyncio.run(scenario())


@pytest.mark.parametrize(('original','truncated'), [
    ('不允许跨项目查询。','允许跨项目查询。'),
    ('仅限本站点值班人员查询。','本站点值班人员查询。'),
    ('仅当权限检查通过时，才允许查询。','允许查询。'),
    ('  不允许跨项目查询。\n保留原权限。\n','不允许跨项目查询。\n保留原权限。'),
])
def test_whole_excerpt_selection_rejects_any_truncation(tmp_path,original,truncated):
    async def scenario():
        store,p,excerpts=fixture(tmp_path)
        p['sources']=[source('S1',original)];excerpts=p['sources'][0]['excerpts']
        calls=[]
        async def invalid(*args):
            calls.append(1)
            return {'tool_calls':[dict(name='EvidenceMemory',args=dict(source_id='S1',excerpt_id='EX-S1',quote=truncated))]},'CALL-truncated'
        with pytest.raises(Problem):
            await memory.prepare(store,p,excerpts,'ingest',invalid,{},lambda _:None)
        assert calls==[1] and not store.records(p['id'],'project_memory')
        valid=await memory.prepare(store,p,excerpts,'ingest',callback_for(excerpts,[]),{},lambda _:None)
        assert valid['entries'][0]['statement']==original
        assert valid['entries'][0]['source_context']==original
    asyncio.run(scenario())


@pytest.mark.parametrize('fault',['cross_source','ambiguous','cross_project'])
def test_selection_cannot_cross_source_or_ambiguous_excerpt(tmp_path,fault):
    async def scenario():
        store,p,excerpts=fixture(tmp_path)
        selected=dict(source_id='S1',excerpt_id='EX-S2' if fault=='cross_source' else 'EX-S1')
        if fault=='ambiguous':excerpts.append(copy.deepcopy(excerpts[0]))
        if fault=='cross_project':
            other=store.create('另一项目来源')
            with store.edit(other['id'],other['revision'],'合成来源') as (current,db):
                current['sources']=[source('S-FOREIGN','不允许删除历史。')]
            selected=dict(source_id='S-FOREIGN',excerpt_id='EX-S-FOREIGN')
        async def callback(*args):
            return {'tool_calls':[dict(name='EvidenceMemory',args=selected)]},'CALL-pair'
        with pytest.raises(Problem):
            await memory.prepare(store,p,excerpts,'ingest',callback,{},lambda _:None)
        assert not store.records(p['id'],'project_memory')
    asyncio.run(scenario())


def test_selection_policy_does_not_reuse_quote_copy_cache(tmp_path,monkeypatch):
    async def scenario():
        store,p,excerpts=fixture(tmp_path);calls=[]
        monkeypatch.setattr(memory,'EXTRACTION_POLICY','whole-excerpt-1')
        old=await memory.prepare(store,p,excerpts,'interview',callback_for(excerpts,calls),{},lambda _:None)
        saved=copy.deepcopy(store.records(p['id'],'project_memory'))
        monkeypatch.setattr(memory,'EXTRACTION_POLICY','excerpt-selection-2')
        new=await memory.prepare(store,p,excerpts,'prd',callback_for(excerpts,calls),{},lambda _:None)
        assert not new['reused'] and old['input_hash']!=new['input_hash'] and len(calls)==2
        assert store.records(p['id'],'project_memory')[0]==saved[0]
    asyncio.run(scenario())


def test_extra_newline_quote_is_rejected_not_silently_normalized(tmp_path):
    async def scenario():
        store,p,excerpts=fixture(tmp_path)
        original='  不允许跨项目查询。\n仅当权限检查通过时才允许查看。\n'
        p['sources']=[source('S1',original)];excerpts=p['sources'][0]['excerpts'];calls=[]
        async def old_quote(*args):
            calls.append(1)
            return {'tool_calls':[dict(name='EvidenceMemory',args=dict(source_id='S1',excerpt_id='EX-S1',quote=original+'\n'))]},'CALL-old'
        with pytest.raises(Problem):
            await memory.prepare(store,p,excerpts,'ingest',old_quote,{},lambda _:None)
        assert calls==[1] and not store.records(p['id'],'project_memory')
        trace=await memory.prepare(store,p,excerpts,'ingest',callback_for(excerpts,[]),{},lambda _:None)
        entry=trace['entries'][0]
        assert entry['statement']==entry['source_context']==original
        assert entry['source_version']==digest(memory._inputs(p,excerpts)['sources'][0])
        assert entry['authority']=='source_statement' and entry['confirmation']=='not_confirmed'
        assert entry['confirmation_identity'] is None and not trace['consumed']
    asyncio.run(scenario())
