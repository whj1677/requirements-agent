"""Bounded offline protocol recovery; no model semantic approval claim."""
import asyncio,copy,json
import pytest
from app.core import Problem,dumps
from app import intake_grounding as g
from app.workflow import Workflow
from tests.test_noninitial_source_workflow import SourceProvider
from tests.test_requirement_recommendation import project,target

class ProtocolProvider(SourceProvider):
    def __init__(self,mode='patch_format',**kwargs):
        super().__init__(source_failures=1 if mode.startswith('patch') else 0,fail_reviews=0,**kwargs)
        self.mode=mode;self.bad_once=False;self.good_patch=None;self.review_requests=[];self.format_requests=0
    async def request(self,config,messages,evidence=None):
        h=json.loads(messages[0]['content'].split('可信任务头：',1)[1]);p=json.loads(messages[1]['content'][0]['text'])
        is_patch=h.get('grounding_protocol')==g.PATCH_VERSION
        if is_patch and self.mode.startswith('patch'):
            evidence.before_send(config);self.received.append(copy.deepcopy(messages));self.configs.append(copy.deepcopy(config))
            if not self.bad_once:
                self.bad_once=True;self.source_patches+=1;slot=next(s for s in p['slots'] if s['path'][-1]=='statement')
                value=copy.deepcopy(p['candidate']['requirements'][0]['statement']);value['text']='保留原主体与适用条件'
                self.good_patch=dict(candidate_hash=p['candidate_hash'],patches=[dict(slot=slot['id'],value=value,reason='自身来源纠正')])
                raw=json.dumps(self.good_patch,ensure_ascii=False,separators=(',',':'))
                if self.mode not in ('patch_valid_shape','patch_valid_fence'):raw=raw.replace(',"patches"','"patches"',1)
                if self.mode=='patch_valid_fence':raw='```json\n'+raw+'\n```'
                if self.mode=='patch_cut':raw=raw[:-8]
                evidence.response(raw,http_status=200,finish_reason='length' if self.mode=='patch_length' else 'stop',usage={'total_tokens':10})
                if self.mode=='patch_redacted':evidence.limitations.append('final_output_redacted')
                raise Problem('SCHEMA_INVALID','Synthetic missing comma at complete patch')
            self.format_requests+=1
            if self.mode=='patch_cancel':raise asyncio.CancelledError()
            result=copy.deepcopy(self.good_patch)
            if self.mode=='patch_changed':result['patches'][0]['value']['text']='无据改写'
            evidence.response(json.dumps(result,ensure_ascii=False),http_status=200,finish_reason='stop',usage={'total_tokens':10})
            return result,dict(finish_reason='stop',usage={'total_tokens':10})
        challenge=h.get('grounding_protocol')==g.CHALLENGE_VERSION
        detail_challenge=challenge and p['fields'][0]['path'][0]!='requirements'
        body_challenge=challenge and not detail_challenge
        full=bool(p.get('rows') and 'required_checks' in p['rows'][0])
        intercept=((self.mode.startswith('body_hash') and body_challenge) or
            (self.mode.startswith('detail_hash') and detail_challenge) or
            (self.mode.startswith('full_hash') and full))
        result,meta=await super().request(config,messages,evidence)
        if intercept:
            self.review_requests.append(copy.deepcopy(messages))
            if not self.bad_once or self.mode.endswith('_twice'):
                self.bad_once=True
                if full:result['quote_pool_hash']='other quote pool'
                else:result['candidate_hash']='other candidate'
                evidence.response(dumps(result),http_status=200,finish_reason='stop',usage={'total_tokens':10})
        return result,meta

@pytest.mark.parametrize('mode,limit,calls,applied',[
    ('patch_format',None,7,True),('patch_changed',None,4,False),('patch_cut',None,3,False),
    ('patch_valid_shape',None,3,False),('patch_valid_fence',None,3,False),('patch_length',None,3,False),('patch_redacted',None,3,False),('patch_cancel',None,4,False),
    ('patch_format',4,3,False),('body_hash',None,5,True),('body_hash_twice',None,3,False),
    ('detail_hash',None,5,True),('detail_hash',4,4,True),('full_hash',4,3,True),('full_hash',None,5,True),('full_hash_twice',None,4,True)])
def test_shared_protocol_recovery_keeps_original_failure_and_revalidates(tmp_path,mode,limit,calls,applied):
    async def run():
        store,p=project(tmp_path);provider=ProtocolProvider(mode);w=Workflow(store,provider)
        config=dict(w.config('clarify'),max_calls=limit)
        r=w.start(p['id'],p['revision'],'clarify','',discussion_target=dict(target(),detail_required=True),config_override=config)
        await w.tasks[r['id']];saved=store.get_record(p['id'],r['id'],'run')
        assert saved['calls']==len(provider.received)==calls
        assert bool(saved.get('result_applied'))==applied
        assert store.get(p['id'])['items'][:len(p['items'])]==p['items']
        if mode=='patch_cancel':assert saved['status']=='cancelled'
        if limit==4 and mode=='patch_format':assert saved['status']=='paused_budget' and not provider.format_requests
        if provider.review_requests and len(provider.review_requests)>1:assert provider.review_requests[0]==provider.review_requests[1]
        records=[json.loads((store.folder/'evidence/model-calls'/(a['call_id']+'.json')).read_text('utf-8')) for a in saved['attempts']]
        assert any(x['validation_result'] in ('SCHEMA_INVALID','INTAKE_GROUNDING_INVALID','DETAIL_REVIEW_INVALID') for x in records)
        assert sum('protocol_recovery' in x for x in records)<=1
        if mode=='patch_format' and limit is None:
            assert provider.source_patches==1 and provider.format_requests==1 and provider.source_reviews==2
            assert any(x.get('source_patch_format_repair',{}).get('status')=='literal_preserved' for x in records)
            assert saved['business_source_review']['corrections']==1 and saved['business_source_review']['status']=='passed'
        if mode in ('detail_hash','full_hash') and limit is None:
            outcome=saved['business_draft_review']['detail_semantic_reviews']['R1']
            assert outcome['status']=='passed' and outcome['source_challenge']['status']=='passed'
        if mode=='full_hash_twice':
            assert saved['business_draft_review']['detail_semantic_reviews']['R1']['status']=='pending'
            assert not any(i.get('detail_contract') for i in saved['response']['proposals'])
    asyncio.run(run())


def test_source_format_and_detail_review_share_one_recovery_and_valid_findings_do_not_retry(tmp_path):
    class Shared(ProtocolProvider):
        async def request(self,config,messages,evidence=None):
            result,meta=await super().request(config,messages,evidence)
            h=json.loads(messages[0]['content'].split('可信任务头：',1)[1]);p=json.loads(messages[1]['content'][0]['text'])
            if h.get('grounding_protocol')==g.CHALLENGE_VERSION and p['fields'][0]['path'][0]!='requirements':
                result['candidate_hash']='wrong second protocol identity'
                evidence.response(dumps(result),http_status=200,finish_reason='stop',usage={'total_tokens':10})
            return result,meta
    async def run():
        for name,provider in [('shared',Shared()),('semantic',SourceProvider(source_failures=3,fail_reviews=0))]:
            store,p=project(tmp_path/name);w=Workflow(store,provider)
            r=w.start(p['id'],p['revision'],'clarify','',discussion_target=dict(target(),detail_required=True))
            await w.tasks[r['id']];saved=store.get_record(p['id'],r['id'],'run')
            records=[json.loads((store.folder/'evidence/model-calls'/(a['call_id']+'.json')).read_text('utf-8')) for a in saved['attempts']]
            if name=='shared':
                assert saved['calls']==7 and provider.format_requests==1
                assert sum('protocol_recovery' in x for x in records)==1
                assert saved['business_draft_review']['detail_semantic_reviews']['R1']['status']=='pending'
                assert not any(i.get('detail_contract') for i in saved['response']['proposals'])
            else:
                assert saved['calls']==6 and provider.source_reviews==3 and provider.source_patches==2
                assert not any('protocol_recovery' in x for x in records)
                assert saved['error']=='BUSINESS_SOURCE_BLOCKED' and not saved.get('result_applied')
    asyncio.run(run())


def test_legacy_seven_policy_does_not_gain_protocol_recovery(tmp_path):
    async def run():
        store,p=project(tmp_path);provider=ProtocolProvider('body_hash');w=Workflow(store,provider)
        config=dict(w.config('clarify'),detail_correction_policy='two-corrections-seven-run-calls-1')
        r=w.start(p['id'],p['revision'],'clarify','',discussion_target=dict(target(),detail_required=True),config_override=config)
        await w.tasks[r['id']];saved=store.get_record(p['id'],r['id'],'run')
        assert saved['calls']==len(provider.received)==2 and not saved.get('result_applied')
    asyncio.run(run())


def test_plan_exposes_one_shared_protocol_slot_without_changing_semantic_or_user_limits(tmp_path,monkeypatch):
    from app.actions import Actions
    store,p=project(tmp_path);w=Workflow(store,ProtocolProvider());a=Actions(store,w)
    plan=a.plan(p['id'],dict(action='clarify',expected_revision=p['revision'],target=dict(target(),detail_required=True),
        message='',document_type='prd',max_calls=4))
    source=plan['business_source_review']['clarify'];detail=plan['detail_review_entry_repair']['clarify']
    assert source['additional_requests_max']==5 and source['max_corrections']==2 and source['shared_protocol_recovery_max']==1
    assert detail['all_run_provider_requests_max']==sum(detail['request_components'].values())==17
    assert detail['max_protocol_repairs_per_run']==1 and detail['max_business_corrections']==2
    assert 'body_source_patch_format' in detail['protocol_recovery_scope'] and 'detail_source_challenge' in detail['protocol_recovery_scope']
    assert plan['max_calls']==4
    original=w.config
    monkeypatch.setattr(w,'config',lambda stage:dict(original(stage),detail_correction_policy='two-corrections-seven-run-calls-1'))
    legacy=a.plan(p['id'],dict(action='clarify',expected_revision=p['revision'],target=dict(target(),detail_required=True),message='',document_type='prd',max_calls=4))
    assert legacy['business_source_review']['clarify']['shared_protocol_recovery_max']==0


@pytest.mark.parametrize('scope',['body','detail','full'])
def test_bound_semantic_findings_survive_mixed_protocol_rejection_without_fresh_pass(tmp_path,monkeypatch,scope):
    class Mixed(SourceProvider):
        def __init__(self):super().__init__(fail_reviews=0);self.mixed_calls=0
        async def request(self,config,messages,evidence=None):
            result,meta=await super().request(config,messages,evidence)
            h=json.loads(messages[0]['content'].split('可信任务头：',1)[1]);p=json.loads(messages[1]['content'][0]['text'])
            challenge=h.get('grounding_protocol')==g.CHALLENGE_VERSION
            selected=challenge and ((p['fields'][0]['path'][0]=='requirements')==(scope=='body')) if scope!='full' else bool(p.get('rows') and 'required_checks' in p['rows'][0])
            if selected:
                self.mixed_calls+=1
                if self.mixed_calls==1:
                    if scope=='full':
                        from app import detail_semantics
                        check=next(c for c in result['rows'][0]['checks'] if c['dimension']=='field_conditions')
                        check.update(verdict='unsupported',affected_keys=check['candidate_keys'][:1],correction='保留来源中真实条件')
                        try:
                            decoded,_=detail_semantics.decode_review(self.plan,result)
                            assert detail_semantics.validate(self.plan,decoded)['R1']['status']=='failed'
                        except Exception as error:
                            self.proof_error=str(getattr(error,'message',error));raise
                        result['unexpected_protocol_member']=True
                    else:
                        field=p['fields'][0]
                        result['findings']=[dict(id=field['id'],quote=field['text'],reason='真实条件缺失',sources=[]),
                            dict(id=field['id'],quote='THIS_QUOTE_IS_NOT_IN_THE_FIELD',reason='另一项协议引句错误',sources=[])]
                        clean=copy.deepcopy(result);clean['findings']=clean['findings'][:1]
                        assert g.validate_challenge(p,clean)['status']=='blocked'
                    evidence.response(dumps(result),http_status=200,finish_reason='stop',usage={'total_tokens':10})
            return result,meta
    async def run():
        store,p=project(tmp_path);provider=Mixed();w=Workflow(store,provider)
        from app import detail_semantics
        prepare=detail_semantics.prepare
        def capture(*args,**kwargs):
            result=prepare(*args,**kwargs);provider.plan=result;return result
        monkeypatch.setattr(detail_semantics,'prepare',capture)
        r=w.start(p['id'],p['revision'],'clarify','',discussion_target=dict(target(),detail_required=True))
        await w.tasks[r['id']];saved=store.get_record(p['id'],r['id'],'run')
        assert not getattr(provider,'proof_error',None),getattr(provider,'proof_error',None)
        assert provider.mixed_calls==1 and saved['calls']=={'body':2,'full':3,'detail':4}[scope]
        records=[json.loads((store.folder/'evidence/model-calls'/(a['call_id']+'.json')).read_text('utf-8')) for a in saved['attempts']]
        assert not any('protocol_recovery' in x for x in records)
        original=json.loads(records[-1]['final_output'])
        if scope=='full':assert any(c['verdict']=='unsupported' for c in original['rows'][0]['checks'])
        else:assert len(original['findings'])==2 and original['findings'][0]['reason']=='真实条件缺失'
        if scope=='body':assert not saved.get('result_applied') and store.get(p['id'])['items']==p['items']
        else:
            assert saved['business_draft_review']['detail_semantic_reviews']['R1']['status']!='passed'
            assert not any(i.get('detail_contract') for i in saved['response']['proposals'])
    asyncio.run(run())
