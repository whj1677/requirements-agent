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
