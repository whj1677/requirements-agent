"""Existing material may fill a row draft; no new user reply or AI approval."""
import asyncio
import json

import pytest

from app.actions import Actions
from app.workflow import Workflow
from tests.test_requirement_recommendation import project, response, target
from tests.test_runtime import FakeProvider


@pytest.mark.parametrize('already_represented', [False, True])
def test_existing_source_fact_can_fill_permissions_as_candidate_without_answering_unknown(tmp_path, already_represented):
    async def scenario():
        store, initial = project(tmp_path)
        known = '业主只能看到自己建立的电价模板和运维账号建立的模板。'
        pending = '运维账号自身的可见范围未说明，待确认。'
        entry_result = '点击新增模板入口后展示新增模板对话框。'
        with store.edit(initial['id'], initial['revision'], '合成储能云权限材料与当前缺口') as (draft, _):
            draft['sources'][0]['excerpts'][0]['text'] = ('电价模板库展示可见模板。' + known + pending
                                                       + entry_result + '对话框保存与异常规则未说明。')
            requirement = next(item for item in draft['items'] if item['id'] == 'REQ-0001')
            requirement.update(title='电价模板库列表', statement='展示电价模板库列表。',
                behavior=dict(actor=('业主（查看模板）' if already_represented else ''),
                    entry='', data='电价模板', flow=('查看列表；' + entry_result if already_represented else '查看列表'),
                    permissions=(known + pending if already_represented else ''),
                    exceptions='', result='展示可见模板'), change_type='unspecified', scope_evidence=[])
            rule = next(item for item in draft['items'] if item['id'] == 'RULE-0001')
            rule.update(title='业主模板可见范围', statement=known, selection_status='candidate')
            draft['questions'][0].update(topic_key='operator-visibility',
                question='运维账号自身可以看到哪些模板？', why='材料仅明确业主范围',
                blocking=True, status='open', answer=None)
        frozen = store.get(initial['id'])
        original = next(item for item in frozen['items'] if item['id'] == 'REQ-0001')
        value = response(frozen)
        value['summary'] = '根据已有原文整理业主范围；运维账号范围仍未知，等待人工核对。'
        value['limitations'] = [pending]
        value['result'] = dict(scope_summary='本行模板列表权限',
            outstanding_decisions=['运维账号自身可以看到哪些模板'], next_focus='人工核对已有事实和未决范围')
        if already_represented:
            value['proposals'] = []
        else:
            value['proposals'][0].update(statement=original['statement'],
                behavior=dict(original['behavior'], actor='业主（查看模板）',
                              flow='查看列表；' + entry_result, permissions=known + pending),
                scope_evidence=[], answer_refs=[])
        provider = FakeProvider([value])
        workflow = Workflow(store, provider)
        actions = Actions(store, workflow)
        body = dict(expected_revision=frozen['revision'], action='clarify', message='',
                    document_type='prd', target=target())
        plan = actions.plan(frozen['id'], body)
        assert not plan['missing']
        task = actions.start(frozen['id'], body, plan['plan_hash'], 'existing-facts-only', False)
        await actions.jobs[task['id']]
        finished = store.get_record(frozen['id'], task['id'], 'user_task')
        result = store.get(frozen['id'])
        assert finished['status'] in ('partial', 'succeeded'), finished
        assert finished['calls'] == 1 and len(provider.received) == 1
        context = json.loads(provider.received[0][1]['content'][0]['text'])
        # The prior material and unresolved question reached the real pipeline;
        # no extra source, saved answer or ad-hoc user instruction is needed.
        assert known in context['excerpts'][0]['text']
        assert context['questions'][0]['answer'] is None
        assert result['sources'] == frozen['sources']
        assert result['questions'] == frozen['questions']
        assert result['items'][:len(frozen['items'])] == frozen['items']
        candidates = result['items'][len(frozen['items']):]
        if already_represented:
            assert candidates == []
        else:
            assert len(candidates) == 1
            candidate = candidates[0]
            assert candidate['kind'] == 'requirement' and candidate['target_item_id'] == original['id']
            assert candidate['selection_status'] == 'candidate'
            assert candidate['source_refs'] == original['source_refs']
            assert candidate['behavior']['permissions'] == known + pending
            assert candidate['behavior']['actor'] == '业主（查看模板）'
            assert entry_result in candidate['behavior']['flow']
            assert candidate['behavior']['entry'] == '' and candidate['behavior']['exceptions'] == ''
            assert candidate['change_type'] == 'unspecified'
            assert candidate['answer_refs'] == [] and not candidate.get('answer_versions')
        assert result['active_baseline_id'] is None

    asyncio.run(scenario())
