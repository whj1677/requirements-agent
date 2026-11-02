"""Actual Provider HTTP serialization against an offline JSON-mode boundary."""
import asyncio
import copy
import json
import httpx
import pytest
from app import detail_semantics
from app.provider import Provider
from app.workflow import Workflow
from app.business_draft import PROTOCOL
from tests.test_business_draft import draft_row,packet
from tests.test_detail_semantic_workflow import generated_detail
from tests.test_detail_semantics import response,model_response,correction_response
from tests.test_requirement_recommendation import project,target
from tests.provider_transport import reply


@pytest.mark.parametrize('sparse_existing',[False,True])
@pytest.mark.parametrize('legacy_transport',[False,True])
@pytest.mark.parametrize('legacy,limit,expected,calls',[(False,4,'passed',4),(False,3,'pending',3),(True,4,'failed',3)])
def test_correction_json_mode_precondition_budget_and_complete_rereview(tmp_path,monkeypatch,legacy,limit,expected,calls,sparse_existing,legacy_transport):
    requests=[];reviews=[];real_client=httpx.AsyncClient
    if legacy:
        original=detail_semantics.correction_messages
        def old_prompt(*args,**kwargs):
            schema,messages=original(*args,**kwargs)
            messages[0]['content']=messages[0]['content'].replace('只返回符合可信任务头schema的JSON对象，不输出Markdown或额外说明。','')
            assert 'json' not in json.dumps(messages,ensure_ascii=False).lower()
            return schema,messages
        monkeypatch.setattr(detail_semantics,'correction_messages',old_prompt)
    def respond(request):
        body=json.loads(request.content);messages=body.get('messages',body.get('input'))
        header=json.loads(messages[0]['content'].split('可信任务头：',1)[1])
        payload=json.loads(messages[1]['content'][0]['text'])
        requests.append(dict(path=request.url.path,body=body))
        if body.get('response_format')=={'type':'json_object'} and 'json' not in json.dumps(messages,ensure_ascii=False).lower():
            return httpx.Response(400,json={'error':{'message':'messages must contain json for json_object'}})
        if header.get('response_protocol')==PROTOCOL:
            row=draft_row();row['detail_contract']=generated_detail()
            if sparse_existing:
                row['detail_contract']['fields'][0]['evidence']={}
                row['detail_contract']['flow']['edges'][0]['evidence']['condition']['status']='recommendation'
            value=packet(row)
        elif payload.get('rows') and 'required_checks' in payload['rows'][0]:
            reviews.append(copy.deepcopy(payload));value=response(payload)
            if len(reviews)==1:
                check=next(c for c in value['rows'][0]['checks'] if c['dimension']=='field_conditions')
                prop=next(p for p in payload['rows'][0]['property_refs'].values() if p['path']=='detail_contract.fields.F1.required')
                check.update(verdict='unclear',affected_paths=[prop['path']],candidate_quotes=[dict(path=prop['path'],quote=prop['value'])],correction='明确填写时机。')
            value=model_response(payload,value)
        else:
            detail=copy.deepcopy(payload['rows'][0]['detail_contract']);detail['fields'][0]['required']='填写时必填'
            value=correction_response(payload,detail)
        return reply(request,json.dumps(value,ensure_ascii=False))
    monkeypatch.setattr('app.provider.httpx.AsyncClient',lambda **kwargs:real_client(**kwargs,transport=httpx.MockTransport(respond)))
    async def run():
        store,p=project(tmp_path);provider=Provider(env_path=tmp_path/'absent.env')
        provider.keys['https://api.deepseek.com']='synthetic-json-mode-key'
        workflow=Workflow(store,provider)
        config=dict(workflow.config('clarify'),max_calls=limit)
        # The old correction prompt and a frozen old review transport are
        # independent compatibility cases. Only this marker selects transport.
        if legacy_transport:config.pop('detail_review_transport_protocol',None)
        task=workflow.start(p['id'],p['revision'],'clarify','',discussion_target=target(),
            config_override=config)
        await workflow.tasks[task['id']]
        saved=store.get_record(p['id'],task['id'],'run')
        assert saved['calls']==saved['request_budget']['used']==len(requests)==calls
        assert saved['business_draft_review']['detail_semantic_reviews']['R1']['status']==expected
        for review_request in [requests[1]]+([requests[3]] if expected=='passed' else []):
            body=review_request['body'];messages=body.get('messages',body.get('input'))
            header=json.loads(messages[0]['content'].split('可信任务头：',1)[1])
            if legacy_transport:
                assert review_request['path'].endswith('/chat/completions')
                assert body['thinking']=={'type':'enabled'} and body['reasoning_effort']=='high'
                assert body['response_format']=={'type':'json_object'} and 'transport_protocol' not in header
            else:
                assert review_request['path'].endswith('/responses') and body['reasoning']=={'effort':'high'}
                assert body['text']['format']['schema']==header['schema']
                assert header['transport_protocol']=='responses-json-schema-v1'
                assert header['strict_json_protocol']=='detail-review-strict-json-1'
        assert requests[0]['body']['reasoning']=={'effort':'none'}
        correction=requests[2]
        assert correction['path'].endswith('/chat/completions') and correction['body']['response_format']=={'type':'json_object'}
        assert ('json' in json.dumps(correction['body']['messages'],ensure_ascii=False).lower()) is (not legacy)
        evidence=[json.loads((store.folder/'evidence/model-calls'/(a['call_id']+'.json')).read_text('utf-8')) for a in saved['attempts']]
        assert json.loads(evidence[0]['final_output'])['requirements'][0]['detail_contract']['fields'][0]['required']=='必填'
        if legacy:
            assert evidence[2]['http_status']==400 and evidence[2]['validation_result']=='PARAMETER_UNSUPPORTED'
        else:
            assert evidence[2]['http_status']==200
            if sparse_existing:
                assert {a['phase'] for a in evidence[2]['detail_correction_normalizations']}=={'original_candidate','correction_output'}
                correction_raw=json.loads(evidence[2]['final_output'])['rows'][0]
                assert 'detail_contract' not in correction_raw and correction_raw['updates']==[dict(path='detail_contract.fields.F1.required',value='填写时必填')]
                assert evidence[2]['detail_correction_patch_assembly'][0]['assigned_paths']==['detail_contract.fields.F1.required']
        if expected=='passed':
            assert len(reviews)==2
            assert reviews[0]['rows'][0]['required_checks']==reviews[1]['rows'][0]['required_checks']
            assert reviews[0]['rows'][0]['candidate_hash']!=reviews[1]['rows'][0]['candidate_hash']
            assert any(i.get('detail_contract') for i in saved['response']['proposals'])
        else:
            assert len(reviews)==1 and all(not i.get('detail_contract') for i in saved['response']['proposals'])
        assert store.get(p['id'])['items'][:len(p['items'])]==p['items']
    asyncio.run(run())
