import asyncio
import copy
import json
import pytest

from fastapi.testclient import TestClient
from app.main import create_app
from app.core import Problem
from app.contracts import gate, validate_document
from app.provider import Provider, DEFAULT, assemble
from app.store import Store


def setup(tmp_path):
    app = create_app(tmp_path, access_token='test-token', env_path=tmp_path/'.env')
    client = TestClient(app)
    login = client.post('/api/session/login', json={'key': 'test-token'})
    client.headers.update({'Origin': 'http://testserver', 'X-CSRF-Token': login.json()['csrf']})
    p = client.post('/api/projects', json={'name': '离线方案测试'}).json()
    with app.state.store.edit(p['id'], 0, '合成候选') as (draft, _):
        draft['items'] = [dict(id=f'ITEM-{n}', kind='assumption', title=f'假设 {n}', statement=f'合成原文 {n}', source_refs=[{'source_id':'SRC-1','excerpt_id':'EX-1'}], selection_status='candidate', epistemic_status='proposed', applies_to='to_be') for n in (1, 2)]
        draft['questions'] = [dict(id='Q-1', question='边界如何？', why='合成关键未知', options=[], related_refs=[], source_refs=[{'source_id':'SRC-1','excerpt_id':'EX-1'}], status='open', answer=None, blocking=True)]
        draft['options'] = [dict(id=f'OPT-{n}', name=f'方向 {n}', selection_status='candidate', proposed_item_refs=['ITEM-1','ITEM-2']) for n in (1, 2)]
        draft['sources'] = [dict(id='SRC-1', title='合成材料', excerpts=[{'id':'EX-1','text':'合成来源原文'}], excluded=False, image_mime=None)]
    return client, app.state.store, p['id']


def test_direction_keeps_items_questions_and_gate(tmp_path):
    c, store, pid = setup(tmp_path)
    root = f'/api/projects/{pid}'
    before = store.get(pid)
    with c:
        r = c.post(root+'/options/OPT-1/direction', json={'expected_revision': before['revision'], 'direction_status':'selected'})
        assert r.status_code == 200, r.text
        after = store.get(pid)
        assert [i['selection_status'] for i in after['items']] == ['candidate','candidate']
        assert after['questions'] == before['questions']
        assert after['options'][0]['selection_status'] == 'candidate'
        assert after['options'][0]['direction_status'] == 'selected'
        assert after['active_baseline_id'] is None
        assert gate(after)
        context = assemble(after, 'brainstorm', '', dict(DEFAULT), tmp_path)[0]
        assert 'direction_status' in json.dumps(context, ensure_ascii=False)


def test_explicit_partial_acceptance_and_shared_refs(tmp_path):
    c, store, pid = setup(tmp_path)
    root = f'/api/projects/{pid}'
    with c:
        p = store.get(pid)
        assert c.post(root+'/options/OPT-1/items', json={'expected_revision':p['revision'], 'item_ids':[]}).status_code == 422
        assert c.post(root+'/options/OPT-1/items', json={'expected_revision':p['revision'], 'item_ids':['ITEM-1']}).status_code == 200
        p = store.get(pid)
        assert [i['selection_status'] for i in p['items']] == ['selected','candidate']
        assert c.post(root+'/options/OPT-2/direction', json={'expected_revision':p['revision'], 'direction_status':'selected'}).status_code == 200
        p = store.get(pid)
        assert c.post(root+'/options/OPT-1/direction', json={'expected_revision':p['revision'], 'direction_status':'rejected'}).status_code == 200
        p = store.get(pid)
        assert [i['selection_status'] for i in p['items']] == ['selected','candidate']
        assert p['options'][0]['direction_status'] == 'rejected'
        assert p['options'][1]['direction_status'] == 'selected'


def test_legacy_option_record_is_not_reinterpreted(tmp_path):
    c, store, pid = setup(tmp_path)
    p = store.get(pid)
    with c:
        response=c.post(f'/api/projects/{pid}/options/OPT-1', json={'expected_revision':p['revision'], 'selection_status':'selected'})
        assert response.status_code == 409 and response.json()['code']=='LEGACY_OPTION_WRITE'
    p = store.get(pid)
    assert 'direction_status' not in p['options'][0]
    assert [i['selection_status'] for i in p['items']] == ['candidate','candidate']
    with store.edit(pid,p['revision'],'合成历史整包快照',bump=False) as (old, _):
        old['options'][0]['selection_status']='selected'
        old['items'][0]['selection_status']='selected'
    p=store.get(pid)
    assert p['options'][0]['selection_status']=='selected' and 'direction_status' not in p['options'][0]
    assert p['items'][0]['selection_status']=='selected'


def test_unknowns_remain_in_draft_context_and_block_confirmation(tmp_path):
    from tests.helpers import prepared
    store = Store(tmp_path)
    p = prepared(store)
    p['questions'][0]['blocking'] = True
    context = assemble(p, 'prd', '', dict(DEFAULT), tmp_path)[0]
    assert '相接边界如何处理？' in json.dumps(context, ensure_ascii=False)
    assert validate_document(p['documents']['prd']['content'], p, 'prd') is None
    assert '相接边界如何处理？' in gate(p)


@pytest.mark.parametrize('failure,expected', [
    ('invalid_json','SCHEMA_INVALID'), ('bad_reference','REFERENCE_INVALID'),
    ('empty','OUTPUT_EMPTY'), ('truncated','OUTPUT_TRUNCATED')])
def test_provider_preserves_failed_raw_before_repair_and_redacts_key(tmp_path, monkeypatch, failure, expected):
    import httpx
    from app.workflow import Workflow
    from app.provider import origin
    from tests.test_runtime import empty_ingest
    invalid_ref = empty_ingest()
    invalid_ref['used_source_refs']=[{'source_id':'SRC-absent','excerpt_id':'EX-absent'}]
    first = {'invalid_json':'{bad JSON sk-synthetic-other',
             'bad_reference':json.dumps(invalid_ref), 'empty':'',
             'truncated':json.dumps(empty_ingest())}[failure]
    second = json.dumps(empty_ingest(), ensure_ascii=False)
    outputs = iter([(first, 'length' if failure=='truncated' else 'stop'), (second,'stop')])
    requests=[]
    class FakeAsyncClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs):
            requests.append(copy.deepcopy(kwargs['json']))
            content, reason = next(outputs)
            return httpx.Response(200, json={'model':'offline','choices':[{'message':{'content':content, 'reasoning_content':'hidden synthetic secret'},'finish_reason':reason}], 'usage':{'prompt_tokens':2,'completion_tokens':3}})
    monkeypatch.setattr(httpx, 'AsyncClient', FakeAsyncClient)
    async def run():
        store = Store(tmp_path); p = store.create('离线请求证据')
        with store.edit(p['id'], 0, '合成授权', bump=False) as (draft, _):
            draft['grants'][origin(DEFAULT)] = {'source_ids':[]}
        provider=Provider(tmp_path/'.env'); provider.keys[origin(DEFAULT)]='sk-synthetic-secret'
        store.setting('model', dict(DEFAULT, max_calls=2))
        workflow=Workflow(store, provider)
        result=workflow.start(p['id'],0,'ingest','合成测试')
        await workflow.tasks[result['id']]
        return store.get_record(p['id'], result['id'])
    result=asyncio.run(run())
    assert result['status']=='succeeded' and result['calls']==2, result
    assert len(result['attempts'])==2
    assert result['attempts'][0]['error']==expected
    if failure=='invalid_json':
        repair=requests[1]['messages'][-1]['content']
        assert '第 1 行' in repair and '第 2 列' in repair and '字符偏移 1' in repair
    files=list((tmp_path/'evidence'/'model-calls').glob('*.json'))
    assert len(files)==2
    payloads=[json.loads(f.read_text('utf-8')) for f in files]
    assert any(x['validation_result']==expected and (x['final_output'] == first or failure=='invalid_json' and '{bad JSON' in x['final_output'] or failure=='empty' and x['final_output']=='') for x in payloads)
    assert any(x['repair_of'] for x in payloads)
    assert all(x['run_id']==result['id'] for x in payloads)
    assert 'sk-synthetic-secret' not in ''.join(f.read_text('utf-8') for f in files)
    assert 'sk-synthetic-other' not in ''.join(f.read_text('utf-8') for f in files)
    assert 'hidden synthetic secret' not in ''.join(f.read_text('utf-8') for f in files)


def test_no_response_records_absence_without_invented_output(tmp_path, monkeypatch):
    import httpx
    from app.workflow import Workflow
    from app.provider import origin
    class NoResponseClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs):
            raise httpx.ConnectError('synthetic network failure')
    monkeypatch.setattr(httpx, 'AsyncClient', NoResponseClient)
    async def run():
        store=Store(tmp_path); p=store.create('无响应合成验证')
        with store.edit(p['id'], 0, '合成授权', bump=False) as (draft, _):
            draft['grants'][origin(DEFAULT)]={'source_ids':[]}
        provider=Provider(tmp_path/'.env'); provider.keys[origin(DEFAULT)]='sk-synthetic-secret'
        store.setting('model', dict(DEFAULT, max_calls=1))
        workflow=Workflow(store, provider)
        result=workflow.start(p['id'],0,'ingest','合成测试')
        await workflow.tasks[result['id']]
        return store.get_record(p['id'],result['id'])
    result=asyncio.run(run())
    assert result['calls']==1 and len(result['attempts'])==1
    evidence=json.loads(next((tmp_path/'evidence'/'model-calls').glob('*.json')).read_text('utf-8'))
    assert evidence['validation_result']=='NETWORK_ERROR'
    assert evidence['response_received'] is False and evidence['final_output'] is None


def test_echoed_key_is_rejected_and_redacted_from_evidence(tmp_path, monkeypatch):
    import httpx
    from app.model_evidence import ModelCallEvidence
    from app.provider import origin
    class EchoClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs):
            return httpx.Response(200, json={'model':'offline','choices':[{'message':{'content':'{"secret":"sk-synthetic-secret"}','reasoning_content':'hidden synthetic secret'},'finish_reason':'stop'}]})
    monkeypatch.setattr(httpx, 'AsyncClient', EchoClient)
    provider=Provider(tmp_path/'.env')
    provider.keys[origin(DEFAULT)]='sk-synthetic-secret'
    evidence=ModelCallEvidence(tmp_path,'CALL-test','RUN-test','ingest',DEFAULT['model'],origin(DEFAULT),None,provider.key(DEFAULT))
    async def run():
        with pytest.raises(Problem) as error:
            await provider.request(dict(DEFAULT), [], evidence=evidence)
        assert error.value.code=='SECURITY_BLOCKED'
    asyncio.run(run())
    saved=evidence.path.read_text('utf-8')
    assert 'sk-synthetic-secret' not in saved and 'hidden synthetic secret' not in saved
    assert 'final_output_redacted' in saved
