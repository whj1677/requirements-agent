"""Strict review transport: synthetic HTTP, isolated stores, no model calls."""
import asyncio
import copy
import json
import pytest
from app import detail_semantics as semantics,detail_review_repair as repair
from app.core import Problem,dumps
from app.provider import Provider,DEFAULT,origin
from app.workflow import Workflow
from app.actions import Actions
from tests.test_detail_review_repair import sample,broken,patch_for
from tests.test_detail_review_patch_workflow import PatchProvider
from tests.test_requirement_recommendation import project,target
from tests.test_runtime import FakeProvider

TRANSPORT='responses-json-schema-v1'


def header(messages):return json.loads(messages[0]['content'].split('可信任务头：',1)[1])


def test_factories_preserve_exact_schemas_and_legacy_defaults(tmp_path):
    plan,good=sample(tmp_path);packet=repair.prepare(plan,broken(good))
    before=copy.deepcopy((plan,packet))
    for factory,args in ((semantics.messages,(plan,)),(repair.messages,(plan,packet))):
        legacy=factory(*args);modern=factory(*args,transport_protocol=TRANSPORT)
        old,new=header(legacy),header(modern)
        assert 'transport_protocol' not in old and 'strict_json_protocol' not in old
        assert new.pop('transport_protocol')==TRANSPORT
        assert new.pop('strict_json_protocol')=='detail-review-strict-json-1'
        assert new==old and legacy[1:]==modern[1:]
        with pytest.raises(Problem):factory(*args,transport_protocol='unsupported')
    assert (plan,packet)==before


@pytest.mark.parametrize('mode,limit,expected', [('pass',7,4),('pass',3,3),('http',7,4),('cancel',7,4),('missing_hash',7,4)])
def test_actual_provider_routes_both_review_and_patch_without_fallback(tmp_path,monkeypatch,mode,limit,expected):
    import httpx
    from tests.provider_transport import reply
    bodies=[];fake=PatchProvider('pass');real_client=httpx.AsyncClient
    class NoEvidence:
        def before_send(self,*a):pass
        def response(self,*a,**kw):pass
    async def run():
        store,p=project(tmp_path);provider=Provider(env_path=tmp_path/'absent.env');provider.keys[origin(DEFAULT)]='synthetic-key'
        workflow=Workflow(store,provider);config=dict(workflow.config('clarify'),max_calls=limit)
        async def respond(request):
            body=json.loads(request.content);bodies.append(body)
            messages=body.get('messages',body.get('input'));h=header(messages)
            if len(bodies)>2:
                assert request.url.path.endswith('/responses')
                assert body['text']['format']['schema']==h['schema']
                assert h['strict_json_protocol']=='detail-review-strict-json-1'
                assert body['reasoning']['effort']=='high' and body['max_output_tokens']==65536
            is_patch=h.get('response_protocol')==repair.PROTOCOL
            if is_patch and mode=='http':return httpx.Response(400,json={'error':{'message':'synthetic unsupported schema'}})
            result,_=await fake.request({},messages,NoEvidence())
            if is_patch and mode=='missing_hash':result.pop('review_hash')
            if is_patch and mode=='cancel':store.update_run(p['id'],task['id'],status='cancelled')
            return reply(request,dumps(result))
        monkeypatch.setattr('app.provider.httpx.AsyncClient',lambda **kw:real_client(**kw,transport=httpx.MockTransport(respond)))
        task=workflow.start(p['id'],p['revision'],'clarify','',discussion_target=target(),config_override=config)
        await workflow.tasks[task['id']]
        saved=store.get_record(p['id'],task['id'],'run')
        assert len(bodies)==saved['calls']==saved['request_budget']['used']==expected
        records=[json.loads((store.folder/'evidence/model-calls'/(a['call_id']+'.json')).read_text('utf-8')) for a in saved['attempts']]
        for record in records[2:]:
            effective=record['authorization']['effective_config']
            assert effective['detail_review_transport_protocol']==TRANSPORT and effective['timeout']==300
            if record.get('final_output'):assert record['response_protocol']=='responses' and 'syntax_normalization' not in record
        if mode=='pass' and limit==7:assert saved['business_draft_review']['detail_semantic_reviews']['R1']['status']=='passed'
        if mode=='cancel':assert saved['status']=='cancelled' and not saved.get('result_applied')
        if mode=='missing_hash':
            assert 'review_hash' not in json.loads(records[-1]['final_output'])
            assert saved['business_draft_review']['detail_semantic_reviews']['R1']['status']=='pending'
    asyncio.run(run())


@pytest.mark.parametrize('bad',['{"a":1','{"a":1,"a":1}','{"a":"bad"quote"}'])
def test_strict_json_never_invokes_local_recovery_and_retains_raw(tmp_path,monkeypatch,bad):
    import httpx
    from tests.provider_transport import reply
    from app.model_evidence import ModelCallEvidence
    plan,_=sample(tmp_path);messages=semantics.messages(plan,transport_protocol=TRANSPORT)
    real_client=httpx.AsyncClient;seen=[]
    async def respond(request):seen.append(request);return reply(request,bad)
    monkeypatch.setattr('app.provider.httpx.AsyncClient',lambda **kw:real_client(**kw,transport=httpx.MockTransport(respond)))
    def forbidden(*a,**kw):raise AssertionError('strict review invoked JSON repair')
    for name in ('schema_closers','business_containers','question_root_close','identical_members'):
        monkeypatch.setattr('app.model_json.'+name,forbidden)
    provider=Provider(env_path=tmp_path/'absent.env');provider.keys[origin(DEFAULT)]='synthetic-key'
    evidence=ModelCallEvidence(tmp_path,'CALL-strict','RUN-test','clarify',DEFAULT['model'],origin(DEFAULT),None,'synthetic-key')
    with pytest.raises(Problem) as caught:asyncio.run(provider.request(DEFAULT,messages,evidence))
    assert caught.value.code=='SCHEMA_INVALID' and len(seen)==1
    assert evidence.value['final_output']==bad and 'syntax_normalization' not in evidence.value


@pytest.mark.parametrize('fault',['model','json_mode','origin','capability','marker'])
def test_marked_unsupported_effective_configuration_fails_before_review_send(tmp_path,fault):
    async def run():
        store,p=project(tmp_path);provider=PatchProvider('pass');workflow=Workflow(store,provider)
        config=dict(workflow.config('clarify'),max_calls=7)
        if fault=='model':config['detail_review_options']['model']='unsupported'
        elif fault=='json_mode':config['detail_review_options']['json_mode']=False
        elif fault=='origin':config['detail_review_options']['base_url']='https://unsupported.invalid'
        elif fault=='capability':config['detail_review_options']['structured_output_protocol']=None
        else:config['detail_review_transport_protocol']='unsupported'
        task=workflow.start(p['id'],p['revision'],'clarify','',discussion_target=target(),config_override=config)
        await workflow.tasks[task['id']]
        saved=store.get_record(p['id'],task['id'],'run')
        assert len(provider.received)==saved['calls']==2
        assert 'DETAIL_REVIEW_TRANSPORT_INVALID' in dumps(saved)
    asyncio.run(run())


def test_plan_freezes_transport_and_requirement_next_step_keeps_partial_prefix(tmp_path):
    async def run():
        store,p=project(tmp_path);provider=PatchProvider('bad_patch');workflow=Workflow(store,provider);actions=Actions(store,workflow)
        body=dict(expected_revision=p['revision'],action='clarify',message='',document_type='prd',target=target(),max_calls=4)
        plan=actions.plan(p['id'],body)
        assert plan['detail_review_transport']['clarify']['protocol']==TRANSPORT and plan['max_calls']==4
        task=actions.start(p['id'],body,plan['plan_hash'],'strict-next-step',False)
        await actions.jobs[task['id']]
        finished=store.get_record(p['id'],task['id'],'user_task')
        assert finished['status']=='partial'
        assert finished['message'].startswith('可用内容已处理，仍有资料或输出限制')
        assert '需求确认表' in finished['message'] and '文档页更新讨论稿' not in finished['message']
    asyncio.run(run())


def test_document_discussion_keeps_document_next_step(tmp_path):
    from tests.test_document_discussion_scope import project as document_project,discussion_response
    async def run():
        store,p=document_project(tmp_path,False);value=discussion_response();value.pop('product_context_proposal')
        value.update(proposals=[],questions=[])
        value['result']=dict(changes=[],impacted_refs=[],unimpacted_refs=[],needs_new_confirmation=False,unresolved_effects=[])
        document=p['documents']['prd'];local=dict(kind='document',id=document['id'],section_id=document['content']['sections'][0]['section_id'])
        actions=Actions(store,Workflow(store,FakeProvider([value])))
        body=dict(expected_revision=p['revision'],action='change',message='只纠正本节措辞',document_type='prd',target=local)
        plan=actions.plan(p['id'],body);task=actions.start(p['id'],body,plan['plan_hash'],'document-next-step',False)
        await actions.jobs[task['id']]
        finished=store.get_record(p['id'],task['id'],'user_task')
        assert finished['status']=='succeeded' and '文档页更新讨论稿' in finished['message']
        assert '需求确认表' not in finished['message']
    asyncio.run(run())
