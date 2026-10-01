"""Paired public-flow counterexamples; isolated synthetic data, no live calls."""
import copy
import json

import pytest

from app.contracts import validate_response
from app.core import Problem, dumps
from app.product_flow import checkpoint, status
from app.provider import DEFAULT, assemble
from app.semantic_quality import current_document_checks
from app.workflow import apply_response
from tests.helpers import EXAMPLES
from tests.test_document_quality_v4 import compile_fixture_plan
from tests.test_semantic_quality_regressions import AMBIGUOUS, CODEPOINTS, plan, project


REF = dict(source_id='SRC-0001', excerpt_id='EX-0001')
OTHER_REF = dict(source_id='SRC-0001', excerpt_id='EX-OTHER')
FAILURE = '服务明确失败时原记录不变，结果未知时按同一请求ID查询，不自动重发。'
BAD_FAILURE = '失败场景原记录不变，不能自动重发。'


def question(temp, related, source, text='备注长度按Unicode码点还是扩展字素簇计数？'):
    return dict(temp_id=temp, topic_key=temp, question=text, why='影响本期验收。',
                options=[], blocking=True, related_refs=[related], source_refs=[source])


def analysis_case(tmp_path, stage):
    p = project(tmp_path, '本期新增备注。')
    p['sources'][0]['excerpts'][0]['text'] = AMBIGUOUS
    p['sources'][0]['excerpts'].append(dict(p['sources'][0]['excerpts'][0],
        id='EX-OTHER', text='姓名颜色待决定。'))
    value = copy.deepcopy(EXAMPLES[stage])
    value['proposals'] = [dict(temp_id='TMP-COUNT', kind='requirement', action='revise',
        target_item_id='REQ-0001', title='备注长度', statement=AMBIGUOUS,
        applies_to='to_be', epistemic_status='reported', source_refs=[REF], related_refs=[])]
    value['questions'] = []
    return p, value


@pytest.mark.parametrize('stage', ['ingest', 'clarify', 'change'])
@pytest.mark.parametrize('case', ['split_binding', 'colour', 'colour_with_counting_why'])
def test_analysis_requires_one_question_jointly_bound_to_object_source_and_dimension(tmp_path, stage, case):
    p, value = analysis_case(tmp_path, stage)
    if case == 'split_binding':
        value['questions'] = [question('QTMP-A', 'TMP-COUNT', OTHER_REF),
                              question('QTMP-B', 'REQ-CANDIDATE', REF)]
    else:
        value['questions'] = [question('QTMP-A', 'TMP-COUNT', REF, 'Unicode码点要显示什么颜色？')]
        if case == 'colour_with_counting_why':
            value['questions'][0]['why'] = '备注按Unicode码点计数与emoji一码点冲突，影响计数验收。'
    before = copy.deepcopy((p, value))
    with pytest.raises(Problem) as error:
        validate_response(value, stage, p, p['sources'][0]['excerpts'])
    assert error.value.code == 'SEMANTIC_BLOCKED'
    assert (p, value) == before


@pytest.mark.parametrize('stage', ['ingest', 'clarify', 'change'])
@pytest.mark.parametrize('reuse', [False, True])
def test_jointly_bound_counting_question_is_accepted_and_existing_question_can_be_reused(tmp_path, stage, reuse):
    p, value = analysis_case(tmp_path, stage)
    q = question('QTMP-A', 'REQ-0001', REF)
    if reuse:
        p['questions'].append(dict(q, id='Q-COUNT', status='open', answer=None))
    else:
        value['questions'] = [q]
    before = copy.deepcopy((p, value))
    assert validate_response(value, stage, p, p['sources'][0]['excerpts']) == value
    assert (p, value) == before


@pytest.mark.parametrize('case', ['new_neutral_proposal', 'additional_unrelated_citation'])
@pytest.mark.parametrize('correct_source', [False, True])
def test_source_conflict_can_bind_new_candidate_without_questioning_unrelated_citation(tmp_path, case, correct_source):
    p, value = analysis_case(tmp_path, 'ingest')
    if case=='new_neutral_proposal':
        p['items'] = []
        p['questions'] = []
        value['proposals'][0].update(action='add', target_item_id=None,
            statement='本期新增备注，长度口径待产品负责人决定。')
    else:
        value['proposals'][0]['source_refs'].append(OTHER_REF)
    value['questions'] = [question('QTMP-COUNT', 'TMP-COUNT', REF if correct_source else OTHER_REF)]
    before = copy.deepcopy((p, value))
    if correct_source:
        assert validate_response(value, 'ingest', p, p['sources'][0]['excerpts']) == value
    else:
        with pytest.raises(Problem) as error:
            validate_response(value, 'ingest', p, p['sources'][0]['excerpts'])
        assert error.value.code == 'SEMANTIC_BLOCKED'
    assert (p, value) == before


@pytest.mark.parametrize('answer,accepted', [
    ('Unicode码点显示蓝色。', False),
    ('尚未决定按Unicode码点还是扩展字素簇计数。', False),
    (CODEPOINTS, True),
])
def test_answer_must_choose_counting_method_before_resolving_source_conflict(tmp_path, answer, accepted):
    p = project(tmp_path, CODEPOINTS)
    p['sources'][0]['excerpts'][0]['text'] = AMBIGUOUS
    p['questions'][0].update(question='备注长度如何计数？', answer=answer,
                            related_refs=['REQ-0001'], source_refs=[REF], blocking=True)
    value = copy.deepcopy(EXAMPLES['ingest'])
    before = copy.deepcopy(p)
    if accepted:
        validate_response(value, 'ingest', p, p['sources'][0]['excerpts'])
    else:
        with pytest.raises(Problem) as error:
            validate_response(value, 'ingest', p, p['sources'][0]['excerpts'])
        assert error.value.code == 'SEMANTIC_BLOCKED'
    assert p == before


def compile_explanation(p, text, kind, context=()):
    proposal = plan(text, list(context))
    if kind == 'mrd':
        proposal['sections'][0]['section_key'] = 'goal'
    return compile_fixture_plan(proposal, p, kind)


@pytest.mark.parametrize('kind', ['prd', 'mrd'])
@pytest.mark.parametrize('evidence,text,accepted', [
    (FAILURE, BAD_FAILURE, False),
    (FAILURE, '失败场景原记录不变，例如网络超时。', False),
    (FAILURE, '失败场景原记录不变，并非允许自动重发。', False),
    (FAILURE, '失败场景原记录不变，不应自动重发。', False),
    (FAILURE, '例如网络超时，失败场景原记录不变。', False),
    (FAILURE, '服务明确失败时原记录不变；网络超时结果未知，不能自动重发，应查询原结果。', True),
    (FAILURE, '失败场景不能保证原记录不变，结果未知需查询原结果。', True),
    ('所有站点成功才整批成功。', '部分站点成功即可整批成功。', False),
    ('所有站点成功才整批成功。', '部分站点成功即可整批成功，不能自动重发。', False),
    ('所有站点成功才整批成功。', '只有全部站点成功才整批成功，部分成功不算整批成功。', True),
    ('所有站点成功才整批成功。', '部分成功不能视为整批成功，只有全部成功才整批成功。', True),
    ('所有站点成功才整批成功。', '并非部分站点成功即可整批成功，只有全部成功才整批成功。', True),
    ('其它会话主动刷新读最新备注。', '需在培训说明中提示刷新，不能自动推送。', False),
    ('其它会话主动刷新读最新备注。', '建议在培训说明中提示刷新，不能自动推送。', True),
])
def test_negation_is_local_to_the_claim_in_public_generation(tmp_path, kind, evidence, text, accepted):
    p = project(tmp_path, evidence)
    before = copy.deepcopy(p)
    if accepted:
        result = compile_explanation(p, text, kind)
        assert text in dumps(result)
        validate_response(result, 'prd', p, p['sources'][0]['excerpts'], kind)
    else:
        with pytest.raises(Problem) as error:
            compile_explanation(p, text, kind)
        assert error.value.code == 'SEMANTIC_BLOCKED'
        assert 'explanations[0]' in error.value.message
    assert p == before


@pytest.mark.parametrize('kind', ['prd', 'mrd'])
@pytest.mark.parametrize('existing,accepted', [
    ('现有系统已支持登录。', False),
    ('现有系统已支持联系人姓名变更审计。', False),
    ('没有证据表明现有系统已支持联系人备注变更审计。', False),
    ('现有系统已支持登录，没有证据表明已支持备注审计。', False),
    ('现有系统已支持联系人备注变更审计，但尚未覆盖失败备注变更审计。', False),
    ('现有系统已支持联系人备注变更审计，仅限成功变更。', False),
    ('现有系统已支持联系人备注变更审计，成功与失败场景均已覆盖。', True),
])
def test_existing_implementation_evidence_matches_capability_and_failure_scenario(tmp_path, kind, existing, accepted):
    p = project(tmp_path, '失败备注变更应沿用审计，保留操作者与变更前后内容。')
    p['product_context']['current_state'] = existing
    for step in (1, 2):
        checkpoint(p, step, status(p)[step-1]['content_hash'])
    text = '现有审计已覆盖失败备注变更场景。'
    before = copy.deepcopy(p)
    if accepted:
        assert text in dumps(compile_explanation(p, text, kind, ['current_state']))
    else:
        with pytest.raises(Problem) as error:
            compile_explanation(p, text, kind, ['current_state'])
        assert error.value.code == 'SEMANTIC_BLOCKED'
    assert p == before


@pytest.mark.parametrize('text', ['现有审计已覆盖这一场景。',
                                '审计已覆盖备注成功变更。',
                                '系统已支持成功备注变更审计。'])
@pytest.mark.parametrize('existing,accepted', [
    ('现有系统已支持登录。', False),
    ('没有证据表明已支持备注审计。', False),
    ('现有系统已支持联系人姓名变更审计。', False),
    ('现有系统已支持联系人备注变更审计，操作者和变更前后内容均有记录。', True),
])
def test_anaphoric_scene_claim_needs_corresponding_remark_audit_evidence(tmp_path, existing, accepted, text):
    p = project(tmp_path, '成功备注变更应沿用既有审计。')
    p['product_context']['current_state'] = existing
    for step in (1, 2):
        checkpoint(p, step, status(p)[step-1]['content_hash'])
    if accepted:
        assert text in dumps(compile_explanation(p, text, 'prd', ['current_state']))
    else:
        with pytest.raises(Problem) as error:
            compile_explanation(p, text, 'prd', ['current_state'])
        assert error.value.code == 'SEMANTIC_BLOCKED'


def stored_documents(tmp_path, bad_mrd=False):
    p = project(tmp_path, FAILURE)
    for kind in ('prd', 'mrd'):
        apply_response(p, compile_explanation(p, '服务明确失败时原记录不变。', kind))
        artifact = p['documents'][kind]
        blocks = [b for s in artifact['content']['sections'] for b in s['blocks'] if b['kind']=='explanation']
        if kind=='prd' or bad_mrd:
            blocks[0]['text'] = BAD_FAILURE
    return p


def review_for_checks(p):
    value = copy.deepcopy(EXAMPLES['review'])
    value['result']['required_decisions'] = []
    value['findings'] = [dict(type='unsupported_claim', severity='warning',
        message=c['check_id']+'：'+c['quote']+' 超出本段引用含义。', related_refs=c['related_refs'],
        source_refs=[], suggested_resolution='恢复明确失败条件，保留结果未知的独立处理。')
        for c in current_document_checks(p)]
    return value


@pytest.mark.parametrize('case', ['false_mrd_id', 'false_block_id', 'wrong_dimension',
                                 'false_mrd_quote', 'false_block_quote', 'both_documents_claim',
                                 'false_mrd_plain', 'false_block_plain', 'same_error_in_valid_finding'])
def test_each_extra_document_error_location_must_be_real_even_after_prd_is_covered(tmp_path, case):
    p = stored_documents(tmp_path)
    checks = current_document_checks(p)
    assert len(checks)==1 and checks[0]['document_type']=='prd'
    value = review_for_checks(p)
    extra = copy.deepcopy(value['findings'][0])
    mrd = p['documents']['mrd']
    section = next(s for s in mrd['content']['sections'] if any(b['kind']=='explanation' for b in s['blocks']))
    index = next(i for i,b in enumerate(section['blocks']) if b['kind']=='explanation')
    location = f"{mrd['id']}.{section['section_id']}.blocks[{index}]"
    if case=='false_mrd_id':
        extra['message'] = 'DOC:'+location+':failure_condition：失败场景原记录不变，条件被泛化。'
    elif case=='false_block_id':
        extra['message'] = checks[0]['check_id'].replace('.blocks[0]', '.blocks[999]')
        if extra['message']==checks[0]['check_id']:
            extra['message'] = 'DOC:'+checks[0]['location'].rsplit('.blocks[',1)[0]+'.blocks[999]:failure_condition'
    elif case=='wrong_dimension':
        extra['message'] = checks[0]['check_id'].replace(':failure_condition', ':success_quantifier')
    elif case=='false_mrd_quote':
        extra['message'] = 'MRD 的段落误写“失败场景原记录不变”，条件被泛化。'
    elif case=='false_block_quote':
        extra['message'] = location+'：误写“失败场景原记录不变”，条件被泛化。'
    elif case=='false_mrd_plain':
        extra['message'] = 'MRD 的失败条件被泛化，明确失败与结果未知混为一谈。'
    elif case=='false_block_plain':
        extra['message'] = location+'：此处也存在失败条件被泛化的错误。'
    elif case=='same_error_in_valid_finding':
        value['findings'][0]['message'] += ' MRD 某章节也存在同样错误。'
        extra = None
    else:
        extra['message'] = 'PRD 和 MRD 都误写“失败场景原记录不变”，条件被泛化。'
    if extra is not None:
        value['findings'].append(extra)
    before = copy.deepcopy(p)
    with pytest.raises(Problem) as error:
        validate_response(value, 'review', p, p['sources'][0]['excerpts'])
    assert error.value.code == 'SEMANTIC_BLOCKED'
    assert p == before


@pytest.mark.parametrize('both_bad', [False, True])
def test_real_document_findings_bind_only_to_actual_erroneous_documents_and_blocks(tmp_path, both_bad):
    p = stored_documents(tmp_path, both_bad)
    before = copy.deepcopy(p)
    messages, excerpts, _ = assemble(p, 'review', '', DEFAULT, tmp_path)
    context = json.loads(messages[1]['content'][0]['text'])
    assert {c['document_type'] for c in context['semantic_document_checks']} == ({'prd','mrd'} if both_bad else {'prd'})
    value = review_for_checks(p)
    if not both_bad:
        value['findings'][0]['message'] += ' PRD 此处有误；MRD 中的明确失败条件表述正确。'
    else:
        value['findings'].append(dict(type='unsupported_claim', severity='warning',
            message='PRD 和 MRD 都误写“失败场景原记录不变”，条件被泛化。',
            related_refs=['REQ-0001'], source_refs=[], suggested_resolution='按明确失败条件纠正。'))
    assert validate_response(value, 'review', p, excerpts) == value
    assert p == before


def test_review_keeps_unsupported_implementation_risk_without_changing_stored_documents(tmp_path):
    p = project(tmp_path, '成功备注变更应沿用既有审计。')
    p['product_context']['current_state'] = '没有证据表明已支持备注审计；现有系统已支持登录。'
    for step in (1, 2):
        checkpoint(p, step, status(p)[step-1]['content_hash'])
    apply_response(p, compile_explanation(p, '成功备注变更应沿用既有审计。', 'prd', ['current_state']))
    block = next(b for s in p['documents']['prd']['content']['sections'] for b in s['blocks'] if b['kind']=='explanation')
    block['text'] = '现有审计已覆盖这一场景。'
    before = copy.deepcopy(p)
    checks = current_document_checks(p)
    assert len(checks)==1 and checks[0]['dimension']=='existing_implementation'
    value = copy.deepcopy(EXAMPLES['review'])
    with pytest.raises(Problem) as error:
        validate_response(value, 'review', p, p['sources'][0]['excerpts'])
    assert error.value.code == 'SEMANTIC_BLOCKED'
    validate_response(review_for_checks(p), 'review', p, p['sources'][0]['excerpts'])
    assert p == before
