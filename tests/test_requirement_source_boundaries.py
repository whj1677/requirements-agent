"""Offline source-boundary regressions for a single energy-cloud requirement."""
import copy
import json

import jsonschema
import pytest

from app.core import Problem
from app.provider import DEFAULT, assemble
from app.requirement_recommendation import compact_context, scope, validate_response
from tests.test_requirement_recommendation import local_header, project, response, target


def test_visual_observation_does_not_become_text_classification_quote(tmp_path):
    _, p = project(tmp_path)
    image = dict(copy.deepcopy(p['sources'][0]), id='SRC-image', image_mime='image/png', excerpts=[
        dict(id='EX-image', source_id='SRC-image', text='图片像素已提取，尚未视觉分析')])
    p['sources'].append(image)
    value = response(p)
    row = value['proposals'][0]
    image_ref = dict(source_id='SRC-image', excerpt_id='EX-image')
    # Ordinary source_refs may still point to visual evidence for human review.
    row['source_refs'] = [image_ref]
    header = local_header(p, tmp_path)
    validator = jsonschema.Draft202012Validator(header['schema'])
    assert validator.is_valid(value)
    row.update(change_type='new', scope_evidence=[dict(state='change',
        quote='筛选区包含“电价名称”输入框', source_ref=image_ref)])
    assert not validator.is_valid(value)
    with pytest.raises(Problem, match='图片视觉观察'):
        validate_response(value, p, scope(p, target()))
    # Even an exact placeholder is not business-classification evidence.
    row['scope_evidence'][0]['quote'] = image['excerpts'][0]['text']
    assert not validator.is_valid(value)


def test_unknown_classification_schema_requires_no_claimed_classification_evidence(tmp_path):
    _, p = project(tmp_path)
    value = response(p)
    value['proposals'][0]['scope_evidence'] = [dict(state='change', quote='原文',
        source_ref=p['items'][0]['source_refs'][0])]
    header = local_header(p, tmp_path)
    assert not jsonschema.Draft202012Validator(header['schema']).is_valid(value)
    assert 'scope_evidence=[]' in header['requirement_recommendation_contract']['quotation']


def test_cross_requirement_acceptance_rejected_not_silently_trimmed(tmp_path):
    _, p = project(tmp_path)
    value = response(p)
    value['proposals'].append(dict(temp_id='TMP-ac', action='add', target_item_id=None,
        kind='acceptance', title='另一功能的保存', statement='在另一功能中保存校验结果。',
        applies_to='to_be', epistemic_status='reported', source_refs=p['items'][0]['source_refs'],
        related_refs=['REQ-0001', 'REQ-other']))
    before = copy.deepcopy(value)
    header = local_header(p, tmp_path)
    assert not jsonschema.Draft202012Validator(header['schema']).is_valid(value)
    with pytest.raises(Problem, match='另一需求的子流程'):
        validate_response(value, p, scope(p, target()))
    assert value == before
    assert '不能仅删除越界 related_refs' in header['requirement_recommendation_contract']['acceptance']


@pytest.mark.parametrize('location', ['original', 'candidate', 'other_row'])
def test_identical_local_rule_reused_but_unrelated_row_does_not_block(tmp_path, location):
    _, p = project(tmp_path)
    rule = next(item for item in p['items'] if item['id'] == 'RULE-0001')
    if location == 'candidate':
        rule = dict(rule, id='RULE-candidate', target_item_id=rule['id'],
                    statement='本行新的待确认规则。', selection_status='candidate')
        p['items'].append(rule)
    elif location == 'other_row':
        rule = dict(rule, id='RULE-other', related_refs=['REQ-other'], statement='本行新的待确认规则。')
        p['items'].append(rule)
    value = response(p)
    value['proposals'].append(dict(temp_id='TMP-rule', action='add', target_item_id=None,
        kind='rule', title=rule['title'], statement=rule['statement'], applies_to=rule['applies_to'],
        epistemic_status='reported', source_refs=rule['source_refs'], related_refs=['REQ-0001']))
    before = copy.deepcopy(value)
    if location == 'other_row':
        validate_response(value, p, scope(p, target()))
    else:
        with pytest.raises(Problem, match='同文条款'):
            validate_response(value, p, scope(p, target()))
    assert value == before


def test_context_deduplicates_only_audit_identities_and_can_round_trip():
    snapshot = [dict(source_id='SRC-1', excerpt_id='EX-1', source_hash='raw', excerpt_hash='part', source_version=1)]
    question = dict(id='Q-1', question='谁可编辑？', answer='管理员',
        answer_source_refs=[dict(source_id='SRC-2', excerpt_id='EX-2')],
        decision_review_proposal=dict(decisions=[dict(coverage='pending')], source_snapshot=snapshot))
    context = dict(questions=[question], requirement_recommendation=dict(questions=[question]),
        excerpts=[dict(id='EX-1', text='保留当前真实业务资料。')],
        historical=dict(source_snapshot=[dict(source_id='SRC-old', excerpt_id='EX-old', text='历史独有正文')]))
    original = copy.deepcopy(context)
    compact = compact_context(context)
    registry = compact.pop('audit_snapshot_registry')
    compact.pop('audit_snapshot_notice')
    assert len(registry) == 1
    assert compact['historical'] == original['historical']

    def restore(value):
        if isinstance(value, list): return [restore(row) for row in value]
        if not isinstance(value, dict): return value
        if set(value) == {'audit_snapshot_ref'}: return registry[value['audit_snapshot_ref']]
        return {key:restore(item) for key,item in value.items()}

    assert restore(compact) == original
    assert context == original


def test_saved_visual_observations_are_visible_despite_unchanged_image_placeholder():
    context = dict(source_status=[dict(id='SRC-observed'), dict(id='SRC-unobserved')],
        excerpts=[dict(id='EX-image', source_id='SRC-observed', text='图片像素已提取，尚未视觉分析')],
        vision_observations=[dict(result=dict(observations=[dict(
            source_ref=dict(source_id='SRC-observed', excerpt_id='EX-image'),
            observation='导航显示列表入口', limitations=['局部文字看不清'])]))])
    original = copy.deepcopy(context)
    result = compact_context(context)
    assert [source['saved_visual_observation_count'] for source in result['source_status']] == [1, 0]
    assert result['excerpts'] == original['excerpts']
    assert result['vision_observations'] == original['vision_observations']
    assert context == original


def test_local_assemble_keeps_all_materials_and_current_answers(tmp_path):
    _, p = project(tmp_path)
    snapshot = [dict(source_id='SRC-0001', excerpt_id='EX-0001', source_hash='s', excerpt_hash='e', source_version=1)] * 30
    p['questions'][0].update(answer='允许相接', status='answered',
        answer_source_refs=p['items'][0]['source_refs'],
        decision_review_proposal=dict(decisions=[dict(coverage='pending')], source_snapshot=snapshot))
    plain, excerpts, omitted = assemble(p, 'clarify', '', DEFAULT, tmp_path)
    local, local_excerpts, local_omitted = assemble(p, 'clarify', '', DEFAULT, tmp_path,
        requirement_recommendation_scope=scope(p, target()))
    ctx = json.loads(local[1]['content'][0]['text'])
    assert local_excerpts == excerpts and local_omitted == omitted
    assert ctx['questions'][0]['answer'] == '允许相接'
    assert ctx['questions'][0]['answer_source_refs'] == p['items'][0]['source_refs']
    assert len(ctx['audit_snapshot_registry']) == 1
    assert 'audit_snapshot_registry' not in json.loads(plain[1]['content'][0]['text'])
