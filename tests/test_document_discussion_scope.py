"""Section discussions preserve the confirmed draft; frozen responses, isolated data."""
import asyncio
import copy
import json

import pytest

from app.core import Problem, dumps
from app.product_flow import BEHAVIOR, INTAKE, SCOPE, blockers, checkpoint, fingerprint
from app.provider import DEFAULT, origin
from app.requirement_confirmation import confirmation_current, row_fingerprint
from app.store import Store
from app.workflow import Workflow
from tests.helpers import prepared
from tests.test_runtime import FakeProvider


def project(tmp_path, shared):
    store = Store(tmp_path)
    initial = prepared(store)
    with store.edit(initial['id'], initial['revision'], 'synthetic section discussion fixture', bump=False) as (p, _):
        if shared:
            p['workflow_mode'] = 'shared-draft'
        p['product_context'] = {key: '合成核对：' + label for key, label in {**INTAKE, **SCOPE}.items()}
        p['product_context']['scope_ids'] = ['REQ-0001']
        p['product_context_proposal'] = {'users': '已有运维使用者建议', 'intent': '已有完整项目目标'}
        p['product_context_proposal_evidence'] = {'users': {'run_id': 'RUN-original', 'source_refs': []}}
        req = p['items'][0]
        req['behavior'] = {key: '已明确：' + label for key, label in BEHAVIOR.items()}
        req['requirement_confirmation'] = dict(actor='authenticated_local_user',
            schema='requirement-confirmation-1', content_hash=row_fingerprint(p, req))
        for step in (1, 2):
            checkpoint(p, step, fingerprint(p, step))
        p['grants'][origin(DEFAULT)] = {'source_ids': [source['id'] for source in p['sources']]}
    return store, store.get(initial['id'])


def discussion_response(stage='change'):
    ref = dict(source_id='SRC-0001', excerpt_id='EX-0001')
    return dict(schema_version='1.1', stage=stage, summary='合成章节讨论，角色权限仍待明确。',
        proposals=[dict(temp_id='TMP-local-role', action='add', target_item_id=None,
            kind='actor', title='待明确的运维角色', statement='建议将运维角色补充到本节，具体权限待确认。',
            applies_to='to_be', epistemic_status='proposed', source_refs=[ref], related_refs=['RULE-0001'])],
        questions=[dict(temp_id='QTMP-local-role', topic_key='local-role-scope',
            question='运维角色适用什么权限范围？', why='角色权限待业务明确', options=[],
            blocking=True, blocking_stage=3, related_refs=['RULE-0001'], source_refs=[ref])],
        findings=[], used_source_refs=[ref], limitations=[],
        product_context_proposal={key: '局部讨论产生的全局摘要建议' for key in {**INTAKE, **SCOPE}},
        result=(dict(changes=[], impacted_refs=['RULE-0001'], unimpacted_refs=['REQ-0001', 'AC-0001'],
            needs_new_confirmation=True, unresolved_effects=['运维角色权限待明确']) if stage == 'change' else
            dict(scope_summary='运维角色权限仍待业务明确。', outstanding_decisions=['明确运维角色权限'], next_focus='核对角色范围')))


class DiscussionProvider(FakeProvider):
    async def request(self, config, messages, evidence=None):
        if not self.received:
            return await super().request(config, messages, evidence)
        self.received.append(copy.deepcopy(messages))
        targets = json.loads(messages[1]['content'])['targets']
        response = dict(reviews=[dict(question_id=row['question_id'], decisions=[dict(
            id=row['question_id'], question=row['questions'][0]['question'], coverage='pending', answer_quote='',
            related_refs=row['questions'][0]['related_refs'], why='资料未明确权限范围')]) for row in targets])
        if evidence:
            evidence.response(dumps(response), http_status=200, finish_reason='stop', usage={'total_tokens': 10})
        return response, dict(response_model='OFFLINE_FIXTURE', usage={'total_tokens': 10},
                              origin=origin(config), finish_reason='stop')


@pytest.mark.parametrize('repair',[False,True])
def test_wording_only_chapter_feedback_uses_empty_refs_and_preserves_canonical(tmp_path,repair):
    async def scenario():
        from app.actions import Actions
        from app.contracts import validate_response
        from app.provider import assemble
        store,before=project(tmp_path,False)
        target=dict(kind='document',id=before['documents']['prd']['id'],
            section_id=before['documents']['prd']['content']['sections'][0]['section_id'])
        good=discussion_response();good.pop('product_context_proposal')
        good.update(summary='本节建议只纠正叙述措辞，保留已有条款及业务决定。',proposals=[],questions=[])
        good['result']=dict(changes=[],impacted_refs=[],unimpacted_refs=[],needs_new_confirmation=False,unresolved_effects=[])
        bad=copy.deepcopy(good);bad['result']['impacted_refs']=[target['id']+'.'+target['section_id']]
        _,excerpts,_=assemble(before,'change','仅纠正叙述',dict(DEFAULT),tmp_path,discussion_target=target)
        validate_response(good,'change',before,excerpts)
        for invalid in (target['id'],target['section_id'],bad['result']['impacted_refs'][0],'DOC-other.MRD-other'):
            invalid_value=copy.deepcopy(good);invalid_value['result']['impacted_refs']=[invalid]
            with pytest.raises(Problem) as caught:validate_response(invalid_value,'change',before,excerpts)
            assert caught.value.code=='REFERENCE_INVALID'
        class Traced(FakeProvider):
            async def request(self,c,m,evidence=None):
                evidence.before_send(c)
                value,meta=await super().request(c,m,evidence)
                evidence.response(dumps(value),http_status=200,finish_reason='stop',usage={'total_tokens':10})
                return value,meta
        provider=Traced([bad,good] if repair else [good]);workflow=Workflow(store,provider);actions=Actions(store,workflow)
        feedback='只纠正本节叙述，不改变条款，不新增业务决定。'
        body=dict(expected_revision=before['revision'],action='change',message=feedback,document_type='prd',target=target,max_calls=2)
        plan=actions.plan(before['id'],body);task=actions.start(before['id'],body,plan['plan_hash'],'wording-only-discussion')
        await actions.jobs[task['id']]
        task=store.get_record(before['id'],task['id'],'user_task');run=store.get_record(before['id'],task['run_ids'][0],'run')
        assert run['status']!='failed',run
        assert task['calls']==len(provider.received)==(2 if repair else 1)
        header=json.loads(provider.received[0][0]['content'].split('可信任务头：',1)[1])
        contract=header['document_discussion_contract']
        assert contract['target']==target and target['id'] not in contract['allowed_reference_ids']
        assert 'impacted_refs=[]' in contract['instruction']
        if repair:
            assert '本次章节讨论引用边界' in provider.received[1][-1]['content']
            assert 'DOC.section' in provider.received[1][-1]['content']
            first=json.loads((store.folder/'evidence/model-calls'/(run['attempts'][0]['call_id']+'.json')).read_text('utf-8'))
            assert json.loads(first['final_output'])==bad
            last=json.loads((store.folder/'evidence/model-calls'/(run['attempts'][-1]['call_id']+'.json')).read_text('utf-8'))
            audit=last['reference_repair_normalization']
            assert audit==run['attempts'][-1]['reference_repair_normalization']
            assert audit['changes'][0]['before']==bad['result']['impacted_refs']
            assert audit['changes'][0]['after']==[] and json.loads(last['final_output'])==good
        assert run['response']==good
        after=store.get(before['id'])
        for key in ('items','questions','documents','product_context','stage_checks'):
            assert after[key]==before[key]
        assert after['sources'][:len(before['sources'])]==before['sources']
        assert len(after['sources'])==len(before['sources'])+1
        assert any(feedback in e['text'] for e in after['sources'][-1]['excerpts'])
        assert run['discussion_result']==dict(item_ids=[],question_ids=[])
    asyncio.run(scenario())


@pytest.mark.parametrize('fault',[None,'non_document_target','other_stage','stale_target','unknown_locator','bare_section',
    'unknown_item','remove_valid','replace_with_valid','body_change','source_change','question_change','ambiguous_document'])
def test_document_locator_repair_is_exact_deletion_only(tmp_path,fault):
    from app.ingest import preserve_response,response_anchor
    _,p=project(tmp_path,False)
    document=p['documents']['prd'];target=dict(kind='document',id=document['id'],section_id=document['content']['sections'][0]['section_id'])
    locator=document['id']+'.'+target['section_id'];stage='change'
    original=discussion_response();original['result']['impacted_refs']=['RULE-0001',locator]
    original['result']['unimpacted_refs']=['REQ-0001',document['id']]
    if fault=='unknown_locator':original['result']['impacted_refs'][1]='DOC-old.PRD-old'
    if fault=='bare_section':original['result']['impacted_refs'][1]=target['section_id']
    if fault=='unknown_item':original['result']['impacted_refs'][1]='REQ-missing'
    repaired=copy.deepcopy(original)
    repaired['result']['impacted_refs']=['RULE-0001'];repaired['result']['unimpacted_refs']=['REQ-0001']
    if fault=='non_document_target':target=dict(kind='item',id='REQ-0001')
    if fault=='other_stage':stage='clarify'
    if fault=='stale_target':target['id']='DOC-old'
    if fault=='remove_valid':repaired['result']['impacted_refs']=[]
    if fault=='replace_with_valid':repaired['result']['impacted_refs']=['RULE-0001','AC-0001']
    if fault=='body_change':repaired['proposals'][0]['statement']='未经批准改变规范文字'
    if fault=='source_change':repaired['used_source_refs']=[]
    if fault=='question_change':repaired['questions']=[]
    if fault=='ambiguous_document':p['documents']['copy']=copy.deepcopy(document)
    excerpts=p['sources'][0]['excerpts'];before=copy.deepcopy((p,original,repaired))
    anchor=response_anchor(original,excerpts,stage,p)
    if fault=='other_stage':
        from app.ingest import reference_repair_anchor
        assert reference_repair_anchor(original,repaired,excerpts,stage,p,document_target=target)==original
        from app.contracts import validate_response
        with pytest.raises(Problem):validate_response(repaired,stage,p,excerpts)
        return
    if fault:
        with pytest.raises(Problem) as caught:
            preserve_response(anchor,repaired,excerpts,stage,original,p,document_target=target)
        assert caught.value.code=='SEMANTIC_BLOCKED'
    else:
        changes=preserve_response(anchor,repaired,excerpts,stage,original,p,document_target=target)
        assert len(changes)==2 and all(c['rule']=='document-locator-reference-removal-1' for c in changes)
    assert (p,original,repaired)==before


@pytest.mark.parametrize('shared', [False, True])
@pytest.mark.parametrize('scoped', [False, True])
@pytest.mark.parametrize('stage', ['change', 'clarify'])
def test_section_scope_is_traceable_and_preserves_global_context(tmp_path, shared, scoped, stage):
    async def scenario():
        store, before = project(tmp_path, shared)
        target = dict(kind='document', id=before['documents']['prd']['id'],
            section_id=before['documents']['prd']['content']['sections'][0]['section_id']) if stage == 'change' else \
            dict(kind='question', id=before['questions'][0]['id'])
        expected_target = copy.deepcopy(target)
        raw = discussion_response(stage)
        provider = DiscussionProvider([raw])
        workflow = Workflow(store, provider)
        started = workflow.start(before['id'], before['revision'], stage, '运维人员',
            discussion_target=target if scoped else None)
        # The stored scope is bound before asynchronous execution, not the caller's dict.
        target['section_id'] = 'tampered-after-start'
        await workflow.tasks[started['id']]
        run = store.get_record(before['id'], started['id'], 'run')
        assert run['status'] == 'awaiting_user', run
        after = store.get(before['id'])
        assert after['documents'] == before['documents']
        assert after['items'][:len(before['items'])] == before['items']
        assert after['questions'][:len(before['questions'])] == before['questions']
        assert after['product_context'] == before['product_context']
        assert after['stage_checks'] == before['stage_checks']
        assert after['sources'] == before['sources']
        assert confirmation_current(after, after['items'][0])
        candidate, question = after['items'][-1], after['questions'][-1]
        assert candidate['selection_status'] == 'candidate'
        assert question['blocking'] and question['status'] == 'open' and question['answer'] is None
        assert question['id'] in [row['actions'][0].get('question_id') for row in blockers(after, 3)]
        assert run['response']['product_context_proposal'] == raw['product_context_proposal']
        if scoped:
            assert run['discussion_target'] == expected_target
            assert run['discussion_result'] == dict(item_ids=[candidate['id']], question_ids=[question['id']])
            assert after['product_context_proposal'] == before['product_context_proposal']
            assert after['product_context_proposal_evidence'] == before['product_context_proposal_evidence']
        else:
            assert 'discussion_target' not in run and 'discussion_result' not in run
            assert after['product_context_proposal'] == raw['product_context_proposal']
            assert 'product_context_proposal_evidence' not in after
    asyncio.run(scenario())


@pytest.mark.parametrize('target', [
    {'kind': 'document', 'id': 'DOC-other', 'section_id': 'S1'},
    {'kind': 'document', 'id': 'DOC-prd', 'section_id': 'missing'},
    {'kind': 'document', 'id': 'DOC-prd', 'section_id': 'S1', 'label': 'client label'},
])
def test_invalid_document_scope_starts_no_run(tmp_path, target):
    store, before = project(tmp_path, True)
    provider = DiscussionProvider([])
    workflow = Workflow(store, provider)
    with pytest.raises(Problem) as error:
        workflow.start(before['id'], before['revision'], 'change', '运维人员', discussion_target=target)
    assert error.value.code == 'TARGET_INVALID'
    assert provider.received == [] and store.records(before['id'], 'run') == []
    assert store.get(before['id']) == before


@pytest.mark.parametrize('shared', [False, True])
def test_section_result_records_reused_output_question_but_not_unrelated_batch_review(tmp_path, shared):
    async def scenario():
        store, before = project(tmp_path, shared)
        raw = discussion_response()
        reused_id = 'Q-existing-role'
        unrelated_id = before['questions'][0]['id']
        with store.edit(before['id'], before['revision'], '合成复用问题及无关旧回答', bump=False) as (p, _):
            p['questions'].append(dict(id=reused_id,
                **{key:copy.deepcopy(value) for key,value in raw['questions'][0].items() if key != 'temp_id'},
                status='open', answer=None))
            # A shared-draft review can include an older pending answer in its
            # batch; the exact chapter result must not claim that question.
            p['questions'][0].update(status='answered', answer='旧的已保存回答。',
                answered_at='synthetic', decision_coverage='pending', understanding_status='pending')
        before = store.get(before['id'])
        provider = DiscussionProvider([raw])
        workflow = Workflow(store, provider)
        target = dict(kind='document', id=before['documents']['prd']['id'],
            section_id=before['documents']['prd']['content']['sections'][0]['section_id'])
        started = workflow.start(before['id'], before['revision'], 'change', '运维人员',
            discussion_target=target)
        await workflow.tasks[started['id']]
        run = store.get_record(before['id'], started['id'], 'run')
        assert run['status'] == 'awaiting_user', run
        after = store.get(before['id'])
        assert run['discussion_result']['question_ids'] == [reused_id]
        assert [q['id'] for q in after['questions']] == [q['id'] for q in before['questions']]
        assert unrelated_id not in run['discussion_result']['question_ids']
        if shared:
            assert unrelated_id in run['decision_review']['reviewed_question_ids']
            assert len(provider.received) == 2
        else:
            assert len(provider.received) == 1
        assert after['documents'] == before['documents']
    asyncio.run(scenario())


@pytest.mark.parametrize('unrelated_split', [False, True])
def test_section_result_keeps_only_output_roots_and_their_actual_split_children(tmp_path, monkeypatch, unrelated_split):
    class SplitProvider(DiscussionProvider):
        async def request(self, config, messages, evidence=None):
            if not self.received:
                return await super().request(config, messages, evidence)
            self.received.append(copy.deepcopy(messages))
            targets = json.loads(messages[1]['content'])['targets']
            response = dict(reviews=[dict(question_id=row['question_id'], decisions=[dict(
                id='TEMP-'+row['question_id']+'-'+str(index), question=text, coverage='pending', answer_quote='',
                related_refs=row['questions'][0]['related_refs'], why='合成独立决定待明确')
                for index,text in enumerate(('运维角色允许查看哪些数据？', '运维角色允许执行哪些操作？'))])
                for row in targets])
            if evidence:
                evidence.response(dumps(response), http_status=200, finish_reason='stop', usage={'total_tokens':10})
            return response, dict(response_model='OFFLINE_FIXTURE', usage={'total_tokens':10},
                                  origin=origin(config), finish_reason='stop')

    async def scenario():
        store, before = project(tmp_path, True)
        unrelated_id = before['questions'][0]['id']
        if unrelated_split:
            from app import decision_run
            original_build_request = decision_run.build_request

            def batch_with_unrelated_split(staged, original, excerpts, question_ids=None):
                # Inject a batch boundary in which the independent audit also
                # splits an unrelated old open question. The workflow input
                # still contains it; chapter ownership must follow only this
                # output's mapped roots, regardless of new IDs in the batch.
                audit_input = copy.deepcopy(original)
                audit_input['questions'] = [q for q in audit_input['questions'] if q['id'] != unrelated_id]
                return original_build_request(staged, audit_input, excerpts, question_ids=question_ids)

            monkeypatch.setattr(decision_run, 'build_request', batch_with_unrelated_split)
        provider = SplitProvider([discussion_response()])
        workflow = Workflow(store, provider)
        target = dict(kind='document', id=before['documents']['prd']['id'],
            section_id=before['documents']['prd']['content']['sections'][0]['section_id'])
        started = workflow.start(before['id'], before['revision'], 'change', '运维人员',
            discussion_target=target)
        await workflow.tasks[started['id']]
        run = store.get_record(before['id'], started['id'], 'run')
        assert run['status'] == 'awaiting_user', run
        after = store.get(before['id'])
        parent = next(q for q in after['questions'] if q.get('superseded_by') and q['id'] != unrelated_id)
        assert len(parent['superseded_by']) == 2
        assert set(run['discussion_result']['question_ids']) == {parent['id'], *parent['superseded_by']}
        if unrelated_split:
            unrelated = next(q for q in after['questions'] if q['id'] == unrelated_id)
            assert len(unrelated['superseded_by']) == 2
            assert set(run['decision_review']['split_question_ids']) == {parent['id'], unrelated_id}
            assert not ({unrelated_id, *unrelated['superseded_by']}
                        & set(run['discussion_result']['question_ids']))
        assert len(provider.received) == 2
    asyncio.run(scenario())
