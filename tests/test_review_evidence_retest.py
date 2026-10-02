"""Paired public review/generation checks from the 2026-10-02 paid retest."""
import copy,json

import jsonschema
import pytest

from app.contracts import validate_response
from app.core import Problem
from app.provider import normalize_review_result_tail,request_schema,review_structure_example
from app.semantic_quality import current_document_checks
from app.workflow import apply_response
from tests.test_semantic_quality_boundaries import compile_explanation,review_for_checks
from tests.test_semantic_quality_regressions import project


@pytest.mark.parametrize('field',['操作者','修改人','操作人'])
@pytest.mark.parametrize('variant',['valid','invented_role','wrong_document','wrong_quote'])
def test_audit_record_actor_field_is_not_an_asserted_runtime_role_error(tmp_path,field,variant):
    p=project(tmp_path,'备注成功变更应沿用已有审计。')
    for kind in ('prd','mrd'):
        apply_response(p,compile_explanation(p,'备注成功变更应沿用已有审计。',kind))
    for section in p['documents']['prd']['content']['sections']:
        for block in section['blocks']:
            if block['kind']=='explanation':
                block['text']=f'现有审计能力已覆盖备注变更：保存成功时留下{field}、联系人ID、时间和变更前后内容。'
    checks=current_document_checks(p)
    assert len(checks)==1 and checks[0]['dimension']=='existing_implementation'
    value=review_for_checks(p)
    check=checks[0]
    value['findings'][0]['message']=f'{check["check_id"]}：PRD {check["location"]} 原句“{check["quote"]}”属无对应能力证据的实现断言。'
    if variant=='invented_role':value['findings'][0]['message']+=' 该段操作者被误写为PM，有角色混淆风险。'
    elif variant=='wrong_document':value['findings'][0]['message']+=' MRD 同样错误。'
    elif variant=='wrong_quote':value['findings'][0]['message']=value['findings'][0]['message'].replace(check['quote'],'系统删除了审计记录。')
    before=copy.deepcopy((p,value))
    if variant=='valid':assert validate_response(value,'review',p,p['sources'][0]['excerpts'])==value
    else:
        with pytest.raises(Problem) as error:validate_response(value,'review',p,p['sources'][0]['excerpts'])
        assert error.value.code=='SEMANTIC_BLOCKED'
    assert (p,value)==before


@pytest.mark.parametrize('kind',['prd','mrd'])
@pytest.mark.parametrize('cite_current',[False,True])
def test_each_implementation_explanation_needs_its_own_current_evidence(tmp_path,kind,cite_current):
    from tests.test_document_quality_v4 import compile_fixture_plan
    p=project(tmp_path,'本期新增工单标题编辑，保持既有登录、查看和导出。')
    p['product_context']['current_state']='运营已能登录、查看和导出现有工单详情；目前标题仅可查看。'
    from app.product_flow import checkpoint,status
    for step in (1,2):checkpoint(p,step,status(p)[step-1]['content_hash'])
    body={'plan_version':'4','sections':[
        dict(section_key='background',context_refs=['current_state'],normative_refs=[],discussion_refs=[],explanations=[]),
        dict(section_key='users',context_refs=['users'],normative_refs=[],discussion_refs=[],explanations=[dict(
            text='运营日常已能登录、查看和导出现有工单详情。',
            evidence_refs=dict(context_refs=['users','current_state'] if cite_current else ['users'],normative_refs=[],business_claim_refs=[]))])
    ]}
    before=copy.deepcopy((p,body))
    if cite_current:assert compile_fixture_plan(body,p,kind)
    else:
        with pytest.raises(Problem) as error:compile_fixture_plan(body,p,kind)
        assert error.value.code=='SEMANTIC_BLOCKED'
    assert (p,body)==before


def test_review_wire_example_demonstrates_complete_perspective_objects():
    value=review_structure_example()
    jsonschema.Draft202012Validator(request_schema('review')).validate(value)
    assert value['result']['perspectives']
    assert set(value['result']['perspectives'][0])=={'role','considerations'}
    assert value['result']['perspectives'][0]['considerations']


def test_nested_review_stray_quote_still_cannot_be_losslessly_normalized():
    value=review_structure_example()
    value['result']['perspectives']=[dict(role='测试',considerations=['不改业务'])]
    malformed=json.dumps(value,ensure_ascii=False).replace('["不改业务"]}', '["不改业务"],"}')
    with pytest.raises(json.JSONDecodeError):json.loads(malformed)
    assert normalize_review_result_tail(malformed) is None
