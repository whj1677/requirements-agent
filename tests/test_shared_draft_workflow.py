"""Real Workflow with an offline Provider; no paid request or daily data."""
import asyncio
import copy
import json

import pytest

from app.core import digest, dumps
from app.product_flow import blockers
from app.provider import DEFAULT, origin
from app.store import Store
from app.understanding import question_fingerprint
from app.workflow import Workflow
from tests.helpers import prepared
from tests.test_runtime import empty_ingest


def primary_result():
    value=empty_ingest()
    ref=dict(source_id='SRC-0001',excerpt_id='EX-0001')
    value['used_source_refs']=[copy.deepcopy(ref)]
    value['questions']=[dict(temp_id='QTMP-price-decisions',topic_key='price-decisions',
        question='版本能否追溯生效，迟到电量如何选价并重算？',
        why='合成需求中的独立选择待定',options=[],blocking=True,blocking_stage=3,
        related_refs=['REQ-0001'],source_refs=[ref],decision_points=[],
        target_question_id=None)]
    return value


def semantic_result(messages,valid=True):
    context=json.loads(messages[1]['content'])
    target=context['targets'][0]
    if not valid:return dict(reviews=[])
    return dict(reviews=[dict(question_id=target['question_id'],decisions=[
        dict(id='TMP-effect',question='能否追溯生效？',coverage='pending',
             answer_quote='',related_refs=['REQ-0001'],why='独立生效决定'),
        dict(id='TMP-version',question='迟到电量用哪个版本？',coverage='pending',
             answer_quote='',related_refs=['REQ-0001'],why='独立选价决定'),
        dict(id='TMP-recalc',question='什么条件下重算？',coverage='pending',
             answer_quote='',related_refs=['REQ-0001'],why='独立重算决定')])])


class OfflineProvider:
    def __init__(self,*,invalid=False,hold=None,entered=None,timeout=False,
                 primary=None,single_decision=False):
        self.invalid=invalid;self.hold=hold;self.entered=entered;self.timeout=timeout
        self.primary=primary;self.single_decision=single_decision
        self.received=[]

    def key(self,_):return 'offline-fixture'

    async def request(self,config,messages,evidence=None):
        self.received.append(copy.deepcopy(messages))
        call=len(self.received)
        if evidence:evidence.before_send(config)
        if call==2:
            if self.entered:self.entered.set()
            if self.timeout:await asyncio.sleep(0.3)
            if self.hold:await self.hold.wait()
        if call==1:
            value=copy.deepcopy(self.primary) if self.primary is not None else primary_result()
        elif self.single_decision:
            context=json.loads(messages[1]['content'])
            target=context['targets'][0]
            value=dict(reviews=[dict(question_id=target['question_id'],decisions=[
                dict(id=target['question_id'],question='站点总览刷新周期？设备详情同样的周期？',
                     coverage='pending',answer_quote='',related_refs=['REQ-0001'],
                     why='两处使用同一参数值')])])
        else:
            value=semantic_result(messages,not self.invalid)
        if evidence:evidence.response(content=dumps(value),http_status=200,
            finish_reason='stop',usage=dict(prompt_tokens=10,completion_tokens=10,total_tokens=20),
            response_model='OFFLINE_FIXTURE')
        return value,dict(request_model=config['model'],response_model='OFFLINE_FIXTURE',
                          usage=dict(prompt_tokens=10,completion_tokens=10,total_tokens=20),
                          origin=origin(config),finish_reason='stop')


def project(store):
    p=prepared(store)
    with store.edit(p['id'],p['revision'],'合成共享草稿授权',bump=False) as (draft,_):
        draft['workflow_mode']='shared-draft'
        draft['grants'][origin(DEFAULT)]={'source_ids':[s['id'] for s in draft['sources']]}
    return store.get(p['id'])


def config(**changes):
    return dict(DEFAULT,**changes)


async def start_and_wait(store,p,provider,*,override=None):
    workflow=Workflow(store,provider)
    run=workflow.start(p['id'],p['revision'],'ingest','合成电价决定整理',
                       config_override=override or config())
    await workflow.tasks[run['id']]
    return store.get_record(p['id'],run['id'],'run'),store.get(p['id'])


def test_valid_primary_and_semantic_audit_activate_one_version(tmp_path):
    async def scenario():
        store=Store(tmp_path)
        p=project(store)
        provider=OfflineProvider()
        run,saved=await start_and_wait(store,p,provider)
        return p,run,saved,provider,store
    original,run,saved,provider,store=asyncio.run(scenario())
    assert len(provider.received)==2
    assert run['result_applied'] is True and run['decision_review']['status']=='proposal_ready'
    assert run['calls']==2 and len(run['attempts'])==2
    assert saved['revision']==original['revision']+1
    parent=next(q for q in saved['questions'] if q.get('topic_key')=='price-decisions')
    assert len(parent['superseded_by'])==3
    assert all(q['status']=='open' and q['answer'] is None for q in saved['questions']
               if q['id'] in parent['superseded_by'])


@pytest.mark.parametrize('failure',['invalid','timeout'])
def test_invalid_or_timed_out_audit_keeps_valid_primary_without_retry(tmp_path,failure):
    async def scenario():
        store=Store(tmp_path)
        p=project(store)
        provider=OfflineProvider(invalid=failure=='invalid',timeout=failure=='timeout')
        cfg=config(timeout=0.02 if failure=='timeout' else 120)
        run,saved=await start_and_wait(store,p,provider,override=cfg)
        return p,run,saved,provider
    original,run,saved,provider=asyncio.run(scenario())
    assert len(provider.received)==2 and len(run['attempts'])==2
    assert run['calls']==2 and run['status']=='partial' and run['result_applied'] is True
    assert run['decision_review']['status']=='pending_manual_review'
    assert run['decision_review']['error']==('TIMEOUT' if failure=='timeout' else 'REFERENCE_INVALID')
    assert saved['revision']==original['revision']+1
    assert any(q.get('topic_key')=='price-decisions' and not q.get('superseded_by')
               for q in saved['questions'])


def test_one_call_budget_skips_extra_audit_and_keeps_draft(tmp_path):
    async def scenario():
        store=Store(tmp_path)
        p=project(store)
        provider=OfflineProvider()
        run,saved=await start_and_wait(store,p,provider,override=config(max_calls=1))
        return p,run,saved,provider
    original,run,saved,provider=asyncio.run(scenario())
    assert len(provider.received)==1
    assert run['calls']==1 and run['result_applied'] is True and run['status']=='partial'
    assert run['decision_review']['status']=='pending_manual_review'
    assert run['decision_review']['error']=='BUDGET_EXHAUSTED'
    assert saved['revision']==original['revision']+1
    assert any(q.get('topic_key')=='price-decisions' for q in saved['questions'])


def test_two_question_marks_for_one_shared_value_reaches_semantic_audit(tmp_path):
    async def scenario():
        store=Store(tmp_path)
        p=project(store)
        primary=primary_result()
        primary['questions'][0]['question']='站点总览刷新周期？设备详情同样的周期？'
        provider=OfflineProvider(primary=primary,single_decision=True)
        run,saved=await start_and_wait(store,p,provider)
        return p,run,saved,provider
    original,run,saved,provider=asyncio.run(scenario())
    assert len(provider.received)==2
    assert run['calls']==2 and len(run['attempts'])==2
    assert run['result_applied'] is True and run['decision_review']['status']=='proposal_ready'
    assert saved['revision']==original['revision']+1
    q=next(q for q in saved['questions'] if q.get('topic_key')=='price-decisions')
    assert q['question']=='站点总览刷新周期？设备详情同样的周期？'
    assert not q.get('superseded_by')
    assert len(q['decision_review_proposal']['decisions'])==1


@pytest.mark.parametrize('interruption',['cancel','input_change'])
def test_audit_in_flight_never_overwrites_cancelled_or_changed_input(tmp_path,interruption):
    async def scenario():
        store=Store(tmp_path)
        p=project(store)
        hold=asyncio.Event();entered=asyncio.Event()
        provider=OfflineProvider(hold=hold,entered=entered)
        workflow=Workflow(store,provider)
        started=workflow.start(p['id'],p['revision'],'ingest','合成电价决定整理',
                               config_override=config())
        await asyncio.wait_for(entered.wait(),3)
        if interruption=='cancel':
            workflow.cancel(p['id'],started['id'])
        else:
            with store.edit(p['id'],p['revision'],'人工改动输入') as (draft,_):
                draft['name']='人工已改动'
        hold.set()
        await workflow.tasks[started['id']]
        return p,store.get_record(p['id'],started['id'],'run'),store.get(p['id']),provider
    original,run,saved,provider=asyncio.run(scenario())
    assert len(provider.received)==2
    assert run['result_applied'] is not True
    assert run['status']==('cancelled' if interruption=='cancel' else 'failed')
    assert run['error']==('CANCELLED' if interruption=='cancel' else 'STALE_REVISION')
    assert not any(q.get('topic_key')=='price-decisions' for q in saved['questions'])
    assert saved['revision']==original['revision']+(interruption=='input_change')
    if interruption=='input_change':assert saved['name']=='人工已改动'


def test_formal_gate_blocks_unselected_group_even_when_answer_is_clear(tmp_path):
    store=Store(tmp_path)
    p=prepared(store)
    q=p['questions'][0]
    q.update(status='answered',answer='已核对的一个值。',blocking=True,
             decision_coverage='clear',understanding_status='applied',
             decision_pending_group_ids=['G-not-selected'])
    q['decision_coverage_review']=dict(answer_hash=digest(q['answer']),
                                       question_hash=digest({key:q.get(key) for key in
            ('id','topic_key','question','why','options','blocking','blocking_stage',
             'related_refs','source_refs','decision_group_id')}))
    assert q['decision_coverage_review']['question_hash']==question_fingerprint(q)
    rows=blockers(p,3)
    assert any(row['code']=='decision_changes_pending' for row in rows)
