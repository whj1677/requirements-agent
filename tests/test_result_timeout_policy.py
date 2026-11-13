"""Policy timeouts are distinct from real request uncertainty in the same text."""
import json

import pytest

from app.result_conditions import uncertainty_event,facts,issues,source_conflicts,validate_item
from app.core import Problem


@pytest.mark.parametrize('text',[
    '不设自动超时','不设置自动超时释放，只有本人释放。','没有自动超时；本人更新记录。',
    '自动超时释放政策不适用。','请求未超时，保存成功。','尚未发生网络超时。','没有发生超时。'])
def test_non_event_timeout_does_not_create_unknown_result(text):
    assert uncertainty_event(text) is None
    assert not any(x['role']=='unknown_result' for x in facts(text,location='source',fallback_operation='update'))


@pytest.mark.parametrize('text',[
    '不设自动超时；请求超时，结果未知。',
    '没有自动超时；请求超时，响应丢失结果未知。',
    '自动超时释放政策未启用，释放请求超时。',
    '未发生超时，但响应丢失。',
    '请求超时，结果未知；不设自动超时释放。',
    '不能排除请求超时。',
    '尚未确认是否超时。',
    '没有请求超时后的服务端确定结果，原记录不变。',
    json.dumps({'policy':'不设自动超时','state':'结果未知','other':'保留其它JSON字段'},ensure_ascii=False)])
def test_real_uncertainty_survives_policy_negation_and_structured_sources(text):
    assert uncertainty_event(text) is not None
    found=facts(text,location='source:S:X',origin='source_statement',fallback_operation='update',fallback_subject='记录')
    assert any(x['role']=='unknown_result' for x in found)
    assert all(x['quote'] in text for x in found)


@pytest.mark.parametrize('text',[
    '不设自动超时释放，释放请求超时，原记录不变。',
    '没有自动超时；请求超时，响应丢失结果未知，原记录不变。',
    '明确未发生超时，但响应丢失，原记录不变。'])
def test_mixed_timeout_still_blocks_asserted_unchanged_state(text):
    found=facts(text,location='REQ.statement',fallback_operation='update',fallback_subject='记录')
    assert issues(found)
    with pytest.raises(Problem) as error:validate_item(dict(id='REQ',title='更新记录',statement=text))
    assert error.value.code=='SEMANTIC_BLOCKED'


def test_missing_result_after_timeout_is_not_negated_timeout_event():
    text='没有请求超时后的服务端确定结果，原记录不变。'
    assert uncertainty_event(text) is not None
    found=facts(text,location='source:S:X',fallback_operation='update',fallback_subject='记录')
    assert any(row['role']=='unknown_result' for row in found) and issues(found)


def test_source_conflict_is_not_hidden_by_policy_or_json_input():
    text=json.dumps({'policy':'不设自动超时','rule':'请求超时，结果未知，原记录不变。'},ensure_ascii=False)
    found=facts(text,location='source:S:X',origin='source_statement',fallback_operation='update',fallback_subject='记录')
    assert issues(found)
    # A true sufficient-condition/exception source conflict still needs a decision.
    conflict=facts('不设自动超时；配额满足则创建记录；请求超时结果未知。',location='source:S:X',
                   origin='source_statement',fallback_operation='create',fallback_subject='记录')
    assert source_conflicts(conflict)
