"""Quoted withdrawn assertions do not define a selected counting alternative."""
import copy

import pytest

from app.contracts import validate_response
from app.core import Problem
from app.semantic_quality import counting_witness
from tests.test_semantic_quality_boundaries import analysis_case, question

BAD = 'emoji按一个码点计'
GOOD = '按Unicode码点分别计数：👍🏽占2个码点，99个A加👍🏽共101个码点，超过上限100而拒绝；同时删除或纠正“emoji按一个码点计”的表述。'


@pytest.mark.parametrize('verb', ['删除', '去掉', '纠正', '删除或纠正'])
@pytest.mark.parametrize('quotes', [('“', '”'), ('「', '」')])
def test_explicitly_rejected_quoted_counting_assertion_is_not_current_rule(verb, quotes):
    text = f'按Unicode码点分别计数；{verb}{quotes[0]}{BAD}{quotes[1]}的表述。'
    assert counting_witness(text, alternative=True) is None
    # A proposed correction is not evidence of adoption in the source/current rule.
    assert counting_witness(text) is not None


@pytest.mark.parametrize('text', [
    '按Unicode码点计数，原文写“emoji按一个码点计”。',
    '按Unicode码点计数；不要删除“emoji按一个码点计”的表述。',
    '按Unicode码点计数；尚未纠正“emoji按一个码点计”。',
    '按Unicode码点计数；删除“背景颜色”，emoji按一个码点计。',
    GOOD + ' 本期emoji按一个码点计。',
])
def test_quoted_retained_or_other_active_conflict_still_has_witness(text):
    assert counting_witness(text, alternative=True) is not None


@pytest.mark.parametrize('stage', ['ingest', 'clarify', 'change'])
@pytest.mark.parametrize('retained', [False, True])
def test_public_analysis_keeps_conflict_question_and_checks_option_scope(tmp_path, stage, retained):
    p, value = analysis_case(tmp_path, stage)
    ref = dict(source_id='SRC-0001', excerpt_id='EX-0001')
    q = question('QTMP-C', 'TMP-COUNT', ref)
    q['options'] = [GOOD if not retained else GOOD + ' 本期emoji按一个码点计。',
                    '按扩展字素簇计数，99个A加👍🏽共100个扩展字素簇，允许。']
    value['questions'] = [q]
    before = copy.deepcopy((p, value))
    if retained:
        with pytest.raises(Problem) as error:
            validate_response(value, stage, p, p['sources'][0]['excerpts'])
        assert error.value.code == 'SEMANTIC_BLOCKED'
    else:
        validate_response(value, stage, p, p['sources'][0]['excerpts'])
    assert (p, value) == before
