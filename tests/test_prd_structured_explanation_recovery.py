import copy
import pytest
from app.prd import compile_plan,normalize_structured_explanations
from app.core import Problem
from tests.test_detail_document import project_fixture


def packet(p):
    return dict(plan_version='4',sections=[dict(section_key='function',context_refs=[],normative_refs=[],discussion_refs=[],
        explanations=[dict(text='管理员可以编辑联系人姓名。',evidence_refs=dict(context_refs=[],normative_refs=['REQ-0001'],business_claim_refs=[]))])])


def test_redundant_explanation_keeps_all_bindings_and_program_rendered_facts(tmp_path):
    p=project_fixture(tmp_path);plan=packet(p);before=copy.deepcopy((p,plan))
    with pytest.raises(Problem):compile_plan(plan,p,'prd',include_sketch=False)
    fixed,proof=normalize_structured_explanations(plan,p,'prd')
    assert fixed['sections'][0]['explanations']==[]
    assert fixed['sections'][0]['normative_refs']==['REQ-0001'] and fixed['sections'][0]['context_refs']==[]
    assert proof[0]['raw']==plan['sections'][0]['explanations'][0]
    value=compile_plan(fixed,p,'prd',include_sketch=False)
    from app.workflow import apply_response
    from app.document_reader import reader_document
    rendered=copy.deepcopy(p);apply_response(rendered,value)
    text=str(reader_document(rendered['documents']['prd']))
    assert '联系人姓名' in text and '1 至 30 个字符' in text
    assert (p,plan)==before


@pytest.mark.parametrize('fault',['candidate','context','claims','mixed','extra'])
def test_recovery_cannot_hide_other_invalid_or_independent_bindings(tmp_path,fault):
    p=project_fixture(tmp_path);plan=packet(p);ex=plan['sections'][0]['explanations'][0]
    if fault=='candidate':ex['evidence_refs']['normative_refs']=['REQ-CANDIDATE']
    if fault=='context':ex['evidence_refs']['context_refs']=['not-a-context']
    if fault=='claims':ex['evidence_refs']['business_claim_refs']=['other-claim']
    if fault=='mixed':ex['evidence_refs']['normative_refs'].append('another-reference')
    if fault=='extra':ex['approved']=True
    fixed,proof=normalize_structured_explanations(plan,p,'prd');assert fixed==plan and not proof


def test_mrd_and_older_plan_protocols_keep_their_own_validation(tmp_path):
    p=project_fixture(tmp_path);plan=packet(p)
    assert normalize_structured_explanations(plan,p,'mrd')==(plan,[])
    plan['plan_version']='3';assert normalize_structured_explanations(plan,p,'prd')==(plan,[])
