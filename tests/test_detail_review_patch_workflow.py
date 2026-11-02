"""Finite entry-patch workflow using a synthetic provider and isolated store."""
import asyncio
import copy
import json
import pytest
from app.core import Problem,dumps
from app import detail_review_repair as repair
from app.workflow import Workflow
from app.business_draft import PROTOCOL
from tests.test_runtime import FakeProvider
from tests.test_detail_semantic_workflow import generated_detail
from tests.test_detail_semantics import model_response,response,correction_response
from tests.test_business_draft import draft_row,packet
from tests.test_requirement_recommendation import project,target


class PatchProvider(FakeProvider):
    business_draft_protocol=PROTOCOL
    def __init__(self,mode):
        super().__init__([]);self.mode=mode;self.review_inputs=[];self.good=[];self.patch_count=0;self.configs=[];self.on_patch=None
    async def request(self,config,messages,evidence=None):
        evidence.before_send(config);self.received.append(copy.deepcopy(messages));self.configs.append(copy.deepcopy(config))
        header=json.loads(messages[0]['content'].split('可信任务头：',1)[1]);payload=json.loads(messages[1]['content'][0]['text'])
        if header.get('grounding_protocol'):
            result=dict(candidate_hash=payload['candidate_hash'],input_hash=payload['input_hash'],
                checked_field_ids=[f['id'] for f in payload['fields']],findings=[])
        elif header.get('response_protocol')==PROTOCOL:
            row=draft_row();row['detail_contract']=generated_detail()
            row['detail_contract']['fields'][0]['evidence']['required']=dict(status='source',sources=[dict(source='S1',locator='L1')])
            result=packet(row)
        elif header.get('response_protocol')==repair.PROTOCOL:
            self.patch_count+=1
            if self.mode=='http_error':raise Problem('PROVIDER_HTTP_ERROR','Synthetic failure, no replay')
            if self.on_patch:self.on_patch()
            rp=payload['repair_packet'];good=self.good[-1]
            result=dict(protocol=repair.PROTOCOL,plan_hash=rp['plan_hash'],review_hash=rp['review_hash'],replacements=[
                dict(slot_id=repair.slot_id(s),original_hash=s['original_hash'],replacement=copy.deepcopy(good['rows'][s['row_index']][s['group']][s['index']])) for s in rp['slots']])
            if self.mode=='bad_patch':result['replacements'][0]['replacement']['traces'][0]['edge_ids']=['E1','E3','E2']
        elif payload.get('rows') and 'required_checks' in payload['rows'][0]:
            self.review_inputs.append(copy.deepcopy(payload));expanded=response(payload)
            if self.mode in ('correct','low_after_correction') and len(self.review_inputs)==1:
                check=next(c for c in expanded['rows'][0]['checks'] if c['dimension']=='field_conditions')
                path='detail_contract.fields.F1.required';prop=next(p for p in payload['rows'][0]['property_refs'].values() if p['path']==path)
                check.update(verdict='unclear',candidate_quotes=[dict(path=path,quote=prop['value'])],affected_paths=[path],correction='按现有资料限定必填时机。')
            result=model_response(payload,expanded);self.good.append(copy.deepcopy(result))
            # Current runs allow one protocol repair; the fresh corrected candidate
            # still receives a complete review, with a valid response this time.
            if len(self.review_inputs)==1 or self.mode!='correct':
                result['rows'][0]['source_checks'][0]['source_quote_keys']=[]
                next(c for c in result['rows'][0]['checks'] if c['traces'])['traces'][0]['edge_ids']=['E1','E3','E2']
        else:
            detail=copy.deepcopy(payload['rows'][0]['detail_contract']);detail['fields'][0]['required']='填写时必填'
            result=correction_response(payload,detail)
        evidence.response(dumps(result),http_status=200,finish_reason='stop',usage={'total_tokens':100})
        return result,dict(finish_reason='stop',usage={'total_tokens':100},response_model='OFFLINE_PROTOCOL_FIXTURE')


@pytest.mark.parametrize('mode,limit,calls,status',[
    ('pass',8,4,'passed'),('correct',8,6,'passed'),('bad_patch',8,4,'pending'),
    ('http_error',8,4,'pending'),('pass',3,3,'pending'),('low_after_correction',6,6,'pending')])
def test_entry_patch_is_finite_and_budgeted_with_fresh_candidate_review(tmp_path,mode,limit,calls,status):
    async def run():
        store,p=project(tmp_path);provider=PatchProvider(mode);workflow=Workflow(store,provider)
        config=dict(workflow.config('clarify'),max_calls=limit)
        task=workflow.start(p['id'],p['revision'],'clarify','',discussion_target=target(),config_override=config)
        await workflow.tasks[task['id']]
        saved=store.get_record(p['id'],task['id'],'run')
        assert saved['calls']==saved['request_budget']['used']==len(provider.received)==calls
        assert saved['business_draft_review']['detail_semantic_reviews']['R1']['status']==status
        assert provider.patch_count<=len(provider.review_inputs)<=2
        records=[json.loads((store.folder/'evidence/model-calls'/(a['call_id']+'.json')).read_text('utf-8')) for a in saved['attempts']]
        if len(provider.review_inputs)==2:
            first,second=provider.review_inputs
            assert first['rows'][0]['candidate_hash']!=second['rows'][0]['candidate_hash']
            assert first['rows'][0]['required_checks']==second['rows'][0]['required_checks']
            assert len(provider.good[0]['rows'][0]['checks'])==len(provider.good[1]['rows'][0]['checks'])
        assert records[2]['validation_result']=='DETAIL_REVIEW_INVALID'
        assert records[2]['review_entry_diagnostics']['slots']
        if mode in ('pass','correct') and calls>3:
            assert records[3]['review_entry_repair']['full_strict_validation']=='passed'
            assert records[3]['review_entry_repair']['preserved_slots']
            assert json.loads(records[3]['final_output'])['protocol']==repair.PROTOCOL
            assert records[3]['merged_detail_review']!=json.loads(records[3]['final_output'])
        if mode=='bad_patch':
            assert records[3]['review_entry_repair']['full_strict_validation']=='DETAIL_REVIEW_INVALID'
            assert 'merged_detail_review' in records[3]
        for c,m in zip(provider.configs,provider.received):
            if 'required_checks' in m[1]['content'][0]['text']:
                assert c['reasoning_effort']=='high' and c['max_tokens']==config['detail_review_options']['max_tokens']
        assert store.get(p['id'])['items'][:len(p['items'])]==p['items']
    asyncio.run(run())


def test_cancelled_patch_is_not_adopted_or_replayed(tmp_path):
    async def run():
        store,p=project(tmp_path);provider=PatchProvider('pass');workflow=Workflow(store,provider)
        task=workflow.start(p['id'],p['revision'],'clarify','',discussion_target=target(),config_override=dict(workflow.config('clarify'),max_calls=8))
        provider.on_patch=lambda:store.update_run(p['id'],task['id'],status='cancelled')
        await workflow.tasks[task['id']]
        saved=store.get_record(p['id'],task['id'],'run')
        assert saved['status']=='cancelled' and saved['calls']==4 and provider.patch_count==1
        assert not saved.get('result_applied')
        assert store.get(p['id'])['items']==p['items']
    asyncio.run(run())


def test_new_plan_announces_finite_protocol_without_raising_user_limit(tmp_path):
    from app.actions import Actions
    store,p=project(tmp_path);workflow=Workflow(store,PatchProvider('pass'));snapshots={}
    plan=Actions(store,workflow).plan(p['id'],dict(expected_revision=p['revision'],action='clarify',message='',document_type='prd',target=target(),max_calls=2),config_snapshot=snapshots)
    policy=plan['detail_review_entry_repair']['clarify']
    assert policy['detail_chain_requests_max']==7 and policy['max_repairs_per_candidate']==1 and policy['max_reviewed_candidates']==2
    assert plan['max_calls']==snapshots['clarify']['max_calls']==2


def test_real_provider_serializes_finite_patch_schema_and_high_review_options(tmp_path,monkeypatch):
    import httpx
    from app.provider import Provider,DEFAULT,origin
    from tests.provider_transport import reply
    fake=PatchProvider('correct');bodies=[];real_client=httpx.AsyncClient
    class NoEvidence:
        def before_send(self,*args):pass
        def response(self,*args,**kwargs):pass
    async def respond(request):
        body=json.loads(request.content);bodies.append(body)
        result,_=await fake.request({},body.get('messages',body.get('input')),NoEvidence())
        return reply(request,json.dumps(result,ensure_ascii=False))
    monkeypatch.setattr('app.provider.httpx.AsyncClient',lambda **kw:real_client(**kw,transport=httpx.MockTransport(respond)))
    async def run():
        store,p=project(tmp_path);provider=Provider(env_path=tmp_path/'absent.env');provider.keys[origin(DEFAULT)]='synthetic-key'
        workflow=Workflow(store,provider);config=dict(workflow.config('clarify'),max_calls=8)
        config.pop('detail_review_transport_protocol',None) # Frozen legacy Chat transport remains covered.
        task=workflow.start(p['id'],p['revision'],'clarify','',discussion_target=target(),config_override=config)
        await workflow.tasks[task['id']]
        saved=store.get_record(p['id'],task['id'],'run')
        assert saved['calls']==len(bodies)==6
        assert saved['business_draft_review']['detail_semantic_reviews']['R1']['status']=='passed'
        for i in (3,):
            messages=bodies[i].get('messages',bodies[i].get('input'))
            header=json.loads(messages[0]['content'].split('可信任务头：',1)[1])
            assert header['response_protocol']==repair.PROTOCOL
            replacements=header['schema']['properties']['replacements']
            assert replacements['minItems']==replacements['maxItems']==2
            assert len(replacements['items']['oneOf'])==2
            assert bodies[i]['reasoning_effort']=='high'
    asyncio.run(run())
