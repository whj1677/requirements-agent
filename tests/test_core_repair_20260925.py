"""Offline negative cases from the 2026-09-25 core diagnosis."""
import asyncio
import copy
import json

import pytest

from app.contracts import gate, review_target, validate_response
from app.core import Problem, hashes
from app.provider import DEFAULT, origin
from app.store import Store
from app.workflow import Workflow, apply_response, confirm
from app.ingest import response_anchor, preserve_response
from tests.helpers import EXAMPLES, prepared
from tests.product_flow_helpers import check_prepared


def test_review_required_decision_and_missing_coverage_block_confirmation(tmp_path):
    store=Store(tmp_path)
    p=prepared(store)
    p=check_prepared(store,p['id'])
    with store.edit(p['id'],p['revision'],'合成审查矛盾',bump=False) as (draft,_):
        result=draft['review']['response']['result']
        result.update(assessment='ready_for_human_review',reviewed_refs=[],perspectives=[],
                      required_decisions=['只读用户是否允许删除记录必须由产品决定'])
        draft['review']['target_hash']=review_target(draft)
    p=store.get(p['id'])
    issues=gate(p)
    assert any('必要决定' in issue for issue in issues)
    assert any('覆盖不足' in issue for issue in issues)
    with pytest.raises(Problem,match='必要决定'):
        confirm(store,p['id'],dict(expected_revision=p['revision'],expected_hashes=hashes(p),
                                   scope_ids=[i['id'] for i in p['items']],idempotency_key='core-review-negative'))
    with store.edit(p['id'],p['revision'],'合成普通说明',bump=False) as (draft,_):
        review=draft['review']['response']
        review['result'].update(reviewed_refs=['REQ-0001'],perspectives=[dict(role='测试',considerations=['已核对'])],required_decisions=[])
        review['findings']=[dict(type='gap',severity='warning',message='普通说明',related_refs=['REQ-0001'],source_refs=[],suggested_resolution='核对措辞')]
        review['limitations']=['非关键排版说明']
    assert gate(store.get(p['id']))==[]


def test_stable_question_id_preserves_risk_answer_and_all_references(tmp_path):
    p=prepared(Store(tmp_path))
    old=p['questions'][0]
    old.update(status='answered',answer='原回答',answered_at='合成时间',blocking=False)
    response=copy.deepcopy(EXAMPLES['clarify'])
    question=response['questions'][0]
    question.update(topic_key=old['topic_key'],related_refs=['REQ-0001'],blocking=True,
                    question='相接边界是否允许管理员覆盖？')
    response['proposals']=[dict(temp_id='TMP-core-rule',action='add',target_item_id=None,
        kind='rule',title='合成风险规则',statement='该规则仍待产品决定。',applies_to='to_be',
        epistemic_status='proposed',source_refs=[],related_refs=[question['temp_id']])]
    apply_response(p,response)
    assert len(p['questions'])==2
    assert p['questions'][0]['id']==old['id']
    assert p['questions'][0]['answer']=='原回答'
    assert p['questions'][1]['status']=='open' and p['questions'][1]['blocking'] is True
    assert p['questions'][1]['prior_question_id']==old['id']
    assert p['items'][-1]['related_refs']==[p['questions'][1]['id']]
    assert all(ref in {i['id'] for i in p['items']}|{q['id'] for q in p['questions']}
               for item in p['items'] for ref in item.get('related_refs',[]))


def test_open_same_topic_escalates_without_dangling_ref(tmp_path):
    p=prepared(Store(tmp_path))
    old=p['questions'][0]
    response=copy.deepcopy(EXAMPLES['clarify'])
    response['questions'][0].update(topic_key=old['topic_key'],related_refs=['REQ-0001'],blocking=True)
    response['proposals']=[dict(temp_id='TMP-risk',action='add',target_item_id=None,
        kind='rule',title='合成待确认规则',statement='等待回答。',applies_to='to_be',
        epistemic_status='proposed',source_refs=[],related_refs=[response['questions'][0]['temp_id']])]
    apply_response(p,response)
    assert len(p['questions'])==1 and p['questions'][0]['id']==old['id']
    assert p['questions'][0]['blocking'] is True
    assert p['items'][-1]['related_refs']==[old['id']]


@pytest.mark.parametrize('new_text,new_stage',[
    ('是否允许覆盖已有工单？',3),
    ('是否允许边界相接？',2),
])
def test_answered_topic_changed_text_or_earlier_gate_needs_new_answer(tmp_path,new_text,new_stage):
    p=prepared(Store(tmp_path))
    old=p['questions'][0]
    old.update(question='是否允许边界相接？',status='answered',answer='合成原回答：允许。',
               blocking=True,blocking_stage=3)
    response=copy.deepcopy(EXAMPLES['clarify'])
    response['questions'][0].update(topic_key=old['topic_key'],question=new_text,
                                   related_refs=['REQ-0001'],blocking=True,blocking_stage=new_stage)
    apply_response(p,response)
    assert len(p['questions'])==2
    assert p['questions'][0]['answer']=='合成原回答：允许。'
    assert p['questions'][1]['status']=='open' and p['questions'][1]['blocking'] is True
    assert p['questions'][1]['prior_question_id']==old['id']
    assert p['questions'][1]['id']!=old['id']
    assert p['questions'][1]['question']==new_text


class OfflineProvider:
    def __init__(self, replies):self.replies=iter(replies)
    def key(self, config):return 'offline-fixture'
    async def request(self, config, messages, evidence=None):
        return copy.deepcopy(next(self.replies)),dict(request_model=config['model'],
            response_model='OFFLINE_FIXTURE',usage={'total_tokens':100},origin=origin(config),finish_reason='stop')


def test_clarify_format_repair_cannot_drop_critical_question(tmp_path):
    async def scenario():
        store=Store(tmp_path)
        p=prepared(store)
        p=check_prepared(store,p['id'],through=2)
        with store.edit(p['id'],p['revision'],'合成授权',bump=False) as (draft,_):
            draft['grants'][origin(DEFAULT)]={'source_ids':[s['id'] for s in draft['sources']]}
        response=copy.deepcopy(EXAMPLES['clarify'])
        response['questions'][0].update(topic_key='critical-permission',question='只读用户能否删除记录？',blocking=True)
        malformed=copy.deepcopy(response);malformed['bad_extra_field']=1
        dropped=copy.deepcopy(response);dropped['questions']=[]
        workflow=Workflow(store,OfflineProvider([malformed,dropped]))
        run=workflow.start(p['id'],p['revision'],'clarify','合成澄清')
        await workflow.tasks[run['id']]
        result=store.get_record(p['id'],run['id'],'run')
        assert result['status']=='failed' and result['error']=='SEMANTIC_BLOCKED'
        assert result['calls']==2 and result['repair_count']==1
        assert not any(q['question']=='只读用户能否删除记录？' for q in store.get(p['id'])['questions'])
        assert not result.get('result_applied')
    asyncio.run(scenario())


@pytest.mark.parametrize('raw',['{"questions": [','{"questions": []} trailing','[]'])
def test_clarify_unparseable_first_output_cannot_be_rewritten_as_format_repair(tmp_path,raw):
    class BrokenOutputProvider(OfflineProvider):
        def __init__(self):self.calls=0
        async def request(self, config, messages, evidence=None):
            self.calls+=1
            if self.calls==1:
                evidence.response(raw,finish_reason='stop')
                raise Problem('SCHEMA_INVALID','模型最终内容不是完整 JSON 对象')
            return await super().request(config,messages,evidence=evidence)

    async def scenario():
        store=Store(tmp_path)
        p=prepared(store)
        p=check_prepared(store,p['id'],through=2)
        with store.edit(p['id'],p['revision'],'合成授权',bump=False) as (draft,_):
            draft['grants'][origin(DEFAULT)]={'source_ids':[s['id'] for s in draft['sources']]}
        provider=BrokenOutputProvider()
        workflow=Workflow(store,provider)
        run=workflow.start(p['id'],p['revision'],'clarify','合成澄清')
        await workflow.tasks[run['id']]
        result=store.get_record(p['id'],run['id'],'run')
        assert provider.calls==1 and result['calls']==1
        assert result['status']=='failed' and result['error']=='SEMANTIC_BLOCKED'
        assert '无法验证业务保真' in result['message']
        assert not result.get('result_applied')
        assert any(json.loads(path.read_text('utf-8'))['final_output']==raw
                   for path in (tmp_path/'evidence'/'model-calls').glob('*.json'))
    asyncio.run(scenario())


def test_clarify_complete_json_with_extra_field_can_repair_without_content_change(tmp_path):
    async def scenario():
        store=Store(tmp_path)
        p=prepared(store)
        p=check_prepared(store,p['id'],through=2)
        with store.edit(p['id'],p['revision'],'合成授权',bump=False) as (draft,_):
            draft['grants'][origin(DEFAULT)]={'source_ids':[s['id'] for s in draft['sources']]}
        response=copy.deepcopy(EXAMPLES['clarify'])
        response['questions'][0].update(topic_key='new-boundary',question='合成新增边界如何决定？')
        malformed=copy.deepcopy(response);malformed['bad_extra_field']=True
        workflow=Workflow(store,OfflineProvider([malformed,response]))
        run=workflow.start(p['id'],p['revision'],'clarify','合成澄清')
        await workflow.tasks[run['id']]
        result=store.get_record(p['id'],run['id'],'run')
        assert result['result_applied'] is True and result['calls']==2
        assert any(q['question']=='合成新增边界如何决定？' for q in store.get(p['id'])['questions'])
    asyncio.run(scenario())


def test_review_format_repair_preserves_decision_coverage_and_warning(tmp_path):
    p=prepared(Store(tmp_path))
    original=copy.deepcopy(p['review']['response'])
    original['bad_extra_field']=True
    original['result']['required_decisions']=['合成必要决定']
    original['result']['reviewed_refs']=['REQ-0001']
    original['findings']=[dict(type='gap',severity='warning',message='合成普通说明',
                              related_refs=['REQ-0001'],source_refs=[],suggested_resolution='核对')]
    excerpts=p['sources'][0]['excerpts']
    anchor=response_anchor(original,excerpts,'review')
    corrected=copy.deepcopy(original);corrected.pop('bad_extra_field')
    preserve_response(anchor,corrected,excerpts,'review')
    for field,change in [('required_decisions',[]),('reviewed_refs',[])]:
        dropped=copy.deepcopy(corrected)
        dropped['result'][field]=change
        with pytest.raises(Problem,match='格式修复改变'):preserve_response(anchor,dropped,excerpts,'review')
    dropped=copy.deepcopy(corrected);dropped['findings']=[]
    with pytest.raises(Problem,match='格式修复改变'):preserve_response(anchor,dropped,excerpts,'review')


def test_clarify_split_repair_keeps_each_decision_point(tmp_path):
    p=prepared(Store(tmp_path))
    original=copy.deepcopy(EXAMPLES['clarify'])
    child=lambda key,text:dict(temp_id='QTMP-'+key,topic_key=key,question=text,
                               why='合成独立决定',options=[],blocking=True,blocking_stage=3,
                               related_refs=['REQ-0001'],source_refs=[])
    original['questions']=[dict(child('group','两项独立决定'),decision_points=[
        child('permission','谁可删除记录？'),child('retention','记录保留多久？')])]
    original['questions'][0]['decision_points'][0]['invalid_extra']=True
    original['invalid_root']=True
    excerpts=p['sources'][0]['excerpts']
    anchor=response_anchor(original,excerpts,'clarify')
    corrected=copy.deepcopy(original)
    corrected.pop('invalid_root')
    corrected['questions'][0]['decision_points'][0].pop('invalid_extra')
    preserve_response(anchor,corrected,excerpts,'clarify')
    validate_response(corrected,'clarify',p,excerpts)
    dropped=copy.deepcopy(corrected)
    dropped['questions'][0]['decision_points'].pop()
    with pytest.raises(Problem,match='格式修复改变'):preserve_response(anchor,dropped,excerpts,'clarify')
    lowered=copy.deepcopy(corrected)
    lowered['questions'][0]['decision_points'][0]['blocking']=False
    with pytest.raises(Problem,match='格式修复改变'):preserve_response(anchor,lowered,excerpts,'clarify')
    unbound=copy.deepcopy(corrected)
    unbound['questions'][0]['decision_points'][0]['related_refs']=[]
    with pytest.raises(Problem,match='格式修复改变'):preserve_response(anchor,unbound,excerpts,'clarify')
