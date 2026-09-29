"""V4 reader depth and provenance using isolated synthetic project snapshots."""
import copy

import pytest

from app.core import Problem, dumps
from app.document_reader import reader_document
from app.prd import MRD_NORMATIVE_APPENDIX_TITLE, compile_plan, plan_contract, verified_context
from app.product_flow import BEHAVIOR
from tests.test_prd_v2_semantics import checked_project


def plan(version, *, context=(), normative=('REQ-0001',), explanation=None):
    section = dict(section_key='goal' if version=='4' or explanation else 'function',
                   context_refs=list(context), normative_refs=list(normative),
                   discussion_refs=[])
    if version in ('3', '4'):
        section['explanations'] = [explanation] if explanation else []
    return dict(plan_version=version, sections=[section])


def artifact(result, project, version):
    return dict(content=result['result'], item_snapshot=copy.deepcopy(project['items']),
                question_snapshot=copy.deepcopy(project['questions']),
                delivery_item_ids=['REQ-0001', 'RULE-0001', 'AC-0001'],
                product_context_snapshot=copy.deepcopy(project['product_context']),
                assembly_version='prd-plan-'+version)


def test_v4_reuses_fully_cited_context_without_duplicate_reader_line(tmp_path):
    project = checked_project(tmp_path)
    key, value = next(iter(verified_context(project).items()))
    explanation = dict(text='本期场景依据：'+value,
                       evidence_refs=dict(context_refs=[key], normative_refs=[],
                                          business_claim_refs=[]))
    result = compile_plan(plan('4', context=[key], explanation=explanation), project, 'mrd')
    readable = reader_document(artifact(result, project, '4'))
    assert sum(block.get('text', '').count(value)
               for section in readable['sections'] for block in section['blocks']) == 1
    assert result['result']['sections']
    assert project['product_context'][key] == value
    assert value in dumps(artifact(result, project, '4')['product_context_snapshot'])


def test_v4_mrd_omits_behavior_fields_prd_and_v3_retain_them(tmp_path):
    project = checked_project(tmp_path)
    requirement = next(item for item in project['items'] if item['id'] == 'REQ-0001')
    requirement['behavior'] = {key:'合成行为-'+key for key in BEHAVIOR}
    for version, kind, expected in (('4', 'mrd', False), ('4', 'prd', True),
                                    ('3', 'mrd', True), ('2', 'mrd', True)):
        result = compile_plan(plan(version), project, kind)
        raw = artifact(result, project, version)
        rendered = dumps(reader_document(raw))
        assert requirement['statement'] in rendered
        assert ('合成行为-result' in rendered) is expected
        assert ('合成行为-exceptions' in rendered) is expected
        assert '合成行为-result' in dumps(raw['item_snapshot'])


def test_v4_mrd_keeps_all_normative_originals_once_in_final_product_annex(tmp_path):
    project=checked_project(tmp_path)
    contract=plan_contract(project,'mrd')
    assert contract['section_keys']==['background','users','goal','scope']
    proposal=plan('4', normative=('REQ-0001','RULE-0001','AC-0001'))
    result=compile_plan(proposal,project,'mrd')['result']
    annex=[section for section in result['sections'] if section['title']==MRD_NORMATIVE_APPENDIX_TITLE]
    assert len(annex)==1
    assert all(block['kind'] not in ('requirement','rule','acceptance')
               for section in result['sections'] if section['title']!=MRD_NORMATIVE_APPENDIX_TITLE
               for block in section['blocks'])
    expected=set(contract['normative_item_ids'])
    actual=[ref for block in annex[0]['blocks'] if block['kind'] in ('requirement','rule','acceptance')
            for ref in block['ref_ids']]
    assert set(actual)==expected and len(actual)==len(expected)
    assert {row['item_id'] for row in result['coverage']}==expected
    assert all(row['section_ids']==[annex[0]['section_id']] for row in result['coverage'])
    assert all(next(item for item in project['items'] if item['id']==ref)['statement']
               in dumps(reader_document(artifact(dict(result=result),project,'4'))) for ref in expected)


def test_v4_context_in_later_explanation_does_not_leave_empty_chapter(tmp_path):
    project = checked_project(tmp_path)
    key, value = next(iter(verified_context(project).items()))
    explanation = dict(text=value, evidence_refs=dict(context_refs=[key], normative_refs=[], business_claim_refs=[]))
    proposed = plan('4', context=[key])
    proposed['sections'][0]['normative_refs'] = []
    later = plan('4', explanation=explanation)['sections'][0]
    later['section_key'] = 'scope'
    proposed['sections'].append(later)
    result = compile_plan(proposed, project, 'mrd')
    assert all(section['blocks'] for section in result['result']['sections'])
    readable = reader_document(artifact(result, project, '4'))
    assert sum(block.get('text', '').count(value) for section in readable['sections'] for block in section['blocks']) == 1


def test_v4_keeps_normative_original_and_unresolved_question(tmp_path):
    project = checked_project(tmp_path)
    result = compile_plan(plan('4'), project, 'prd')
    readable = dumps(reader_document(artifact(result, project, '4')))
    assert project['items'][0]['statement'] in readable
    assert project['questions'][0]['question'] in readable
    assert '本期未决' in readable


def test_v4_places_unplanned_context_in_product_sections_without_process_heading(tmp_path):
    project=checked_project(tmp_path)
    expected={
        'product':'背景与现状','module':'背景与现状','current_state':'背景与现状',
        'intent':'目标与价值','value':'目标与价值','priority':'目标与价值',
        'users':'使用者与权限','change_scope':'本期范围与边界',
        'preserve_scope':'本期范围与边界','out_of_scope':'本期范围与边界',
    }
    for kind in ('mrd','prd'):
        result=compile_plan(plan('4'),project,kind)
        readable=reader_document(artifact(result,project,'4'))
        assert all(section['title']!='补列的已核对事实与规范条款'
                   for section in readable['sections'])
        for key,title in expected.items():
            actual=[section['title'] for section in readable['sections']
                    for block in section['blocks'] if project['product_context'][key] in block.get('text','')]
            assert title in actual, (kind,key,actual)
        assert any('章节建议未指定的已核对上下文字段' in limit
                   for limit in result['limitations'])
    for version in ('2','3'):
        old=compile_plan(plan(version),project,'prd')
        assert any(section['title']=='补列的已核对事实与规范条款'
                   for section in old['result']['sections'])


def test_v4_prd_fixes_clause_kinds_to_one_section_each(tmp_path):
    project=checked_project(tmp_path)
    key=next(iter(verified_context(project)))
    proposed=dict(plan_version='4',sections=[
        dict(section_key='background',context_refs=[key],
             normative_refs=['AC-0001','REQ-0001','RULE-0001'],discussion_refs=[],explanations=[]),
        dict(section_key='function',context_refs=[],
             normative_refs=['REQ-0001'],discussion_refs=[],explanations=[])])
    result=compile_plan(proposed,project,'prd')['result']
    by_title={section['title']:section for section in result['sections']}
    assert project['product_context'][key] in dumps(by_title['背景与现状']['blocks'])
    placements={ref:[] for ref in ('REQ-0001','RULE-0001','AC-0001')}
    for section in result['sections']:
        for block in section['blocks']:
            if block['kind'] in ('requirement','rule','acceptance'):
                placements[block['ref_ids'][0]].append(section['title'])
    assert placements=={'REQ-0001':['功能需求'],'RULE-0001':['业务规则'],
                        'AC-0001':['验收条件']}
    assert {row['item_id'] for row in result['coverage']}==set(placements)


def test_v4_rejects_writing_instructions_but_not_product_use_of_prd(tmp_path):
    project=checked_project(tmp_path)
    ref=dict(context_refs=['users'],normative_refs=[],business_claim_refs=[])
    for prose in ('PRD需要把流程说清。','文档正文不应重复结果。',
                  '规则层需要把约束放在一起。','值班人员需要在文档中核对结果。'):
        with pytest.raises(Problem) as error:
            compile_plan(plan('4',explanation=dict(text=prose,evidence_refs=ref)),project,'prd')
        assert error.value.code=='SCHEMA_INVALID'
        assert 'sections[0].explanations[0].text' in error.value.message
    # A product statement mentioning a PRD is not an instruction to the author.
    result=compile_plan(plan('4',explanation=dict(text='用户需要导出PRD。',evidence_refs=ref)),project,'prd')
    assert result['result']['sections']
    old=compile_plan(plan('3',explanation=dict(text='PRD需要把流程说清。',evidence_refs=ref)),project,'prd')
    assert old['result']['sections']


def test_v4_rejects_unverified_explanation_basis(tmp_path):
    project = checked_project(tmp_path)
    explanation = dict(text='未经证实的结果字段',
                       evidence_refs=dict(context_refs=['missing'], normative_refs=[],
                                          business_claim_refs=[]))
    with pytest.raises(Problem) as error:
        compile_plan(plan('4', explanation=explanation), project, 'prd')
    assert error.value.code == 'REFERENCE_INVALID'
