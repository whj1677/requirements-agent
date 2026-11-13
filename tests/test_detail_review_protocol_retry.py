"""Real Provider serialization with synthetic HTTP only; no semantic oracle."""
import asyncio
import copy
import json
import httpx
from jsonschema import Draft202012Validator
import pytest
from app.provider import Provider,origin,DEFAULT
from app.workflow import Workflow
from app.business_draft import PROTOCOL
from app.sources import save_source
from tests.provider_transport import reply
from tests.test_requirement_recommendation import project,target
from tests.test_detail_semantic_workflow import generated_detail
from tests.test_business_draft import draft_row,packet
from tests.test_detail_semantics import response,model_response,correction_response


@pytest.mark.parametrize('mode,limit,calls,status',[
    ('fixed',6,6,'passed'),('human_fixed',6,6,'passed'),('fixed',5,5,'pending'),('fixed',3,3,'pending'),
    ('twice_invalid',6,4,'pending'),('http_error',6,3,'pending'),('old_plan',6,3,'pending'),('final_invalid',7,6,'pending')])
def test_same_candidate_protocol_retry_has_exact_budget_and_no_http_replay(tmp_path,monkeypatch,mode,limit,calls,status):
    sent=[];review_inputs=[];review_outputs=[];real_client=httpx.AsyncClient
    def respond(request):
        body=json.loads(request.content);messages=body.get('messages',body.get('input'))
        sent.append(copy.deepcopy(messages))
        header=json.loads(messages[0]['content'].split('可信任务头：',1)[1]);payload=json.loads(messages[1]['content'][0]['text'])
        if header.get('grounding_protocol'):
            value=dict(candidate_hash=payload['candidate_hash'],input_hash=payload['input_hash'],
                checked_field_ids=[f['id'] for f in payload['fields']],findings=[])
        elif header.get('response_protocol')==PROTOCOL:
            row=draft_row();row['detail_contract']=generated_detail()
            row['detail_contract']['fields'][0]['evidence']['required']=dict(status='source',sources=[dict(source='S1',locator='L1')])
            value=packet(row)
        elif payload.get('rows') and 'required_checks' in payload['rows'][0]:
            review_inputs.append(copy.deepcopy(payload))
            if mode=='http_error':return httpx.Response(400,json={'error':{'message':'synthetic HTTP failure'}})
            expanded=response(payload)
            if len(review_inputs)<=2:
                check=next(c for c in expanded['rows'][0]['checks'] if c['dimension']=='field_conditions')
                prop=next(p for p in payload['rows'][0]['property_refs'].values() if p['path']=='detail_contract.fields.F1.required')
                check.update(verdict='unclear',candidate_quotes=[dict(path=prop['path'],quote=prop['value'])],
                    affected_paths=[prop['path']],correction='按已有资料限定必填时机。')
            value=model_response(payload,expanded)
            if mode=='human_fixed':
                human=next(q for q in payload['quote_pool'] if q['kind']=='human')
                value['rows'][0]['checks'][0]['human_quote_keys']=['Q-unsaved' if len(review_inputs)==1 else human['key']]
            elif len(review_inputs)==1 or mode=='twice_invalid' or mode=='final_invalid' and len(review_inputs)==3:
                claim=payload['rows'][0]['source_claims'][0];own=claim['source_refs'][0]
                if payload['version']=='detail-source-review-3':
                    alternate=next(q for q in payload['quote_pool'] if q['kind']=='source' and (q['material']['source_id'],q['material']['excerpt_id'])!=(own['source_id'],own['excerpt_id']))
                    wrong=alternate['key']
                else:
                    alternate=next(s for s in payload['sources'] if (s['source_id'],s['excerpt_id'])!=(own['source_id'],own['excerpt_id']))
                    value['source_quotes'].append(dict(key='Qwrong',source_id=alternate['source_id'],excerpt_id=alternate['excerpt_id'],quote=alternate['text']))
                    wrong='Qwrong'
                value['rows'][0]['source_checks'][0]['source_quote_keys']=[wrong]
            review_outputs.append(copy.deepcopy(value))
        else:
            detail=copy.deepcopy(payload['rows'][0]['detail_contract']);detail['fields'][0]['required']='填写时必填'
            value=correction_response(payload,detail)
        return reply(request,json.dumps(value,ensure_ascii=False))
    monkeypatch.setattr('app.provider.httpx.AsyncClient',lambda **kw:real_client(**kw,transport=httpx.MockTransport(respond)))
    async def run():
        store,p=project(tmp_path)
        source=save_source(store,'独立合成资料.txt','另一份真实输入，仅用于引用错配反例。'.encode(),'goal')
        with store.edit(p['id'],p['revision'],'synthetic distinct source') as (draft,_):
            draft['sources'].append(source);draft['grants'][origin(DEFAULT)]['source_ids'].append(source['id'])
            draft['items'][0]['source_refs'].append(dict(source_id=source['id'],excerpt_id=source['excerpts'][0]['id']))
        p=store.get(p['id']);provider=Provider(env_path=tmp_path/'absent.env');provider.keys[origin(DEFAULT)]='synthetic-protocol-key'
        workflow=Workflow(store,provider);config=dict(workflow.config('clarify'),max_calls=limit)
        # Frozen legacy plans retain whole-review, run-wide retry semantics.
        config['detail_correction_policy']='two-corrections-seven-run-calls-1'
        config.pop('detail_review_repair_protocol',None)
        config.pop('detail_property_reference_encoding',None)
        config.pop('detail_unknown_scope_protocol',None)
        config.pop('detail_default_scope_protocol',None)
        if mode=='old_plan':
            config.pop('detail_review_protocol_retries');config.pop('detail_review_protocol_version');config.pop('detail_correction_protocol_version')
        task=workflow.start(p['id'],p['revision'],'clarify','当前操作原话；未提交draft.answers包含Q-unsaved，仅供本轮参照。',discussion_target=target(),config_override=config)
        await workflow.tasks[task['id']]
        saved=store.get_record(p['id'],task['id'],'run')
        assert saved['calls']==saved['request_budget']['used']==len(sent)==calls
        assert saved['business_draft_review']['detail_semantic_reviews']['R1']['status']==status
        records=[json.loads((store.folder/'evidence/model-calls'/(a['call_id']+'.json')).read_text('utf-8')) for a in saved['attempts']]
        assert records[0]['final_output']
        if mode!='http_error':
            assert json.loads(records[2]['final_output'])==review_outputs[0]
            assert records[2]['validation_result']=='DETAIL_REVIEW_INVALID'
            assert records[2]['semantic_protocol_error']['code']=='DETAIL_REVIEW_INVALID'
        if len(review_inputs)>1:
            assert review_inputs[0]==review_inputs[1] and sent[2][0]==sent[3][0]
            diagnostic=json.loads(sent[3][-1]['content'])
            assert diagnostic['previous_review']==review_outputs[0]
            if mode=='human_fixed':
                # v3 rejects unknown quote keys in the sent schema before quote resolution.
                header=json.loads(sent[2][0]['content'].split('可信任务头：',1)[1])
                errors=list(Draft202012Validator(header['schema']).iter_errors(review_outputs[0]))
                assert len(errors)==1
                assert list(errors[0].absolute_path)==['rows',0,'checks',0,'human_quote_keys',0]
                assert errors[0].validator=='enum' and errors[0].instance=='Q-unsaved'
                human_keys=[q['key'] for q in review_inputs[0]['quote_pool'] if q['kind']=='human']
                assert errors[0].validator_value==human_keys and 'Q-unsaved' not in human_keys
                assert diagnostic['validator_error']['code']=='DETAIL_REVIEW_INVALID'
            else:
                assert '原绑定=' in diagnostic['validator_error']['message']
            assert records[2]['authorization']['effective_config']==records[3]['authorization']['effective_config']
        if status=='passed':
            assert len(review_inputs)==3
            audit=records[3]['quote_reference_resolution']['R1']
            assert audit['mode']=='frozen_quote_pool_key'
            assert 'source_quotes' not in json.loads(records[3]['final_output'])
            if mode=='human_fixed':
                selected=[q for q in audit['selected_quotes'] if q['kind']=='human']
                assert selected and all(q['material']['kind']=='current_user_words' for q in selected)
                assert all(q['material']['statement_id']=='current-user-message' for q in selected)
            assert review_inputs[-1]['rows'][0]['candidate_hash']!=review_inputs[0]['rows'][0]['candidate_hash']
            assert review_inputs[-1]['rows'][0]['required_checks']==review_inputs[0]['rows'][0]['required_checks']
        else:assert all(not x.get('detail_contract') for x in saved['response']['proposals'])
        assert store.get(p['id'])['items'][:len(p['items'])]==p['items']
    asyncio.run(run())
