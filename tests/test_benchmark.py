"""Synthetic, offline comparison boundaries; no provider or network calls."""

import copy

import pytest

from app.benchmark import analysis_schema, contract, validate_analysis, validate_proposal_roles, validate_request
from app.core import Problem


def project():
    return {'sources': [
        {'id': 'SRC-B', 'purpose': 'current', 'parse_status': 'read', 'excluded': False,
         'excerpts': [{'id': 'EX-B', 'text': '对标产品支持按日期筛选。'}]},
        {'id': 'SRC-G', 'purpose': 'goal', 'parse_status': 'read', 'excluded': False,
         'excerpts': [{'id': 'EX-G', 'text': '本产品本期提供查询。'}]},
        {'id': 'SRC-X', 'purpose': 'reference', 'parse_status': 'read', 'excluded': True,
         'excerpts': [{'id': 'EX-X', 'text': '排除材料'}]},
        {'id': 'SRC-F', 'purpose': 'reference', 'parse_status': 'failed', 'excluded': False,
         'excerpts': []},
        {'id': 'SRC-I', 'purpose': 'reference', 'parse_status': 'awaiting_vision', 'excluded': False,
         'image_mime': 'image/png', 'excerpts': []},
        {'id': 'SRC-T', 'purpose': 'template', 'parse_status': 'read', 'excluded': False,
         'excerpts': [{'id': 'EX-T', 'text': '模板样例'}]},
        {'id': 'SRC-C', 'purpose': 'business', 'business_context': {}, 'parse_status': 'read',
         'excluded': False, 'excerpts': [{'id': 'EX-C', 'text': '工程事实'}]},
    ], 'items': [{'id': 'REQ-1', 'statement': '原需求'}]}


def request():
    return {'target': '竞品甲', 'focus': '筛选入口', 'source_ids': ['SRC-B']}


def response():
    return {'proposals': [{'temp_id': 'TMP-1', 'applies_to': 'to_be',
                           'epistemic_status': 'proposed',
                           'source_refs': [{'source_id': 'SRC-B', 'excerpt_id': 'EX-B'}]}],
            'benchmark_analysis': {'summary': '观察到一个筛选入口，是否采用待核对。',
                                   'observations': [{
                                       'title': '按日期筛选', 'reference_quote': '支持按日期筛选',
                                       'source_ref': {'source_id': 'SRC-B', 'excerpt_id': 'EX-B'},
                                       'difference': '', 'suggested_application': '可作为候选。',
                                       'proposal_refs': ['TMP-1', 'REQ-1']}],
                                   'limitations': []}}


def excerpts():
    return [{'source_id': 'SRC-B', 'id': 'EX-B', 'text': '对标产品支持按日期筛选。'}]


def rejects(code, action):
    with pytest.raises(Problem) as error:
        action()
    assert error.value.code == code


def test_request_preserves_source_purpose_and_identity():
    p = project()
    before = copy.deepcopy(p)
    value = validate_request(p, {'target': ' 竞品甲 ', 'focus': ' 筛选入口 ', 'source_ids': ['SRC-B']})
    assert value == request()
    assert p == before
    assert validate_request(p, {'target': '截图', 'focus': '', 'source_ids': ['SRC-I']})['source_ids'] == ['SRC-I']


@pytest.mark.parametrize('ids,code', [
    ([], 'BENCHMARK_INVALID'), (['SRC-B', 'SRC-B'], 'BENCHMARK_INVALID'),
    (['SRC-MISSING'], 'BENCHMARK_SOURCE_INVALID'),
    (['SRC-X'], 'BENCHMARK_SOURCE_INVALID'), (['SRC-F'], 'BENCHMARK_SOURCE_UNREADABLE'),
    (['SRC-T'], 'BENCHMARK_SOURCE_INVALID'), (['SRC-C'], 'BENCHMARK_SOURCE_INVALID'),
])
def test_request_rejects_invalid_or_unread_sources(ids, code):
    value = request()
    value['source_ids'] = ids
    rejects(code, lambda: validate_request(project(), value))


def test_analysis_accepts_exact_quote_and_existing_or_new_identity_without_mutation():
    p, value, ex = project(), response(), excerpts()
    before = copy.deepcopy((p, value, ex))
    result = validate_analysis(value, request(), ex, p)
    assert result == value['benchmark_analysis'] and result is not value['benchmark_analysis']
    assert (p, value, ex) == before
    assert analysis_schema()['additionalProperties'] is False
    assert '不自动采纳' in contract()


@pytest.mark.parametrize('mutation,code', [
    ('wrong_pair', 'REFERENCE_INVALID'), ('wrong_quote', 'SEMANTIC_BLOCKED'),
    ('unknown_proposal', 'REFERENCE_INVALID'), ('extra_field', 'SCHEMA_INVALID'),
])
def test_analysis_rejects_unbound_observation(mutation, code):
    value = response()
    row = value['benchmark_analysis']['observations'][0]
    if mutation == 'wrong_pair': row['source_ref']['excerpt_id'] = 'EX-G'
    if mutation == 'wrong_quote': row['reference_quote'] = '支持语音筛选'
    if mutation == 'unknown_proposal': row['proposal_refs'] = ['TMP-UNKNOWN']
    if mutation == 'extra_field': row['approved'] = True
    rejects(code, lambda: validate_analysis(value, request(), excerpts(), project()))


def test_no_transmitted_excerpt_cannot_claim_comparison_success():
    value = response()
    value['benchmark_analysis']['observations'] = []
    value['benchmark_analysis']['limitations'] = ['本轮未送入可用片段']
    rejects('SEMANTIC_BLOCKED', lambda: validate_analysis(value, request(), [], project()))


def test_reference_only_reported_decision_is_rejected_in_any_stage():
    value = response()
    value['proposals'][0]['epistemic_status'] = 'reported'
    before = copy.deepcopy(value)
    rejects('SEMANTIC_BLOCKED', lambda: validate_analysis(value, request(), excerpts(), project()))
    rejects('SEMANTIC_BLOCKED', lambda: validate_proposal_roles(value, {'SRC-B'}))
    assert value == before
    value['proposals'][0]['source_refs'] = []
    rejects('SEMANTIC_BLOCKED', lambda: validate_proposal_roles(value, {'SRC-B'}))
    value['proposals'][0]['source_refs'] = [{'source_id': 'SRC-G', 'excerpt_id': 'EX-G'}]
    assert validate_proposal_roles(value, {'SRC-B'}) is value
    value['proposals'][0]['epistemic_status'] = 'inferred'
    value['proposals'][0]['source_refs'] = [{'source_id': 'SRC-B', 'excerpt_id': 'EX-B'}]
    assert validate_proposal_roles(value, {'SRC-B'}) is value


@pytest.mark.parametrize('epistemic_status', ['proposed', 'reported', 'observed'])
def test_external_reference_cannot_become_our_existing_state(epistemic_status):
    value = response()
    value['proposals'][0].update(applies_to='as_is', epistemic_status=epistemic_status)
    rejects('SEMANTIC_BLOCKED', lambda: validate_proposal_roles(value, {'SRC-B'}))
    value['proposals'][0]['source_refs'] = [{'source_id': 'SRC-G', 'excerpt_id': 'EX-G'}]
    assert validate_proposal_roles(value, {'SRC-B'}) is value


def test_benchmark_intake_message_cannot_launder_reference_into_product_decision():
    p, value = project(), response()
    p['sources'].append({'id': 'SRC-INTENT', 'origin': 'benchmark_intake', 'purpose': 'goal',
                         'parse_status': 'read', 'excluded': False,
                         'excerpts': [{'id': 'EX-INTENT', 'text': '想对标竞品，生成待审核草稿。'}]})
    value['proposals'][0].update(epistemic_status='reported', source_refs=[
        {'source_id': 'SRC-B', 'excerpt_id': 'EX-B'},
        {'source_id': 'SRC-INTENT', 'excerpt_id': 'EX-INTENT'}])
    rejects('SEMANTIC_BLOCKED', lambda: validate_analysis(value, request(), excerpts(), p))
    rejects('SEMANTIC_BLOCKED', lambda: validate_proposal_roles(value, {'SRC-B', 'SRC-INTENT'}))


def image_case():
    p, value = project(), response()
    image = next(s for s in p['sources'] if s['id'] == 'SRC-I')
    image.update(parse_status='read', vision_run_id='RUN-LATEST',
                 excerpts=[{'id': 'EX-I', 'text': '图片原始像素；尚未视觉分析'}])
    p['messages'] = [{'role': 'assistant', 'stage': 'vision', 'run_id': 'RUN-LATEST',
                      'response': {'result': {'observations': [
                          {'source_ref': {'source_id': 'SRC-I', 'excerpt_id': 'EX-I'},
                           'observation': '截图中有按日期筛选入口。', 'uncertain': False}]}}}]
    value['benchmark_analysis']['observations'][0].update(
        reference_quote='按日期筛选入口',
        source_ref={'source_id': 'SRC-I', 'excerpt_id': 'EX-I'})
    return p, value, [{'source_id': 'SRC-I', 'id': 'EX-I', 'text': image['excerpts'][0]['text']}]


def test_image_quote_requires_actual_bound_certain_vision_observation():
    p, value, ex = image_case()
    assert validate_analysis(value, {'target': '截图', 'focus': '', 'source_ids': ['SRC-I']}, ex, p)


@pytest.mark.parametrize('mutation', ['pixel_placeholder', 'uncertain', 'unbound_pair',
                                      'missing_vision_run', 'stale_vision_run', 'non_assistant'])
def test_image_quote_rejects_unverified_or_unbound_visual_evidence(mutation):
    p, value, ex = image_case()
    observation = p['messages'][0]['response']['result']['observations'][0]
    if mutation == 'pixel_placeholder':
        value['benchmark_analysis']['observations'][0]['reference_quote'] = '图片原始像素'
    elif mutation == 'uncertain':
        observation['uncertain'] = True
    elif mutation == 'unbound_pair':
        observation['source_ref']['excerpt_id'] = 'EX-OTHER'
    elif mutation == 'missing_vision_run':
        next(s for s in p['sources'] if s['id'] == 'SRC-I').pop('vision_run_id')
    elif mutation == 'stale_vision_run':
        p['messages'][0]['run_id'] = 'RUN-OLD'
    else:
        p['messages'][0]['role'] = 'user'
    rejects('SEMANTIC_BLOCKED', lambda: validate_analysis(
        value, {'target': '截图', 'focus': '', 'source_ids': ['SRC-I']}, ex, p))
