import copy,json
import jsonschema
import pytest
from app.core import Problem,dumps
from app.prd import plan_schema,plan_contract,compile_plan,repair_instruction
from app.provider import assemble,DEFAULT
from tests.test_prd01 import state,plan,prepare_http,precheck_http
from tests.test_ui02_actions import case,plan as action_plan,start,finished

@pytest.mark.parametrize('kind',['mrd','prd'])
def test_actual_wire_schema_matches_document_contract(tmp_path,kind):
    _,p=state(tmp_path)
    messages,_,_=assemble(p,'prd','',DEFAULT,tmp_path,kind)
    header=json.loads(messages[0]['content'].split('可信任务头：')[1])
    keys=header['schema']['properties']['sections']['items']['properties']['section_key']['enum']
    expected=header['plan_contract']['section_keys']+(['discussion'] if header['plan_contract']['discussion_item_ids'] else [])
    assert keys==expected
    jsonschema.validate(header['plan_contract']['example'],header['schema'])
    assert ('validation' in keys)==(kind=='mrd')
    assert ('function' in keys)==(kind=='prd')

@pytest.mark.parametrize('kind,key',[('prd','validation'),('prd','priority'),('prd','risks'),('mrd','function'),('prd','invented'),('mrd','invented')])
def test_unknown_and_cross_type_keys_rejected_without_mutation(tmp_path,kind,key):
    _,p=state(tmp_path);before=copy.deepcopy(p)
    value=dict(plan_version='4',sections=[dict(section_key=key,context_refs=[],normative_refs=['REQ-0001'],discussion_refs=[],explanations=[dict(text='管理员可以编辑联系人姓名。',evidence_refs=dict(context_refs=[],normative_refs=['REQ-0001'],business_claim_refs=[]))])])
    with pytest.raises(jsonschema.ValidationError):jsonschema.validate(value,plan_schema(p,kind))
    with pytest.raises(Problem):compile_plan(value,p,kind)
    assert p==before

def test_repair_focus_preserves_business_and_uses_only_current_type(tmp_path):
    _,p=state(tmp_path)
    error=Problem('REFERENCE_INVALID','sections[7].section_key 不适用于 PRD 正文')
    prd=repair_instruction(error,p,'prd');mrd=repair_instruction(error,p,'mrd')
    assert '不丢弃非法章节中的有效正文和依据' in prd
    assert 'MRD按 required_analysis_sections' not in prd
    assert 'MRD按 required_analysis_sections' in mrd
    assert dumps(plan_contract(p,'prd')) in prd

def test_failed_document_update_retains_previous_document_and_business(case):
    c,app,model,root=prepare_http(case)
    precheck_http(c,root)
    body,preview=action_plan(c,root,'document','',allow_review_draft=True)
    assert start(c,root,body,preview).status_code==200
    assert finished(c,root)['status']=='succeeded'
    before=c.get(root).json();doc=copy.deepcopy(before['documents']['prd'])
    def invalid(value,body,ctx):
        value.update(plan_version='4')
        value['sections']=[dict(section_key='validation',context_refs=[],normative_refs=[],discussion_refs=value['sections'][0]['discussion_refs'],explanations=[dict(text='这段原文必须保留。',evidence_refs=dict(context_refs=[],normative_refs=[],business_claim_refs=[]))])]
        return value
    model['transform']=invalid
    body,preview=action_plan(c,root,'document','',allow_review_draft=True)
    assert start(c,root,body,preview).status_code==200
    task=finished(c,root)
    # Identical invalid plans stop after the first repair repeats the same error.
    assert task['status']=='failed' and task['calls']==2
    after=c.get(root).json()
    assert after['documents']['prd']==doc
    assert after['items']==before['items'] and after['questions']==before['questions']
    assert not after['active_baseline_id']
