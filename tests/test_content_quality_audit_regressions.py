"""Public-flow content-quality regressions using synthetic, isolated projects."""

import copy
import json
import re

import pytest

from app.contracts import gate, validate_response
from app.core import Problem, dumps
from app.document_reader import reader_document
from app.product_flow import checkpoint, status
from app.provider import DEFAULT, assemble
from app.semantic_quality import current_document_checks
from app.store import Store
from app.workflow import apply_response
from tests.helpers import EXAMPLES, prepared
from tests.product_flow_helpers import check_prepared
from tests.test_semantic_quality_boundaries import (
    REF,
    analysis_case,
    compile_explanation,
    review_for_checks,
    stored_documents,
)
from tests.test_semantic_quality_regressions import (
    AMBIGUOUS,
    CODEPOINTS,
    GRAPHEMES,
    project,
)


FAILURE = '服务明确失败时原记录不变，结果未知时按同一请求ID查询，不自动重发。'


@pytest.mark.parametrize('kind', ['prd', 'mrd'])
@pytest.mark.parametrize(
    ('case', 'text', 'accepted'),
    [
        ('tail_negation', '失败场景原记录不变，不能自动重发。', False),
        ('front_negation', '不能自动重发且失败场景原记录不变。', False),
        ('punctuation', '失败场景下，原记录不变。', False),
        ('uncertain_failure', '不明确失败场景原记录不变。', False),
        (
            'condition_preserved',
            '服务明确失败后，原记录并未被改写，仍保留原样；结果未知按同一请求ID查询，不自动重发。',
            True,
        ),
    ],
)
def test_failure_generalization_stays_blocked_despite_negation_and_punctuation(
    tmp_path, kind, case, text, accepted
):
    p = project(tmp_path / f'{case}-{kind}', FAILURE)
    before = copy.deepcopy(p)
    if accepted:
        assert text in dumps(compile_explanation(p, text, kind))
    else:
        with pytest.raises(Problem) as error:
            compile_explanation(p, text, kind)
        assert error.value.code == 'SEMANTIC_BLOCKED'
    assert p == before


@pytest.mark.parametrize(
    ('case', 'existing', 'accepted'),
    [
        ('missing_scenario', '现有系统已支持备注变更审计。', False),
        (
            'unrelated_failure',
            '现有系统已支持备注变更审计，失败登录已支持自动告警。',
            False,
        ),
        (
            'same_sentence_unrelated_failure',
            '现有系统已支持备注变更审计且失败登录已支持自动告警。',
            False,
        ),
        (
            'both_outcomes',
            '现有系统已支持备注变更审计，成功与失败场景均已覆盖。',
            True,
        ),
        ('explicit_failure', '现有系统已支持失败备注变更审计。', True),
        ('possible_support', '现有系统可能已支持失败备注变更审计。', False),
        ('unconfirmed_support', '现有系统未确认已支持失败备注变更审计。', False),
        ('unproven_support', '无法证明现有系统已支持失败备注变更审计。', False),
        ('possible_continuation', '现有系统已支持备注变更审计，失败场景可能已覆盖。', False),
        ('login_only', '现有系统已支持登录。', False),
    ],
)
def test_failure_login_evidence_does_not_prove_failure_note_audit(
    tmp_path, case, existing, accepted
):
    evidence = '失败备注变更应沿用审计，保留操作者与变更前后内容。'
    p = project(tmp_path / case, evidence)
    p['product_context']['current_state'] = existing
    for step in (1, 2):
        checkpoint(p, step, status(p)[step - 1]['content_hash'])
    before = copy.deepcopy(p)
    text = '现有审计已覆盖失败备注变更场景。'
    if accepted:
        assert text in dumps(
            compile_explanation(p, text, 'prd', ['current_state'])
        )
    else:
        with pytest.raises(Problem) as error:
            compile_explanation(p, text, 'prd', ['current_state'])
        assert error.value.code == 'SEMANTIC_BLOCKED'
    assert p == before


@pytest.mark.parametrize(
    ('case', 'answer', 'current', 'accepted'),
    [
        ('matching_count', CODEPOINTS, CODEPOINTS, True),
        ('question_then_choice', '备注长度按Unicode码点还是扩展字素簇计数？我选择按Unicode码点计数，复合emoji逐码点计数。', CODEPOINTS, True),
        ('choice_then_undecided', '按Unicode码点计数，具体采用何种口径待确认。', CODEPOINTS, False),
        ('opposite_count', CODEPOINTS, GRAPHEMES, False),
        (
            'unresolved_count',
            '不按Unicode码点计数，具体采用何种口径待确认。',
            GRAPHEMES,
            False,
        ),
    ],
)
def test_counting_answer_must_match_current_requirement_and_resolve_choice(
    tmp_path, case, answer, current, accepted
):
    p = project(tmp_path / case, current)
    p['sources'][0]['excerpts'][0]['text'] = AMBIGUOUS
    p['questions'][0].update(
        question='备注长度如何计数?',
        answer=answer,
        related_refs=['REQ-0001'],
        source_refs=[REF],
        blocking=True,
    )
    value = copy.deepcopy(EXAMPLES['ingest'])
    before = copy.deepcopy(p)
    if accepted:
        assert validate_response(value, 'ingest', p, p['sources'][0]['excerpts']) == value
    else:
        with pytest.raises(Problem) as error:
            validate_response(value, 'ingest', p, p['sources'][0]['excerpts'])
        assert error.value.code == 'SEMANTIC_BLOCKED'
    assert p == before


@pytest.mark.parametrize('kind', ['prd', 'mrd'])
@pytest.mark.parametrize('current,accepted', [(CODEPOINTS, True), (GRAPHEMES, False)])
def test_generation_keeps_current_unit_consistent_with_the_answer(tmp_path, kind, current, accepted):
    p = project(tmp_path / kind, current)
    p['questions'][0].update(question='备注长度按何种单位计数？', answer=CODEPOINTS,
                             related_refs=['REQ-0001'])
    before = copy.deepcopy(p)
    if accepted:
        compile_explanation(p, current, kind)
    else:
        with pytest.raises(Problem) as error:
            compile_explanation(p, current, kind)
        assert error.value.code == 'SEMANTIC_BLOCKED'
    assert p == before


@pytest.mark.parametrize(
    ('case', 'statement', 'accepted'),
    [
        ('plain_conflict', AMBIGUOUS, False),
        (
            'unrelated_negated_clause',
            AMBIGUOUS.rstrip('。') + '，emoji并非蓝色显示。',
            False,
        ),
        ('consistent_codepoint_rule', CODEPOINTS, True),
    ],
)
def test_unrelated_denial_and_punctuation_do_not_waive_counting_conflict(
    tmp_path, case, statement, accepted
):
    p = project(tmp_path / case, statement)
    before = copy.deepcopy(p)
    if accepted:
        assert compile_explanation(p, statement, 'prd')['result']['document_type'] == 'prd'
    else:
        with pytest.raises(Problem) as error:
            compile_explanation(p, statement, 'prd')
        assert error.value.code == 'SEMANTIC_BLOCKED'
    assert p == before


@pytest.mark.parametrize(
    ('case', 'correction', 'accepted'),
    [
        ('revision_matches_answer', CODEPOINTS, True),
        ('revision_opposes_answer', GRAPHEMES, False),
    ],
)
def test_revision_candidate_must_follow_answered_counting_choice(
    tmp_path, case, correction, accepted
):
    p, value = analysis_case(tmp_path / case, 'clarify')
    p['items'][0]['statement'] = AMBIGUOUS
    answer_ref = dict(source_id='SRC-0001', excerpt_id='EX-ANSWER')
    p['sources'][0]['excerpts'].append(
        dict(p['sources'][0]['excerpts'][0], id='EX-ANSWER', text=CODEPOINTS)
    )
    question = p['questions'][0]
    question.update(
        question='备注长度按Unicode码点还是扩展字素簇计数?',
        answer=CODEPOINTS,
        related_refs=['REQ-0001'],
        source_refs=[REF],
        answer_source_refs=[answer_ref],
        blocking=True,
    )
    value['proposals'][0].update(
        statement=correction,
        answer_refs=[question['id']],
        source_refs=[REF, answer_ref],
    )
    before = copy.deepcopy(p)
    if accepted:
        assert validate_response(value, 'clarify', p, p['sources'][0]['excerpts']) == value
    else:
        with pytest.raises(Problem) as error:
            validate_response(value, 'clarify', p, p['sources'][0]['excerpts'])
        assert error.value.code == 'SEMANTIC_BLOCKED'
    assert p == before


@pytest.mark.parametrize(
    ('case', 'accepted'),
    [
        ('prd_only_real_finding', True),
        ('same_finding_claims_mrd', False),
        ('extra_mrd_finding', False),
        ('mrd_claim_when_absent', False),
    ],
)
def test_every_claimed_document_finding_must_match_a_real_document_location(
    tmp_path, case, accepted
):
    p = stored_documents(tmp_path / case)
    if case == 'mrd_claim_when_absent':
        del p['documents']['mrd']
    value = review_for_checks(p)
    if case == 'same_finding_claims_mrd':
        value['findings'][0]['message'] += ' MRD 某章节也存在同样错误。'
    elif case in ('extra_mrd_finding', 'mrd_claim_when_absent'):
        extra = copy.deepcopy(value['findings'][0])
        extra['message'] = 'MRD 某章节也存在同样错误。'
        value['findings'].append(extra)
    before = copy.deepcopy(p)
    if accepted:
        assert validate_response(value, 'review', p, p['sources'][0]['excerpts']) == value
    else:
        with pytest.raises(Problem) as error:
            validate_response(value, 'review', p, p['sources'][0]['excerpts'])
        assert error.value.code == 'SEMANTIC_BLOCKED'
    assert p == before


def test_review_can_report_a_genuinely_missing_mrd_without_inventing_a_location(tmp_path):
    p = stored_documents(tmp_path)
    prd = p['documents']['prd']
    for section in prd['content']['sections']:
        for block in section['blocks']:
            if block['kind'] == 'explanation':
                block['text'] = '服务明确失败时原记录不变。'
    del p['documents']['mrd']
    assert current_document_checks(p) == []

    value = copy.deepcopy(EXAMPLES['review'])
    value['findings'] = [
        dict(
            type='gap',
            severity='warning',
            message='MRD 未生成，当前没有市场需求正文可供内容核对。',
            related_refs=[],
            source_refs=[],
            suggested_resolution='生成 MRD 后再审阅其内容。',
        )
    ]
    value['result'].update(assessment='needs_changes', required_decisions=[])
    before = copy.deepcopy(p)
    assert validate_response(value, 'review', p, p['sources'][0]['excerpts']) == value
    assert p == before


def test_same_real_document_defect_may_be_reported_for_both_prd_and_mrd(tmp_path):
    p = stored_documents(tmp_path, bad_mrd=True)
    checks = current_document_checks(p)
    assert {check['document_type'] for check in checks} == {'prd', 'mrd'}
    value = review_for_checks(p)
    assert validate_response(value, 'review', p, p['sources'][0]['excerpts']) == value


@pytest.mark.parametrize(
    ('case', 'quote', 'accepted'),
    [
        ('real_normative_quote', FAILURE, True),
        ('fabricated_normative_quote', '失败场景原记录不变', False),
        ('real_explanation_quote', '服务明确失败时原记录不变。', True),
    ],
)
def test_review_quote_must_exist_in_the_cited_canonical_document_block(
    tmp_path, case, quote, accepted
):
    p = project(tmp_path / case, FAILURE)
    explanation = '服务明确失败时原记录不变。'
    apply_response(p, compile_explanation(p, explanation, 'prd'))
    artifact = p['documents']['prd']
    frozen_statement = artifact['item_snapshot'][0]['statement']
    if case == 'real_normative_quote':
        # Review the frozen document after the live item has received a later revision.
        p['items'][0]['statement'] = '当前条目后来修改为另一个口径；不得替换已存文档原句。'
    block_kind = 'explanation' if case == 'real_explanation_quote' else 'requirement'
    section = next(
        section
        for section in artifact['content']['sections']
        if any(block['kind'] == block_kind for block in section['blocks'])
    )
    section_id = section['section_id']
    canonical_index = next(
        index
        for index, block in enumerate(section['blocks'])
        if block['kind'] == block_kind
    )
    location = f"{artifact['id']}.{section_id}.blocks[{canonical_index}]"
    view = reader_document(artifact)
    # Reader blocks are filtered projections; do not treat reader positions as canonical indices.
    assert (quote in dumps(view)) is accepted
    if case == 'real_normative_quote':
        assert quote in frozen_statement
        assert quote not in p['items'][0]['statement']

    value = copy.deepcopy(EXAMPLES['review'])
    value['findings'].append(
        dict(
            type='gap',
            severity='warning',
            message=(
                location
                + '：原文“'
                + quote
                + '”尚未说明结果未知的查询超时后如何让操作者继续核对。'
            ),
            related_refs=['REQ-0001'],
            source_refs=[REF],
            suggested_resolution='保留原文，核对查询仍无结果时的可观察行为。',
        )
    )
    before = copy.deepcopy(p)
    if accepted:
        assert validate_response(value, 'review', p, p['sources'][0]['excerpts']) == value
    else:
        with pytest.raises(Problem) as error:
            validate_response(value, 'review', p, p['sources'][0]['excerpts'])
        assert error.value.code == 'SEMANTIC_BLOCKED'
    assert p == before


def test_public_review_input_carries_stable_canonical_source_locations(tmp_path):
    p = stored_documents(tmp_path)
    checks = current_document_checks(p)
    assert len(checks) == 1
    check = checks[0]
    artifact = p['documents'][check['document_type']]
    artifact_before_projection = copy.deepcopy(artifact)
    default_reader = reader_document(artifact)
    assert all(
        'source_location' not in block
        for section in default_reader['sections']
        for block in section['blocks']
    )
    located_reader = reader_document(artifact, include_locations=True)
    projected_locations = [
        block.get('source_location')
        for section in located_reader['sections']
        for block in section['blocks']
        if block.get('source_location')
    ]
    assert check['location'] in projected_locations
    assert artifact == artifact_before_projection

    messages, _, _ = assemble(p, 'review', '', DEFAULT, tmp_path)
    context = json.loads(messages[1]['content'][0]['text'])

    public_check = next(
        row for row in context['semantic_document_checks']
        if row['check_id'] == check['check_id']
    )
    assert public_check['location'] == check['location']
    located_blocks = [
        block
        for section in context['documents'][check['document_type']]['content']['sections']
        for block in section['blocks']
        if block.get('source_location') == check['location']
    ]
    assert len(located_blocks) == 1

    match = re.fullmatch(
        re.escape(artifact['id']) + r'\.(.+)\.blocks\[(\d+)\]',
        check['location'],
    )
    assert match is not None
    section_id, canonical_index = match.group(1), int(match.group(2))
    canonical_section = next(
        section for section in artifact['content']['sections']
        if section['section_id'] == section_id
    )
    assert canonical_section['blocks'][canonical_index]['text'] == located_blocks[0]['text']


@pytest.mark.parametrize(
    ('case', 'reviewed_refs', 'accepted'),
    [
        ('all_selected_normative_refs', ['REQ-0001', 'RULE-0001', 'AC-0001'], True),
        ('requirement_only', ['REQ-0001'], False),
    ],
)
def test_review_readiness_gate_requires_selected_requirement_rule_and_acceptance(
    tmp_path, case, reviewed_refs, accepted
):
    store = Store(tmp_path / case)
    p = prepared(store)
    p = check_prepared(store, p['id'])
    assert gate(p) == []
    value = copy.deepcopy(EXAMPLES['review'])
    value['findings'] = []
    value['result'].update(
        assessment='ready_for_human_review',
        reviewed_refs=reviewed_refs,
        required_decisions=[],
    )
    before = copy.deepcopy(p)

    def review_through_gate():
        validate_response(value, 'review', p, p['sources'][0]['excerpts'])
        candidate = copy.deepcopy(p)
        apply_response(candidate, value)
        issues = gate(candidate)
        if issues:
            raise Problem('GATE_BLOCKED', dumps(issues))
        return issues

    if accepted:
        assert review_through_gate() == []
    else:
        with pytest.raises(Problem) as error:
            review_through_gate()
        assert error.value.code == 'GATE_BLOCKED'
    assert p == before


def test_needs_changes_review_saves_uncovered_scope_but_keeps_gate_closed(tmp_path):
    store = Store(tmp_path / 'needs_changes_scope')
    p = check_prepared(store, prepared(store)['id'])
    value = copy.deepcopy(EXAMPLES['review'])
    value['findings'] = []
    value['result'].update(
        assessment='needs_changes',
        reviewed_refs=['REQ-0001'],
        required_decisions=[],
    )
    validate_response(value, 'review', p, p['sources'][0]['excerpts'])
    candidate = copy.deepcopy(p)
    apply_response(candidate, value)

    assert candidate['review']['response']['result']['assessment'] == 'needs_changes'
    assert candidate['review']['response']['result']['reviewed_refs'] == ['REQ-0001']
    issues = gate(candidate)
    coverage_issues = [issue for issue in issues if '语义审查覆盖不足' in issue]
    assert len(coverage_issues) == 1
    assert 'RULE-0001' in coverage_issues[0] and 'AC-0001' in coverage_issues[0]
