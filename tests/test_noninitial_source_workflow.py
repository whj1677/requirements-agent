"""Offline source-review protocol and state safety, not a semantic oracle."""
import asyncio,copy,json
import pytest
from app import intake_grounding as g
from app.core import Problem,dumps,digest
from app.workflow import Workflow
from tests.test_detail_bounded_corrections import BoundedProvider
from tests.test_requirement_recommendation import project,target
from tests.test_business_draft import packet,draft_row

class SourceProvider(BoundedProvider):
    def __init__(self,*,source_failures=0,source_error=None,**kwargs):
        super().__init__(**kwargs);self.source_failures=source_failures;self.source_error=source_error
        self.source_reviews=0;self.source_patches=0;self.source_packets=[]
    async def request(self,config,messages,evidence=None):
        h=json.loads(messages[0]['content'].split('可信任务头：',1)[1]);p=json.loads(messages[1]['content'][0]['text'])
        source=p.get('fields') and all(f['path'][0]=='requirements' for f in p['fields'])
        if not source:return await super().request(config,messages,evidence)
        evidence.before_send(config);self.received.append(copy.deepcopy(messages));self.configs.append(copy.deepcopy(config))
        self.source_packets.append(copy.deepcopy(p))
        if h.get('grounding_protocol')==g.PATCH_VERSION:
            self.source_patches+=1;slot=next(s for s in p['slots'] if s['path'][-1]=='statement')
            value=copy.deepcopy(p['candidate']['requirements'][0]['statement'])
            value['text']='仅在来源限定条件下处理，第'+str(self.source_patches)+'次核对'
            result=dict(candidate_hash=p['candidate_hash'],patches=[dict(slot=slot['id'],value=value,reason='保留主体与原前提')])
        else:
            self.source_reviews+=1
            if self.source_error=='timeout':raise Problem('TIMEOUT','offline source timeout')
            if self.source_error=='cancel':raise asyncio.CancelledError()
            if self.source_error=='cancelled_state':raise Problem('CANCELLED','Run cancelled before accepting source response')
            result=dict(candidate_hash=p['candidate_hash'],input_hash=p['input_hash'],checked_field_ids=[f['id'] for f in p['fields']],findings=[])
            if self.source_error=='tamper':result['input_hash']='other input'
            if self.source_reviews<=self.source_failures:
                field=next(f for f in p['fields'] if f['path'][-1]=='statement')
                result['findings']=[dict(id=field['id'],quote=field['text'],reason='候选遗漏来源限定前提',sources=[])]
        evidence.response(dumps(result),http_status=200,finish_reason='stop',usage={'total_tokens':10})
        return result,dict(finish_reason='stop',usage={'total_tokens':10})

@pytest.mark.parametrize('failures,limit,error,calls,patches,applied',[
    (0,None,None,4,0,True),(2,None,None,8,2,True),(3,None,None,6,2,False),
    (1,3,None,2,0,False),(0,None,'timeout',2,0,False),
    (0,None,'tamper',3,0,False),(0,None,'cancel',2,0,False),(0,None,'cancelled_state',2,0,False)])
def test_noninitial_body_source_gate_precedes_detail_and_preserves_old_state(tmp_path,failures,limit,error,calls,patches,applied):
    async def run():
        store,p=project(tmp_path);before=copy.deepcopy(p)
        provider=SourceProvider(source_failures=failures,source_error=error,fail_reviews=0);w=Workflow(store,provider)
        config=dict(w.config('clarify'),max_calls=limit)
        task=w.start(p['id'],p['revision'],'clarify','',discussion_target=dict(target(),detail_required=True),config_override=config)
        await w.tasks[task['id']];r=store.get_record(p['id'],task['id'],'run')
        assert r['calls']==len(provider.received)==calls
        assert provider.source_patches==patches
        if error in ('cancel','cancelled_state'):assert r['status']=='cancelled' and r['error']=='CANCELLED'
        if limit==3:assert r['status']=='paused_budget' and r['error']=='BUDGET_EXHAUSTED'
        assert bool(r.get('result_applied'))==applied
        assert store.get(p['id'])['items'][:len(before['items'])]==before['items']
        if applied:
            assert r['business_draft_review']['business_source_review']['status']=='passed'
            assert r['business_draft_review']['source_validation']=='body_source_semantic_checked_not_human_approval'
            assert r['business_source_review']['source_only'] is True and r['business_source_review']['coverage_review'] is False
            assert r['business_draft_review']['detail_semantic_reviews']['R1']['status']=='passed'
        else:
            assert not provider.reviews
            assert store.get(p['id'])['items']==before['items']
            assert r['business_source_review']['status']!='passed'
        assert all(c['max_calls']==limit for c in provider.configs)
        assert all(not any(f['path'][0]!='requirements' or f['path'][2] not in ('statement','behavior','acceptance') for f in a['fields']) for a in provider.source_packets)
    asyncio.run(run())

def test_source_slots_preserve_unknown_and_candidate_authority_and_per_row_target_whitelist():
    row=draft_row();row['statement']['basis']='unknown';row['statement']['text']='历史记录只读；其他细节待定'
    row['acceptance'][0]['target_ref']='C7'
    row2=copy.deepcopy(row);row2['row_key']='R2'
    c=packet(row);c['requirements'].append(row2)
    context=dict(excerpts=[dict(source='S1',locator='L1',text='释放后历史只读')],current_rows=[
        dict(row_key='R1',editable_acceptance_refs=['C7'],related_clauses=[dict(item_ref='C7',selection_status='candidate')]),
        dict(row_key='R2',editable_acceptance_refs=['C9'])])
    p=g.source_plan(c,context)
    assert any(f['basis']=='unknown' and f['text']==row['statement']['text'] for f in p['fields'])
    assert p['context']==context and p['candidate']==c
    messages=g.challenge_messages(p)
    header=json.loads(messages[0]['content'].split('可信任务头：',1)[1])
    assert header['source_only'] is True and header['scope']==p['scope']=='generated_requirement_statement_behavior_acceptance_only'
    sent=json.loads(messages[1]['content'][0]['text'])
    assert sent['candidate']==c and sent['context']==context
    assert sent['candidate_hash']==digest(c) and sent['input_hash']==digest(context)
    initial=g.challenge_messages(g.prepare(c,context))
    initial_header=json.loads(initial[0]['content'].split('可信任务头：',1)[1])
    assert set(initial_header)=={'response_protocol','grounding_protocol','schema'}
    assert initial[0]['content']==g.CHALLENGE_PROMPT+'\n可信任务头：'+dumps(initial_header)
    for field in sent['fields']:
        assert [(ref['source'],ref['locator']) for ref in field['own_sources']]==[(ref['source'],ref['locator']) for ref in field['sources']]
    assert any(field['path']==['requirements',0,'acceptance',0] for field in sent['fields'])
    field=next(f for f in p['fields'] if f['path']==['requirements',0,'acceptance',0])
    review=dict(findings=[dict(path=field['path'],reason='缺自身引文')])
    schema,msg=g.source_correction_messages(p,review);payload=json.loads(msg[1]['content'][0]['text'])
    assert [s['path'] for s in payload['slots']]==[['requirements',0,'acceptance']]
    slot=payload['slots'][0];value=copy.deepcopy(row['acceptance']);value[0]['target_ref']='C9'
    with pytest.raises(Problem):g.apply_correction(p,dict(candidate_hash=p['candidate_hash'],patches=[dict(slot=slot['id'],value=value,reason='跨行')]),schema)
    value[0]['target_ref']='C7';value[0]['text']+='（原限定）'
    result=g.apply_correction(p,dict(candidate_hash=p['candidate_hash'],patches=[dict(slot=slot['id'],value=value,reason='保留条件')]),schema)
    assert result['requirements'][1]==row2 and result['requirements'][0]['statement']==row['statement']
    assert result['requirements'][0]['acceptance'][0]['target_ref']=='C7'

class TwoDetailChallenges(SourceProvider):
    def __init__(self,**kwargs):super().__init__(source_failures=2,fail_reviews=0,patches=(3,),**kwargs);self.detail_challenges=0
    async def request(self,config,messages,evidence=None):
        h=json.loads(messages[0]['content'].split('可信任务头：',1)[1]);p=json.loads(messages[1]['content'][0]['text'])
        if h.get('grounding_protocol')==g.CHALLENGE_VERSION and p.get('fields') and p['fields'][0]['path'][0]!='requirements':
            evidence.before_send(config);self.received.append(copy.deepcopy(messages));self.configs.append(copy.deepcopy(config));self.detail_challenges+=1
            result=dict(candidate_hash=p['candidate_hash'],input_hash=p['input_hash'],checked_field_ids=[f['id'] for f in p['fields']],findings=[])
            if self.detail_challenges<=2:
                field=next(f for f in p['fields'] if f['path'][1]=='detail_contract.fields.F1.required')
                result['findings']=[dict(id=field['id'],quote=field['text'],reason='条件需保持来源范围',sources=[])]
            evidence.response(dumps(result),http_status=200,finish_reason='stop',usage={'total_tokens':10})
            return result,dict(finish_reason='stop',usage={'total_tokens':10})
        return await super().request(config,messages,evidence)

@pytest.mark.parametrize('limit,prior,calls,passed',[(None,0,15,True),(None,2,17,True),(None,3,16,False),(10,0,8,False)])
def test_two_source_and_two_detail_corrections_plus_single_protocol_repair(tmp_path,monkeypatch,limit,prior,calls,passed):
    async def run():
        store,p=project(tmp_path);provider=TwoDetailChallenges();w=Workflow(store,provider)
        execute=w.execute
        async def counted_optional_prefix(pid,rid,project,config):
            # Already counted optional memory/preflight calls consume the same
            # persistent run limit; these are simulated ledger entries, not HTTP.
            if prior:store.update_run(pid,rid,calls=prior)
            await execute(pid,rid,project,config)
        monkeypatch.setattr(w,'execute',counted_optional_prefix)
        config=dict(w.config('clarify'),max_calls=limit)
        r=w.start(p['id'],p['revision'],'clarify','',discussion_target=dict(target(),detail_required=True),config_override=config)
        await w.tasks[r['id']];saved=store.get_record(p['id'],r['id'],'run')
        assert saved['calls']==calls and len(provider.received)==calls-prior
        assert provider.source_patches==2 and provider.corrections<=2 and provider.patch_count<=1
        outcome=saved['business_draft_review']['detail_semantic_reviews']['R1']
        assert (outcome['status']=='passed')==passed
        assert saved['business_draft_review']['business_source_review']['status']=='passed'
        if passed:assert provider.corrections==2 and provider.patch_count==1 and provider.detail_challenges==3
        else:
            assert not any(i.get('detail_contract') for i in saved['response']['proposals'])
            assert ('DETAIL_RUN_CALL_LIMIT' if prior else 'BUDGET_EXHAUSTED') in dumps(saved)
        assert saved['provider']['max_calls']==limit
    asyncio.run(run())

def test_new_plan_exposes_exact_sum_and_does_not_change_user_budget(tmp_path):
    from app.actions import Actions
    store,p=project(tmp_path);w=Workflow(store,SourceProvider());a=Actions(store,w)
    body=dict(expected_revision=p['revision'],action='clarify',message='',document_type='prd',target=dict(target(),detail_required=True),max_calls=4)
    plan=a.plan(p['id'],body);policy=plan['detail_review_entry_repair']['clarify']
    assert plan['max_calls']==4
    assert sum(policy['request_components'].values())==policy['all_run_provider_requests_max']==17
    assert policy['max_protocol_repairs_per_run']==1
    assert plan['business_source_review']['clarify']['additional_requests_max']==5
    assert plan['business_source_review']['clarify']['coverage_review'] is False

@pytest.mark.parametrize('basis',['unknown','recommendation'])
@pytest.mark.parametrize('bad_body_source',[False,True])
def test_source_patch_accepts_independent_body_with_existing_bad_detail(tmp_path,basis,bad_body_source):
    from tests.test_detail_semantic_workflow import generated_detail
    from app.business_draft import PROTOCOL
    class PartialProvider(SourceProvider):
        async def request(self,config,messages,evidence=None):
            h=json.loads(messages[0]['content'].split('可信任务头：',1)[1]);p=json.loads(messages[1]['content'][0]['text'])
            main=h.get('response_protocol')==PROTOCOL and not h.get('grounding_protocol')
            bad_patch=bad_body_source and h.get('grounding_protocol')==g.PATCH_VERSION
            if not main and not bad_patch:return await super().request(config,messages,evidence)
            evidence.before_send(config);self.received.append(copy.deepcopy(messages));self.configs.append(copy.deepcopy(config))
            if main:
                row=draft_row();row['statement']['basis']=basis;row['detail_contract']=generated_detail()
                row['detail_contract']['flow']['field_refs']=['F1']
                result=packet(row)
            else:
                self.source_patches+=1;slot=next(s for s in p['slots'] if s['path'][-1]=='statement')
                value=copy.deepcopy(p['candidate']['requirements'][0]['statement']);value['text']='修补仍含非法来源'
                value['sources']=[dict(source='S404',locator='L1')]
                result=dict(candidate_hash=p['candidate_hash'],patches=[dict(slot=slot['id'],value=value,reason='无效引用负例')])
            evidence.response(dumps(result),http_status=200,finish_reason='stop',usage={'total_tokens':10})
            return result,dict(finish_reason='stop',usage={'total_tokens':10})
    async def run():
        store,p=project(tmp_path)
        with store.edit(p['id'],p['revision'],'合成既有详细定义') as (edited,_):edited['items'][0]['detail_contract']=generated_detail()
        p=store.get(p['id']);provider=PartialProvider(source_failures=1,fail_reviews=0);w=Workflow(store,provider)
        task=w.start(p['id'],p['revision'],'clarify','',discussion_target=dict(target(),detail_required=True))
        await w.tasks[task['id']];saved=store.get_record(p['id'],task['id'],'run');after=store.get(p['id'])
        assert saved['calls']==len(provider.received)==(3 if bad_body_source else 4)
        assert provider.source_patches==1 and not provider.reviews
        assert after['items'][:len(p['items'])]==p['items']
        if bad_body_source:
            assert saved['error']=='BUSINESS_SOURCE_BLOCKED' and not saved.get('result_applied')
            assert after['items']==p['items']
        else:
            assert saved['status']=='partial' and saved['result_applied']
            report=saved['business_draft_review']
            assert report['business_source_review']['status']=='passed' and report['complete'] is False
            assert any(x['path']==['requirements',0,'detail_contract'] for x in report['field_rejected'])
            accepted=report['accepted'][0]
            assert accepted['raw']['statement']['basis']==basis
            assert accepted['raw']['statement']['text']=='仅在来源限定条件下处理，第1次核对'
            assert accepted['raw']['detail_contract']['flow']['field_refs']==['F1']
            assert all('field_refs' not in (item.get('detail_contract') or {}).get('flow',{}) for item in after['items'][len(p['items']):])
    asyncio.run(run())

@pytest.mark.parametrize('field',['statement','behavior','acceptance'])
def test_source_patch_identity_allows_unknown_without_promoting_it(tmp_path,field):
    from tests.test_business_draft import setup,business_value
    from app.business_draft import adapt
    p,local,_,messages,excerpts,_=setup(tmp_path)
    candidate=packet(draft_row());context=json.loads(messages[1]['content'][0]['text'])
    plan=g.source_plan(candidate,context)
    path=['requirements',0,field]+(['actor'] if field=='behavior' else [])
    slot=next(s for s in plan['slots'] if s['path']==path)
    value=business_value('该业务选择资料尚未确定','unknown')
    if field=='acceptance':value=[value]
    review=dict(findings=[dict(path=path+([0] if field=='acceptance' else []))])
    schema,_=g.source_correction_messages(plan,review)
    patch=dict(candidate_hash=plan['candidate_hash'],patches=[dict(slot=slot['id'],value=value,reason='未知保持未知')])
    corrected=g.apply_correction(plan,patch,schema)
    _,report=adapt(corrected,p,excerpts,'clarify',local)
    before=copy.deepcopy(report);g.validate_source_correction(plan,patch,report)
    assert report==before
    actual=report['accepted'][0]['raw']
    for key in path[2:]:actual=actual[key]
    assert actual==value and (actual[0] if isinstance(actual,list) else actual)['basis']=='unknown'
    damaged=copy.deepcopy(report);damaged['field_rejected'].append(dict(path=path,code='BUSINESS_DRAFT_SOURCE'))
    with pytest.raises(Problem):g.validate_source_correction(plan,patch,damaged)
