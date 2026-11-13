"""No-network recovery and typed duplicate checks, with isolated saved evidence."""
import asyncio
import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from app.core import Problem, digest, execution_hash, now
from app.ingest_recovery import recover
from app.model_evidence import ModelCallEvidence
from app.model_json import identical_members
from app.provider import DEFAULT, Provider, assemble
from app import ingest
from tests.test_runtime import client, empty_ingest



@pytest.mark.parametrize('explicit_null',[False,True])
def test_initial_intake_resume_preserves_legacy_api_receipt_and_plan(tmp_path,monkeypatch,explicit_null):
    from app.main import ActionPlan
    c,app=client(tmp_path)
    with c:
        store=app.state.store;p=store.create('旧提交回执')
        submitted=dict(expected_revision=p['revision'],action='organize',message='原始整理请求')
        legacy=ActionPlan(**submitted).model_dump(exclude={'intake_resume_task_id'})
        configs={}
        direct=app.state.actions.plan(p['id'],legacy,config_snapshot=configs)
        assert direct['plan_hash']==digest(dict(input=execution_hash(p),request=legacy,configs=configs,prior_task_ids=[]))
        if explicit_null:submitted['intake_resume_task_id']=None
        planned=c.post(f'/api/projects/{p["id"]}/actions/plan',json=submitted)
        assert planned.status_code==200 and planned.json()['plan_hash']==direct['plan_hash']
        key='legacy-unknown-submit';old_plan='legacy-approved-hash'
        receipt=dict(status='failed',action='organize',idempotency_key=key,
            request_hash=digest(dict(body=legacy,plan_hash=old_plan)),run_ids=[],error='TIMEOUT')
        store.record(p['id'],'user_task',receipt,'TASK-legacy-receipt')
        original=store.get_record(p['id'],'TASK-legacy-receipt','user_task')
        with store.connect() as db:before=[tuple(r) for r in db.execute('SELECT * FROM records ORDER BY rowid')]
        async def forbidden(*a,**kw):raise AssertionError('receipt replay cannot send')
        monkeypatch.setattr(app.state.workflow.provider,'request',forbidden)
        replay=c.post(f'/api/projects/{p["id"]}/actions',json=dict(submitted,plan_hash=old_plan,idempotency_key=key))
        assert replay.status_code==200 and replay.json()==original
        assert store.get(p['id'])==p and not app.state.actions.jobs
        with store.connect() as db:assert [tuple(r) for r in db.execute('SELECT * FROM records ORDER BY rowid')]==before
        changed=c.post(f'/api/projects/{p["id"]}/actions',json=dict(submitted,intake_resume_task_id='TASK-other',plan_hash=old_plan,idempotency_key=key))
        assert changed.status_code==409 and changed.json()['code']=='IDEMPOTENCY_CONFLICT'


async def blocked_initial_intake(tmp_path,challenge_failure=None,ungrounded_acceptance=False):
    from tests.test_intake_coverage import PairProvider
    from tests.test_business_draft import packet,draft_row,business_value
    from app.business_draft import SUMMARY_FIELDS
    from tests.test_requirement_recommendation import project
    from app.actions import Actions
    from app.workflow import Workflow
    store,p=project(tmp_path);raw=packet(draft_row('R1'),draft_row('R2'))
    raw['summary_fields']={k:business_value() for k in SUMMARY_FIELDS}
    if ungrounded_acceptance:
        ungrounded=draft_row('new-unproven')
        ungrounded['acceptance'].append(business_value('建议增加未说明的展示字段。','recommendation',[]))
        raw['requirements'].append(ungrounded)
    class InterruptedPair(PairProvider):
        failure=challenge_failure
        async def request(self,config,messages,evidence=None):
            header=json.loads(messages[0]['content'].split('可信任务头：',1)[1])
            if self.failure and header.get('grounding_protocol')=='intake-grounding-challenge-2':
                evidence.before_send(config);self.received.append(copy.deepcopy(messages))
                raise Problem(self.failure,'合成独立来源质疑无响应')
            return await super().request(config,messages,evidence)
    provider=InterruptedPair(raw,'pass' if challenge_failure else 'source');workflow=Workflow(store,provider);actions=Actions(store,workflow)
    body=dict(expected_revision=p['revision'],action='organize',message='请整理初稿',document_type='prd',max_calls=None)
    plan=actions.plan(p['id'],body);task=actions.start(p['id'],body,plan['plan_hash'],'original-intake-failure')
    await actions.jobs[task['id']]
    task=store.get_record(p['id'],task['id'],'user_task');p=store.get(p['id'])
    assert task['status']=='failed' and task['error']==('INTAKE_GROUNDING_UNVERIFIED' if challenge_failure else 'INTAKE_GROUNDING_BLOCKED')
    body.update(expected_revision=p['revision'],intake_resume_task_id=task['id'])
    return store,p,actions,provider,task,body


@pytest.mark.parametrize('fault',[None,'candidate','patch','source','still_invalid'])
def test_completed_patch_after_local_rejection_resumes_only_fresh_reviews(tmp_path,monkeypatch,fault):
    from app import business_draft
    from app.ingest_recovery import derive_intake_resume
    original=business_draft.adapt;adapt_calls=0
    def rejected_once(*args,**kwargs):
        nonlocal adapt_calls
        value,report=original(*args,**kwargs);adapt_calls+=1
        if adapt_calls==2:report['complete']=False
        return value,report
    async def run():
        monkeypatch.setattr(business_draft,'adapt',rejected_once)
        store,p,actions,provider,old,body=await blocked_initial_intake(tmp_path)
        monkeypatch.setattr(business_draft,'adapt',original)
        run_before=store.get_record(p['id'],old['run_ids'][-1],'run')
        assert run_before['calls']==4
        folder=tmp_path/'evidence/model-calls'
        main=folder/(run_before['attempts'][0]['call_id']+'.json')
        if fault in ('candidate','patch'):
            file=main if fault=='candidate' else folder/(run_before['attempts'][-1]['call_id']+'.json')
            proof=json.loads(file.read_text('utf-8'))
            if fault=='candidate':proof['intake_grounding_corrected_candidate']['summary']='伪造候选'
            else:proof['final_output']='{}'
            file.write_text(json.dumps(proof,ensure_ascii=False),encoding='utf-8')
        elif fault=='source':p['sources'][0]['excerpts'][0]['text']='已变资料'
        elif fault=='still_invalid':
            def still_invalid(*args,**kwargs):
                value,report=original(*args,**kwargs);report['complete']=False
                return value,report
            monkeypatch.setattr(business_draft,'adapt',still_invalid)
        if fault:
            with pytest.raises(Problem):derive_intake_resume(store,p,old['id'])
            return
        packet=derive_intake_resume(store,p,old['id'])
        assert packet['candidate']==json.loads(main.read_text('utf-8'))['intake_grounding_corrected_candidate']
        evidence={file.name:file.read_bytes() for file in folder.glob('*.json')}
        provider.pair_mode='pass';provider.received.clear();provider.coverages=provider.challenges=0
        plan=actions.plan(p['id'],body);task=actions.start(p['id'],body,plan['plan_hash'],'resume-complete-patch')
        await actions.jobs[task['id']]
        saved=store.get_record(p['id'],task['id'],'user_task')
        run_after=store.get_record(p['id'],saved['run_ids'][-1],'run')
        assert saved['status']=='succeeded' and run_after['result_applied']
        assert run_after['calls']==len(provider.received)==2
        assert provider.coverages==provider.challenges==1
        assert run_after['intake_resume_audit']['generation_skipped']
        assert store.get_record(p['id'],old['id'],'user_task')==old
        assert store.get_record(p['id'],run_before['id'],'run')==run_before
        assert all((folder/name).read_bytes()==raw for name,raw in evidence.items())
    asyncio.run(run())


@pytest.mark.parametrize('failure',['TIMEOUT','NETWORK_ERROR'])
def test_interrupted_intake_resume_restarts_both_reviews_without_regeneration(tmp_path,failure):
    from app.ingest_recovery import derive_intake_resume,intake_resume_available
    async def run():
        store,p,actions,provider,old,body=await blocked_initial_intake(tmp_path,failure)
        before=store.get_record(p['id'],old['run_ids'][-1],'run')
        evidence={f.name:f.read_bytes() for f in (tmp_path/'evidence/model-calls').glob('*.json')}
        packet=derive_intake_resume(store,p,old['id'])
        assert intake_resume_available(store,p,old) and packet['candidate']==provider.raw
        provider.failure=None;provider.received.clear();provider.coverages=provider.challenges=0
        plan=actions.plan(p['id'],body);new=actions.start(p['id'],body,plan['plan_hash'],'resume-interrupted')
        await actions.jobs[new['id']]
        task=store.get_record(p['id'],new['id'],'user_task');saved=store.get_record(p['id'],task['run_ids'][-1],'run')
        assert task['status']=='succeeded' and saved['result_applied']
        assert saved['calls']==len(provider.received)==2 and provider.coverages==provider.challenges==1
        assert saved['intake_resume_audit']['generation_skipped']
        assert store.get_record(p['id'],old['id'],'user_task')==old
        assert store.get_record(p['id'],before['id'],'run')==before
        assert all((tmp_path/'evidence/model-calls'/name).read_bytes()==raw for name,raw in evidence.items())
    asyncio.run(run())


@pytest.mark.parametrize('fault',['source','missing','protocol','applied','candidate','response'])
def test_interrupted_intake_resume_rejects_changed_or_unproven_input(tmp_path,fault):
    from app.ingest_recovery import derive_intake_resume,intake_resume_available
    from app.core import dumps
    async def run():
        store,p,actions,provider,task,body=await blocked_initial_intake(tmp_path,'TIMEOUT')
        old=store.get_record(p['id'],task['run_ids'][-1],'run');last=old['attempts'][-1]
        path=tmp_path/'evidence/model-calls'/(last['call_id']+'.json')
        if fault=='source':p['sources'][0]['excerpts'][0]['text']='changed source'
        elif fault=='applied':store.update_run(p['id'],old['id'],result_applied=True)
        elif fault=='missing':path.unlink()
        else:
            record=json.loads(path.read_text('utf-8'))
            if fault=='response':record['response_received']=True
            else:
                messages=json.loads(record['request_input'])
                if fault=='protocol':
                    prefix,raw=messages[0]['content'].split('可信任务头：',1);header=json.loads(raw)
                    header['grounding_protocol']='different-protocol';messages[0]['content']=prefix+'可信任务头：'+dumps(header)
                else:
                    content=json.loads(messages[1]['content'][0]['text']);content['candidate']['summary']='changed candidate'
                    messages[1]['content'][0]['text']=dumps(content)
                record['request_input']=dumps(messages);record['actual_input_hash']=digest(messages)
                last['actual_input_hash']=digest(messages);store.update_run(p['id'],old['id'],attempts=old['attempts'])
            path.write_text(dumps(record),encoding='utf-8')
        assert not intake_resume_available(store,p,task)
        with pytest.raises(Problem):derive_intake_resume(store,p,task['id'])
    asyncio.run(run())


def test_interrupted_intake_resume_does_not_promote_old_coverage_pass(tmp_path):
    async def run():
        store,p,actions,provider,old,body=await blocked_initial_intake(tmp_path,'TIMEOUT')
        provider.failure=None;provider.pair_mode='source';provider.received.clear();provider.reviews=provider.coverages=provider.challenges=0
        plan=actions.plan(p['id'],body);new=actions.start(p['id'],body,plan['plan_hash'],'resume-still-rejected')
        await actions.jobs[new['id']]
        task=store.get_record(p['id'],new['id'],'user_task');saved=store.get_record(p['id'],task['run_ids'][-1],'run')
        assert task['error']=='INTAKE_GROUNDING_BLOCKED' and not saved.get('result_applied')
        assert provider.coverages==provider.challenges==3 and saved['calls']==len(provider.received)==8
        current=store.get(p['id'])
        assert {k:v for k,v in current.items() if k!='grants'}=={k:v for k,v in p.items() if k!='grants'}
        assert store.get_record(p['id'],old['id'],'user_task')==old
    asyncio.run(run())


def test_interrupted_resume_can_review_unproven_suggestion_but_cannot_apply_it(tmp_path):
    from app.ingest_recovery import derive_intake_resume
    from app.business_draft import adapt
    async def run():
        store,p,actions,provider,old,body=await blocked_initial_intake(tmp_path,'TIMEOUT',True)
        packet=derive_intake_resume(store,p,old['id'])
        _,report=adapt(packet['candidate'],p,packet['excerpts'],'ingest',initial_intake_scope=True)
        assert not report['complete'] and report['rejected'][0]['code']=='BUSINESS_DRAFT_SOURCE'
        assert packet['candidate']==provider.raw  # no removed suggestion or invented citation
        provider.failure=None;provider.pair_mode='source';provider.received.clear();provider.reviews=provider.coverages=provider.challenges=0
        plan=actions.plan(p['id'],body);new=actions.start(p['id'],body,plan['plan_hash'],'resume-unproven-suggestion')
        await actions.jobs[new['id']]
        task=store.get_record(p['id'],new['id'],'user_task');saved=store.get_record(p['id'],task['run_ids'][-1],'run')
        assert task['error']=='INTAKE_GROUNDING_BLOCKED' and not saved.get('result_applied')
        assert provider.coverages==provider.challenges==1 and saved['calls']==len(provider.received)==3
        current=store.get(p['id'])
        assert {k:v for k,v in current.items() if k!='grants'}=={k:v for k,v in p.items() if k!='grants'}
        assert store.get_record(p['id'],old['id'],'user_task')==old
    asyncio.run(run())


def test_initial_intake_resume_normal_api_uses_last_candidate_no_new_source_or_main(tmp_path,monkeypatch):
    import time
    from app.ingest_recovery import derive_intake_resume
    from app.core import dumps
    store,p,_,provider,old,body=asyncio.run(blocked_initial_intake(tmp_path))
    original_task=copy.deepcopy(old);original_run=store.get_record(p['id'],old['run_ids'][-1],'run')
    records={f.name:f.read_bytes() for f in (tmp_path/'evidence/model-calls').glob('*.json')}
    packet=derive_intake_resume(store,p,old['id'])
    assert packet['candidate']!=provider.raw
    provider.pair_mode='pass';provider.received.clear();provider.coverages=provider.challenges=0
    async def forbidden(*a,**kw):raise AssertionError('resume cannot regenerate memory or DSH')
    monkeypatch.setattr('app.project_memory.prepare',forbidden);monkeypatch.setattr('app.dsh_intake.request',forbidden)
    c,app=client(tmp_path);app.state.workflow.provider=provider
    with c:
        listed=c.get(f'/api/projects/{p["id"]}/actions').json()
        assert next(t for t in listed if t['id']==old['id'])['intake_resume_available']
        plan=c.post(f'/api/projects/{p["id"]}/actions/plan',json=body)
        assert plan.status_code==200,plan.text
        plan=plan.json();assert plan['intake_resume']['candidate_hash']==digest(packet['candidate'])
        without_marker=dict(body);without_marker.pop('intake_resume_task_id')
        rejected=c.post(f'/api/projects/{p["id"]}/actions',json=dict(without_marker,plan_hash=plan['plan_hash'],idempotency_key='resume-marker-removed'))
        assert rejected.status_code==409 and rejected.json()['code']=='STALE_PLAN'
        assert not provider.received and app.state.store.get(p['id'])==p
        response=c.post(f'/api/projects/{p["id"]}/actions',json=dict(body,plan_hash=plan['plan_hash'],idempotency_key='resume-intake-api'))
        assert response.status_code==200,response.text
        new=response.json();deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            task=app.state.store.get_record(p['id'],new['id'],'user_task')
            if task['status'] not in ('queued','running'):break
            time.sleep(.01)
        assert task['status']=='succeeded',task
        run=app.state.store.get_record(p['id'],task['run_ids'][0],'run')
        assert run['calls']==len(provider.received)==2 and len(run['attempts'])==2
        assert provider.coverages==provider.challenges==1 and run['result_applied']
        assert run['intake_resume_audit']['generation_skipped'] and run['resumed_from']==original_run['id']
        assert run.get('memory_trace') is None
        assert task['retry_input']['intake_resume_task_id']==task['id']
        sent=json.loads(provider.received[0][1]['content'][0]['text'])
        assert sent['candidate']==packet['candidate'] and sent['context']==packet['context']
        assert 'project_memory' not in sent['context']
        current=app.state.store.get(p['id']);assert current['sources']==p['sources']
        assert not any(m.get('role')=='user' and m.get('stage')=='ingest' for m in current['messages'])
        assert app.state.store.get_record(p['id'],old['id'],'user_task')==original_task
        assert app.state.store.get_record(p['id'],original_run['id'],'run')==original_run
        assert all((tmp_path/'evidence/model-calls'/name).read_bytes()==raw for name,raw in records.items())
        assert len(list((tmp_path/'evidence/model-calls').glob('*.json')))==len(records)+2
        assert 'intake_resume' in dumps(run)


@pytest.mark.parametrize('fault',['state','source','cross_project','applied','detail','missing','candidate','context','input','latest','alias'])
def test_initial_intake_resume_rejects_stale_or_incomplete_identity(tmp_path,fault):
    from app.ingest_recovery import derive_intake_resume
    from app.core import dumps
    async def run():
        store,p,actions,provider,task,body=await blocked_initial_intake(tmp_path)
        packet=derive_intake_resume(store,p,task['id']);identity=packet['identity'];run=store.get_record(p['id'],identity['run_id'],'run')
        path=tmp_path/'evidence/model-calls'/(identity['call_id']+'.json')
        if fault=='state':p['items'][0]['statement']='changed'
        elif fault=='source':p['sources'][0]['excerpts'][0]['text']='changed'
        elif fault=='cross_project':p=store.create('另一隔离项目')
        elif fault=='applied':store.update_run(p['id'],run['id'],result_applied=True)
        elif fault=='latest':store.update_record(p['id'],task['id'],'user_task',run_ids=[run['id'],'RUN-not-current'])
        elif fault=='alias':store.update_run(p['id'],run['id'],sent_excerpt_ids=list(reversed(run['sent_excerpt_ids'])))
        else:
            if fault=='missing':path.unlink()
            else:
                record=json.loads(path.read_text('utf-8'))
                if fault=='candidate':record['intake_grounding_corrected_candidate']['summary']='changed'
                elif fault=='detail':record['intake_grounding_corrected_candidate']['requirements'][0]['detail_contract']={'schema_version':1}
                elif fault=='context':record['intake_grounding']['input_hash']='changed'
                else:record['actual_input_hash']='changed'
                path.write_text(dumps(record),encoding='utf-8')
        with pytest.raises(Problem):derive_intake_resume(store,p,task['id'])
    asyncio.run(run())


def test_initial_intake_resume_ignores_first_memory_call_and_uses_last_pair(tmp_path):
    from app.ingest_recovery import derive_intake_resume
    from app.core import dumps
    async def run():
        store,p,actions,provider,task,body=await blocked_initial_intake(tmp_path)
        packet=derive_intake_resume(store,p,task['id']);identity=packet['identity']
        old=store.get_record(p['id'],identity['run_id'],'run')
        main=json.loads((tmp_path/'evidence/model-calls'/(identity['call_id']+'.json')).read_text('utf-8'))
        # A complete synthetic auxiliary record before main; never a live memory call.
        memory={k:v for k,v in main.items() if not k.startswith('intake_grounding')}
        memory['call_id']='CALL-synthetic-memory';memory['final_output']='{"memories":[]}'
        (tmp_path/'evidence/model-calls/CALL-synthetic-memory.json').write_text(dumps(memory),encoding='utf-8')
        first=dict(call_id=memory['call_id'],actual_input_hash=memory['actual_input_hash'],evidence_limitations=[])
        store.update_run(p['id'],old['id'],attempts=[first,*old['attempts']])
        derived=derive_intake_resume(store,p,task['id'])
        assert derived['identity']['call_id']==identity['call_id']
        assert derived['candidate']==packet['candidate'] and derived['candidate']!=provider.raw
        assert derived['context']==packet['context']
    asyncio.run(run())


@pytest.mark.parametrize('fault',[None,'stream','phase','coverage','snapshot','raw'])
def test_initial_intake_resume_dsh_aggregate_requires_actual_final_http_and_complete_read(tmp_path,fault):
    from app.ingest_recovery import derive_intake_resume
    from app.core import dumps
    async def run():
        store,p,actions,provider,task,body=await blocked_initial_intake(tmp_path)
        before=derive_intake_resume(store,p,task['id']);path=tmp_path/'evidence/model-calls'/(before['identity']['call_id']+'.json')
        record=json.loads(path.read_text('utf-8'));record.update(http_status=None,finish_reason=None)
        snapshot=[dict(path='current-workspace.json',sha256='synthetic',total_chars=10)]
        record['dsh_intake']=dict(snapshot=dict(input_hash=record['actual_input_hash']))
        record['dsh_http_requests']=[dict(reading_phase='candidate_only',status='response_received',http_status=200,
            sse_done=True,relay_stream_complete=True,finalization=dict(snapshot_identity=snapshot))]
        record['dsh_reading_coverage']=dict(skill_loaded=True,files=[dict(snapshot[0],complete=True,missing_ranges=[])])
        if fault=='stream':record['dsh_http_requests'][0]['sse_done']=False
        elif fault=='phase':record['dsh_http_requests'][0]['reading_phase']='reading'
        elif fault=='coverage':record['dsh_reading_coverage']['files'][0]['complete']=False
        elif fault=='snapshot':record['dsh_intake']['snapshot']['input_hash']='changed'
        elif fault=='raw':record['final_output']='{}'
        path.write_text(dumps(record),encoding='utf-8')
        if fault:
            with pytest.raises(Problem):derive_intake_resume(store,p,task['id'])
        else:assert derive_intake_resume(store,p,task['id'])['candidate']==before['candidate']
    asyncio.run(run())


@pytest.mark.parametrize('fault',['config','evidence','before_dispatch','record'])
def test_initial_intake_resume_plan_start_and_dispatch_recheck(tmp_path,fault,monkeypatch):
    from app.core import dumps
    async def run():
        store,p,actions,provider,task,body=await blocked_initial_intake(tmp_path)
        plan=actions.plan(p['id'],body);count=len(provider.received)
        if fault=='config':
            settings=store.setting('model') or dict(DEFAULT);settings['timeout']=settings.get('timeout',300)+1;store.setting('model',settings)
        elif fault=='record':store.update_record(p['id'],task['id'],'user_task',message='旧任务记录被修改')
        else:
            path=tmp_path/'evidence/model-calls'/(plan['intake_resume']['call_id']+'.json')
            def change():
                record=json.loads(path.read_text('utf-8'));record['qa_tamper']='changed';path.write_text(dumps(record),encoding='utf-8')
            if fault=='evidence':change()
            else:
                execute=actions.workflow.execute
                async def changed(*a,**kw):change();await execute(*a,**kw)
                monkeypatch.setattr(actions.workflow,'execute',changed)
        if fault=='before_dispatch':
            new=actions.start(p['id'],body,plan['plan_hash'],'resume-dispatch-test');await actions.jobs[new['id']]
            assert store.get_record(p['id'],new['id'],'user_task')['status']=='failed'
        else:
            with pytest.raises(Problem) as error:actions.start(p['id'],body,plan['plan_hash'],'resume-tamper-test')
            assert error.value.code=='STALE_PLAN'
        assert len(provider.received)==count and store.get(p['id'])['sources']==p['sources']
    asyncio.run(run())


def test_initial_intake_resume_changed_source_between_checks_stops_next_send(tmp_path):
    async def run():
        store,p,actions,provider,task,body=await blocked_initial_intake(tmp_path)
        provider.received.clear();provider.pair_mode='pass';original=provider.request
        async def request(config,messages,evidence=None):
            result=await original(config,messages,evidence)
            current=store.get(p['id'])
            with store.edit(p['id'],current['revision'],'合成续审期间来源变化') as (changed,_):
                changed['sources'][0]['excerpts'][0]['text']='新的原始材料'
            return result
        provider.request=request
        plan=actions.plan(p['id'],body);new=actions.start(p['id'],body,plan['plan_hash'],'resume-mid-source-change')
        await actions.jobs[new['id']];saved=store.get_record(p['id'],new['id'],'user_task')
        run=store.get_record(p['id'],saved['run_ids'][0],'run')
        assert len(provider.received)==run['calls']==1 and not run.get('result_applied')
        assert run['intake_grounding']['status']!='passed'
        assert store.get(p['id'])['items']==p['items']
    asyncio.run(run())


@pytest.mark.parametrize('mode,limit,calls',[('source',None,8),('pass',1,0),('timeout',None,1),('cancel',None,1)])
def test_initial_intake_resume_failure_budget_cancel_preserve_old_and_no_apply(tmp_path,mode,limit,calls):
    from app import intake_grounding as g
    async def run():
        store,p,actions,provider,task,body=await blocked_initial_intake(tmp_path)
        before=copy.deepcopy(p);provider.received.clear();provider.reviews=provider.coverages=provider.challenges=0
        provider.pair_mode='source' if mode=='source' else 'pass'
        original=provider.request
        async def request(config,messages,evidence=None):
            if mode in ('timeout','cancel'):
                evidence.before_send(config);provider.received.append(copy.deepcopy(messages))
                if mode=='cancel':raise asyncio.CancelledError()
                raise asyncio.TimeoutError()
            return await original(config,messages,evidence)
        provider.request=request;body['max_calls']=limit
        plan=actions.plan(p['id'],body);new=actions.start(p['id'],body,plan['plan_hash'],'resume-failure-test');await actions.jobs[new['id']]
        saved=store.get_record(p['id'],new['id'],'user_task');run=store.get_record(p['id'],saved['run_ids'][0],'run')
        assert run['calls']==len(provider.received)==calls and not run.get('result_applied')
        assert saved['status'] in ('failed','cancelled','paused_budget')
        assert store.get(p['id'])['items']==before['items'] and store.get(p['id'])['questions']==before['questions']
        assert store.get(p['id'])['sources']==before['sources']
        assert store.get_record(p['id'],task['id'],'user_task')==task
        assert all(json.loads(m[0]['content'].split('可信任务头：',1)[1]).get('grounding_protocol') in (g.COVERAGE_VERSION,g.CHALLENGE_VERSION,g.PATCH_VERSION) for m in provider.received)
        retry=dict(saved['retry_input'],expected_revision=store.get(p['id'])['revision'])
        with pytest.raises(Problem):actions.plan(p['id'],retry)
        assert len(provider.received)==calls
    asyncio.run(run())


@pytest.mark.parametrize('raw', [
    '{"a":1,"a":2}', '{"a":true,"a":1}', '{"a":1,"a":1.0}',
    '{"a":0.12345678901234567890,"a":0.12345678901234567891}',
    '{"a":[1,2],"a":[2,1]}', '{"a":null,"a":"null"}',
    '{"a":{"k":1,"k":2},"a":{"k":2}}',
    '{"a":1,"a":1,"b":{"x":true,"x":false}}',
    '{"a":NaN,"a":NaN}', '{"a":1e999,"a":1e999}',
    '{"a":1,"a":1}{}', '{"a":1,"a":1', '{"a":1}',
])
def test_ambiguous_or_incomplete_json_is_not_normalized(raw):
    assert identical_members(raw) is None


def test_nested_identical_members_preserve_types_values_and_order():
    raw='{"a":[{"source_refs":[{"id":"X"}],"source_refs":[{"id":"X"}]}],"n":false,"n":false}'
    value, paths=identical_members(raw)
    assert value=={'a':[{'source_refs':[{'id':'X'}]}],'n':False}
    assert paths==[['a',0,'source_refs'],['n']]


@pytest.mark.parametrize('stage,conflict', [(s,c) for s in ('ingest','review','clarify','change','interview','prd') for c in (False,True)])
def test_provider_only_normalizes_allowed_complete_identical_responses_and_keeps_raw(tmp_path,monkeypatch,stage,conflict):
    raw='{"stage":"'+stage+'","summary":"原句","summary":"'+('不同' if conflict else '原句')+'"}'
    original=httpx.AsyncClient
    transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'choices':[{'message':{'content':raw},'finish_reason':'stop'}]}))
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=transport,**kw))
    provider=Provider(tmp_path/'absent.env');provider.keys['https://api.deepseek.com']='test-key'
    proof=ModelCallEvidence(tmp_path,'CALL-test','RUN-test',stage,'synthetic','https://api.deepseek.com',None,'test-key')
    if not conflict:
        result,meta=asyncio.run(provider.request(DEFAULT,[],evidence=proof))
        assert result['summary']=='原句'
        assert meta['syntax_normalization']['paths']==[['summary']]
        assert 'json_integrity_failure' not in proof.value
    else:
        with pytest.raises(Problem):asyncio.run(provider.request(DEFAULT,[],evidence=proof))
        assert proof.value['json_integrity_failure']=='duplicate_member'
    assert json.loads(proof.path.read_text('utf-8'))['final_output']==raw


def seed_failed_draft(app):
    """A historical failed response: no provider request is made by this fixture."""
    store=app.state.store;p=store.create('历史整理恢复 · 合成验证')
    from app.sources import save_source
    from app.interview import record_input
    text='管理员维护联系人列表。只读角色不能修改。'
    source=save_source(store,'对话原话.txt',text.encode(),purpose='goal')
    with store.edit(p['id'],p['revision'],'合成历史对话') as (p,db):
        p['sources'].append(source)
        record_input(p,text,'TASK-chat',source['id'])
    p=store.get(p['id']);messages,excerpts,omitted=assemble(p,'ingest','',DEFAULT,store.folder)
    ref=[dict(source_id=excerpts[0]['source_id'],excerpt_id=excerpts[0]['id'])]
    value=empty_ingest()
    value['proposals']=[dict(temp_id='TMP-R',action='add',target_item_id=None,kind='requirement',
        title='联系人维护',statement='管理员维护联系人列表。',applies_to='to_be',epistemic_status='reported',source_refs=ref,related_refs=[])]
    value['questions']=[dict(temp_id='QTMP-parent',topic_key='boundary',question='如何处理边界？',
        why='需要明确两项边界',options=[],blocking=True,blocking_stage=3,related_refs=['TMP-R'],source_refs=ref,
        decision_points=[dict(temp_id='QTMP-'+str(i),topic_key='boundary-'+str(i),question=q,
            why='边界未定',options=[],blocking=False,blocking_stage=4,related_refs=['TMP-R'],source_refs=ref)
            for i,q in enumerate(['重复姓名如何处理？','空姓名如何处理？'])])]
    raw=json.dumps(value,ensure_ascii=False)
    literal='"source_refs": '+json.dumps(ref,ensure_ascii=False)
    raw=raw.replace(literal,literal+', '+literal,1)
    proof=ModelCallEvidence(store.folder,'CALL-history','RUN-history','ingest','synthetic','http://127.0.0.1',None,'')
    proof.request_context(DEFAULT,messages,{},{});proof.response(raw,http_status=200,finish_reason='stop')
    proof.value['json_integrity_failure']='duplicate_member';proof.finish('SCHEMA_INVALID')
    run=dict(stage='ingest',status='failed',error='SEMANTIC_BLOCKED',revision=p['revision'],message='',
        document_type='prd',calls=1,attempts=[dict(call_id='CALL-history',actual_input_hash=digest(messages),evidence_limitations=[])],
        input_state_hash=execution_hash(p),sent_excerpt_ids=[e['id'] for e in excerpts],omitted_excerpt_ids=omitted,
        user_task_id='TASK-history',result_applied=False)
    task=dict(action='organize',label='整理当前信息',status='failed',error='SEMANTIC_BLOCKED',calls=1,
        message='模型 JSON 有重复键，无法证明格式修复保持原义；原始证据保留，未追加修复请求',
        input_revision=p['revision'],run_ids=['RUN-history'],stages=['ingest'],completed_steps=0,
        step_states=[dict(stage='ingest',status='failed')],source_ids=[source['id']],max_calls=None,cost=None,
        created=now(),completed=now(),retry_input=dict(action='organize',message='',document_type='prd'))
    store.record(p['id'],'run',run,'RUN-history');store.record(p['id'],'user_task',task,'TASK-history')
    return p,proof,value


def test_new_question_gates_inherit_without_rewriting_business_or_old_questions(tmp_path):
    c,app=client(tmp_path);p,proof,value=seed_failed_draft(app)
    before=copy.deepcopy(value);derived,changes=ingest.inherit_new_question_gates(value,p)
    assert value==before and len(changes)==2
    for child,original in zip(derived['questions'][0]['decision_points'],value['questions'][0]['decision_points']):
        assert child==dict(original,blocking=True,blocking_stage=3)
    value['questions'][0]['target_question_id']='Q-old'
    assert ingest.inherit_new_question_gates(value,p)==(value,[])


def test_recovery_is_atomic_idempotent_candidate_only_and_preserves_original_evidence(tmp_path,monkeypatch):
    c,app=client(tmp_path);p,proof,value=seed_failed_draft(app);root='/api/projects/'+p['id']
    async def forbidden(*args,**kwargs):raise AssertionError('recovery must never call a model')
    monkeypatch.setattr(app.state.provider,'request',forbidden)
    raw=proof.path.read_bytes();run=app.state.store.get_record(p['id'],'RUN-history');task=app.state.store.get_record(p['id'],'TASK-history')
    response=c.post(root+'/actions/TASK-history/recover-draft',json={'expected_revision':p['revision']})
    assert response.status_code==200,response.text
    receipt=response.json();after=c.get(root).json()
    assert after['revision']==p['revision']+1
    assert len(after['items'])==1 and after['items'][0]['selection_status']=='candidate'
    assert after['items'][0]['statement']==value['proposals'][0]['statement']
    assert all(q['blocking'] and q['blocking_stage']==3 for q in after['questions'])
    assert after['active_baseline_id'] is None and not after['product_flow'][1]['complete']
    assert after['messages'][:len(p['messages'])]==p['messages']
    assert proof.path.read_bytes()==raw
    assert app.state.store.get_record(p['id'],'RUN-history')==run
    assert app.state.store.get_record(p['id'],'TASK-history')==task
    assert receipt['evidence_sha256']==hashlib.sha256(raw).hexdigest() and receipt['external_model_calls']==0
    again=c.post(root+'/actions/TASK-history/recover-draft',json={'expected_revision':p['revision']})
    assert again.status_code==200 and again.json()['id']==receipt['id']
    assert c.get(root).json()['revision']==after['revision']
    projection=c.get(root+'/actions').json()[-1]
    assert projection['status']=='failed' and projection['calls']==1 and projection['local_recovery']['id']==receipt['id']


@pytest.mark.parametrize('mutation', ['stale','active','conflict','truncated','redacted','source','request_hash','wrong_run','semantic','benchmark','cancelled'])
def test_recovery_rejects_unsafe_inputs_without_writes(tmp_path,mutation):
    c,app=client(tmp_path);p,proof,_=seed_failed_draft(app);store=app.state.store
    if mutation=='stale':
        with store.edit(p['id'],p['revision'],'新的聊天') as (current,db):current['messages'].append(dict(role='user',text='新的业务内容'))
    elif mutation=='active':store.record(p['id'],'user_task',dict(status='running'),'TASK-active')
    elif mutation in ('benchmark','cancelled'):
        store.update_record(p['id'],'TASK-history','user_task',**({'benchmark':{'target':'other'}} if mutation=='benchmark' else {'status':'cancelled'}))
    else:
        e=json.loads(proof.path.read_text('utf-8'))
        if mutation=='conflict':e['final_output']=e['final_output'].replace('"source_refs": [','"source_refs": [], "source_refs": [',1)
        elif mutation in ('truncated','redacted'):e['evidence_limitations']=['final_output_'+mutation]
        elif mutation=='source':
            request=json.loads(e['request_input']);ctx=json.loads(request[1]['content'][0]['text']);ctx['excerpts'][0]['text']='不一致的来源'
            request[1]['content'][0]['text']=json.dumps(ctx,ensure_ascii=False);e['request_input']=json.dumps(request,ensure_ascii=False);e['actual_input_hash']=digest(request)
            store.update_run(p['id'],'RUN-history',attempts=[dict(call_id='CALL-history',actual_input_hash=digest(request))])
        elif mutation=='request_hash':e['actual_input_hash']='wrong'
        elif mutation=='wrong_run':e['run_id']='RUN-other'
        elif mutation=='semantic':e['final_output']=e['final_output'].replace('"TMP-R"','"TMP-missing"',1)
        proof.path.write_text(json.dumps(e,ensure_ascii=False),'utf-8')
    before=store.get(p['id']);revision=before['revision']
    with pytest.raises(Problem):recover(store,p['id'],'TASK-history',revision)
    assert store.get(p['id'])==before and not store.records(p['id'],'local_recovery')


def test_concurrent_recovery_cannot_apply_twice(tmp_path):
    c,app=client(tmp_path);p,_,_=seed_failed_draft(app);store=app.state.store
    def run():
        try:return recover(store,p['id'],'TASK-history',p['revision'])
        except Problem as error:return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:run(),range(2)))
    assert any(isinstance(row,dict) for row in results)
    assert len(store.get(p['id'])['items'])==1 and len(store.records(p['id'],'local_recovery'))==1
