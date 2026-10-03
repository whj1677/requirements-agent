"""Source-faithful input cards, not a claim of real generated content approval."""
import copy
import json
import asyncio
import pytest
from app.contracts import validate_response
from app.core import digest
from app.generation_context import SOURCE_FIDELITY, RESULT_TASK
from app.provider import assemble, DEFAULT, request_schema
from app.understanding import current_answer
from tests.test_generation_evidence_context import draft, assembled
from tests.test_result_conditions import case, NORMAL

# Exact four independent findings from the synthetic real CALL. Deliberately
# not a product validator: these need source review, even if schema accepts.
FINDINGS=[
    ('$.proposals[2].behavior.permissions','仅本租户管理员可见反馈，不泄露其他租户信息。',
     '向本租户管理员反馈每个配额不满足项的标识及原因，不泄露其他租户信息；沿用既有权限体系。',
     '仅本租户管理员可见反馈；不泄露其他租户信息。','exclusive_visibility'),
    ('$.proposals[3].title','预算不足/拒绝之外的提交结果验收',
     '申请云资源，任一项配额不满足则整批拒绝。',
     '本期处理预算不足时的拒绝反馈。','budget_is_not_quota'),
    ('$.proposals[3].statement','前提：各项配额均不缺失。',
     '全部配额满足仍不能保证成功；服务可以明确拒绝。',
     '校验配额值是否存在；各项配额均不缺失时通过存在性校验。','existence_is_not_sufficiency'),
    ('$.product_context_proposal.value','支持一次提交多项云资源申请，减少逐项提交操作；具体效率收益未量化。',
     '本期新增批量云资源申请。',
     '既有系统需逐项提交；本期改为批量提交，预期减少动作量，收益未量化。','comparison_premise'),
]


def test_answer_result_cards_bind_original_objects_without_schema_or_state_changes(tmp_path):
    p=draft(tmp_path);q=p['questions'][0]
    q.update(status='answered',answer='合成操作者：未知结果先查询原ID，确认前不重复。',understanding_status='pending')
    ref=p['items'][0]['source_refs'][0];q['answer_source_refs']=[ref]
    before=copy.deepcopy(p);schema=copy.deepcopy(request_schema('clarify'))
    header,ctx,ex,omitted=assembled(p,tmp_path,'clarify')
    assert not omitted and p==before and request_schema('clarify')==schema
    policy=header['generation_evidence_contract']
    assert policy['source_fidelity_policy']==SOURCE_FIDELITY and policy['result_task_policy']==RESULT_TASK
    tasks=ctx['result_condition_facts']['local_result_tasks']
    assert tasks==[dict(target_item_id='REQ-original',kind='requirement',question_ids=['Q-original'],
        predicate_locations=sorted({f['location'] for f in [*ctx['result_condition_facts']['facts'],*ctx['result_condition_facts']['clause_assertions']] if f.get('object_id')=='REQ-original'}),
        evidence_role='answer_revision_task_not_adoption')]
    assert ctx['items']==p['items'] and ctx['questions']==p['questions']
    assert ctx['excerpts']==[e for s in p['sources'] for e in s['excerpts']]
    assert any(f['role']=='sufficient_condition' and f['evidence_role']=='unadopted_clause' for f in ctx['result_condition_facts']['clause_assertions'])
    assert not any(f['origin']=='model' for f in ctx['result_condition_facts']['facts'])


@pytest.mark.parametrize('change',['open','superseded','unrelated'])
def test_no_task_card_invents_saved_or_applicable_answer(tmp_path,change):
    p=draft(tmp_path);q=p['questions'][0]
    if change!='open':q.update(status='answered',answer='合成回答',understanding_status='pending')
    if change=='superseded':q['superseded_by']='Q-new'
    if change=='unrelated':q['related_refs']=['REQ-other']
    _,ctx,_,_=assembled(p,tmp_path,'clarify')
    assert ctx['result_condition_facts']['local_result_tasks']==[]
    assert ctx['questions']==p['questions']


@pytest.mark.parametrize('path,quote,unsupported,supported,dimension',FINDINGS)
def test_fixed_source_review_pairs_preserve_exact_quote_and_legal_source(path,quote,unsupported,supported,dimension):
    # These exact historical gap cases now have a bounded source-review gate.
    # Preserve their original quotes and verify both unsupported and legal cases.
    p,v,ex=case(NORMAL);item=v['proposals'][0]
    if path.endswith('permissions'):item['behavior']['permissions']=quote
    elif path.endswith('title'):item['title']=quote
    elif path.endswith('statement'):item['statement']=quote
    else:v['product_context_proposal']['value']=quote
    before=copy.deepcopy(v)
    from app.core import Problem
    with pytest.raises(Problem) as error:validate_response(v,'ingest',p,ex)
    assert error.value.code=='SEMANTIC_BLOCKED' and not error.value.model_repair_allowed
    assert v==before
    assert unsupported!=supported
    if dimension=='exclusive_visibility':
        assert '向本租户管理员反馈' in unsupported and '仅本租户管理员' not in unsupported
        assert '仅本租户管理员可见' in supported
    elif dimension=='budget_is_not_quota':
        assert '配额' in unsupported and '预算' not in unsupported and '预算不足' in supported
    elif dimension=='existence_is_not_sufficiency':
        assert '满足' in unsupported and '存在性校验' not in unsupported and '存在性校验' in supported
    else:
        assert '逐项' not in unsupported and '既有系统需逐项提交' in supported
    # A genuinely supported counterpart is also legal, not blanket rejected.
    ex[0]['text']+=' '+supported
    assert validate_response(v,'ingest',p,ex)==v


@pytest.mark.parametrize('value',[
    '假设既有操作需要逐项提交，批量申请预期减少动作量；需验证，尚无量化证据。',
    '建议比较批量与逐项提交的动作量，未采纳，具体收益待验证。',
    '本期目标是便于租户管理员申请资源；定性价值待验证。',
])
def test_explicit_hypothesis_suggestion_and_unquantified_goal_remain_legal(value):
    p,v,ex=case(NORMAL);v['product_context_proposal']['value']=value
    before=copy.deepcopy((p,v,ex));validate_response(v,'ingest',p,ex)
    assert (p,v,ex)==before


@pytest.mark.parametrize('valid',[True,False])
def test_mock_answer_workflow_sends_cards_and_keeps_human_and_request_boundaries(tmp_path,monkeypatch,valid):
    from app.store import Store
    from app.provider import Provider, origin
    from app.workflow import Workflow
    from tests.test_request_cap import mock_transport
    store=Store(tmp_path);p=draft(tmp_path);q=p['questions'][0]
    ref=p['items'][0]['source_refs'][0]
    q.update(status='answered',answer='合成操作者：未知先查原ID，不重复。',answer_source_refs=[ref],understanding_status='pending')
    with store.edit(p['id'],0,'isolated synthetic fixture',bump=False) as (state,_):
        state.update(copy.deepcopy(p));state['grants'][origin(DEFAULT)]={'source_ids':[s['id'] for s in p['sources']]}
        from app.product_flow import checkpoint, fingerprint
        state['product_context']={'product':'合成园区云平台','module':'云资源申请','intent':'本期新增批量申请'}
        checkpoint(state,1,fingerprint(state,1))
    p=store.get(p['id']);before=copy.deepcopy(p)
    _,v,_=case('结果未知，不断言创建、未创建或既有资源未变。' if valid else '结果未知，原批已创建。',stage='clarify')
    item=v['proposals'][0];item.update(action='revise',target_item_id='REQ-original',answer_refs=['Q-original'],source_refs=[ref],
        scope_evidence=[dict(state='change',quote=p['sources'][1]['excerpts'][0]['text'].split('。')[0]+'。',source_ref=ref)])
    v['used_source_refs']=[ref]
    calls=mock_transport(monkeypatch,[v])
    async def once():
        provider=Provider(tmp_path/'absent.env');provider.set_key(DEFAULT,'synthetic-answer-fidelity-key')
        workflow=Workflow(store,provider)
        r=workflow.start(p['id'],p['revision'],'clarify','',config_override=dict(DEFAULT,max_calls=1),
                         discussion_target=dict(kind='question',id='Q-original'))
        await workflow.tasks[r['id']]
        return store.get_record(p['id'],r['id'],'run'),store.get(p['id'])
    run,after=asyncio.run(once())
    assert len(calls)==run['calls']==run['request_budget']['used']==1 and run['repair_count']==0
    header=json.loads(calls[0]['messages'][0]['content'].split('可信任务头：',1)[1])
    ctx=json.loads(calls[0]['messages'][1]['content'][0]['text'])
    assert header['generation_evidence_contract']['source_fidelity_policy']==SOURCE_FIDELITY
    assert ctx['result_condition_facts']['local_result_tasks'][0]['question_ids']==['Q-original']
    assert ctx['items']==before['items'] and ctx['questions']==before['questions']
    assert after['items'][0]==before['items'][0]
    if valid:
        candidate=after['items'][-1]
        assert run['result_applied'] and candidate['selection_status']=='candidate'
        assert candidate['target_item_id']=='REQ-original' and candidate['answer_refs']==['Q-original']
        assert candidate['answer_versions']=={'Q-original':digest(q['answer'])}
        actual_q=after['questions'][0];original_q=before['questions'][0]
        assert {k:v for k,v in actual_q.items() if k not in ('understanding_status','answer_effect')}=={k:v for k,v in original_q.items() if k not in ('understanding_status','answer_effect')}
        assert actual_q['understanding_status']=='candidate_ready' and actual_q['status']=='answered'
        assert not actual_q.get('answer_applications') and not actual_q.get('applied_item_ids')
    else:
        assert run['status']=='failed' and run['attempts'][0]['validation_result']=='SEMANTIC_BLOCKED'
        assert after['items']==before['items'] and not run.get('result_applied')
        assert after['questions']==before['questions']
