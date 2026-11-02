"""Structural entry contract only, not acceptance of real business semantics."""
import copy
from app.detail_draft import PROMPT
from app.requirement_details import detail_issues
from tests.test_detail_business_draft import sample_detail


def graph_with_independent_entry(kind):
    detail=sample_detail()
    detail['flow']['nodes'].extend([
        dict(id='N_external',label='合成资料定义的外部入口',kind=kind,actor='合成外部操作者'),
        dict(id='N_external_result',label='本次处理结束',kind='end')])
    detail['flow']['edges'].append(dict(id='E_external',**{'from':'N_external','to':'N_external_result'},condition='本次结果已确定'))
    return detail


def test_multiple_independent_starts_remain_structurally_supported():
    detail=graph_with_independent_entry('start');before=copy.deepcopy(detail)
    assert len([n for n in detail['flow']['nodes'] if n['kind']=='start'])==2
    assert not [i for i in detail_issues(dict(detail_contract=detail)) if i['code'].startswith('DETAIL_FLOW_')]
    assert detail==before


def test_unreachable_action_is_not_automatically_promoted_to_start():
    detail=graph_with_independent_entry('action');before=copy.deepcopy(detail)
    issues=detail_issues(dict(detail_contract=detail))
    assert any(i['code']=='DETAIL_FLOW_UNREACHABLE' and i['object_id']=='N_external' for i in issues)
    assert detail==before and detail['flow']['nodes'][-2]['kind']=='action'


def test_generation_prompt_explains_multiple_entries_without_inventing_edges():
    assert '允许多个独立 start' in PROMPT
    assert '不能凭空补一条从查看页面或正常操作通往直提的业务边' in PROMPT
    assert '不能仅按节点名称或为了消除不可达错误改成 start' in PROMPT
