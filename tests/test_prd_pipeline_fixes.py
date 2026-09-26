"""PRD 生成链路三次真实失败（截断/Schema根报错/幻觉引用）的回归测试。"""
import copy
import pytest
from app.core import Problem
from app.store import Store
from app.contracts import VALIDATOR, validate_response, validate_document
from app.workflow import next_output_budget
from tests.helpers import EXAMPLES, prepared


@pytest.fixture
def state(tmp_path):
    store = Store(tmp_path)
    return store, prepared(store)


def test_dotted_section_ids_are_schema_valid(state):
    """PRD-4.1.1 这类点分层章节 ID 必须合法（参考 profile 自身 ID 即含点）。"""
    _, p = state
    r = copy.deepcopy(EXAMPLES['prd'])
    parent = r['result']['sections'][0]['section_id']
    child = dict(r['result']['sections'][0], section_id=parent + '.1', parent_section_id=parent, level=2)
    r['result']['sections'].append(child)
    assert not list(VALIDATOR.iter_errors(r))
    validate_document(r['result'], p, 'prd')


def test_schema_error_reports_deepest_path(state):
    """oneOf 根报错必须下钻到真实错误路径，否则修复提示词不可用。"""
    _, p = state
    r = copy.deepcopy(EXAMPLES['prd'])
    r['result']['sections'][0]['section_id'] = 'PRD-4. 1'
    with pytest.raises(Problem) as exc:
        validate_response(r, 'prd', p, p['sources'][0]['excerpts'])
    assert exc.value.code == 'SCHEMA_INVALID'
    assert 'section_id' in exc.value.message


def test_reference_error_lists_offending_ids(state):
    """引用校验失败必须列出非法 ID，供修复轮次精确定位。"""
    _, p = state
    r = copy.deepcopy(EXAMPLES['prd'])
    r['result']['sections'][0]['blocks'][0]['ref_ids'] = ['REQ-0001', 'REQ-9999', 'Q-9999']
    with pytest.raises(Problem) as exc:
        validate_response(r, 'prd', p, p['sources'][0]['excerpts'])
    assert exc.value.code == 'REFERENCE_INVALID'
    assert 'REQ-9999' in exc.value.message and 'Q-9999' in exc.value.message


def test_truncation_repair_escalates_output_budget():
    """截断修复必须提高输出预算：6000→16384→32768→32768（封顶）。"""
    assert next_output_budget(6000) == 16384
    assert next_output_budget(16384) == 32768
    assert next_output_budget(32768) == 32768
