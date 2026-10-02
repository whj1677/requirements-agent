"""Synthetic generation-context boundaries; never evidence of live model quality."""
import copy
import json
import asyncio

import jsonschema
import pytest

from app.contracts import validate_response
from app.core import Problem, digest, dumps
from app.provider import DEFAULT, Provider, assemble, origin
from app.result_conditions import projection, validate_response_conditions
from app.sources import save_source
from app.store import Store
from app.workflow import Workflow
from tests.test_request_cap import mock_transport
from tests.test_result_conditions import BAD, NORMAL, SOURCE, case


def draft(tmp_path):
    p=Store(tmp_path).create('合成生成上下文')
    historical=save_source(Store(tmp_path),'合成原诉求.txt',
        '本期新增云资源申请。任一项配额不足则整批拒绝不创建；超时视作失败并允许再次提交。'.encode(),'reference')
    decision=save_source(Store(tmp_path),'合成明确决定.txt',SOURCE.encode(),'goal')
    p['sources']=[historical,decision]
    ref=dict(source_id=decision['id'],excerpt_id=decision['excerpts'][0]['id'])
    _,value,_=case()
    item=dict(copy.deepcopy(value['proposals'][0]),id='REQ-original',content_version=1,selection_status='candidate',source_refs=[ref])
    item.pop('temp_id');item['target_item_id']=None
    item['scope_evidence']=[dict(state='change',quote=SOURCE.split('。')[0]+'。',source_ref=ref)]
    p['items']=[item];p['revision']=3
    p['questions']=[dict(id='Q-original',topic_key='timeout',status='open',answer=None,
        question='超时采用哪个口径？',why='原问题保留',options=[],blocking=True,blocking_stage=3,
        related_refs=['REQ-original'],source_refs=[ref])]
    p['product_context_proposal']=dict(value='预期减少操作量，未量化。')
    p['messages']=[dict(role='user',stage='ingest',created='first',text='本期只做资源申请，不增加审批。'),
        dict(role='assistant',stage='ingest',created='second',text=BAD,response=value)]
    return p


def assembled(p,tmp_path,stage='ingest'):
    messages,excerpts,omitted=assemble(p,stage,'',dict(DEFAULT,max_calls=1),tmp_path)
    return json.loads(messages[0]['content'].split('可信任务头：',1)[1]),json.loads(messages[1]['content'][0]['text']),excerpts,omitted


def test_unadopted_predicates_are_retained_outside_source_fact_layer(tmp_path):
    p=draft(tmp_path);before=copy.deepcopy(p)
    _,ctx,ex,_=assembled(p,tmp_path)
    assert ctx['items']==p['items'] and p==before
    assert all(f['origin']=='source_statement' for f in ctx['result_condition_facts']['facts'])
    assertions=ctx['result_condition_facts']['clause_assertions']
    assert ctx['items'][0]['behavior']['result']==BAD
    assert any(f['quote']==BAD.split('；')[0] and f['evidence_role']=='unadopted_clause' for f in assertions)
    assert all(f['selection_status']=='candidate' for f in assertions)
    assert projection(p,ex)['facts']!=ctx['result_condition_facts']['facts']


def test_source_identity_time_and_current_decision_are_complete_not_approved(tmp_path):
    p=draft(tmp_path);_,ctx,ex,omitted=assembled(p,tmp_path)
    assert not omitted and ctx['excerpts']==ex==[e for s in p['sources'] for e in s['excerpts']]
    timeline=ctx['generation_evidence']['sources']
    assert [r['source_id'] for r in timeline]==[s['id'] for s in p['sources']]
    for row,source in zip(timeline,p['sources']):
        assert row['created']==source['created'] and row['purpose']==source['purpose'] and row['sha256']==source['sha256']
        assert row['excerpt_ids']==[e['id'] for e in source['excerpts']]
    assert ctx['questions']==p['questions'] and ctx['questions'][0]['answer'] is None
    assert not any('confirmed' in r or 'approved' in r for r in timeline)
    assert '明确本期决定' in ctx['generation_evidence']['source_policy']
    assert '时间或purpose本身' in ctx['generation_evidence']['source_policy']
    assert SOURCE in dumps(ctx)


def test_old_model_response_is_not_repeated_as_current_fact(tmp_path):
    p=draft(tmp_path);before=copy.deepcopy(p);_,ctx,_,_=assembled(p,tmp_path)
    assert ctx['recent_messages'][0]['text']==p['messages'][0]['text']
    assert 'text' not in ctx['recent_messages'][1] and 'response' not in ctx['recent_messages'][1]
    assert ctx['recent_messages'][1]['use']=='historical_model_event'
    assert ctx['generation_evidence']['product_context_proposal']['content']==p['product_context_proposal']
    assert ctx['generation_evidence']['product_context_proposal']['evidence_role']=='unadopted_proposal'
    assert p==before


def test_revision_targets_are_exact_current_ids_no_title_mapping(tmp_path):
    p=draft(tmp_path);header,ctx,_,_=assembled(p,tmp_path)
    target=ctx['generation_evidence']['revision_targets'][0]
    assert target==dict(target_item_id='REQ-original',kind='requirement',selection_status='candidate',content_version=1)
    assert '同名不能' in header['generation_evidence_contract']['revision_policy']
    assert header['generation_evidence_contract']['identity_patterns']==dict(proposal='^TMP-[A-Za-z0-9_-]+$',question='^QTMP-[A-Za-z0-9_-]+$')


@pytest.mark.parametrize('status,role',[('candidate','unadopted_clause'),('selected','selected_clause'),('deferred','unadopted_clause'),('rejected','unadopted_clause')])
def test_clause_status_is_explicit_without_reinterpreting_quote(tmp_path,status,role):
    p=draft(tmp_path);p['items'][0]['selection_status']=status
    _,ctx,_,_=assembled(p,tmp_path)
    facts=ctx['result_condition_facts']['clause_assertions']
    assert facts and all(f['evidence_role']==role and f['selection_status']==status for f in facts)
    assert ctx['items'][0]['behavior']['result']==BAD
    assert any(f['quote']==BAD.split('；')[0] for f in facts)


def test_explicit_source_sufficient_condition_keeps_its_quote_scope_and_identity(tmp_path):
    p=draft(tmp_path)
    sufficient='本期明确规则：同一租户每批最多20项时，全部配额满足则整批创建资源。'
    p['sources'].append(save_source(Store(tmp_path),'合成充分来源.txt',sufficient.encode(),'goal'))
    _,ctx,_,_=assembled(p,tmp_path)
    evidence=ctx['result_condition_facts']['facts']
    found=[f for f in evidence if f['role']=='sufficient_condition' and sufficient.rstrip('。') in f['quote']]
    assert found and found[0]['scope']==['at_most:20','same_tenant']
    assert found[0]['source_refs'][0]['source_id']==p['sources'][-1]['id']


@pytest.mark.parametrize('temp_id,valid',[('QTMP-timeout',True),('TMP-Q-timeout',False),('QTMP-a b',False)])
def test_generation_pattern_and_strict_public_response_contract_agree(tmp_path,temp_id,valid):
    p=draft(tmp_path);header,_,_,_=assembled(p,tmp_path)
    rule=header['schema']['$defs']['Question']['properties']['temp_id']
    assert jsonschema.Draft202012Validator(rule).is_valid(temp_id)==valid
    pr,response,ex=case(NORMAL)
    response['questions']=[dict(temp_id=temp_id,topic_key='independent',question='入口如何展示？',why='独立未知',options=[],blocking=False,related_refs=['TMP-resource'],source_refs=[])]
    before=copy.deepcopy(response)
    if valid:validate_response(response,'ingest',pr,ex)
    else:
        with pytest.raises(Problem) as caught:validate_response(response,'ingest',pr,ex)
        assert caught.value.code=='SCHEMA_INVALID'
    assert response==before


def test_original_failure_shapes_still_reject_without_cleaning(tmp_path):
    p,v,ex=case(BAD)
    v['questions']=[dict(temp_id='TMP-Q-timeout',topic_key='timeout',question='超时如何处理？',why='原格式反例',options=[],blocking=True,related_refs=['TMP-resource'],source_refs=[])]
    before=copy.deepcopy((p,v,ex))
    with pytest.raises(Problem) as schema_error:validate_response(v,'ingest',p,ex)
    with pytest.raises(Problem) as semantic_error:validate_response_conditions(v,p,ex)
    assert schema_error.value.code=='SCHEMA_INVALID' and semantic_error.value.code=='SEMANTIC_BLOCKED'
    assert (p,v,ex)==before


@pytest.mark.parametrize('stage',['clarify','change'])
def test_other_clause_generation_keeps_same_identity_and_evidence_boundary(tmp_path,stage):
    p=draft(tmp_path);before=copy.deepcopy(p);header,ctx,_,_=assembled(p,tmp_path,stage)
    assert ctx['items']==p['items'] and ctx['questions']==p['questions'] and p==before
    assert header['generation_evidence_contract']['identity_patterns']['question'].startswith('^QTMP-')
    assert all(f['origin']=='source_statement' for f in ctx['result_condition_facts']['facts'])
    assert ctx['generation_evidence']['saved_decision_ids']==[]


@pytest.mark.parametrize('valid',[True,False])
def test_mock_provider_receives_layers_and_preserves_strict_result_boundary(tmp_path,monkeypatch,valid):
    store=Store(tmp_path);p=draft(tmp_path)
    with store.edit(p['id'],0,'isolated synthetic fixture',bump=False) as (draft_state,_):
        draft_state.update(copy.deepcopy(p))
        draft_state['grants'][origin(DEFAULT)]={'source_ids':[s['id'] for s in p['sources']]}
    # Store reconciles identity metadata on save; compare the actual saved snapshot.
    p=store.get(p['id'])
    _,reply,_=case(NORMAL)
    ref=p['items'][0]['source_refs'][0]
    item=reply['proposals'][0]
    item.update(action='revise',target_item_id='REQ-original',source_refs=[ref],
                scope_evidence=[dict(state='change',quote=SOURCE.split('。')[0]+'。',source_ref=ref)])
    reply['used_source_refs']=[ref]
    if not valid:reply['questions']=[dict(temp_id='TMP-Q-timeout',topic_key='timeout',question='超时采用哪个口径？',why='真实失败形状',options=[],blocking=True,related_refs=['REQ-original'],source_refs=[ref])]
    requests=mock_transport(monkeypatch,[reply])
    async def run_once():
        provider=Provider(tmp_path/'absent.env');provider.set_key(DEFAULT,'synthetic-generation-key')
        workflow=Workflow(store,provider)
        r=workflow.start(p['id'],3,'ingest','',config_override=dict(DEFAULT,max_calls=1))
        await workflow.tasks[r['id']]
        return store.get_record(p['id'],r['id'],'run'),store.get(p['id'])
    run,after=asyncio.run(run_once())
    assert len(requests)==run['calls']==run['request_budget']['used']==1
    sent=json.loads(requests[0]['messages'][1]['content'][0]['text'])
    assert sent['generation_evidence']['revision_targets'][0]['target_item_id']=='REQ-original'
    assert sent['items']==p['items'] and after['items'][0]==p['items'][0]
    assert after['questions']==p['questions'] and run['repair_count']==0
    if valid:
        assert run['result_applied'] and after['items'][-1]['target_item_id']=='REQ-original'
        assert after['items'][-1]['selection_status']=='candidate'
    else:
        assert run['status']=='paused_budget' and run['attempts'][0]['validation_result']=='SCHEMA_INVALID'
        assert after['items']==p['items'] and not run.get('result_applied')
