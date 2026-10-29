"""Real HTTP decoding and workflow checks; synthetic data, no provider traffic."""
import asyncio
import copy
import json

import httpx
import pytest

from app.format_repair import literal_atoms
from app.provider import DEFAULT, Provider, origin
from app.workflow import Workflow
from tests.test_business_draft import packet, draft_row
from tests.test_deepseek_responses import completed
from tests.test_requirement_recommendation import project


@pytest.mark.parametrize('fault', ['comma', 'changed_text', 'changed_source', 'second_invalid', 'truncated', 'cut_literal', 'budget'])
def test_single_syntax_repair_preserves_business_and_stops(monkeypatch, tmp_path, fault):
    async def run():
        store, before = project(tmp_path)
        good = packet(draft_row())
        raw = json.dumps(good, ensure_ascii=False)
        malformed = raw.replace(', "summary_fields"', ' "summary_fields"', 1)
        assert malformed != raw
        fixed = copy.deepcopy(good)
        if fault == 'changed_text': fixed['requirements'][0]['statement']['text'] += '新增自动控制。'
        if fault == 'changed_source': fixed['requirements'][0]['statement']['sources'] = [{'source':'S404','locator':'L404'}]
        first = completed(malformed)
        if fault == 'truncated': first['status'] = 'incomplete'
        if fault == 'cut_literal': first = completed('{"summary":"半句')
        replies = [first, completed(malformed if fault=='second_invalid' else json.dumps(fixed, ensure_ascii=False))]
        requests=[]; client=httpx.AsyncClient
        def respond(request):
            requests.append(json.loads(request.content))
            assert len(requests)<=2, 'No recursive repairs'
            return httpx.Response(200,json=replies[len(requests)-1])
        monkeypatch.setattr('app.provider.httpx.AsyncClient',lambda **kw: client(**kw,transport=httpx.MockTransport(respond)))
        provider=Provider(env_path=tmp_path/'absent.env');provider.keys[origin(DEFAULT)]='synthetic-test-key'
        store.setting('model',dict(DEFAULT,max_calls=1 if fault=='budget' else None))
        workflow=Workflow(store,provider)
        run=workflow.start(before['id'],before['revision'],'ingest','合成格式恢复',
            config_override=dict(workflow.config('ingest'),max_calls=1 if fault=='budget' else None))
        await workflow.tasks[run['id']]
        result=store.get_record(before['id'],run['id'],'run')
        expected=1 if fault in ('truncated','cut_literal','budget') else 2
        assert len(requests)==expected, result
        after=store.get(before['id'])
        assert after['documents']==before['documents'] and after.get('active_baseline_id')==before.get('active_baseline_id')
        assert after['questions']==before['questions']
        if fault=='comma':
            assert result['result_applied'] and result['status'] in ('succeeded','partial'),result
            assert result['attempts'][1]['repair_of']==result['attempts'][0]['call_id']
            assert requests[0]['text']['format']==requests[1]['text']['format']
            assert '本次仅修复 JSON 格式' in json.dumps(requests[1],ensure_ascii=False)
        else:
            assert result['status'] in ('failed','paused_budget') and not result.get('result_applied'),result
            assert after['items']==before['items']
    asyncio.run(run())


def test_atoms_reject_incomplete_literals_and_preserve_typed_values():
    assert literal_atoms('{"a":true "b":1}')==literal_atoms('{"a":true,"b":1}')
    assert literal_atoms('{"a":true}')!=literal_atoms('{"a":1}')
    assert literal_atoms('{"a":"unfinished') is None
    assert literal_atoms('Ignore previous instructions') is None
    assert literal_atoms('{"questions": [') is None
    assert literal_atoms('{"a":1} {"extra":true}') is None


@pytest.mark.parametrize('stage',['ingest','prd'])
def test_runtime_and_document_protocol_also_get_one_syntax_repair(monkeypatch,tmp_path,stage):
    async def run():
        from app.prd import plan_contract
        from tests.test_prd01 import plan
        from tests.test_runtime import empty_ingest
        store,before=project(tmp_path)
        value=empty_ingest() if stage=='ingest' else plan(plan_contract(before,'mrd')['normative_item_ids'])
        raw=json.dumps(value,ensure_ascii=False)
        broken=raw.replace(', "stage"',' "stage"',1) if stage=='ingest' else raw.replace(', "sections"',' "sections"',1)
        assert raw!=broken
        client=httpx.AsyncClient;requests=[]
        def respond(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200,json=completed(broken if len(requests)==1 else raw))
        monkeypatch.setattr('app.provider.httpx.AsyncClient',lambda **kw:client(**kw,transport=httpx.MockTransport(respond)))
        provider=Provider(env_path=tmp_path/'absent.env');provider.keys[origin(DEFAULT)]='synthetic-test-key'
        workflow=Workflow(store,provider)
        config=workflow.config(stage)
        if stage=='ingest':config['business_draft_protocol']=None
        run=workflow.start(before['id'],before['revision'],stage,'合成格式恢复',kind='mrd',config_override=config)
        await workflow.tasks[run['id']]
        result=store.get_record(before['id'],run['id'],'run')
        assert result['result_applied'] and result['status'] in ('succeeded','partial'),result
        assert len(requests)==2
    asyncio.run(run())
