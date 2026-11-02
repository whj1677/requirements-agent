import copy
import pytest
import jsonschema
from app.prd import compile_plan,normalize_structured_explanations,plan_schema,plan_contract,SECTION_TITLES
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


def test_users_keeps_role_operation_conditions_and_exact_evidence_in_own_chapter(tmp_path):
    p=project_fixture(tmp_path);plan=packet(p)
    plan['sections'][0]['section_key']='users'
    explanation=plan['sections'][0]['explanations'][0]
    explanation['text']='管理员具有当前电站编辑权限时，可以编辑联系人姓名。'
    before=copy.deepcopy((p,plan))
    contract=plan_contract(p,'prd')
    assert 'REQ-0001' not in contract['explanation_normative_item_ids']
    assert 'REQ-0001' in contract['section_explanation_normative_item_ids']['users']
    jsonschema.Draft202012Validator(plan_schema(p,'prd')).validate(plan)
    fixed,proof=normalize_structured_explanations(plan,p,'prd')
    assert fixed==plan and not proof
    value=compile_plan(fixed,p,'prd',include_sketch=False)
    from app.workflow import apply_response
    from app.document_reader import reader_document
    rendered=copy.deepcopy(p);apply_response(rendered,value)
    sections=rendered['documents']['prd']['content']['sections']
    placed=[(s['title'],b) for s in sections for b in s['blocks'] if b.get('text')==explanation['text']]
    assert len(placed)==1 and placed[0][0]==SECTION_TITLES['users']
    assert placed[0][1]['evidence_refs']==explanation['evidence_refs']
    reader=reader_document(rendered['documents']['prd'])
    assert explanation['text'] in str(next(s for s in reader['sections'] if s['title']==SECTION_TITLES['users']))
    assert rendered['items']==p['items'] and (p,plan)==before
    baseline=compile_plan(dict(plan_version='4',sections=[dict(section_key='function',
        context_refs=[],normative_refs=['REQ-0001'],discussion_refs=[],explanations=[])]),p,'prd',include_sketch=False)
    baseline_p=copy.deepcopy(p);apply_response(baseline_p,baseline)
    def authoritative(document):
        return [b for s in document['content']['sections'] for b in s['blocks']
                if b['kind'] in ('requirement','rule','acceptance','detail')]
    assert authoritative(rendered['documents']['prd'])==authoritative(baseline_p['documents']['prd'])


@pytest.mark.parametrize('fault',['candidate','outside','context','claims','mixed'])
def test_users_exception_cannot_hide_invalid_or_unselected_references(tmp_path,fault):
    p=project_fixture(tmp_path);plan=packet(p);plan['sections'][0]['section_key']='users'
    basis=plan['sections'][0]['explanations'][0]['evidence_refs']
    if fault=='candidate':basis['normative_refs']=['REQ-CANDIDATE']
    elif fault=='outside':basis['normative_refs']=['REQ-EXTERNAL']
    elif fault=='context':basis['context_refs']=['not-current-context']
    elif fault=='claims':basis['business_claim_refs']=['foreign-claim']
    else:basis['normative_refs'].append('REQ-CANDIDATE')
    fixed,proof=normalize_structured_explanations(plan,p,'prd')
    assert fixed==plan and not proof
    assert list(jsonschema.Draft202012Validator(plan_schema(p,'prd')).iter_errors(fixed))
    with pytest.raises(Problem) as caught:compile_plan(fixed,p,'prd',include_sketch=False)
    assert caught.value.code=='REFERENCE_INVALID'


@pytest.mark.parametrize('key',['function','goal','acceptance','interaction','business_flow'])
def test_users_exception_does_not_widen_other_chapters(tmp_path,key):
    p=project_fixture(tmp_path);plan=packet(p);plan['sections'][0]['section_key']=key
    assert list(jsonschema.Draft202012Validator(plan_schema(p,'prd')).iter_errors(plan))
    with pytest.raises(Problem):compile_plan(plan,p,'prd',include_sketch=False)
    fixed,proof=normalize_structured_explanations(plan,p,'prd')
    assert not fixed['sections'][0]['explanations'] and len(proof)==1
    assert fixed['sections'][0]['normative_refs']==['REQ-0001']
