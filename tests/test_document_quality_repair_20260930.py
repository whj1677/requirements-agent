"""Document placement and identity checks using an isolated synthetic project."""
import copy

import jsonschema
import pytest

from app.core import Problem, dumps
from app.document_reader import reader_document
from app.prd import PLAN_SCHEMA_V3, PLAN_SCHEMA_V4, compile_plan, plan_contract
from tests.test_prd_v2_semantics import checked_project


def explanation(text, *, context=(), normative=(), claims=()):
    return dict(text=text, evidence_refs=dict(
        context_refs=list(context), normative_refs=list(normative),
        business_claim_refs=list(claims)))


def section(key, *, context=(), normative=(), explanations=()):
    return dict(section_key=key, context_refs=list(context),
                normative_refs=list(normative), discussion_refs=[],
                explanations=list(explanations))


def artifact(result, project):
    return dict(content=result['result'], item_snapshot=copy.deepcopy(project['items']),
                question_snapshot=copy.deepcopy(project['questions']),
                delivery_item_ids=[i['id'] for i in project['items']],
                assembly_version='prd-plan-4')


def test_prd_explanation_stays_with_its_requirement_and_preserves_original(tmp_path):
    project = checked_project(tmp_path)
    requirement = next(i for i in project['items'] if i['id']=='REQ-0001')
    requirement['behavior'] = dict(exceptions='未提及异常分支', result='真实结果仍需核对')
    before = copy.deepcopy(project)
    proposed = dict(plan_version='4', sections=[
        section('function', normative=['REQ-0001'], explanations=[
            explanation('这条需求的边界需要按验收场景核对。', normative=['REQ-0001'])]),
        section('rules', normative=['RULE-0001']),
        section('acceptance', normative=['AC-0001']),
    ])
    result = compile_plan(proposed, project, 'prd')
    assert project == before
    readable = reader_document(artifact(result, project))
    function = next(s for s in readable['sections'] if s['title']=='功能需求')
    rule = next(s for s in readable['sections'] if s['title']=='业务规则')
    requirement_at = next(n for n,b in enumerate(function['blocks'])
                          if b['kind']=='requirement' and b['ref_ids']==['REQ-0001'])
    assert function['blocks'][requirement_at+1]['text']=='这条需求的边界需要按验收场景核对。'
    assert '这条需求的边界需要按验收场景核对。' not in dumps(rule)
    assert requirement['statement'] in dumps(readable)
    assert '未提及异常分支' not in dumps(readable)
    assert '真实结果仍需核对' in dumps(readable)
    assert {row['item_id'] for row in result['result']['coverage']}=={
        'REQ-0001','RULE-0001','AC-0001'}


def test_multi_requirement_flow_has_own_section_and_keeps_both_references(tmp_path):
    project = checked_project(tmp_path)
    second=copy.deepcopy(next(i for i in project['items'] if i['id']=='REQ-0001'))
    second.update(id='REQ-0002',title='第二个需求',statement='第二个已采纳需求原文。')
    project['items'].append(second)
    project['product_context']['scope_ids'].append('REQ-0002')
    proposed=dict(plan_version='4', sections=[section('rules', explanations=[
        explanation('先执行第一步，再处理第二步。', normative=['REQ-0001','REQ-0002'])])])
    result=compile_plan(proposed,project,'prd')['result']
    association=next(s for s in result['sections'] if s['title']=='功能需求关联说明')
    assert association['level']==1 and association['parent_section_id'] is None
    assert association['blocks'][0]['text']=='关联条款：REQ-0001、REQ-0002'
    assert association['blocks'][1]['ref_ids']==['REQ-0001','REQ-0002']
    readable=reader_document(artifact(dict(result=result),project))
    assert sum(s['title']=='功能需求关联说明' for s in readable['sections'])==1
    assert '先执行第一步，再处理第二步。' in dumps(readable)
    assert '先执行第一步，再处理第二步。' not in dumps(next(
        s for s in readable['sections'] if s['title']=='功能需求'))
    assert {r['item_id'] for r in result['coverage']} >= {'REQ-0001','REQ-0002'}
    assert second['statement'] in dumps(readable)


def test_cross_kind_explanation_has_neutral_section_and_unknown_refs_rejected(tmp_path):
    project = checked_project(tmp_path)
    proposed=dict(plan_version='4', sections=[section('function', explanations=[
        explanation('提交与权限约束一起核对。', normative=['REQ-0001','RULE-0001'])])])
    result=compile_plan(proposed,project,'prd')['result']
    cross=next(s for s in result['sections'] if s['title']=='跨条目业务流程与关联说明')
    assert cross['parent_section_id'] is None
    assert cross['blocks'][1]['ref_ids']==['REQ-0001','RULE-0001']
    for ref in ('REQ-UNKNOWN','REQ-CANDIDATE'):
        invalid=dict(plan_version='4', sections=[section('function', explanations=[
            explanation('未经采纳的关联。', normative=['REQ-0001',ref])])])
        with pytest.raises(Problem) as error:
            compile_plan(invalid, project, 'prd')
        assert error.value.code=='REFERENCE_INVALID'


def test_prd_context_explanation_moves_to_its_evidence_chapter(tmp_path):
    project = checked_project(tmp_path)
    proposed = dict(plan_version='4', sections=[section('rules', explanations=[
        explanation('已有使用者需要核对。', context=['users'])])])
    readable = reader_document(artifact(compile_plan(proposed, project, 'prd'), project))
    user_section = next(s for s in readable['sections'] if s['title']=='使用者与权限')
    assert '已有使用者需要核对。' in dumps(user_section)
    assert all('已有使用者需要核对。' not in dumps(s)
               for s in readable['sections'] if s['title']=='业务规则')


def test_mrd_prompt_keeps_decision_basis_and_no_invented_numbers(tmp_path):
    project = checked_project(tmp_path)
    contract=plan_contract(project,'mrd')
    assert {'priority','validation'}<=set(contract['section_keys'])
    assert contract['required_analysis_sections']==['priority','validation']
    assert contract['validation_acceptance_ids']==['AC-0001']
    assert not {'priority','validation','risks'} & set(plan_contract(project,'prd')['section_keys'])


def test_mrd_priority_and_trial_analysis_have_separate_evidence_bound_chapters(tmp_path):
    project=checked_project(tmp_path)
    priority=project['product_context']['priority']
    proposed=dict(plan_version='4',sections=[
        section('goal',context=['priority','value']),
        section('priority',explanations=[explanation(
            '先安排内部试点，以确认现场整理工作的实际影响。',context=['priority'])]),
        section('validation',explanations=[explanation(
            '试点核对失败提示与原数据是否保持。',normative=['AC-0001'])]),
    ])
    before=copy.deepcopy(project)
    result=compile_plan(proposed,project,'mrd')
    assert project==before
    readable=reader_document(artifact(result,project))
    chapters={s['title']:s for s in readable['sections']}
    assert priority in dumps(chapters['优先级与决策依据'])
    assert priority not in dumps(chapters['目标与价值'])
    assert '试点核对失败提示' in dumps(chapters['试点验证与成功判据'])
    assert {row['item_id'] for row in result['result']['coverage']}=={
        'REQ-0001','RULE-0001','AC-0001'}
    assert project['items'][2]['statement'] in dumps(readable)


def test_mrd_required_analysis_rejects_missing_or_wrong_evidence(tmp_path):
    project=checked_project(tmp_path)
    valid_priority=section('priority',explanations=[explanation('先核对试点价值。',context=['priority'])])
    valid_validation=section('validation',explanations=[explanation('核对验收结果。',normative=['AC-0001'])])
    for sections in ([valid_priority], [valid_validation],
                     [section('priority',explanations=[explanation('先核对试点价值。',context=['value'])]),valid_validation],
                     [valid_priority,section('validation',explanations=[explanation('核对验收结果。',context=['users'])])]):
        with pytest.raises(Problem) as error:
            compile_plan(dict(plan_version='4',sections=sections),project,'mrd')
        assert error.value.code=='SEMANTIC_BLOCKED'
    with pytest.raises(Problem) as error:
        compile_plan(dict(plan_version='4',sections=[valid_priority,section('validation',
            explanations=[explanation('引用不存在。',normative=['AC-NOT-FOUND'])])]),project,'mrd')
    assert error.value.code=='REFERENCE_INVALID'


def test_mrd_without_sourced_acceptance_does_not_invent_trial_analysis(tmp_path):
    project=checked_project(tmp_path)
    acceptance=next(i for i in project['items'] if i['id']=='AC-0001')
    acceptance['source_refs']=[]
    assert plan_contract(project,'mrd')['required_analysis_sections']==['priority']
    result=compile_plan(dict(plan_version='4',sections=[section('priority',
        explanations=[explanation('先核对试点。',context=['priority'])])]),project,'mrd')
    assert not any(s['title']=='试点验证与成功判据' for s in result['result']['sections'])
    assert {row['item_id'] for row in result['result']['coverage']}=={
        'REQ-0001','RULE-0001','AC-0001'}


def test_mrd_fully_quoted_priority_is_not_repeated_in_same_chapter(tmp_path):
    project=checked_project(tmp_path)
    priority=project['product_context']['priority']
    result=compile_plan(dict(plan_version='4',sections=[
        section('priority',explanations=[explanation(priority,context=['priority'])]),
        section('validation',explanations=[explanation('核对试点验收。',normative=['AC-0001'])]),
    ]),project,'mrd')
    readable=reader_document(artifact(result,project))
    assert sum(block.get('text','').count(priority) for chapter in readable['sections']
               for block in chapter['blocks'])==1


def test_mrd_only_keys_do_not_enter_prd_or_older_plan_schemas(tmp_path):
    project=checked_project(tmp_path)
    candidate=dict(plan_version='4',sections=[section('priority',
        explanations=[explanation('先核对试点。',context=['priority'])])])
    jsonschema.Draft202012Validator(PLAN_SCHEMA_V4).validate(candidate)
    with pytest.raises(Problem) as error:
        compile_plan(candidate,project,'prd')
    assert error.value.code=='REFERENCE_INVALID'
    old=dict(candidate,plan_version='3')
    assert list(jsonschema.Draft202012Validator(PLAN_SCHEMA_V3).iter_errors(old))
    legacy=dict(plan_version='3',sections=[section('goal',context=['priority'])])
    assert compile_plan(legacy,project,'mrd')['result']['sections']


def test_acceptance_precondition_remains_verbatim_after_reader_projection(tmp_path):
    project = checked_project(tmp_path)
    acceptance = next(i for i in project['items'] if i['id']=='AC-0001')
    acceptance['statement'] = '前提：已有批量导出审计写入能力；核对审计记录。'
    result = compile_plan(dict(plan_version='4', sections=[
        section('acceptance', normative=['AC-0001'])]), project, 'prd')
    readable = reader_document(artifact(result, project))
    assert acceptance['statement'] in dumps(readable)
