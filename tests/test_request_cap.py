"""No paid calls: real Provider HTTP boundary uses only MockTransport.

Public flow cases use synthetic isolated projects. Ledger fixtures below are
internal unit state, never restoration/seeding of a daily business project.
"""
import asyncio
import copy
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core import Problem, digest, execution_hash
from app.main import create_app
from app.model_evidence import ModelCallEvidence
from app.provider import DEFAULT, Provider, origin
from app.request_budget import RequestBudget, initial_budget
from app.store import Store
from tests.helpers import EXAMPLES, prepared
from tests.product_flow_helpers import check_prepared


def mock_transport(monkeypatch, outputs):
    requests=[]; iterator=iter(outputs); real_client=httpx.AsyncClient
    def respond(request):
        body=json.loads(request.content); requests.append(body)
        output=next(iterator)
        if callable(output):output=output(body)
        if isinstance(output,Exception):raise output
        if isinstance(output,int):return httpx.Response(output)
        finish='stop'
        if isinstance(output,tuple): output,finish=output
        content=output if isinstance(output,str) else json.dumps(output,ensure_ascii=False)
        return httpx.Response(200,json=dict(model='synthetic-mock',
            choices=[dict(message=dict(content=content),finish_reason=finish)],
            usage=dict(prompt_tokens=11,completion_tokens=7,total_tokens=18)))
    transport=httpx.MockTransport(respond)
    monkeypatch.setattr('app.provider.httpx.AsyncClient',
        lambda **kwargs:real_client(**kwargs,transport=transport))
    return requests


@pytest.fixture
def public_case(tmp_path):
    app=create_app(tmp_path,access_token='cap-test-login',env_path=tmp_path/'absent.env')
    app.state.provider.set_key(DEFAULT,'synthetic-cap-key')
    p=check_prepared(app.state.store,prepared(app.state.store)['id'],through=3)
    with app.state.store.edit(p['id'],p['revision'],'synthetic grant',bump=False) as (p,_):
        p['grants'][origin(DEFAULT)]={'source_ids':['SRC-0001']}
    with TestClient(app) as client:
        csrf=client.post('/api/session/login',json={'key':'cap-test-login'}).json()['csrf']
        client.headers.update({'Origin':'http://testserver','X-CSRF-Token':csrf})
        yield client,app,'/api/projects/'+p['id']


def public_start(case, limit, *, action='clarify'):
    client,app,root=case
    body=dict(expected_revision=client.get(root).json()['revision'],action=action,
              message='',document_type='prd')
    if limit!='omitted':body['max_calls']=limit
    preview=client.post(root+'/actions/plan',json=body)
    assert preview.status_code==200,preview.text
    plan=preview.json(); assert not plan['missing'],plan['missing']
    response=client.post(root+'/actions',json=dict(body,plan_hash=plan['plan_hash'],
                        idempotency_key=uuid.uuid4().hex,authorize=True))
    assert response.status_code==200,response.text
    for _ in range(350):
        task=client.get(root+'/actions').json()[-1]
        if task['status'] not in ('queued','running'):return plan,response.json(),task
        time.sleep(.01)
    raise AssertionError('action never terminated')


@pytest.mark.parametrize('limit',[1,2,None,'omitted'])
def test_public_plan_approval_run_response_same_limit(public_case,monkeypatch,limit):
    requests=mock_transport(monkeypatch,[EXAMPLES['clarify']])
    plan,started,task=public_start(public_case,limit)
    effective=None if limit=='omitted' else limit
    client,app,root=public_case
    run=client.get(root+'/runs').json()[-1]
    assert plan['max_calls']==started['max_calls']==task['max_calls']==run['max_calls']==effective
    assert run['provider']['max_calls']==run['authorization']['approved_max_calls']==effective
    assert task['authorized_configs']['clarify']['max_calls']==effective
    assert task['calls']==run['calls']==len(requests)==1
    assert run['result_applied'] and task['completed_steps']==1
    assert plan['budget_policies']['clarify']['initial_output_tokens']==DEFAULT['max_tokens']
    if effective is not None:
        assert task['request_budget']['used']==1 and task['request_budget']['limit']==effective
        assert task['retry_input']['max_calls']==effective
        assert task['request_budget']['reservations'][0]['state']=='response_received'
    else:assert 'request_budget' not in task


@pytest.mark.parametrize('limit',[0,-1,True,False,1.0,1.5,'2',{},[]])
def test_illegal_cap_rejected_before_plan_or_execution(public_case,monkeypatch,limit):
    client,app,root=public_case; requests=mock_transport(monkeypatch,[])
    before=app.state.store.get(root.rsplit('/',1)[-1])
    base=dict(expected_revision=before['revision'],action='clarify',max_calls=limit)
    assert client.post(root+'/actions/plan',json=base).status_code==422
    assert client.post(root+'/actions',json=dict(base,plan_hash='invalid',idempotency_key='invalid-key')).status_code==422
    assert client.post(root+'/runs',json=dict(expected_revision=before['revision'],stage='clarify',max_calls=limit)).status_code==422
    assert not requests and client.get(root+'/actions').json()==[]
    assert app.state.store.get(before['id'])==before


@pytest.mark.parametrize('errors',[[429,429],[503,503],[429,503]])
@pytest.mark.parametrize('limit',[1,2])
def test_known_http_retries_stop_at_cap_preserve_failures(public_case,monkeypatch,errors,limit):
    requests=mock_transport(monkeypatch,errors+[EXAMPLES['clarify']])
    _,_,task=public_start(public_case,limit)
    client,app,root=public_case; run=client.get(root+'/runs').json()[-1]
    assert len(requests)==task['calls']==run['calls']==limit
    assert task['status']==run['status']=='paused_budget' and task['error']=='BUDGET_EXHAUSTED'
    assert task['request_budget']['used']==limit and task['completed_steps']==0
    assert len(run['attempts'])==limit and not run.get('result_applied')
    assert all(a['error'] in ('RATE_LIMITED','PROVIDER_ERROR') for a in run['attempts'])
    assert client.post(root+'/runs/'+run['id']+'/resume',json={'expected_revision':client.get(root).json()['revision']}).status_code==409
    assert len(requests)==limit


@pytest.mark.parametrize('error',[httpx.ReadTimeout('synthetic timeout'),httpx.ConnectError('synthetic network')])
def test_unknown_network_never_retried_or_refunded(public_case,monkeypatch,error):
    requests=mock_transport(monkeypatch,[error,EXAMPLES['clarify']])
    before=copy.deepcopy(public_case[1].state.store.get(public_case[2].rsplit('/',1)[-1]))
    _,_,task=public_start(public_case,2)
    run=public_case[0].get(public_case[2]+'/runs').json()[-1]
    assert len(requests)==task['calls']==run['calls']==1 and task['status']=='failed'
    assert run['error'] in ('TIMEOUT','NETWORK_ERROR')
    assert task['request_budget']['used']==1 and task['request_budget']['reservations'][0]['state']=='dispatch_unknown'
    evidence=json.loads((public_case[1].state.store.folder/'evidence/model-calls'/(run['attempts'][0]['call_id']+'.json')).read_text('utf-8'))
    assert not evidence['response_received'] and evidence['usage'] is None
    after=public_case[1].state.store.get(before['id'])
    assert after['documents']==before['documents'] and after['questions']==before['questions']


@pytest.mark.parametrize('limit',[1,2])
def test_valid_finite_schema_repair_keeps_original_and_tokens(public_case,monkeypatch,limit):
    invalid=copy.deepcopy(EXAMPLES['clarify']); invalid['result']['accidental_field']=True
    requests=mock_transport(monkeypatch,[invalid,EXAMPLES['clarify']])
    _,_,task=public_start(public_case,limit)
    run=public_case[0].get(public_case[2]+'/runs').json()[-1]
    assert len(requests)==task['calls']==limit
    assert run['attempts'][0]['error']=='SCHEMA_INVALID'
    assert run['attempts'][0]['usage']==dict(prompt_tokens=11,completion_tokens=7,total_tokens=18)
    if limit==1:assert task['error']=='BUDGET_EXHAUSTED' and not run.get('result_applied')
    else:
        assert run['result_applied'] and run['attempts'][1]['repair_of']==run['attempts'][0]['call_id']
        assert run['attempts'][0]['usage']==run['attempts'][1]['usage']
    first=json.loads((public_case[1].state.store.folder/'evidence/model-calls'/(run['attempts'][0]['call_id']+'.json')).read_text('utf-8'))
    assert json.loads(first['final_output'])==invalid


@pytest.mark.parametrize('limit',[1,2])
def test_truncation_escalation_preserves_output_policy_within_cap(public_case,monkeypatch,limit):
    # Review has the approved documented model ceiling; clarify input remains unchanged.
    requests=mock_transport(monkeypatch,[(EXAMPLES['review'],'length'),EXAMPLES['review']])
    _,_,task=public_start(public_case,limit,action='review')
    run=public_case[0].get(public_case[2]+'/runs').json()[-1]
    assert len(requests)==task['calls']==limit
    assert requests[0]['max_tokens']==65536
    if limit==1:assert task['error']=='BUDGET_EXHAUSTED'
    else:
        assert requests[1]['max_tokens']==393216 and requests[0]['messages']==requests[1]['messages']
        assert run['result_applied'],run
    assert run['authorization']['approved_max_tokens']==393216


def ledger_setup(tmp_path,limit,run_count=1):
    store=Store(tmp_path);p=prepared(store); config=dict(DEFAULT,max_calls=limit)
    with store.edit(p['id'],p['revision'],'synthetic grant',bump=False) as (p,_):
        p['grants'][origin(config)]={'source_ids':['SRC-0001']}
    provider=Provider(env_path=tmp_path/'absent.env');provider.set_key(config,'synthetic-cap-key')
    snapshot={k:v for k,v in config.items() if k!='proxy'}
    tid=store.record(p['id'],'user_task',dict(status='running',calls=0,max_calls=limit,
        authorized_configs={'clarify':snapshot},request_budget=initial_budget(limit)))
    rids=[]
    for _ in range(run_count):
        rids.append(store.record(p['id'],'run',dict(status='running',stage='clarify',calls=0,
            provider=snapshot,user_task_id=tid,input_state_hash=execution_hash(p),
            authorization={'approved_max_tokens':config['max_tokens']},request_budget=initial_budget(limit))))
    return store,p,config,provider,tid,rids


def dispatch(store,p,config,provider,rid,call_id):
    budget=RequestBudget(store,p['id'],rid,config['max_calls'])
    evidence=ModelCallEvidence(store.folder,call_id,rid,'clarify',config['model'],origin(config),None,'synthetic-cap-key',request_budget=budget)
    return asyncio.run(provider.request(config,[{'role':'user','content':'synthetic counter unit'}],evidence=evidence))


def test_atomic_concurrent_last_slot_shared_across_run_stages(tmp_path,monkeypatch):
    store,p,config,provider,tid,rids=ledger_setup(tmp_path,2,run_count=8)
    requests=mock_transport(monkeypatch,[dict(ok=True)]*8)
    def attempt(pair):
        n,rid=pair
        try:dispatch(store,p,config,provider,rid,'CALL-concurrent-'+str(n));return 'sent'
        except Problem as error:return error.code
    with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(attempt,enumerate(rids)))
    task=store.get_record(p['id'],tid,'user_task')
    assert results.count('sent')==len(requests)==task['calls']==task['request_budget']['used']==2
    assert results.count('BUDGET_EXHAUSTED')==6
    assert sum(store.get_record(p['id'],rid,'run')['calls'] for rid in rids)==2


def test_cancel_between_reservation_and_dispatch_proves_unsent_but_keeps_slot(tmp_path,monkeypatch):
    store,p,config,provider,tid,rids=ledger_setup(tmp_path,1)
    requests=mock_transport(monkeypatch,[]);original=RequestBudget.reserve
    def cancel_after_reserving(budget,call_id,config):
        original(budget,call_id,config)
        store.update_record(p['id'],tid,'user_task',status='cancelled')
    monkeypatch.setattr(RequestBudget,'reserve',cancel_after_reserving)
    with pytest.raises(Problem) as rejected:dispatch(store,p,config,provider,rids[0],'CALL-cancelled-before-post')
    assert rejected.value.code=='CANCELLED' and not requests
    task=store.get_record(p['id'],tid,'user_task')
    assert task['calls']==0 and task['request_budget']['used']==1
    evidence=json.loads((tmp_path/'evidence/model-calls/CALL-cancelled-before-post.json').read_text('utf-8'))
    assert evidence['request_state']=='not_sent' and not evidence['response_received']


def test_frozen_five_action_allocation_admits_at_most_eight_posts(tmp_path,monkeypatch):
    requests=mock_transport(monkeypatch,[dict(ok=True)]*8)
    results=[]
    for n,limit in enumerate([2,2,2,1,1]):
        store,p,config,provider,tid,rids=ledger_setup(tmp_path/str(n),limit,run_count=3)
        for attempt in range(limit):dispatch(store,p,config,provider,rids[attempt],'CALL-slot-'+str(attempt))
        with pytest.raises(Problem) as rejected:
            dispatch(store,p,config,provider,rids[-1],'CALL-over')
        assert rejected.value.code=='BUDGET_EXHAUSTED'
        results.append(store.get_record(p['id'],tid,'user_task')['calls'])
    assert results==[2,2,2,1,1] and len(requests)==sum(results)==8


def test_reservation_crash_window_is_unknown_not_refunded_or_zero_fee(tmp_path,monkeypatch):
    store,p,config,provider,tid,rids=ledger_setup(tmp_path,1)
    requests=mock_transport(monkeypatch,[]);budget=RequestBudget(store,p['id'],rids[0],1)
    budget.reserve('CALL-unknown-reservation',config)
    old=copy.deepcopy(store.get_record(p['id'],tid,'user_task'))
    assert old['calls']==0 and old['request_budget']['used']==1
    assert old['request_budget']['reservations'][0]['state']=='reserved_unknown'
    with pytest.raises(Problem) as rejected:dispatch(store,p,config,provider,rids[0],'CALL-new')
    assert rejected.value.code=='BUDGET_EXHAUSTED' and not requests
    assert store.get_record(p['id'],tid,'user_task')==old
    Store(tmp_path)  # process recovery pauses, never replays or refunds
    after=store.get_record(p['id'],tid,'user_task')
    assert after['status']=='paused_budget' and after['request_budget']==old['request_budget']


@pytest.mark.parametrize('change',['input','grant','config','owner-limit'])
def test_outbound_input_or_authorization_change_rejected_without_send(tmp_path,monkeypatch,change):
    store,p,config,provider,tid,rids=ledger_setup(tmp_path,2)
    requests=mock_transport(monkeypatch,[])
    if change in ('input','grant'):
        with store.edit(p['id'],p['revision'],'synthetic external edit',bump=change=='input') as (current,_):
            if change=='input':current['name']='changed synthetic input'
            else:current['grants'].clear()
    elif change=='config':config['max_calls']=3
    else:store.update_record(p['id'],tid,'user_task',max_calls=3)
    with pytest.raises(Problem) as rejected:dispatch(store,p,config,provider,rids[0],'CALL-rejected')
    assert rejected.value.code in ('STALE_REVISION','DATA_AUTH_REQUIRED','BUDGET_AUTH_CHANGED')
    assert not requests and store.get_record(p['id'],rids[0],'run')['calls']==0


def test_provider_cannot_send_finite_cap_without_bound_counter(tmp_path,monkeypatch):
    requests=mock_transport(monkeypatch,[]);provider=Provider(env_path=tmp_path/'absent.env')
    config=dict(DEFAULT,max_calls=2);provider.set_key(config,'synthetic-cap-key')
    with pytest.raises(Problem) as rejected:asyncio.run(provider.request(config,[]))
    assert rejected.value.code=='BUDGET_AUTH_REQUIRED' and not requests


def test_changed_cap_or_source_invalidates_public_approval(public_case,monkeypatch):
    client,app,root=public_case;requests=mock_transport(monkeypatch,[])
    p=client.get(root).json();body=dict(expected_revision=p['revision'],action='clarify',max_calls=2)
    plan=client.post(root+'/actions/plan',json=body).json()
    response=client.post(root+'/actions',json=dict(body,max_calls=1,plan_hash=plan['plan_hash'],idempotency_key='cap-changed-key',authorize=True))
    assert response.status_code==409 and response.json()['code']=='STALE_PLAN'
    assert not requests and client.get(root+'/actions').json()==[]


def test_direct_run_cap_requires_new_review_instead_of_resume(public_case,monkeypatch):
    client,app,root=public_case;requests=mock_transport(monkeypatch,[503])
    started=client.post(root+'/runs',json=dict(expected_revision=client.get(root).json()['revision'],stage='clarify',max_calls=1))
    assert started.status_code==200 and started.json()['max_calls']==1
    for _ in range(250):
        run=client.get(root+'/runs').json()[-1]
        if run['status'] not in ('queued','running'):break
        time.sleep(.01)
    assert run['status']=='paused_budget' and run['calls']==len(requests)==1
    assert client.post(root+'/runs/'+run['id']+'/resume',json={'expected_revision':client.get(root).json()['revision']}).json()['code']=='TASK_REVIEW_REQUIRED'


def test_approved_snapshot_survives_later_settings_change(public_case,monkeypatch):
    client,app,root=public_case
    def change_settings(body):
        app.state.store.setting('model',dict(DEFAULT,max_calls=99,max_tokens=1000))
        return 503
    requests=mock_transport(monkeypatch,[change_settings,EXAMPLES['clarify']])
    _,_,task=public_start(public_case,2)
    assert task['completed_steps']==1 and len(requests)==task['calls']==2
    assert all(r['max_tokens']==DEFAULT['max_tokens'] for r in requests)
    assert task['max_calls']==task['authorized_configs']['clarify']['max_calls']==2


def test_nonrepairable_answered_target_retains_strict_workflow_gate(public_case,monkeypatch):
    client,app,root=public_case
    p=client.get(root).json()
    assert client.post(root+'/answers',json={'expected_revision':p['revision'],'answers':{'Q-0001':'允许相接；合成测试回答'}}).status_code==200
    before=app.state.store.get(p['id'])
    invalid=copy.deepcopy(EXAMPLES['clarify']);invalid['questions'][0]['target_question_id']='Q-0001'
    requests=mock_transport(monkeypatch,[invalid,EXAMPLES['clarify']])
    _,_,task=public_start(public_case,2)
    assert len(requests)==task['calls']==1 and task['error']=='REFERENCE_INVALID'
    after=app.state.store.get(p['id'])
    assert after['questions']==before['questions'] and after['documents']==before['documents']


def test_mixed_truncation_then_http_failure_cannot_send_third_request(public_case,monkeypatch):
    requests=mock_transport(monkeypatch,[(EXAMPLES['review'],'length'),503,EXAMPLES['review']])
    _,_,task=public_start(public_case,2,action='review')
    run=public_case[0].get(public_case[2]+'/runs').json()[-1]
    assert len(requests)==task['calls']==2 and task['error']=='BUDGET_EXHAUSTED'
    assert [a['error'] for a in run['attempts']]==['OUTPUT_TRUNCATED','PROVIDER_ERROR']
    assert run['attempts'][0]['usage']['total_tokens']==18


def test_duplicate_failed_output_guard_stops_before_unused_third_slot(public_case,monkeypatch):
    invalid=copy.deepcopy(EXAMPLES['clarify']);invalid['result']['accidental_field']=True
    requests=mock_transport(monkeypatch,[invalid,invalid,EXAMPLES['clarify']])
    _,_,task=public_start(public_case,3)
    run=public_case[0].get(public_case[2]+'/runs').json()[-1]
    assert len(requests)==task['calls']==2 and task['error']=='SCHEMA_INVALID'
    assert any('重复' in e['phase'] for e in run['events'])


def test_spent_approval_cannot_start_new_task_without_new_plan(public_case,monkeypatch):
    client,app,root=public_case;requests=mock_transport(monkeypatch,[503])
    plan,_,task=public_start(public_case,1)
    assert task['status']=='paused_budget'
    body=dict(expected_revision=client.get(root).json()['revision'],action='clarify',
              message='',document_type='prd',max_calls=1)
    denied=client.post(root+'/actions',json=dict(body,plan_hash=plan['plan_hash'],
        idempotency_key='new-key-old-plan',authorize=True))
    assert denied.status_code==409 and denied.json()['code']=='STALE_PLAN'
    new_plan=client.post(root+'/actions/plan',json=body).json()
    assert new_plan['plan_hash']!=plan['plan_hash'] and new_plan['max_calls']==1
    assert len(requests)==1 and len(client.get(root+'/actions').json())==1


def test_legacy_global_limit_does_not_become_connection_test_cap(public_case,monkeypatch):
    client,app,root=public_case
    legacy=dict(DEFAULT,max_calls=1);app.state.store.setting('model',legacy)
    requests=mock_transport(monkeypatch,[{'connection':'ok'}])
    result=client.post('/api/models/model/test')
    assert result.status_code==200 and result.json()['connection']=='succeeded'
    assert len(requests)==1 and app.state.store.setting('model')==legacy
    assert client.get(root+'/actions').json()==[]
