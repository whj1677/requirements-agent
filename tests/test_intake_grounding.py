"""Synthetic reviewer replies test guards, not model semantic accuracy."""
import asyncio
import copy
import json

import pytest

from app import intake_grounding as grounding
from app.business_draft import SUMMARY_FIELDS
from app.core import Problem, dumps
from app.workflow import Workflow
from tests.test_business_draft import business_value, draft_row, packet
from tests.test_business_draft_workflow import BusinessProvider
from tests.test_requirement_recommendation import project


def fixture():
    candidate=packet(draft_row())
    context=dict(excerpts=[dict(source='S1',locator='L1',text='允许查看当前负责人；释放后历史记录只读。'),
                           dict(source='S2',locator='L1',text='冲突不按文件顺序覆盖。')],
        user_message='请整理初稿',recent_messages=[],current_rows=[])
    return grounding.prepare(candidate,context)


def review(plan):
    """Deliberately synthetic all-pass oracle; no entailment claim."""
    sources={(entry['source'],entry['locator']):entry['text'] for entry in plan['context']['excerpts']}
    fields=[]
    for field in plan['fields']:
        refs=field['sources']
        claims=[]
        if field['text'].strip():
            supported=field['basis']=='fact'
            claims=[dict(quote=field['text'],verdict='supported' if supported else 'unknown',
                evidence=[dict(**ref,quote=sources[(ref['source'],ref['locator'])]) for ref in refs] if supported else [],
                suggested_citations=[],relation_checks={kind:[] for kind in ('location','temporal','scope')},
                reason='离线合成核对，不代表真实语义通过')]
        fields.append(dict(id=field['id'],claims=claims,
            question_status='unresolved' if field['question'] else 'not_question',reason='合成结构检查'))
    return dict(candidate_hash=plan['candidate_hash'],input_hash=plan['input_hash'],fields=fields,
        inputs=[dict(id=entry['id'],decisions=[dict(quote=entry['text'],verdict='context',targets=[],
            constraint_checks=[],
            reason='仅用于离线协议测试的合成判定')]) for entry in plan['inputs']])


def test_local_guard_rejects_half_answer_and_half_field_coverage():
    plan=fixture()
    for field in ('fields','inputs'):
        response=review(plan)
        key='claims' if field=='fields' else 'decisions'
        response[field][0][key][0]['quote']=response[field][0][key][0]['quote'].split('；')[0][1:]
        with pytest.raises(Problem,match='全部子'):
            grounding.validate(plan,response)


def test_each_value_cannot_borrow_another_source_or_invent_quote():
    plan=fixture()
    response=review(plan)
    response['fields'][0]['claims'][0]['evidence']=[dict(source='S2',locator='L1',quote='冲突不按文件顺序覆盖。')]
    checked=grounding.validate(plan,response)
    assert checked['status']=='blocked' and checked['findings'][0]['kind']=='field_citation_missing'
    response=review(plan)
    response['fields'][0]['claims'][0]['evidence'][0]['quote']='不存在的来源原话'
    checked=grounding.validate(plan,response)
    assert checked['status']=='blocked' and checked['findings'][0]['kind']=='source_quote_unproven'


def test_missing_decision_repeat_question_and_fact_downgrade_are_blocked():
    plan=fixture()
    response=review(plan)
    response['inputs'][0]['decisions']=[
        dict(quote='允许查看当前负责人；',verdict='represented',targets=['F1'],constraint_checks=[],reason='已带入'),
        dict(quote='释放后历史记录只读。',verdict='missing',targets=[],constraint_checks=[],reason='未带入')]
    assert grounding.validate(plan,response)['status']=='blocked'
    response=review(plan)
    response['fields'][0]['claims'][0]['verdict']='unknown'
    assert grounding.validate(plan,response)['status']=='blocked'
    candidate=copy.deepcopy(plan['candidate'])
    candidate['requirements'][0]['questions']=[dict(text='释放后历史记录可否编辑？',reason='此决定尚未确定',
        blocking=True,sources=[],recommendations=[])]
    plan=grounding.prepare(candidate,plan['context']);response=review(plan)
    entry=next(entry for entry in response['fields'] if entry['question_status']=='unresolved')
    entry['question_status']='already_answered'
    assert grounding.validate(plan,response)['status']=='blocked'


def test_repair_cannot_change_identity_and_full_recheck_catches_lost_answer():
    plan=fixture();response=review(plan)
    response['inputs'][0]['decisions'][0]['verdict']='missing'
    checked=grounding.validate(plan,response)
    schema,_=grounding.correction_messages(plan,checked)
    slot=next(slot for slot in plan['slots'] if slot['path']==['requirements',0,'statement'])
    patch=dict(candidate_hash=plan['candidate_hash'],patches=[dict(slot=slot['id'],
        value=business_value('只显示当前负责人。','fact'),reason='合成遗漏纠正')])
    corrected=grounding.apply_correction(plan,patch,schema)
    assert corrected['requirements'][0]['row_key']==plan['candidate']['requirements'][0]['row_key']
    revised=grounding.prepare(corrected,plan['context']);recheck=review(revised)
    recheck['inputs'][0]['decisions'][0]['verdict']='missing'
    assert grounding.validate(revised,recheck)['status']=='blocked'
    patch['patches'][0]['slot']='requirements'
    with pytest.raises(Problem):grounding.apply_correction(plan,patch,schema)


@pytest.mark.parametrize('mutation',['duplicate','missing','hash'])
def test_review_binding_and_total_inventory_are_required(mutation):
    plan=fixture();response=review(plan)
    if mutation=='duplicate':response['fields'].append(response['fields'][0])
    elif mutation=='missing':response['inputs'].pop()
    else:response['candidate_hash']='wrong'
    with pytest.raises(Problem):grounding.validate(plan,response)


class GroundingProvider(BusinessProvider):
    def __init__(self,raw,mode):
        super().__init__([])
        self.raw=raw;self.mode=mode;self.reviews=0

    async def request(self,config,messages,evidence=None):
        header=json.loads(messages[0]['content'].split('可信任务头：',1)[1])
        protocol=header.get('grounding_protocol')
        if evidence:evidence.before_send(config)
        self.received.append(copy.deepcopy(messages))
        if protocol==grounding.COVERAGE_VERSION:
            self.reviews+=1
            if self.mode=='timeout':raise asyncio.TimeoutError()
            if self.mode=='cancelled':raise asyncio.CancelledError()
            payload=json.loads(messages[1]['content'][0]['text'])
            result=dict(candidate_hash=payload['candidate_hash'],input_hash=payload['input_hash'],
                checked_input_ids=[row['id'] for row in payload['inputs']],
                checked_question_ids=[row['id'] for row in payload['questions']],findings=[])
            if self.mode=='invalid':result['checked_input_ids']=[]
            blocked=(self.mode in ('blocked','unproven_blocked') or
                self.mode in ('repair','unproven_repair') and self.reviews==1 or
                self.mode in ('second_repair','second_loses_answer') and self.reviews<=2 or
                self.mode=='second_loses_answer' and self.reviews==3)
            if blocked:result['findings']=[dict(kind='input',id=payload['inputs'][0]['id'],reason='合成核对：已答约束未完整保留')]
        elif protocol==grounding.CHALLENGE_VERSION:
            payload=json.loads(messages[1]['content'][0]['text'])
            result=dict(candidate_hash=payload['candidate_hash'],input_hash=payload['input_hash'],
                checked_field_ids=[field['id'] for field in payload['fields']],findings=[])
        elif protocol==grounding.PATCH_VERSION:
            payload=json.loads(messages[1]['content'][0]['text'])
            slot=next(slot for slot in payload['slots'] if slot['path']==['requirements',0,'statement'])
            result=dict(candidate_hash=payload['candidate_hash'],patches=[dict(slot=slot['id'],
                value=business_value('管理员能够维护电价时段；保留原有约束。' if self.reviews==1 else '管理员能够维护电价时段；完整保留原有约束。','fact'),reason='合成定位纠正')])
        else:result=copy.deepcopy(self.raw)
        meta=dict(usage={'prompt_tokens':1,'completion_tokens':1},finish_reason='stop')
        if evidence:evidence.response(dumps(result),http_status=200,finish_reason='stop',usage=meta['usage'])
        return result,meta


@pytest.mark.parametrize('mode,expected_calls,expected_status',[
    ('second_repair',9,'repaired'),('second_loses_answer',9,'blocked'),('pass',3,'passed'),('repair',6,'repaired'),('blocked',9,'blocked'),('invalid',2,'unverified'),('timeout',2,'unverified'),('cancelled',2,'unverified'),
    ('unproven_repair',6,'repaired'),('unproven_blocked',9,'blocked')])
def test_workflow_bounded_review_preserves_originals_and_failed_candidates(tmp_path,mode,expected_calls,expected_status):
    async def scenario():
        store,p=project(tmp_path)
        raw=packet(draft_row('R1'),draft_row('R2'))
        raw['summary_fields']={key:business_value() for key in SUMMARY_FIELDS}
        provider=GroundingProvider(raw,mode)
        workflow=Workflow(store,provider)
        run=workflow.start(p['id'],p['revision'],'ingest','请整理初稿')
        await workflow.tasks[run['id']]
        run=store.get_record(p['id'],run['id'],'run')
        assert run['intake_grounding']['status']==expected_status,run
        assert run['calls']==expected_calls and len(provider.received)==expected_calls
        assert run['intake_grounding']['corrections']==(2 if expected_calls==9 else 1 if expected_calls==6 else 0)
        after=store.get(p['id'])
        assert after['items'][:len(p['items'])]==p['items']
        assert after['questions']==p['questions'] and after['sources']==p['sources']
        if expected_status in ('passed','repaired'):
            assert run['result_applied'] is True
            assert after['business_draft_reviews'][-1]['source_validation']=='model_semantic_checked_not_human_approval'
        else:
            assert not run.get('result_applied') and after==p
        evidence_files=list((tmp_path/'evidence'/'model-calls').glob('CALL-*.json'))
        assert len(evidence_files)==expected_calls
        initial=next(json.loads(path.read_text('utf-8')) for path in evidence_files
            if json.loads(path.read_text('utf-8')).get('intake_grounding'))
        assert initial['intake_grounding']['status']==expected_status
        assert len(initial['intake_grounding_history'])==(0 if mode in ('timeout','cancelled') else expected_calls-1)
    asyncio.run(scenario())


def test_budget_does_not_dispatch_patch_without_room_for_full_recheck(tmp_path):
    async def scenario():
        store,p=project(tmp_path)
        raw=packet(draft_row('R1'),draft_row('R2'))
        raw['summary_fields']={key:business_value() for key in SUMMARY_FIELDS}
        provider=GroundingProvider(raw,'repair');workflow=Workflow(store,provider)
        config=workflow.config('ingest');config['max_calls']=3
        run=workflow.start(p['id'],p['revision'],'ingest','请整理初稿',config_override=config)
        await workflow.tasks[run['id']]
        completed=store.get_record(p['id'],run['id'],'run')
        assert completed['status']=='paused_budget',completed
        assert completed['calls']==3 and len(provider.received)==3
        assert completed['intake_grounding']['status']=='unverified'
        assert store.get(p['id'])==p
    asyncio.run(scenario())


def test_actions_projection_is_readonly_and_uses_only_its_own_runs(tmp_path):
    from tests.test_runtime import client
    c,app=client(tmp_path)
    store=app.state.store;p=store.create('隔离任务状态')
    for number,status in ((1,'rechecking'),(2,'unverified')):
        rid='RUN-grounding-'+str(number);tid='TASK-grounding-'+str(number)
        store.record(p['id'],'run',dict(stage='ingest',calls=number,status='running',
            result_applied=False,intake_grounding=dict(protocol=grounding.VERSION,status=status,
            reason='阶段'+str(number),corrections=1)),rid)
        store.record(p['id'],'user_task',dict(action='clarify',status='running',run_ids=[rid],calls=0,message='旧消息'),tid)
    before=copy.deepcopy(store.records(p['id'],'user_task'))
    response=c.get('/api/projects/'+p['id']+'/actions')
    assert response.status_code==200,response.text
    tasks=response.json()
    assert [task['intake_grounding']['status'] for task in tasks]==['rechecking','unverified']
    assert [task['message'] for task in tasks]==['阶段1','阶段2']
    assert all(task['intake_grounding']['candidate_applied'] is False for task in tasks)
    assert store.records(p['id'],'user_task')==before


def test_sentence_separator_repair_is_exact_audited_and_immutable():
    plan=fixture();response=review(plan)
    response['inputs'][0]['decisions']=[dict(quote=part,verdict='context',targets=[],constraint_checks=[],reason='合成')
        for part in ('允许查看当前负责人','释放后历史记录只读')]
    response['fields']={'items':response['fields']}
    response['inputs']={'items':response['inputs']}
    before=copy.deepcopy(response)
    normalized,changes=grounding.normalize_review(plan,response)
    assert response==before
    assert grounding.validate(plan,normalized)['status']=='passed'
    mapping=next(change for change in changes if change['kind']=='restore_exact_sentence_separators')
    assert ''.join(part['normalized_quote'] for part in mapping['spans'])==mapping['original_text']
    for part in mapping['spans']:
        start,end=part['normalized_span']
        assert mapping['original_text'][start:end]==part['normalized_quote']


@pytest.mark.parametrize('missing',['不','未','仅','1','.','(',')','>','=','或'])
def test_partition_repair_never_discards_business_characters_or_operators(missing):
    plan=fixture();plan['inputs'][0]['text']='甲'+missing+'乙'
    response=review(plan)
    response['inputs'][0]['decisions']=[dict(quote=part,verdict='context',targets=[],constraint_checks=[],reason='合成')
        for part in ('甲','乙')]
    normalized,_=grounding.normalize_review(plan,response)
    with pytest.raises(Problem,match='全部子决定'):grounding.validate(plan,normalized)


def test_extra_wrapper_keys_and_reordered_claims_are_not_repaired():
    plan=fixture();response=review(plan)
    response['inputs']={'items':response['inputs'],'hidden_decision':'不可删除'}
    normalized,changes=grounding.normalize_review(plan,response)
    assert not changes
    with pytest.raises(Problem):grounding.validate(plan,normalized)
    response=review(plan)
    response['inputs'][0]['decisions']=[dict(quote=part,verdict='context',targets=[],constraint_checks=[],reason='合成')
        for part in ('释放后历史记录只读','允许查看当前负责人')]
    normalized,changes=grounding.normalize_review(plan,response)
    with pytest.raises(Problem):grounding.validate(plan,normalized)


@pytest.mark.parametrize('supported',[False,True])
def test_foreign_citation_can_only_be_a_suggestion_on_an_unsupported_claim(supported):
    plan=fixture();response=review(plan)
    claim=response['fields'][0]['claims'][0]
    claim['verdict']='supported' if supported else 'unsupported'
    claim['evidence']=[dict(source='S2',locator='L1',quote='冲突不按文件顺序覆盖。')]
    normalized,changes=grounding.normalize_review(plan,response)
    if supported:
        assert not changes
        checked=grounding.validate(plan,normalized)
        assert checked['status']=='blocked' and checked['findings'][0]['kind']=='field_citation_missing'
    else:
        assert normalized['fields'][0]['claims'][0]['suggested_citations']==claim['evidence']
        assert grounding.validate(plan,normalized)['status']=='blocked'


@pytest.mark.parametrize('candidate,source,dimension,qualifier,action,passed',[
    ('详情显示当前负责人','成功后显示当前负责人；详情可查询处理记录。','location','详情','显示当前负责人',False),
    ('详情显示当前负责人','成功后详情显示当前负责人；详情可查询处理记录。','location','详情','显示当前负责人',True),
    ('发起后显示当前负责人','成功后显示当前负责人。','temporal','发起后','显示当前负责人',False),
    ('成功后显示当前负责人','成功后显示当前负责人。','temporal','成功后','显示当前负责人',True),
    ('后发起者认领不生效','首个成功者有效；另一人的认领不生效。','temporal','后发起者','认领不生效',False),
])
def test_relation_guard_binds_the_qualifier_to_the_same_action(candidate,source,dimension,qualifier,action,passed):
    raw=packet(draft_row());raw['requirements'][0]['statement']=business_value(candidate,'fact')
    plan=grounding.prepare(raw,dict(excerpts=[dict(source='S1',locator='L1',text=source)]))
    response=review(plan)
    response['fields'][0]['claims'][0]['relation_checks'][dimension]=[dict(candidate_qualifier=qualifier,
        candidate_action=action,candidate_anchor=candidate,source='S1',locator='L1',source_anchor=source,
        reason='离线故意提供邻近动作引用，验证不能借用限定')]
    result=grounding.validate(plan,response)
    assert (result['status']=='passed')==passed
    if not passed:assert result['findings'][0]['kind']=='relation'


@pytest.mark.parametrize('preserved',[False,True])
def test_source_exhaustive_constraint_must_survive_in_fact_target(preserved):
    source='记录仅包含人员、动作。'
    candidate=source if preserved else '记录包含人员、动作，不包括附件。'
    raw=packet(draft_row());raw['requirements'][0]['statement']=business_value(candidate,'fact')
    plan=grounding.prepare(raw,dict(excerpts=[dict(source='S1',locator='L1',text=source)]))
    response=review(plan)
    decision=response['inputs'][0]['decisions'][0]
    decision.update(verdict='represented',targets=['F1'],constraint_checks=[dict(kind='scope',
        source_qualifier='仅',source_action='包含人员、动作',source_anchor='仅包含人员、动作',
        targets=[dict(id='F1',candidate_anchor=candidate)],reason='验证穷尽量词不能被部分排除项替代')])
    checked=grounding.validate(plan,response)
    assert (checked['status']=='passed')==preserved
    if not preserved:assert checked['findings'][0]['kind']=='input_constraint'


def test_field_inventory_does_not_depend_on_json_object_key_order():
    raw=packet(draft_row())
    raw['summary_fields']={key:business_value(key) for key in ('users','intent','product')}
    context=dict(excerpts=[])
    first=grounding.prepare(raw,context)
    second=grounding.prepare(json.loads(dumps(raw)),context)
    assert first['candidate_hash']==second['candidate_hash']
    assert first['fields']==second['fields'] and first['slots']==second['slots']


@pytest.mark.parametrize('source,candidate,kind,qualifier,action',[
    ('认领成功后显示当前认领人。','发起认领后显示当前认领人。','temporal','成功后','显示当前认领人'),
    ('释放后旧记录仍只读。','释放后保留旧记录。','preservation','仍只读','旧记录'),
])
def test_source_precondition_and_preservation_cannot_be_lost(source,candidate,kind,qualifier,action):
    raw=packet(draft_row());raw['requirements'][0]['statement']=business_value(candidate,'fact')
    plan=grounding.prepare(raw,dict(excerpts=[dict(source='S1',locator='L1',text=source)]))
    response=review(plan)
    response['inputs'][0]['decisions'][0].update(verdict='represented',targets=['F1'],constraint_checks=[dict(
        kind=kind,source_qualifier=qualifier,source_action=action,source_anchor=source[:-1],
        targets=[dict(id='F1',candidate_anchor=candidate)],reason='离线复现遗漏已答条件')])
    assert any(finding['kind']=='input_constraint' for finding in grounding.validate(plan,response)['findings'])


def test_normalization_never_invents_missing_semantic_relation_fields():
    plan=fixture();response=review(plan)
    del response['fields'][0]['claims'][0]['relation_checks']
    normalized,_=grounding.normalize_review(plan,response)
    assert 'relation_checks' not in normalized['fields'][0]['claims'][0]
    with pytest.raises(Problem):grounding.validate(plan,normalized)


@pytest.mark.parametrize('exact',[False,True])
def test_reordered_action_annotation_requires_whole_claim_evidence_or_finding(exact):
    source='处理说明由当前认领人填写。'
    candidate=source if exact else '处理说明只能由当前认领人填写。'
    raw=packet(draft_row());raw['requirements'][0]['statement']=business_value(candidate,'fact')
    plan=grounding.prepare(raw,dict(excerpts=[dict(source='S1',locator='L1',text=source)]));response=review(plan)
    relation=dict(candidate_qualifier='当前认领人',candidate_action='填写处理说明',
        candidate_anchor=candidate[:-1],source='S1',locator='L1',source_anchor=source[:-1],reason='真实模型倒装形状')
    response['fields'][0]['claims'][0]['relation_checks']['scope']=[relation]
    checked=grounding.validate(plan,response)
    assert (checked['status']=='passed')==exact
    if exact:
        assert checked['annotations'][0]['kind']=='subannotation_unverified_whole_claim_exact'
        assert checked['annotations'][0]['relation']==relation
    else:assert checked['findings'][0]['kind']=='relation_binding_unproven'


def test_short_location_anchor_is_not_whole_claim_evidence():
    source='成功后显示当前负责人；详情可查询处理记录。'
    raw=packet(draft_row());raw['requirements'][0]['statement']=business_value('详情显示当前负责人。','fact')
    plan=grounding.prepare(raw,dict(excerpts=[dict(source='S1',locator='L1',text=source)]));response=review(plan)
    response['fields'][0]['claims'][0]['relation_checks']['location']=[dict(candidate_qualifier='详情',
        candidate_action='显示当前负责人',candidate_anchor='详情',source='S1',locator='L1',
        source_anchor='详情可查询处理记录',reason='另一个动作的位置不构成完整断言支持')]
    checked=grounding.validate(plan,response)
    assert checked['status']=='blocked' and not checked['annotations']


@pytest.mark.parametrize('exact',[False,True])
def test_input_annotation_reorder_only_preserves_whole_decision_exactly(exact):
    source='处理说明由当前认领人填写；释放后旧记录只读。'
    candidate=source if exact else '处理说明由当前认领人填写。'
    raw=packet(draft_row());raw['requirements'][0]['statement']=business_value(candidate,'fact')
    plan=grounding.prepare(raw,dict(excerpts=[dict(source='S1',locator='L1',text=source)]));response=review(plan)
    response['inputs'][0]['decisions'][0].update(verdict='represented',targets=['F1'],constraint_checks=[dict(
        kind='scope',source_qualifier='当前认领人',source_action='填写处理说明',
        source_anchor='处理说明由当前认领人填写',targets=[dict(id='F1',candidate_anchor=candidate)],reason='实测倒装形状')])
    checked=grounding.validate(plan,response)
    assert (checked['status']=='passed')==exact
    if exact:assert checked['annotations'][0]['kind']=='subannotation_unverified_whole_decision_retained'
    else:assert checked['findings'][0]['kind']=='input_constraint_binding_unproven'


@pytest.mark.parametrize('exact',[False,True])
def test_current_state_requires_positive_full_own_source_anchor(exact):
    source='平台已有告警列表。设备控制和通知不在本次改动内。'
    candidate='平台已有告警列表。' if exact else '平台已有设备控制和通知能力。'
    raw=packet(draft_row());raw['summary_fields']={'current_state':business_value(candidate,'fact')}
    plan=grounding.prepare(raw,dict(excerpts=[dict(source='S1',locator='L1',text=source)]));response=review(plan)
    checked=grounding.validate(plan,response)
    assert (checked['status']=='passed')==exact
    if not exact:assert checked['findings'][0]['kind']=='current_state_unproven'


def test_represented_decision_without_fact_is_not_silently_reclassified():
    plan=fixture();response=review(plan)
    target=next(field for field in plan['fields'] if field['basis']=='unknown')
    response['inputs'][0]['decisions'][0].update(verdict='represented',targets=[target['id']])
    checked=grounding.validate(plan,response)
    assert checked['status']=='blocked' and checked['findings'][0]['kind']=='input_disposition_unproven'
    assert checked['findings'][0]['decision']['verdict']=='represented'


@pytest.mark.parametrize('source,quoted,expected',[
    ('这是平台。本次新增认领、释放和记录查询，减少重复处理。','这是平台。本次新增认领、释放和记录查询。','这是平台。本次新增认领、释放和记录查询，'),
    ('保留原数据','保留原数据。','保留原数据'),
    ('不修改旧记录，保留数据。','修改旧记录。',None),
    ('不修改旧记录，保留数据。','不旧记录。',None),
    ('结果为1.5，保持原值。','结果为15。',None),
    ('甲，乙，丙。','甲乙。',None),
    ('甲（乙），丙。','甲乙。',None),
    ('甲结果，甲结果；','甲结果。',None),
    ('不修改旧记录，保留数据。','不修改旧。',None),
])
def test_only_unique_source_quote_terminal_separator_can_be_restored(source,quoted,expected):
    plan=fixture();plan['context']['excerpts'][0]['text']=source
    response=review(plan)
    response['fields'][0]['claims'][0]['evidence'][0]['quote']=quoted
    normalized,changes=grounding.normalize_review(plan,response)
    actual=normalized['fields'][0]['claims'][0]['evidence'][0]['quote']
    assert actual==(expected if expected is not None else quoted)
    restored=[change for change in changes if change['kind']=='restore_unique_source_quote_terminal_separator']
    assert bool(restored)==(expected is not None)
    if restored:
        start,end=restored[0]['source_span']
        assert source[start:end]==actual


def test_terminal_separator_repair_does_not_hide_a_removed_negation_inside_quote():
    plan=fixture();plan['context']['excerpts'][0]['text']='该操作不修改旧记录，保留数据。'
    response=review(plan);response['fields'][0]['claims'][0]['evidence'][0]['quote']='该操作修改旧记录。'
    normalized,changes=grounding.normalize_review(plan,response)
    assert not changes
    checked=grounding.validate(plan,normalized)
    assert checked['status']=='blocked' and checked['findings'][0]['kind']=='source_quote_unproven'


def test_fabricated_witness_cannot_enter_whole_claim_fallback():
    plan=fixture();response=review(plan)
    claim=response['fields'][0]['claims'][0]
    claim['evidence'][0]['quote']=claim['quote']  # Candidate words do not occur in this source.
    claim['relation_checks']['scope']=[dict(candidate_qualifier='管理员',candidate_action='倒装的动作',
        candidate_anchor=claim['quote'],source='',locator='',source_anchor='',reason='伪引句不允许触发完整断言例外')]
    checked=grounding.validate(plan,response)
    assert checked['status']=='blocked'
    assert any(f['kind']=='source_quote_unproven' for f in checked['findings'])
    assert not checked['annotations']


def test_wrong_whole_relation_anchor_is_a_finding_without_claim_rewrite():
    plan=fixture();response=review(plan)
    response['fields'][0]['claims'][0]['relation_checks']['location']=[dict(
        candidate_qualifier='当前',candidate_action='查看',candidate_anchor='允许查看当前负责人',
        source='S1',locator='L1',source_anchor='允许查看当前负责人',reason='来源不能冒充候选原文')]
    before=copy.deepcopy(response)
    checked=grounding.validate(plan,response)
    assert response==before and checked['status']=='blocked'
    assert checked['findings'][0]['kind']=='relation_candidate_unproven'


@pytest.mark.parametrize('fact_problem',[False,True])
def test_nonquestion_annotation_does_not_change_business_or_skip_fact_review(fact_problem):
    plan=fixture();response=review(plan)
    response['fields'][0]['question_status']='already_answered'
    if fact_problem:response['fields'][0]['claims'][0]['verdict']='unsupported'
    checked=grounding.validate(plan,response)
    assert (checked['status']=='blocked')==fact_problem
    assert checked['annotations'][0]['kind']=='irrelevant_question_annotation'
    assert not any(f['kind']=='question' for f in checked['findings'])


def test_question_text_requires_real_duplicate_question_check():
    plan=fixture();raw=copy.deepcopy(plan['candidate'])
    raw['requirements'][0]['questions']=[dict(text='谁可以操作？',reason='权限未决',blocking=True,sources=[],recommendations=[])]
    plan=grounding.prepare(raw,plan['context']);response=review(plan)
    entry=next(entry for entry in response['fields'] if entry['question_status']=='unresolved')
    entry['question_status']='not_question'
    checked=grounding.validate(plan,response)
    assert checked['status']=='blocked'
    assert checked['findings'][0]['kind']=='question_review_unproven'


def test_conflict_reason_is_fully_grounded_without_being_another_question():
    plan=fixture();raw=copy.deepcopy(plan['candidate'])
    raw['requirements'][0]['questions']=[dict(text='释放权限采用哪份资料的规则？',reason='两份资料对释放权限存在冲突。',
        blocking=True,sources=[dict(source='S1',locator='L1'),dict(source='S2',locator='L1')],recommendations=[])]
    context=dict(excerpts=[dict(source='S1',locator='L1',text='甲角色可以释放。'),dict(source='S2',locator='L1',text='乙角色可以释放。')])
    plan=grounding.prepare(raw,context);response=review(plan)
    reason=next(field for field in plan['fields'] if field['path'][-1]=='reason')
    text=next(field for field in plan['fields'] if field['path'][-1]=='text')
    assert reason['question'] is False and text['question'] is True
    checked=next(entry for entry in response['fields'] if entry['id']==reason['id'])
    checked['claims'][0].update(verdict='supported',evidence=[dict(source=x['source'],locator=x['locator'],quote=x['text']) for x in context['excerpts']])
    assert checked['question_status']=='not_question'
    assert grounding.validate(plan,response)['status']=='passed'
    checked['claims'][0]['evidence'][1]['quote']='伪造的冲突原话'
    assert grounding.validate(plan,response)['status']=='blocked'


def test_unknown_input_targets_and_nonliteral_constraint_anchors_are_findings():
    plan=fixture();response=review(plan)
    decision=response['inputs'][0]['decisions'][0]
    decision.update(verdict='represented',targets=['invented'],constraint_checks=[dict(
        kind='scope',source_qualifier='不存在',source_action='动作',source_anchor='不存在的动作',
        targets=[dict(id='invented',candidate_anchor='不存在的候选')],reason='全部保留为未证明而非补造')])
    checked=grounding.validate(plan,response)
    assert checked['status']=='blocked' and not checked['annotations']
    assert {f['kind'] for f in checked['findings']} >= {'input_target_unproven','input_disposition_unproven','input_constraint_target_unproven','input_constraint_binding_unproven'}


@pytest.mark.parametrize('concrete_assertion',[False,True])
def test_unknown_current_state_does_not_exempt_concrete_supported_assertions(concrete_assertion):
    source='设备控制与通知不在本次改动内。'
    raw=packet(draft_row())
    text='平台已有设备控制与通知能力。' if concrete_assertion else '资料未明确现有能力。'
    raw['summary_fields']={'current_state':business_value(text,'unknown',[dict(source='S1',locator='L1')])}
    plan=grounding.prepare(raw,dict(excerpts=[dict(source='S1',locator='L1',text=source)]));response=review(plan)
    if concrete_assertion:
        response['fields'][0]['claims'][0].update(verdict='supported',evidence=[dict(source='S1',locator='L1',quote=source)])
    checked=grounding.validate(plan,response)
    assert (checked['status']=='blocked')==concrete_assertion
    if concrete_assertion:assert checked['findings'][0]['kind']=='current_state_unproven'

@pytest.mark.parametrize('exact',[False,True])
def test_empty_qualifier_requires_whole_fact_decision(exact):
    source='不增加附件、原因分类和审批字段。'
    candidate=source if exact else '不增加附件。'
    raw=packet(draft_row());raw['requirements'][0]['statement']=business_value(candidate,'fact')
    plan=grounding.prepare(raw,dict(excerpts=[dict(source='S1',locator='L1',text=source)]));response=review(plan)
    response['inputs'][0]['decisions'][0].update(verdict='represented',targets=['F1'],constraint_checks=[dict(
        kind='negative',source_qualifier='',source_action=source,source_anchor=source,
        targets=[dict(id='F1',candidate_anchor=candidate)],reason='原实测空子注解')])
    checked=grounding.validate(plan,response)
    assert (checked['status']=='passed')==exact
    if exact:
        assert checked['annotations'][0]['binding_checks']['qualifier_in_anchor'] is False
    else:assert any(f['kind']=='input_constraint_binding_unproven' for f in checked['findings'])


def test_second_patch_not_sent_without_full_review_budget(tmp_path):
    async def scenario():
        store,p=project(tmp_path);raw=packet(draft_row('R1'),draft_row('R2'))
        raw['summary_fields']={key:business_value() for key in SUMMARY_FIELDS}
        provider=GroundingProvider(raw,'second_repair');workflow=Workflow(store,provider)
        config=dict(workflow.config('ingest'),max_calls=8)
        run=workflow.start(p['id'],p['revision'],'ingest','请整理初稿',config_override=config)
        await workflow.tasks[run['id']]
        saved=store.get_record(p['id'],run['id'],'run')
        assert saved['status']=='paused_budget' and saved['calls']==6
        assert saved['intake_grounding']['corrections']==1
        assert len(provider.received)==6 and store.get(p['id'])==p
    asyncio.run(scenario())


@pytest.mark.parametrize('scope',['fields','inputs'])
def test_partition_added_chinese_comma_is_finding_not_approval(scope):
    plan=fixture();response=review(plan);original=copy.deepcopy(response)
    key='claims' if scope=='fields' else 'decisions'
    quote=response[scope][0][key][0]['quote']
    response[scope][0][key][0]['quote']=quote[:2]+'，'+quote[2:]
    checked=grounding.validate(plan,response)
    assert checked['status']=='blocked'
    finding=next(f for f in checked['findings'] if f['kind']=='punctuation_partition_unproven')
    assert finding['review_quotes']==[response[scope][0][key][0]['quote']]
    assert finding['original_text']==original[scope][0][key][0]['quote']
    assert response[scope][0][key][0]['quote']==quote[:2]+'，'+quote[2:]


@pytest.mark.parametrize('scope',['fields','inputs'])
def test_partition_missing_negation_remains_hard_invalid(scope):
    raw=packet(draft_row());raw['requirements'][0]['statement']=business_value('不得修改原有数据。','fact')
    plan=grounding.prepare(raw,dict(excerpts=[dict(source='S1',locator='L1',text='不得修改原有数据。')]))
    response=review(plan);key='claims' if scope=='fields' else 'decisions'
    assert response[scope][0][key][0]['quote'].startswith('不')
    response[scope][0][key][0]['quote']=response[scope][0][key][0]['quote'][1:]
    with pytest.raises(Problem,match='全部子'):
        grounding.validate(plan,response)

@pytest.mark.parametrize('evidence_present',[True,False])
def test_success_reason_omission_does_not_replace_evidence(evidence_present):
    plan=fixture();response=review(plan)
    response['fields'][0].pop('reason')
    claim=response['fields'][0]['claims'][0];claim.pop('reason')
    decision=response['inputs'][0]['decisions'][0]
    decision.update(verdict='represented',targets=['F1']);decision.pop('reason')
    if not evidence_present:claim['evidence']=[]
    before=copy.deepcopy(response);checked=grounding.validate(plan,response)
    assert (checked['status']=='passed')==evidence_present
    assert response==before
    assert sum(a['kind']=='explanation_omitted' for a in checked['annotations'])==3
    if not evidence_present:assert any(f['kind']=='supported_claim_unproven' for f in checked['findings'])


@pytest.mark.parametrize('section,key,verdict',[('fields','claims','unsupported'),('inputs','decisions','missing')])
def test_non_success_reason_still_required(section,key,verdict):
    plan=fixture();response=review(plan)
    item=response[section][0][key][0];item['verdict']=verdict;item.pop('reason')
    with pytest.raises(Problem):grounding.validate(plan,response)


class ChallengeProvider(GroundingProvider):
    def __init__(self,raw,mode,after_main=None):
        super().__init__(raw,'pass');self.challenge_mode=mode;self.challenges=0;self.after_main=after_main
    async def request(self,config,messages,evidence=None):
        header=json.loads(messages[0]['content'].split('可信任务头：',1)[1])
        protocol=header.get('grounding_protocol')
        if protocol!=grounding.CHALLENGE_VERSION:
            result,meta=await super().request(config,messages,evidence)
            if protocol==grounding.COVERAGE_VERSION and self.after_main:self.after_main()
            return result,meta
        self.challenges+=1;self.received.append(copy.deepcopy(messages))
        if evidence:evidence.before_send(config)
        payload=json.loads(messages[1]['content'][0]['text'])
        result=dict(candidate_hash=payload['candidate_hash'],input_hash=payload['input_hash'],
            checked_field_ids=[field['id'] for field in payload['fields']],findings=[])
        if self.challenge_mode in ('blocked','repair') and (self.challenge_mode=='blocked' or self.challenges==1):
            field=next(f for f in payload['fields'] if f['path']==['requirements',0,'statement'])
            result['findings']=[dict(id=field['id'],quote=field['text'],reason='合成独立短核：该动作的位置不在自身资料中，不能借另一动作的位置。',sources=field['sources'])]
        if self.challenge_mode=='explicit_location':
            field=next(f for f in payload['fields'] if f['path']==['requirements',0,'statement'])
            assert field['text']=='在控制台首页显示处理结果。'
            assert any(ref['text']==field['text'] for ref in field['own_sources'])
        if self.challenge_mode=='scope_change':result['candidate']={'scope':'injected'}
        meta=dict(usage={'prompt_tokens':1,'completion_tokens':1},finish_reason='stop')
        if evidence:evidence.response(dumps(result),http_status=200,finish_reason='stop',usage=meta['usage'])
        return result,meta


@pytest.mark.parametrize('mode,limit,expected_calls,status',[
    ('blocked',None,9,'blocked'),('repair',None,6,'repaired'),('explicit_location',None,3,'passed'),
    ('scope_change',None,3,'unverified'),('blocked',4,3,'unverified'),('blocked',2,1,'unverified'),
    ('cancel',None,2,'unverified')])
def test_source_challenge_blocks_false_main_pass_and_preserves_scope(tmp_path,mode,limit,expected_calls,status):
    async def scenario():
        store,p=project(tmp_path);raw=packet(draft_row('R1'),draft_row('R2'))
        raw['summary_fields']={key:business_value() for key in SUMMARY_FIELDS}
        # Fixture oracle only: this is not model semantic validation.
        raw['requirements'][0]['statement']=business_value('在控制台首页显示处理结果。','fact')
        if mode=='explicit_location':
            # Put the explicit location in the authorized synthetic source content.
            with store.edit(p['id'],p['revision'],'合成明确来源') as (current,_):
                current['sources'][0]['excerpts'][0]['text']='在控制台首页显示处理结果。'
            p=store.get(p['id'])
        provider=ChallengeProvider(raw,mode);workflow=Workflow(store,provider)
        config=dict(workflow.config('ingest'),max_calls=limit)
        run=workflow.start(p['id'],p['revision'],'ingest','请整理初稿',config_override=config)
        if mode=='cancel':provider.after_main=lambda:store.update_run(p['id'],run['id'],status='cancelled')
        await workflow.tasks[run['id']]
        saved=store.get_record(p['id'],run['id'],'run')
        assert saved['intake_grounding']['status']==status,saved
        assert len(provider.received)==saved['calls']==expected_calls
        if status in ('passed','repaired'):
            assert saved['result_applied'] and saved['intake_grounding']['challenge_status']=='passed'
            if mode=='explicit_location':
                accepted=store.get(p['id'])['business_draft_reviews'][-1]['accepted']
                assert accepted[0]['raw']['statement']==raw['requirements'][0]['statement']
        else:
            assert not saved.get('result_applied') and store.get(p['id'])==p
        if mode=='blocked' and limit is None:
            assert saved['intake_grounding']['corrections']==2 and provider.challenges==3
        if mode=='cancel':assert provider.challenges==0
    asyncio.run(scenario())


@pytest.mark.parametrize('mutation',['missing','duplicate','hash','span','scope'])
def test_challenge_cannot_skip_fields_change_scope_or_invent_location(mutation):
    plan=fixture();response=dict(candidate_hash=plan['candidate_hash'],input_hash=plan['input_hash'],
        checked_field_ids=[f['id'] for f in plan['fields']],findings=[])
    if mutation=='missing':response['checked_field_ids'].pop()
    if mutation=='duplicate':response['checked_field_ids'].append(response['checked_field_ids'][0])
    if mutation=='hash':response['candidate_hash']='wrong'
    if mutation=='span':response['findings']=[dict(id='F1',quote='不存在的动作位置',reason='合成',sources=[])]
    if mutation=='scope':response['scope']='injected'
    with pytest.raises(Problem):grounding.validate_challenge(plan,response)

def test_relation_and_constraint_reason_are_optional_metadata_only():
    source='在控制台显示结果。'
    raw=packet(draft_row());raw['requirements'][0]['statement']=business_value(source,'fact')
    plan=grounding.prepare(raw,dict(excerpts=[dict(source='S1',locator='L1',text=source)]));response=review(plan)
    response['fields'][0]['claims'][0]['relation_checks']['location']=[dict(
        candidate_qualifier='控制台',candidate_action='显示',candidate_anchor=source,
        source='S1',locator='L1',source_anchor=source)]
    response['inputs'][0]['decisions'][0].update(verdict='represented',targets=['F1'],constraint_checks=[dict(
        kind='scope',source_qualifier='控制台',source_action='显示',source_anchor=source,
        targets=[dict(id='F1',candidate_anchor=source)])])
    checked=grounding.validate(plan,response)
    assert checked['status']=='passed'
    assert {a['role'] for a in checked['annotations'] if a['kind']=='explanation_omitted'}=={'relation','constraint'}
    response['fields'][0]['claims'][0]['relation_checks']['location'][0]['source_anchor']='另一个动作'
    assert grounding.validate(plan,response)['status']=='blocked'
