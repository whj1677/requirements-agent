"""Frozen source-scope examples; offline model verdicts are protocol fixtures."""
import copy
import json

import pytest

from app import intake_grounding as g
from app.core import Problem
from tests.test_business_draft import packet, draft_row, business_value


SOURCE = ('告警详情中新增处理记录，失败提示原因并保留输入，可手工重试。'
          '场站交接页新增交接备注，仅记录作者可修改，交接备注最多200字。'
          '两种对象修改成功立即显示修改后的内容，失败保留原数据和输入。'
          '验收以各对象新建和修改、保存失败保留输入为准。')


def frozen_plan(text, basis='fact'):
    row = draft_row('new-handover')
    row['statement'] = business_value(text, basis, [dict(source='S1', locator='L1')])
    return g.prepare(packet(row), dict(excerpts=[dict(source='S1', locator='L1', text=SOURCE)]))


def test_actual_pair_and_correction_receive_full_late_shared_scope_without_rewriting_source():
    plan = frozen_plan('交接备注长度不得超过200字；保存失败后已填内容不清空。')
    before = copy.deepcopy(plan)
    _, correction = g.correction_messages(plan, dict(findings=[]))
    for messages in (g.coverage_messages(plan), g.challenge_messages(plan), correction):
        assert g.SOURCE_SCOPE_POLICY in messages[0]['content']
        payload = json.loads(messages[1]['content'][0]['text'])
        assert payload['context']['excerpts'][0]['text'] == SOURCE
    challenge = json.loads(g.challenge_messages(plan)[1]['content'][0]['text'])
    statement = next(f for f in challenge['fields'] if f['path'] == ['requirements', 0, 'statement'])
    assert statement['own_sources'][0]['text'] == SOURCE
    assert plan == before


def test_source_only_challenge_keeps_policy_and_exact_own_locator():
    original = frozen_plan('交接备注长度不得超过200字。')
    context = copy.deepcopy(original['context'])
    context['excerpts'].append(dict(source='S1', locator='L2', text='其它位置的资料不能自动作为本字段引用。'))
    plan = g.source_plan(original['candidate'], context)
    messages = g.challenge_messages(plan)
    assert messages[0]['content'].count(g.SOURCE_SCOPE_POLICY) == 1
    assert '支持只能来自该字段自身引用的source/locator' in messages[0]['content']
    assert '先以suggested提出补引并复核' in messages[0]['content']
    payload = json.loads(messages[1]['content'][0]['text'])
    statement = next(f for f in payload['fields'] if f['path'] == ['requirements', 0, 'statement'])
    assert statement['own_sources'] == [dict(source='S1', locator='L1', text=SOURCE)]
    assert all(f['path'][2] in ('statement', 'behavior', 'acceptance') for f in payload['fields'])


@pytest.mark.parametrize('text', [
    '在场站交接页新增并查看交接备注。',
    '交接备注空值保存不成功并提示必填。',
    '交接备注保存失败允许手工重试。',
    '交接备注修改失败保留原数据或输入。',
    '本站所有值班员都可修改交接备注。',
    '交接备注发起修改后立即显示修改后的内容。',
    '现有场站交接页已支持备注修改，每次修改追加一条历史。',
])
def test_shared_scope_policy_never_filters_real_reviewer_rejections(text):
    plan = frozen_plan(text)
    field = next(f for f in plan['fields'] if f['path'] == ['requirements', 0, 'statement'])
    response = dict(candidate_hash=plan['candidate_hash'], input_hash=plan['input_hash'],
        checked_field_ids=[f['id'] for f in plan['fields']], findings=[dict(id=field['id'],
            quote=text, reason='固定反例：对象动作或限定不受原文支持。', sources=[dict(source='S1', locator='L1')])])
    checked = g.validate_challenge(plan, response)
    assert checked['status'] == 'blocked' and checked['findings'][0]['quote'] == text
    response['findings'][0]['sources'][0]['locator'] = 'invented'
    with pytest.raises(Problem):
        g.validate_challenge(plan, response)
