import copy
import pytest
from app.core import Problem
from app.prd import compile_plan, normalize_structured_explanations
from app.semantic_quality import source_condition_issues
from tests.test_detail_document import project_fixture


def test_empty_optional_heading_keeps_exact_document_without_repair(tmp_path):
    p=project_fixture(tmp_path)
    plan=dict(plan_version='4',sections=[dict(section_key='function',context_refs=[],
        normative_refs=['REQ-0001'],discussion_refs=[],explanations=[])])
    baseline=compile_plan(plan,p,'prd',include_sketch=False)
    plan['sections'].insert(0,dict(section_key='rules',context_refs=[],normative_refs=[],discussion_refs=[],explanations=[]))
    before=copy.deepcopy((p,plan))
    normalized,changes=normalize_structured_explanations(plan,p,'prd')
    assert changes[0]['rule']=='empty-optional-section'
    assert compile_plan(normalized,p,'prd',include_sketch=False)==baseline
    assert compile_plan(plan,p,'prd',include_sketch=False)==baseline
    assert (p,plan)==before


def test_optional_normalization_does_not_hide_invalid_references(tmp_path):
    p=project_fixture(tmp_path)
    plan=dict(plan_version='4',sections=[dict(section_key='rules',context_refs=[],
        normative_refs=['INVALID'],discussion_refs=[],explanations=[])])
    normalized,changes=normalize_structured_explanations(plan,p,'prd')
    assert not changes
    with pytest.raises(Problem):compile_plan(normalized,p,'prd',include_sketch=False)


@pytest.mark.parametrize('text,existing,blocked',[
    ('现有能力能说明某条告警发生了什么，却不能说明此刻由谁在跟进。','现有云平台已有告警列表和告警详情。',True),
    ('现有平台不能导出负责人。','现有平台不能查询负责人。',True),
    ('现有平台不能导出负责人。','现有工单系统不能导出负责人。',True),
    ('现有平台不能导出负责人。','现有平台不能导出负责人。',False),
    ('如果现有平台不能导出负责人，可考虑增加导出。','',False),
    ('目前是否不能导出负责人仍待核对。','',False),
    ('本期不能自动重发请求。','',False),
    ('释放只面向该告警的当前认领者本人，其他人包括管理员都不能代为释放。','',False),
    ('当前系统管理员不能编辑他人的联系人。','',False),
    ('缺少资料不代表现有平台不能导出负责人。','',False),
])
def test_absence_predicate_needs_its_own_fact(text,existing,blocked):
    assert bool(source_condition_issues(text,'',existing)) is blocked


@pytest.mark.parametrize('text,blocked',[
    ('后到的认领请求不生效。',True),
    ('告警已被他人成功认领后，后到的认领请求不生效。',False),
    ('先到的请求不代表成功。',False),
    ('服务端第一个成功认领的请求生效。',False),
])
def test_arrival_order_cannot_substitute_success(text,blocked):
    assert bool(source_condition_issues(text,'同一告警以服务端第一个成功认领为准。','')) is blocked


def test_explicit_arrival_rule_is_preserved():
    assert not source_condition_issues('后到的请求不生效。','先到的请求生效，后到的请求不生效。','')
