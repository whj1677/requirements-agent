"""Provider protocol simulation, not a semantic oracle or a live model test."""
import asyncio
import copy
import json
import pytest
from app.business_draft import PROTOCOL
from app.core import dumps,Problem
from app.workflow import Workflow
from tests.test_runtime import FakeProvider
from tests.test_business_draft import packet,draft_row
from tests.test_detail_business_draft import sample_detail
from tests.test_requirement_recommendation import project,target
from tests.test_detail_semantics import response,model_response,correction_response


@pytest.mark.parametrize('mode,limit,expected_calls,expected_status',[
    ('pass',7,4,'passed'),('finding',7,7,'failed'),('finding',4,4,'failed'),
    ('timeout',7,4,'pending'),('bad',7,5,'pending'),('cancel',7,4,None)])
def test_detail_independent_challenge_is_required_before_pass_and_reserves_full_recheck(tmp_path,mode,limit,expected_calls,expected_status):
    async def run():
        class Challenger(SemanticProvider):
            def __init__(self):super().__init__();self.challenge_count=0
            async def request(self,config,messages,evidence=None):
                header=json.loads(messages[0]['content'].split('可信任务头：',1)[1])
                if (not header.get('grounding_protocol') or
                    json.loads(messages[1]['content'][0]['text'])['fields'][0]['path'][0]=='requirements'):return await super().request(config,messages,evidence)
                evidence.before_send(config);self.received.append(copy.deepcopy(messages));self.challenge_count+=1
                payload=json.loads(messages[1]['content'][0]['text'])
                if mode=='timeout':raise Problem('TIMEOUT','offline challenge timeout')
                if mode=='cancel':raise asyncio.CancelledError()
                reply=dict(candidate_hash=payload['candidate_hash'],input_hash=payload['input_hash'],
                    checked_field_ids=[f['id'] for f in payload['fields']],findings=[])
                if mode=='bad':reply['candidate_hash']='other candidate'
                if mode=='finding':
                    field=next(f for f in payload['fields'] if f['path'][1]=='detail_contract.fields.F1.required')
                    reply['findings']=[dict(id=field['id'],quote=field['text'],reason='保留来源中必填的适用前提。',sources=[])]
                evidence.response(dumps(reply),http_status=200,finish_reason='stop',usage={'total_tokens':10})
                return reply,dict(finish_reason='stop',usage={'total_tokens':10})
        store,p=project(tmp_path);provider=Challenger();workflow=Workflow(store,provider)
        config=dict(workflow.config('clarify'),max_calls=limit)
        task=workflow.start(p['id'],p['revision'],'clarify','',discussion_target=dict(target(),detail_required=True),config_override=config)
        await workflow.tasks[task['id']]
        saved=store.get_record(p['id'],task['id'],'run')
        assert saved['calls']==len(provider.received)==expected_calls
        assert saved['request_budget']['used']==expected_calls
        if mode=='cancel':
            assert saved['status']=='cancelled' and not saved.get('result_applied')
        else:
            outcome=saved['business_draft_review']['detail_semantic_reviews']['R1']
            assert outcome['status']==expected_status
            if expected_status!='passed':assert not any(x.get('detail_contract') for x in saved['response']['proposals'])
            if mode=='finding':
                assert len(outcome['findings'])==1
                assert 'BUDGET_EXHAUSTED' in dumps(saved)
                assert provider.review_count==(2 if limit==7 else 1)
            records=[json.loads((store.folder/'evidence/model-calls'/(a['call_id']+'.json')).read_text('utf-8')) for a in saved['attempts']]
            assert records[2]['detail_source_challenge']['candidate_hash']
            assert records[3]['request_input'] and records[3]['actual_input_hash']
        assert store.get(p['id'])['items'][:len(p['items'])]==p['items']
    asyncio.run(run())


def generated_detail():
    detail=sample_detail()
    for node in detail['flow']['nodes']:node.setdefault('actor','云平台')
    from app.requirement_details import _objects
    for _,row in _objects(detail):
        row['evidence']={key:dict(status='proposal',reason='合成协议测试，不是已确认业务。') for key,value in row.items()
            if key!='id' and isinstance(value,str)}
    return detail


class SemanticProvider(FakeProvider):
    business_draft_protocol=PROTOCOL
    def __init__(self,mode='pass'):
        super().__init__([]);self.mode=mode;self.review_count=0
    async def request(self,config,messages,evidence=None):
        evidence.before_send(config)
        self.received.append(copy.deepcopy(messages))
        header=json.loads(messages[0]['content'].split('可信任务头：',1)[1])
        payload=json.loads(messages[1]['content'][0]['text'])
        if header.get('grounding_protocol'):
            result=dict(candidate_hash=payload['candidate_hash'],input_hash=payload['input_hash'],
                checked_field_ids=[f['id'] for f in payload['fields']],findings=[])
        elif header.get('response_protocol')==PROTOCOL:
            row=draft_row();row['detail_contract']=generated_detail();result=packet(row)
        elif 'required_checks' in str(payload.get('rows',[])):
            self.review_count+=1
            if self.mode=='timeout':raise Problem('TIMEOUT','合成延迟超时，已采集结果保留。')
            result=response(payload)
            if self.mode in ('correct','still_bad') and (self.review_count==1 or self.mode=='still_bad'):
                check=next(c for c in result['rows'][0]['checks'] if c['dimension']=='field_conditions')
                check.update(verdict='unclear',affected_paths=['detail_contract.fields.F1.required'],
                    candidate_quotes=[dict(path='detail_contract.fields.F1.required',quote=next(r['value'] for r in payload['rows'][0]['property_refs'].values() if r['path']=='detail_contract.fields.F1.required'))],
                    correction='按已有口径限定必填时机。')
            result=model_response(payload,result)
            if self.mode=='normalize':result['rows'][0]['checks'][0].pop('affected_keys')
        else:
            detail=copy.deepcopy(payload['rows'][0]['detail_contract']);detail['fields'][0]['required']='填写时必填'
            result=correction_response(payload,detail)
        evidence.response(dumps(result),http_status=200,finish_reason='stop',usage={'total_tokens':100})
        return result,dict(finish_reason='stop',usage={'total_tokens':100},response_model='OFFLINE_PROTOCOL_FIXTURE')


@pytest.mark.parametrize('base_timeout',[45,450])
@pytest.mark.parametrize('thinking',[False,True])
def test_source_review_timeout_policy_does_not_mutate_model_settings(tmp_path,base_timeout,thinking):
    from app.provider import DEFAULT
    store,_=project(tmp_path);settings=dict(DEFAULT,timeout=base_timeout,review_thinking=thinking)
    store.setting('model',settings);workflow=Workflow(store,SemanticProvider())
    config=workflow.config('clarify')
    assert config['timeout']==base_timeout
    assert config['detail_review_options']['timeout']==(max(base_timeout,300) if thinking else base_timeout)
    assert store.setting('model')==settings


@pytest.mark.parametrize('thinking',[True,False])
@pytest.mark.parametrize('generation_disabled',[True,False])
def test_review_uses_frozen_thinking_option_with_shared_budget(tmp_path,thinking,generation_disabled):
    async def run():
        from app.provider import DEFAULT
        from app.core import digest
        store,p=project(tmp_path)
        store.setting('model',dict(DEFAULT,thinking_disabled=generation_disabled,review_thinking=thinking))
        seen=[]
        class Capture(SemanticProvider):
            async def request(self,config,messages,evidence=None):
                seen.append(copy.deepcopy(config))
                if len(seen)==1:
                    # The setting changes after approval; this run must retain its snapshot.
                    store.setting('model',dict(DEFAULT,thinking_disabled=True,review_thinking=not thinking))
                return await super().request(config,messages,evidence)
        provider=Capture('correct');workflow=Workflow(store,provider)
        config=dict(workflow.config('clarify'),max_calls=5)
        assert config['detail_review_options']==dict(thinking_disabled=not thinking,max_tokens=65536,
            timeout=max(config['timeout'],300) if thinking else config['timeout'],**({'reasoning_effort':'high'} if thinking else {}))
        task=workflow.start(p['id'],p['revision'],'clarify','',discussion_target=target(),config_override=config)
        await workflow.tasks[task['id']]
        saved=store.get_record(p['id'],task['id'],'run')
        assert saved['calls']==saved['request_budget']['used']==len(seen)==5
        assert [c['thinking_disabled'] for c in seen]==[generation_disabled,generation_disabled,not thinking,generation_disabled,not thinking]
        assert [c.get('reasoning_effort') for c in seen]==[None,None,'high' if thinking else None,None,'high' if thinking else None]
        assert [c['timeout'] for c in seen]==[config['timeout'],config['timeout'],config['detail_review_options']['timeout'],
            config['timeout'],config['detail_review_options']['timeout']]
        assert saved['business_draft_review']['detail_semantic_reviews']['R1']['status']=='passed'
        for index in (1,2,3,4):
            attempt=saved['attempts'][index]
            record=json.loads((store.folder/'evidence/model-calls'/(attempt['call_id']+'.json')).read_text('utf-8'))
            effective=record['authorization']['effective_config']
            assert effective=={k:v for k,v in seen[index].items() if k!='proxy'}
            assert digest(effective)==attempt['effective_config_hash']==record['authorization']['effective_config_hash']
            assert digest(provider.received[index])==attempt['actual_input_hash']==record['actual_input_hash']
    asyncio.run(run())


def test_action_plan_freezes_review_options_and_rejects_changed_plan(tmp_path):
    from app.actions import Actions
    from app.provider import DEFAULT
    store,p=project(tmp_path);provider=SemanticProvider();workflow=Workflow(store,provider)
    store.setting('model',dict(DEFAULT,review_thinking=True))
    actions=Actions(store,workflow)
    body=dict(expected_revision=p['revision'],action='clarify',message='',document_type='prd',target=target(),max_calls=4)
    snapshots={};plan=actions.plan(p['id'],body,config_snapshot=snapshots)
    assert plan['detail_review_protocol_version']=={'clarify':3}
    assert plan['detail_recovery_guard']=={'clarify':True}
    assert plan['recovery_outcome_checks']=={'clarify':True}
    assert plan['detail_correction_protocol_version']=={'clarify':2}
    assert snapshots['clarify']['detail_review_protocol_version']==3
    assert plan['detail_review_options']=={'clarify':{'thinking_disabled':False,'reasoning_effort':'high','timeout':300,'max_tokens':65536}}
    assert snapshots['clarify']['detail_review_options']=={'thinking_disabled':False,'reasoning_effort':'high','timeout':300,'max_tokens':65536}
    store.setting('model',dict(DEFAULT,review_thinking=False))
    with pytest.raises(Problem) as caught:actions.start(p['id'],body,plan['plan_hash'],'changed-review-options')
    assert caught.value.code=='STALE_PLAN' and not provider.received


@pytest.mark.parametrize('fault',['without_role','changed_option','changed_model','changed_task_snapshot','changed_effort','changed_effort_policy','changed_timeout','changed_timeout_policy','changed_protocol','changed_recovery_guard','changed_correction_protocol','changed_review_policy','changed_recovery_outcomes'])
@pytest.mark.parametrize('effort',['low','high'])
def test_review_budget_rejects_unapproved_derived_config_before_reservation(tmp_path,fault,effort):
    from tests.test_request_cap import ledger_setup
    from app.request_budget import RequestBudget
    store,p,config,_,tid,rids=ledger_setup(tmp_path,4)
    config['detail_review_protocol_version']=3
    config['detail_recovery_guard']=True
    config['detail_correction_protocol_version']=2
    if effort=='high':config['detail_review_reasoning_effort']='high'
    config['recovery_outcome_checks']=True
    config['detail_review_options']={'thinking_disabled':False,'reasoning_effort':effort,'timeout':max(config['timeout'],300)}
    snapshot={k:v for k,v in config.items() if k!='proxy'}
    store.update_record(p['id'],tid,'user_task',authorized_configs={'clarify':copy.deepcopy(snapshot)})
    store.update_run(p['id'],rids[0],provider=copy.deepcopy(snapshot))
    actual=dict(config,thinking_disabled=False,reasoning_effort=effort,timeout=config['detail_review_options']['timeout']);role='detail_source_review'
    if fault=='without_role':role=None
    elif fault=='changed_option':actual['detail_review_options']={'thinking_disabled':True}
    elif fault=='changed_model':actual['model']='unapproved-model'
    elif fault=='changed_effort':actual['reasoning_effort']='high' if effort=='low' else 'low'
    elif fault=='changed_review_policy':actual['detail_review_reasoning_effort']='low' if effort=='high' else 'high'
    elif fault=='changed_recovery_outcomes':actual['recovery_outcome_checks']=False
    elif fault=='changed_effort_policy':actual['detail_review_options']={'thinking_disabled':False,'reasoning_effort':'max'}
    elif fault=='changed_correction_protocol':actual['detail_correction_protocol_version']=1
    elif fault=='changed_recovery_guard':actual['detail_recovery_guard']=False
    elif fault=='changed_protocol':actual['detail_review_protocol_version']=2
    elif fault=='changed_timeout':actual['timeout']+=1
    elif fault=='changed_timeout_policy':actual['detail_review_options']=dict(actual['detail_review_options'],timeout=999)
    else:
        changed=copy.deepcopy(snapshot);changed['detail_review_options']['thinking_disabled']=True
        store.update_record(p['id'],tid,'user_task',authorized_configs={'clarify':changed})
    budget=RequestBudget(store,p['id'],rids[0],4)
    with pytest.raises(Problem) as caught:budget.reserve('CALL-unapproved',actual,config_role=role)
    assert caught.value.code=='BUDGET_AUTH_CHANGED'
    assert store.get_record(p['id'],tid,'user_task')['request_budget']['used']==0


@pytest.mark.parametrize('legacy_snapshot',[False,True])
def test_deferred_review_timeout_uses_frozen_effective_limit_and_preserves_raw(tmp_path,monkeypatch,legacy_snapshot):
    async def run():
        store,p=project(tmp_path);timeouts=[];provider_configs=[]
        class Deferred(SemanticProvider):
            async def request(self,config,messages,evidence=None):
                provider_configs.append(copy.deepcopy(config))
                if len(self.received)>=2:
                    evidence.before_send(config);self.received.append(copy.deepcopy(messages))
                    await asyncio.Event().wait()
                return await super().request(config,messages,evidence)
        provider=Deferred();workflow=Workflow(store,provider)
        config=dict(workflow.config('clarify'),max_calls=3)
        if legacy_snapshot:config['detail_review_options'].pop('timeout')
        expected=config['timeout'] if legacy_snapshot else max(config['timeout'],300)
        real_wait=asyncio.wait_for
        async def controlled_wait(awaitable,timeout):
            timeouts.append(timeout)
            # Preserve the exact configured argument while simulating expiry in milliseconds.
            return await real_wait(awaitable,timeout=.01 if len(timeouts)==3 else timeout)
        monkeypatch.setattr('app.workflow.asyncio.wait_for',controlled_wait)
        task=workflow.start(p['id'],p['revision'],'clarify','',discussion_target=target(),config_override=config)
        await workflow.tasks[task['id']]
        saved=store.get_record(p['id'],task['id'],'run')
        assert timeouts==[config['timeout'],config['timeout'],expected]
        assert [c['timeout'] for c in provider_configs]==timeouts
        assert saved['calls']==saved['request_budget']['used']==len(provider.received)==3
        assert saved['business_draft_review']['detail_semantic_reviews']['R1']['status']=='pending'
        assert all(not r.get('detail_contract') for r in saved['response']['proposals'])
        records=[json.loads((store.folder/'evidence/model-calls'/(a['call_id']+'.json')).read_text('utf-8')) for a in saved['attempts']]
        assert records[2]['validation_result']=='TIMEOUT' and records[2]['ended_at']
        assert records[2]['authorization']['effective_config']['timeout']==expected
        assert json.loads(records[0]['final_output'])['requirements'][0]['detail_contract']
        assert store.get(p['id'])['items'][:len(p['items'])]==p['items']
    asyncio.run(run())


@pytest.mark.parametrize('mode,limit,calls,status',[
    ('pass',3,3,'passed'),('normalize',3,3,'passed'),('pass',2,2,'pending'),('correct',3,3,'failed'),
    ('correct',4,4,'pending'),('correct',5,5,'passed'),('still_bad',5,5,'failed'),('timeout',3,3,'pending')])
def test_shared_budget_evidence_and_exactly_one_correction(tmp_path,mode,limit,calls,status):
    async def run():
        store,p=project(tmp_path);before=copy.deepcopy(p)
        provider=SemanticProvider(mode);workflow=Workflow(store,provider)
        config=dict(workflow.config('clarify'),max_calls=limit)
        task=workflow.start(p['id'],p['revision'],'clarify','',discussion_target=target(),config_override=config)
        await workflow.tasks[task['id']]
        saved=store.get_record(p['id'],task['id'],'run')
        assert saved['status'] not in ('failed','cancelled'),saved
        assert saved['calls']==calls==len(provider.received)
        assert saved['request_budget']['used']==calls
        assert len(saved['attempts'])==calls
        assert [a['call'] for a in saved['attempts']]==list(range(1,calls+1))
        report=saved['business_draft_review'];outcome=report['detail_semantic_reviews']['R1']
        assert outcome['status']==status,outcome
        if status!='passed':
            assert report['status']=='partial' and report['field_rejected']
            assert all('detail_contract' not in r for r in saved['response']['proposals'])
        else:assert any('detail_contract' in r for r in saved['response']['proposals'])
        after=store.get(p['id'])
        assert after['sources']==before['sources'] and after['questions']==before['questions']
        assert after['items'][:len(before['items'])]==before['items']
        records=[json.loads((store.folder/'evidence/model-calls'/(a['call_id']+'.json')).read_text('utf-8')) for a in saved['attempts']]
        assert all(r['request_state']=='response_received' for r in records if mode!='timeout' or r==records[0])
        original=json.loads(records[0]['final_output'])
        assert original['requirements'][0]['detail_contract']['fields'][0]['required']=='必填'
        assert records[0]['detail_semantic_review']['outcomes']['R1']['status']==status
        if mode=='normalize':
            assert 'affected_keys' not in json.loads(records[2]['final_output'])['rows'][0]['checks'][0]
            assert records[2]['normalized_detail_review']['rows'][0]['checks'][0]['affected_keys']==[]
            assert records[2]['response_normalizations'][0]['rule']=='supported-empty-correction-scope-2'
        assert sum(a.get('protocol') in ('detail-source-correction-1','detail-property-correction-2') for a in saved['attempts'])<=1
    asyncio.run(run())


def test_cancellation_during_review_closes_auxiliary_evidence(tmp_path):
    async def run():
        store,p=project(tmp_path);started=asyncio.Event()
        class Blocking(SemanticProvider):
            async def request(self,config,messages,evidence=None):
                if self.received:
                    evidence.before_send(config);self.received.append(copy.deepcopy(messages))
                    started.set();await asyncio.Event().wait()
                return await super().request(config,messages,evidence)
        provider=Blocking();workflow=Workflow(store,provider)
        task=workflow.start(p['id'],p['revision'],'clarify','',discussion_target=target(),
            config_override=dict(workflow.config('clarify'),max_calls=4))
        await asyncio.wait_for(started.wait(),5)
        running=workflow.tasks[task['id']];workflow.cancel(p['id'],task['id']);await running
        saved=store.get_record(p['id'],task['id'],'run')
        assert saved['status']=='cancelled' and saved['calls']==saved['request_budget']['used']==2
        assert len(provider.received)==len(saved['attempts'])==2
        aux=json.loads((store.folder/'evidence/model-calls'/(saved['attempts'][1]['call_id']+'.json')).read_text('utf-8'))
        assert aux['validation_result']=='CANCELLED' and aux['ended_at']
        original=json.loads((store.folder/'evidence/model-calls'/(saved['attempts'][0]['call_id']+'.json')).read_text('utf-8'))
        assert original['final_output'] and store.get(p['id'])==p
    asyncio.run(run())


def test_running_parent_displays_auxiliary_phase_before_response_and_preserves_cancel(tmp_path):
    async def run():
        from app.request_budget import initial_budget
        store,p=project(tmp_path);started=asyncio.Event()
        class Deferred(SemanticProvider):
            async def request(self,config,messages,evidence=None):
                if self.received:
                    evidence.before_send(config);self.received.append(copy.deepcopy(messages))
                    started.set();await asyncio.Event().wait()
                return await super().request(config,messages,evidence)
        provider=Deferred();workflow=Workflow(store,provider);config=dict(workflow.config('clarify'),max_calls=4)
        from app.requirement_recommendation import scope
        tid=store.record(p['id'],'user_task',dict(status='running',message='旧阶段',max_calls=4,calls=0,
            request_budget=initial_budget(4),requirement_recommendation_scope=scope(p,target()),
            authorized_configs={'clarify':{k:v for k,v in config.items() if k!='proxy'}}))
        task=workflow.start(p['id'],p['revision'],'clarify','',discussion_target=target(),user_task_id=tid,config_override=config)
        store.update_record(p['id'],tid,'user_task',run_ids=[task['id']])
        await asyncio.wait_for(started.wait(),5)
        parent=store.get_record(p['id'],tid,'user_task')
        assert '正文与验收条款各自的来源、主体及适用条件' in parent['message'] and '第 2 次' in parent['message']
        assert parent['calls']==parent['request_budget']['used']==2
        store.update_record(p['id'],tid,'user_task',status='cancelled',message='用户已取消后续步骤')
        running=workflow.tasks[task['id']];workflow.cancel(p['id'],task['id']);await running
        assert store.get_record(p['id'],tid,'user_task')['message']=='用户已取消后续步骤'
        assert len(provider.received)==2
    asyncio.run(run())
