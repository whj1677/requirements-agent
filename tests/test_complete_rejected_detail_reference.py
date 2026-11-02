"""Offline full-reference identity and unchanged normal-generation pipeline."""
import asyncio
import copy
import json
import pytest
from app.core import digest, dumps, execution_hash
from app.ingest_recovery import rejected_detail_reference
from app.workflow import Workflow
from tests.test_requirement_recommendation import project, target
from tests.test_detail_semantic_workflow import SemanticProvider, generated_detail
from unittest.mock import patch


def fixture(tmp_path, limit=3):
    async def make():
        store,p=project(tmp_path)
        provider=SemanticProvider('still_bad');workflow=Workflow(store,provider)
        config=dict(workflow.config('clarify'),max_calls=limit)
        def sourced_detail():
            detail=generated_detail()
            detail['fields'][0]['evidence']['meaning']=dict(status='source',sources=[dict(source='S1',locator='L1')])
            return detail
        with patch('tests.test_detail_semantic_workflow.generated_detail',sourced_detail):
            run=workflow.start(p['id'],p['revision'],'clarify','',discussion_target=dict(target(),detail_required=True),config_override=config)
            await workflow.tasks[run['id']]
        saved=store.get_record(p['id'],run['id'],'run')
        assert saved['status']=='partial' and saved['calls']==(3 if limit==3 else 5) and saved['result_applied']
        p=store.get(p['id'])
        ex=[dict(e,source_id=s['id']) for s in p['sources'] for e in s['excerpts']]
        scope=dict(requirement_id=target()['id'],detail_required=True)
        return store,p,ex,dict(requirement_recommendation_scope=scope),saved
    return asyncio.run(make())


def test_final_fields_and_missing_refs_preserved_and_normal_main_still_runs(tmp_path):
    store,p,ex,run,old=fixture(tmp_path)
    before=copy.deepcopy(p)
    ref=rejected_detail_reference(store,p,run,{},ex)
    assert ref['status']=='unaccepted_ai_candidate',ref
    raw=p['business_draft_reviews'][-1]['field_rejected'][0]['raw']
    assert ref['detail_contract']==raw and raw['fields']
    assert 'default_scope_review' not in ref and 'passed' not in ref
    assert ref['identity']['run_id']==old['id']
    assert store.get(p['id'])==before
    async def normal():
        provider=SemanticProvider();workflow=Workflow(store,provider)
        task=workflow.start(p['id'],p['revision'],'clarify','',discussion_target=dict(target(),detail_required=True),
            config_override=dict(workflow.config('clarify'),max_calls=4))
        await workflow.tasks[task['id']]
        saved=store.get_record(p['id'],task['id'],'run')
        assert saved['rejected_detail_reference']['status']=='unaccepted_ai_candidate'
        assert saved['calls']==len(provider.received)==4
        payload=json.loads(provider.received[0][1]['content'][0]['text'])
        assert payload['current_rows'][0]['rejected_detail_candidate']['detail_contract']==raw
        assert len(payload['current_rows'])==1
        assert saved['request_budget']['used']==4
        assert saved['business_draft_review']['detail_semantic_reviews']['R1']['status']=='passed'
    asyncio.run(normal())
    assert store.get_record(p['id'],old['id'],'run')==old


@pytest.mark.parametrize('fault',['source','answer','selection','other_row','raw','call','wrong_project','alias','local_ref'])
def test_stale_or_unverifiable_reference_not_reused(tmp_path,fault):
    store,p,ex,run,old=fixture(tmp_path)
    if fault=='source':p['sources'][0]['version']+=1
    elif fault=='answer':p['questions'][0]['answer']='a different saved decision'
    elif fault=='selection':p['items'][0]['selection_status']='deferred'
    elif fault=='other_row':run['requirement_recommendation_scope']['requirement_id']='REQ-other'
    elif fault=='raw':p['business_draft_reviews'][-1]['field_rejected'][0]['raw']['fields'][0]['meaning']='tampered'
    elif fault=='wrong_project':p['id']='P-other'
    elif fault=='alias':ex[0]['text']='changed source excerpt'
    elif fault=='local_ref':p['business_draft_reviews'][-1]['field_rejected'][0]['raw']['depends_on']=['R999']
    else:
        a=old['attempts'][0]
        path=store.folder/'evidence/model-calls'/(a['call_id']+'.json')
        value=json.loads(path.read_text('utf-8'));value['request_input']='[]';path.write_text(dumps(value),'utf-8')
    ref=rejected_detail_reference(store,p,run,{},ex)
    assert ref['status']=='not_reused' and ref['reason']


def test_current_task_input_is_only_allowed_source_addition(tmp_path):
    from app.sources import save_source
    store,p,ex,run,old=fixture(tmp_path)
    with store.edit(p['id'],p['revision'],'保存当前任务输入为来源') as (current,db):
        source=save_source(store,'本次反馈.txt','只核对旧引用'.encode(),'goal')
        current['sources'].append(source);current['current_task_source_id']=source['id']
    from app.actions import target_context
    p=store.get(p['id']);target_value=dict(target(),detail_required=True);original='只核对旧引用'
    run.update(id='RUN-new',user_task_id='TASK-new',revision=p['revision'],discussion_target=target_value,
        message='本次局部讨论对象：'+target_context(p,target_value)+'\n仅围绕此对象提出候选；保留其他需求、未知和采纳状态。\n用户修改意图：'+original)
    store.record(p['id'],'user_task',dict(action='clarify',run_ids=['RUN-new'],input_revision=p['revision'],input_state_hash=execution_hash(p),
        retry_input=dict(message=original,target=target_value)),'TASK-new')
    ex=[dict(e,source_id=s['id']) for s in p['sources'] for e in s['excerpts']]
    # Current aliases can change independently of the old stable source identity.
    ex=[e for e in ex if e['source_id']==source['id']]+[e for e in ex if e['source_id']!=source['id']]
    result=rejected_detail_reference(store,p,run,{},ex)
    assert result['status']=='unaccepted_ai_candidate',result
    proof=result['detail_contract']['fields'][0]['evidence']['meaning']
    assert proof['sources']==[dict(source='S2',locator='L1')]
    old_raw=p['business_draft_reviews'][-1]['field_rejected'][0]['raw']
    assert old_raw['fields'][0]['evidence']['meaning']['sources']==[dict(source='S1',locator='L1')]
    assert result['detail_contract']['fields'][0]['meaning']==old_raw['fields'][0]['meaning']
    p['sources'][-1]['sha256']='changed'
    assert rejected_detail_reference(store,p,run,{},ex)['status']=='not_reused'


def test_latest_corrected_detail_not_original_main(tmp_path):
    store,p,ex,run,old=fixture(tmp_path,6)
    result=rejected_detail_reference(store,p,run,{},ex)
    assert result['status']=='unaccepted_ai_candidate',result
    main=json.loads((store.folder/'evidence/model-calls'/(old['attempts'][0]['call_id']+'.json')).read_text('utf-8'))
    original=json.loads(main['final_output'])['requirements'][0]['detail_contract']
    assert result['detail_contract']['fields'][0]['required']=='填写时必填'
    assert result['detail_contract']!=original


@pytest.mark.parametrize('message',['核对原详细候选。','原话包含用户修改意图：请不要按这几个字截断。'])
def test_real_actions_entry_attaches_complete_reference(tmp_path,monkeypatch,message):
    store,p,ex,run,old=fixture(tmp_path)
    from app.actions import Actions,target_context
    import app.ingest_recovery as recovery
    observed=[];original=recovery.rejected_detail_reference
    def capture(store,project,run,context,excerpts):
        result=original(store,project,run,context,excerpts)
        observed.append((copy.deepcopy(project),copy.deepcopy(run),copy.deepcopy(context),copy.deepcopy(excerpts),result))
        return result
    monkeypatch.setattr(recovery,'rejected_detail_reference',capture)
    async def execute():
        provider=SemanticProvider();workflow=Workflow(store,provider);actions=Actions(store,workflow)
        body=dict(expected_revision=p['revision'],action='clarify',message=message,document_type='prd',
            target=dict(target(),detail_required=True),max_calls=4)
        plan=actions.plan(p['id'],body)
        task=actions.start(p['id'],body,plan['plan_hash'],'normal-wrapped-reference')
        await actions.jobs[task['id']]
        finished=store.get_record(p['id'],task['id'],'user_task')
        assert finished['calls']==4 and len(provider.received)==4
        sent=json.loads(provider.received[0][1]['content'][0]['text'])
        assert sent['current_rows'][0]['rejected_detail_candidate']['detail_contract']['fields']
        assert sent['current_rows'][0]['rejected_detail_candidate']['status']=='unaccepted_ai_candidate'
    asyncio.run(execute())
    current,actual,context,excerpts,result=observed[-1]
    assert result['status']=='unaccepted_ai_candidate',result
    assert actual['message']=='本次局部讨论对象：'+target_context(current,actual['discussion_target'])+'\n仅围绕此对象提出候选；保留其他需求、未知和采纳状态。\n用户修改意图：'+message
    assert len(current['sources'])==len(p['sources'])+1
    assert store.get_record(p['id'],old['id'],'run')==old
    # Re-evaluate only read-only copied inputs; no additional Provider calls.
    for fault in ('target','other_task','extra_source','message'):
        bad_project=copy.deepcopy(current);bad_run=copy.deepcopy(actual)
        if fault=='target':bad_run['discussion_target']['id']='REQ-other'
        elif fault=='other_task':bad_run['user_task_id']='TASK-missing'
        elif fault=='extra_source':
            extra=copy.deepcopy(bad_project['sources'][-1]);extra['id']='SRC-unrelated';bad_project['sources'].append(extra)
        else:bad_run['message']+='different intent'
        denied=original(store,bad_project,bad_run,context,excerpts)
        assert denied['status']=='not_reused',fault


@pytest.mark.parametrize('fault',[None,'message','target','source','answer'])
def test_real_actions_cancel_then_same_input_chain(tmp_path,monkeypatch,fault):
    from app.actions import Actions
    from app.sources import save_source
    from app.business_draft import PROTOCOL
    store,p,ex,run,old=fixture(tmp_path)
    message='用户修改意图：仅核对原候选，保留所有原证据。'
    class CancelMain(SemanticProvider):
        async def request(self,config,messages,evidence=None):
            header=json.loads(messages[0]['content'].split('可信任务头：',1)[1])
            if header.get('response_protocol')==PROTOCOL:
                evidence.before_send(config);self.received.append(copy.deepcopy(messages))
                raise asyncio.CancelledError()
            return await super().request(config,messages,evidence)
    async def execute():
        first_provider=CancelMain();first_workflow=Workflow(store,first_provider);first_actions=Actions(store,first_workflow)
        body=dict(expected_revision=p['revision'],action='clarify',message=message,document_type='prd',
            target=dict(target(),detail_required=True),max_calls=4)
        plan=first_actions.plan(p['id'],body);first=first_actions.start(p['id'],body,plan['plan_hash'],'cancel-before-reference-retry')
        await first_actions.jobs[first['id']]
        first_task=store.get_record(p['id'],first['id'],'user_task')
        assert first_task['status']=='cancelled' and first_task['completed_steps']==0
        first_run=store.get_record(p['id'],first_task['run_ids'][0],'run')
        assert first_run['status']=='cancelled' and not first_run.get('result_applied')
        assert first_run['rejected_detail_reference']['status']=='unaccepted_ai_candidate'
        current=store.get(p['id']);sources=copy.deepcopy(current['sources'])
        if fault in ('source','answer'):
            with store.edit(p['id'],current['revision'],'合成外部变化') as (changed,db):
                if fault=='source':changed['sources'].append(save_source(store,'其它资料.txt','其它内容'.encode(),'goal'))
                else:changed['questions'][0]['answer']='不同已保存答案'
            current=store.get(p['id'])
        second_provider=SemanticProvider();second_workflow=Workflow(store,second_provider);second_actions=Actions(store,second_workflow)
        body.update(expected_revision=current['revision'])
        if fault=='message':body['message']+='不同内容'
        if fault=='target':body['target']=dict(kind='item',id='REQ-other',detail_required=True)
        plan=second_actions.plan(p['id'],body);second=second_actions.start(p['id'],body,plan['plan_hash'],'normal-same-input-retry')
        await second_actions.jobs[second['id']]
        task=store.get_record(p['id'],second['id'],'user_task');actual=store.get_record(p['id'],task['run_ids'][0],'run')
        assert actual['rejected_detail_reference']['status']==('unaccepted_ai_candidate' if fault is None else 'not_reused')
        payload=json.loads(second_provider.received[0][1]['content'][0]['text'])
        if fault is None:
            assert payload['current_rows'][0]['rejected_detail_candidate']['detail_contract']['fields']
            assert actual['calls']==4 and actual['request_budget']['used']==4
        assert store.get_record(p['id'],first_task['run_ids'][0],'run')==first_run
        assert store.get(p['id'])['sources'][:len(sources)]==sources
    asyncio.run(execute())
