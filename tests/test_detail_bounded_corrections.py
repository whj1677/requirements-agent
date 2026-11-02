"""Finite business-detail retries under unchanged shared authorization."""
import asyncio
import copy
import json
import pytest
from app.core import Problem,dumps
from app.business_draft import PROTOCOL
from app import detail_review_repair as repair
from app.workflow import Workflow
from tests.test_runtime import FakeProvider
from tests.test_requirement_recommendation import project,target
from tests.test_detail_semantic_workflow import generated_detail
from tests.test_business_draft import draft_row,packet
from tests.test_detail_semantics import response,model_response,correction_response


class BoundedProvider(FakeProvider):
    business_draft_protocol=PROTOCOL
    def __init__(self,*,patches=(),always_fail=False,syntax=False,cancel=False,fail_reviews=2):
        super().__init__([]);self.patches=patches;self.always_fail=always_fail;self.syntax=syntax;self.cancel=cancel
        self.fail_reviews=fail_reviews
        self.reviews=[];self.good=[];self.corrections=0;self.patch_count=0;self.on_cancel=None;self.configs=[]
    async def request(self,config,messages,evidence=None):
        evidence.before_send(config);self.received.append(copy.deepcopy(messages));self.configs.append(copy.deepcopy(config))
        h=json.loads(messages[0]['content'].split('可信任务头：',1)[1]);p=json.loads(messages[1]['content'][0]['text'])
        if h.get('grounding_protocol'):
            result=dict(candidate_hash=p['candidate_hash'],input_hash=p['input_hash'],
                checked_field_ids=[f['id'] for f in p['fields']],findings=[])
        elif h.get('response_protocol')==PROTOCOL:
            row=draft_row();row['detail_contract']=generated_detail();result=packet(row)
            if self.syntax and len(self.received)==1:
                evidence.response(dumps(result)[:-1],http_status=200,finish_reason='stop',usage={'total_tokens':10})
                raise Problem('SCHEMA_INVALID','Synthetic one missing root closer')
        elif h.get('response_protocol')==repair.PROTOCOL:
            self.patch_count+=1;rp=p['repair_packet'];good=self.good[-1]
            result=dict(protocol=repair.PROTOCOL,plan_hash=rp['plan_hash'],review_hash=rp['review_hash'],replacements=[
                dict(slot_id=repair.slot_id(s),original_hash=s['original_hash'],replacement=copy.deepcopy(good['rows'][s['row_index']][s['group']][s['index']])) for s in rp['slots']])
        elif p.get('rows') and 'required_checks' in p['rows'][0]:
            self.reviews.append(copy.deepcopy(p));expanded=response(p)
            if len(self.reviews)<=self.fail_reviews or self.always_fail:
                path='detail_contract.fields.F1.required';prop=next(v for v in p['rows'][0]['property_refs'].values() if v['path']==path)
                check=next(c for c in expanded['rows'][0]['checks'] if c['dimension']=='field_conditions')
                check.update(verdict='unclear',affected_paths=[path],candidate_quotes=[dict(path=path,quote=prop['value'])],correction='按本次来源核对字段条件。')
            result=model_response(p,expanded);self.good.append(copy.deepcopy(result))
            if len(self.reviews) in self.patches:
                next(c for c in result['rows'][0]['checks'] if c['traces'])['traces'][0]['edge_ids']=['E1','E3','E2']
        else:
            self.corrections+=1
            detail=copy.deepcopy(p['rows'][0]['detail_contract']);detail['fields'][0]['required']='第'+str(self.corrections)+'次来源限定填写条件'
            result=correction_response(p,detail)
            if self.cancel and self.corrections==2:self.on_cancel()
        evidence.response(dumps(result),http_status=200,finish_reason='stop',usage={'total_tokens':10})
        return result,dict(finish_reason='stop',usage={'total_tokens':10},response_model='OFFLINE_BOUNDED_FIXTURE')


@pytest.mark.parametrize('legacy,limit,patches,syntax,calls,corrections,status',[
    (False,None,(),False,8,2,'passed'),(False,7,(),False,5,1,'failed'),
    (True,None,(),False,5,1,'failed'),(False,5,(),False,3,0,'failed'),
    (False,None,(1,2),False,6,1,'pending'),
    (False,None,(3,),False,9,2,'passed'),
    (False,None,(1,3),False,8,2,'pending'),
    (False,None,(),True,9,2,'passed'),
])
def test_finite_candidates_share_source_and_detail_call_budget(tmp_path,legacy,limit,patches,syntax,calls,corrections,status):
    async def run():
        store,p=project(tmp_path);provider=BoundedProvider(patches=patches,syntax=syntax);workflow=Workflow(store,provider)
        config=dict(workflow.config('clarify'),max_calls=limit)
        if legacy:config.pop('detail_correction_policy')
        task=workflow.start(p['id'],p['revision'],'clarify','',discussion_target=dict(target(),detail_required=True),config_override=config)
        await workflow.tasks[task['id']];saved=store.get_record(p['id'],task['id'],'run')
        assert saved['calls']==len(provider.received)==calls and provider.corrections==corrections
        assert saved['business_draft_review']['detail_semantic_reviews']['R1']['status']==status
        hashes=[plan['rows'][0]['candidate_hash'] for plan in provider.reviews]
        assert len(hashes)==len(set(hashes)) and len(hashes)<=3
        if status!='passed':assert not any(x.get('detail_contract') for x in saved['response']['proposals'])
        if not legacy and patches in ((1,2),(1,3)):
            assert 'DETAIL_REVIEW_INVALID' in dumps(saved) and provider.patch_count==1
        if limit==5:assert 'BUDGET_EXHAUSTED' in dumps(saved) and '至少 3 次' in dumps(saved)
        assert saved['provider']['max_calls']==limit
        for c in provider.configs:
            assert c['max_calls']==limit
            if c.get('reasoning_effort')=='high':assert c['max_tokens']==65536 and c['timeout']==300
    asyncio.run(run())


def test_protocol_repaired_pass_still_requires_independent_challenge(tmp_path):
    async def run():
        store,p=project(tmp_path);provider=BoundedProvider(patches=(1,),fail_reviews=0);workflow=Workflow(store,provider)
        task=workflow.start(p['id'],p['revision'],'clarify','',discussion_target=dict(target(),detail_required=True))
        await workflow.tasks[task['id']];saved=store.get_record(p['id'],task['id'],'run')
        assert provider.patch_count==1 and provider.corrections==0 and saved['calls']==len(provider.received)==5
        assert json.loads(provider.received[-1][0]['content'].split('可信任务头：',1)[1]).get('grounding_protocol')
        assert saved['business_draft_review']['detail_semantic_reviews']['R1']['status']=='passed'
        assert saved['business_draft_review']['detail_semantic_reviews']['R1']['source_challenge']['status']=='passed'
    asyncio.run(run())


def test_third_correction_forbidden_and_cancel_does_not_send_fresh_high(tmp_path):
    async def run():
        for name,cancel in [('third',False),('cancel',True)]:
            store,p=project(tmp_path/name);provider=BoundedProvider(always_fail=True,cancel=cancel);workflow=Workflow(store,provider)
            task=workflow.start(p['id'],p['revision'],'clarify','',discussion_target=dict(target(),detail_required=True))
            provider.on_cancel=lambda:store.update_run(p['id'],task['id'],status='cancelled')
            await workflow.tasks[task['id']];saved=store.get_record(p['id'],task['id'],'run')
            assert provider.corrections==2 and saved['calls']==(6 if cancel else 7)
            if cancel:assert saved['status']=='cancelled'
            else:assert saved['business_draft_review']['detail_semantic_reviews']['R1']['status']=='failed'
    asyncio.run(run())


def test_available_two_counts_task_reserved_unknown_without_mutating_authorization(tmp_path):
    from tests.test_request_cap import ledger_setup
    from app.request_budget import RequestBudget
    store,p,config,_,tid,rids=ledger_setup(tmp_path,3,run_count=2)
    budget=RequestBudget(store,p['id'],rids[0],3)
    budget.reserve('CALL-reserved-one',config)
    RequestBudget(store,p['id'],rids[1],3).reserve('CALL-reserved-two',config)
    task=store.get_record(p['id'],tid,'user_task');before=copy.deepcopy(task)
    assert task['request_budget']['used']==2 and store.get_record(p['id'],rids[0],'run')['calls']==0
    budget.available()
    with pytest.raises(Problem) as caught:budget.available(2)
    assert caught.value.code=='BUDGET_EXHAUSTED'
    assert store.get_record(p['id'],tid,'user_task')==before


def test_main_entry_checks_persistent_seventeen_before_dispatch_even_without_shared_limit(tmp_path,monkeypatch):
    async def run():
        store,p=project(tmp_path);provider=BoundedProvider();workflow=Workflow(store,provider)
        execute=workflow.execute
        async def with_prior_calls(pid,rid,project,config):
            store.update_run(pid,rid,calls=17)
            await execute(pid,rid,project,config)
        monkeypatch.setattr(workflow,'execute',with_prior_calls)
        task=workflow.start(p['id'],p['revision'],'clarify','',discussion_target=dict(target(),detail_required=True))
        await workflow.tasks[task['id']];saved=store.get_record(p['id'],task['id'],'run')
        assert saved['calls']==17 and not provider.received and saved['error']=='DETAIL_RUN_CALL_LIMIT'
    asyncio.run(run())


@pytest.mark.parametrize('code',['RATE_LIMITED','PROVIDER_ERROR','OUTPUT_TRUNCATED'])
def test_business_main_failure_is_counted_without_hidden_replay(tmp_path,code):
    class Failure(BoundedProvider):
        async def request(self,config,messages,evidence=None):
            evidence.before_send(config);self.received.append(messages)
            raise Problem(code,'Synthetic main transport failure')
    async def run():
        store,p=project(tmp_path);provider=Failure();workflow=Workflow(store,provider)
        task=workflow.start(p['id'],p['revision'],'clarify','',discussion_target=dict(target(),detail_required=True))
        await workflow.tasks[task['id']];saved=store.get_record(p['id'],task['id'],'run')
        assert saved['calls']==len(provider.received)==1 and saved['status']=='failed'
    asyncio.run(run())


def test_plan_announces_new_cap_only_for_business_detail_and_keeps_old_snapshot(tmp_path):
    from app.actions import Actions
    store,p=project(tmp_path);workflow=Workflow(store,BoundedProvider());actions=Actions(store,workflow)
    base=dict(expected_revision=p['revision'],action='clarify',message='',document_type='prd',max_calls=None)
    detail=actions.plan(p['id'],dict(base,target=dict(target(),detail_required=True)))
    policy=detail['detail_review_entry_repair']['clarify']
    assert policy['max_business_corrections']==2 and policy['max_reviewed_candidates']==3 and policy['all_run_provider_requests_max']==17
    assert policy['other_task_auxiliary_requests_included'] is True and policy['request_count_scope']=='all_provider_requests_in_this_business_detail_run'
    ordinary=actions.plan(p['id'],dict(base,target=target()))
    assert 'all_run_provider_requests_max' not in ordinary['detail_review_entry_repair']['clarify']
    assert ordinary['detail_review_entry_repair']['clarify']['other_task_auxiliary_requests_included'] is False
    nonbusiness=actions.plan(p['id'],base)
    assert 'all_run_provider_requests_max' not in nonbusiness['detail_review_entry_repair']['clarify']
