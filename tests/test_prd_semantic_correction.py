import asyncio
import copy
import json
import pytest
from app.core import dumps
from app.workflow import Workflow
from tests.test_local_prd import setup
from tests.test_runtime import FakeProvider


@pytest.mark.parametrize('mode,limit,calls,status',[
    ('correct',2,2,'succeeded'),('wrong',3,3,'failed'),('correct',1,1,'paused_budget')])
def test_exact_paragraph_repair_has_budget_evidence_and_never_edits_canonical(tmp_path,mode,limit,calls,status):
    async def scenario():
        store,p=setup(tmp_path)
        bad=dict(text='现有平台不能说明由谁负责。',evidence_refs=dict(context_refs=[],normative_refs=['REQ-0001'],business_claim_refs=[]))
        plan=dict(plan_version='4',sections=[
            dict(section_key='users',context_refs=[],normative_refs=['REQ-0001'],discussion_refs=[],explanations=[bad]),
            dict(section_key='function',context_refs=[],normative_refs=['REQ-0001'],discussion_refs=[],explanations=[])])
        fixed=copy.deepcopy(bad);fixed['text']='管理员具有当前电站编辑权限时，可以编辑联系人姓名。'
        class Provider(FakeProvider):
            async def request(self,c,m,evidence=None):
                evidence.before_send(c)
                value,meta=await super().request(c,m,evidence)
                evidence.response(dumps(value),http_status=200,finish_reason='stop',usage=meta['usage'])
                return value,meta
        provider=Provider([plan,*[dict(explanation=fixed if mode=='correct' else bad) for _ in range(2)]])
        workflow=Workflow(store,provider);config=dict(workflow.config('prd'),max_calls=limit)
        run=workflow.start(p['id'],p['revision'],'prd','',kind='prd',config_override=config)
        await workflow.tasks[run['id']]
        saved=store.get_record(p['id'],run['id'],'run')
        assert saved['status']==status,saved
        assert saved['calls']==len(provider.received)==calls
        after=store.get(p['id'])
        for key in ('items','sources','questions','product_context','prewrite_review'):
            assert after.get(key)==p.get(key)
        evidence=json.loads((store.folder/'evidence/model-calls'/(saved['attempts'][0]['call_id']+'.json')).read_text('utf-8'))
        assert json.loads(evidence['final_output'])==plan
        if status=='succeeded':
            assert evidence['prd_semantic_corrections'][0]['path']=='sections[0].explanations[0].text'
            normalized=evidence['normalized_prd_plan']
            assert normalized['sections'][1]==plan['sections'][1]
            assert not saved['prd_semantic_pending']
        else:
            assert after.get('documents')==p.get('documents')
            assert saved['pending_prd_plan']==plan and saved['result_applied'] is False
    asyncio.run(scenario())


@pytest.mark.parametrize('text,basis,dimension,instruction',[
    ('现有能力能说明告警发生了什么，却不能说明此刻由谁在跟进。',
     '现有云平台已有告警列表和告警详情。', 'current_capability_absence', '不证明现有系统不能做到其它事情'),
    ('后到的认领请求失败。',
     '并发认领以服务端第一个成功为准。', 'success_order_condition', '按来源恢复成功这一条件'),
])
def test_correction_targets_bind_exact_rejected_claim_to_direct_source(tmp_path,monkeypatch,text,basis,dimension,instruction):
    from app import prd
    from app.core import Problem
    from app.semantic_quality import validate_explanation
    _,p=setup(tmp_path)
    context={'current_state':basis,'intent':'让两名值班员协作处理同一站点告警。'}
    monkeypatch.setattr(prd,'verified_context',lambda project:copy.deepcopy(context))
    original=dict(text=text,evidence_refs=dict(context_refs=['current_state'],normative_refs=[],business_claim_refs=[]))
    untouched=dict(section_key='function',context_refs=[],normative_refs=['REQ-0001'],discussion_refs=[],explanations=[])
    plan=dict(plan_version='4',sections=[dict(section_key='background',context_refs=['current_state'],
        normative_refs=[],discussion_refs=[],explanations=[original]),untouched])
    before=copy.deepcopy((plan,p))
    with pytest.raises(Problem) as caught:
        validate_explanation(text,basis,basis,'sections[0].explanations[0].text')
    prepared=prd.semantic_correction(plan,caught.value,p,'prd')
    packet=json.loads(prepared['messages'][1]['content'][0]['text'])
    target=next(t for t in packet['correction_targets'] if t['dimension']==dimension)
    assert target['quote'] in text and instruction in target['instruction']
    assert packet['direct_evidence']['context_refs']=={'current_state':basis}
    assert packet['context']['intent']==context['intent']
    fixed=copy.deepcopy(original);fixed['text']=basis
    output=prd.apply_semantic_correction(plan,prepared,dict(explanation=fixed))
    validate_explanation(fixed['text'],basis,basis,'sections[0].explanations[0].text')
    assert output['sections'][1]==untouched
    assert output['sections'][0]['explanations'][0]['evidence_refs']==original['evidence_refs']
    assert (plan,p)==before


@pytest.mark.parametrize('text,blocked',[
    ('先到的认领请求成功，后到的认领请求失败。',True),
    ('先到的认领请求成功、后到的认领请求失败。',True),
    ('先到的认领请求成功而后到的认领请求失败。',True),
    ('已被他人成功认领后，后到请求不生效。',False),
    ('先成功后其余拒绝，后到请求不生效。',False),
])
def test_success_premise_does_not_bridge_to_later_arrival(text,blocked):
    from app.semantic_quality import source_condition_issues
    issues=source_condition_issues(text,'并发认领以服务端第一个成功为准。','')
    assert any(i['dimension']=='success_order_condition' for i in issues) is blocked


def test_explicit_source_arrival_rule_is_not_rewritten_to_success_order():
    from app.semantic_quality import source_condition_issues
    rule='先到的认领请求成功，后到的认领请求失败。'
    assert source_condition_issues(rule,rule,'')==[]


@pytest.mark.parametrize('text,blocked',[
    ('后到者的认领请求被拒绝并看到“该告警已被认领”。',True),
    ('被他人成功认领后，后到者的认领请求被拒绝。',False),
    ('对已认领告警，后到者的认领请求被拒绝。',False),
    ('后到者对已被他人认领的告警发起的请求被拒绝。',False),
    ('先到者的认领请求成功，后到者的认领请求被拒绝。',True),
])
def test_rejection_keeps_success_or_claimed_condition(text,blocked):
    from app.semantic_quality import source_condition_issues
    issues=source_condition_issues(text,'并发认领以服务端第一个成功为准。','')
    assert any(i['dimension']=='success_order_condition' for i in issues) is blocked


def test_source_explicitly_supporting_arrival_rejection_remains_valid():
    from app.semantic_quality import source_condition_issues
    rule='后到者的认领请求被拒绝。'
    assert source_condition_issues(rule,rule,'')==[]


def test_generation_excludes_old_document_facts_but_preserves_review_and_current_sources(tmp_path):
    from app.provider import assemble,DEFAULT
    from app.prd import compile_plan
    from app.workflow import apply_response
    from tests.test_semantic_quality_regressions import project,plan,CODEPOINTS
    p=project(tmp_path,CODEPOINTS)
    p['sources'][0]['excerpts'][0]['text']=CODEPOINTS
    p['questions'][0]['answer']=CODEPOINTS
    apply_response(p,compile_plan(plan(CODEPOINTS),p,'prd'))
    before=copy.deepcopy(p)
    for stage in ('prd','review'):
        messages,_,_=assemble(p,stage,'',DEFAULT,tmp_path)
        context=json.loads(messages[1]['content'][0]['text'])
        facts=context['counting_definition_facts']
        origins={f['origin'] for f in facts}
        assert {'current_item','human_answer','source'}<=origins
        assert ('document' in origins)==(stage=='review')
        assert all(f['definition_present'] for f in facts if f['quote']==CODEPOINTS)
    assert p==before
