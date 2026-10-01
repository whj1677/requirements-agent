"""Public generation/review regressions; synthetic PM decisions, no live calls."""
import copy,json
import pytest
from app.core import Problem,dumps
from app.prd import compile_plan
from app.provider import assemble,DEFAULT
from app.contracts import validate_response
from app.product_flow import checkpoint,status
from tests.helpers import EXAMPLES
from tests.test_prd_v2_semantics import checked_project

AMBIGUOUS='备注最多100个Unicode码点（含空格与换行，emoji按一个码点计）。'
CODEPOINTS='备注最多100个Unicode码点，空格、换行和复合emoji的各组成码点分别计数。99个A加👍🏽为101码点，拒绝保存且原文和原记录不变。'
GRAPHEMES='备注最多100个扩展字素簇，空格和换行计入，肤色与ZWJ复合emoji按字素簇计数。99个A加👍🏽为100字素簇，允许保存；101字素簇拒绝且原文和原记录不变。'

def project(tmp_path,statement):
 p=checked_project(tmp_path);p['items'][0]['statement']=statement
 p['questions'][0].update(status='answered',answer='重复姓名可保存。',understanding_status='applied',blocking=False)
 return p

def plan(text=None,context=None):
 section=dict(section_key='function',context_refs=context or [],normative_refs=['REQ-0001'],discussion_refs=[])
 if text is None:return dict(plan_version='2',sections=[section])
 section['explanations']=[dict(text=text,evidence_refs=dict(context_refs=context or [],normative_refs=['REQ-0001'],business_claim_refs=[]))]
 return dict(plan_version='4',sections=[section])

@pytest.mark.parametrize('text',[AMBIGUOUS,'地址最多80码点，每个表情符号按1个码点计。','maximum 64 Unicode code points; every emoji counts as one code point.'])
def test_ambiguous_current_rule_cannot_become_certain_document(tmp_path,text):
 p=project(tmp_path,text);before=copy.deepcopy(p)
 with pytest.raises(Problem) as e:compile_plan(plan(),p,'prd')
 assert e.value.code=='SEMANTIC_BLOCKED' and p==before

@pytest.mark.parametrize('choice',[CODEPOINTS,GRAPHEMES])
def test_explicit_pm_choices_produce_consistent_documents_without_reasking_history(tmp_path,choice):
 p=project(tmp_path,choice);p['sources'][0]['excerpts'][0]['text']=AMBIGUOUS
 p['items'][2]['statement']='验收口径由PM明确：'+choice
 p['questions'][0].update(question='按码点还是扩展字素簇计数？',answer=choice,related_refs=['REQ-0001'],blocking=True)
 p['items'][0]['behavior']={'actor':'园区运营人员','data':choice,'result':choice}
 p['product_context']['change_scope']=choice
 for step in [1,2]:checkpoint(p,step,status(p)[step-1]['content_hash'])
 before=copy.deepcopy(p)
 from app.workflow import apply_response
 from app.document_reader import reader_document
 from app.exports import document_files
 import asyncio,io
 from docx import Document
 for kind in ['prd','mrd']:
  r=compile_plan(plan(),p,kind)
  assert 'REQ-0001' in dumps(r) and r['result']['document_type']==kind
  candidate=copy.deepcopy(p);apply_response(candidate,r)
  view=dumps(reader_document(candidate['documents'][kind]))
  files=asyncio.run(document_files(candidate,kind))
  md=files[kind.upper()+'.md'].decode('utf-8')
  word='\n'.join(x.text for x in Document(io.BytesIO(files[kind.upper()+'.docx'])).paragraphs)
  for output in [view,md,word]:
   assert choice in output and AMBIGUOUS not in output
  assert candidate['sources'][0]['excerpts'][0]['text']==AMBIGUOUS
 assert p==before and p['sources'][0]['excerpts'][0]['text']==AMBIGUOUS

def test_sourced_answer_repeating_contradiction_does_not_resolve_it(tmp_path):
 p=project(tmp_path,CODEPOINTS);p['questions'][0].update(question='如何计数？',answer=AMBIGUOUS,answer_source_refs=[{'source_id':'SRC-0001','excerpt_id':'EX-0001'}])
 with pytest.raises(Problem):compile_plan(plan(),p,'prd')

@pytest.mark.parametrize('evidence,explanation',[
 ('服务明确失败时原记录不变。结果未知时按同一请求ID查询，不自动重发。','失败场景原记录不变。'),
 ('服务明确失败时原数据不变。结果未知需查询原结果。','失败后原数据不变。'),
 ('所有站点成功才整批成功，失败则全部不应用。','部分站点成功即可整批成功。'),
 ('其它会话主动刷新后读取最新备注，不新增推送。','需在培训或说明中提示其它会话刷新。'),
 ('备注成功变更应沿用已有审计。','现有审计能力已覆盖这一场景。'),
])
def test_cited_explanation_cannot_change_conditions_quantifiers_or_fact_identity(tmp_path,evidence,explanation):
 p=project(tmp_path,evidence);before=copy.deepcopy(p)
 with pytest.raises(Problem) as error:compile_plan(plan(explanation),p,'prd')
 assert error.value.code=='SEMANTIC_BLOCKED' and 'explanations[0]' in error.value.message
 assert p==before

@pytest.mark.parametrize('evidence,explanation',[
 ('服务明确失败时原记录不变。结果未知按同一请求ID查询，不自动重发。','服务明确失败时原记录不变；结果未知需查询，不能按失败处理。'),
 ('所有站点成功才整批成功，失败则全部不应用。','全部站点成功才整批成功。'),
 ('其它会话主动刷新后读取最新备注，不新增推送。','建议在培训或说明中提示刷新；这只是建议，不构成本期交付义务。'),
 ('本期决定必须在培训说明中提示刷新。','需在培训说明中提示刷新。'),
])
def test_supported_or_labelled_explanations_are_not_false_positives(tmp_path,evidence,explanation):
 p=project(tmp_path,evidence);r=compile_plan(plan(explanation),p,'prd');assert explanation in dumps(r)

def test_actual_existing_source_exception_allows_existing_implementation_claim(tmp_path):
 p=project(tmp_path,'备注成功变更沿用审计。')
 p['product_context']['current_state']='已核对的合成实现资料：现有系统已支持联系人备注变更审计，操作者与变更前后内容均有记录。'
 for step in [1,2]:checkpoint(p,step,status(p)[step-1]['content_hash'])
 assert status(p)[1]['complete']
 text='现有系统已支持联系人备注变更审计，本期沿用。'
 assert text in dumps(compile_plan(plan(text,['current_state']),p,'prd'))

def test_review_cannot_silently_pass_reproducible_counting_conflict(tmp_path):
 p=project(tmp_path,AMBIGUOUS);value=copy.deepcopy(EXAMPLES['review'])
 with pytest.raises(Problem) as e:validate_response(value,'review',p,p['sources'][0]['excerpts'])
 assert e.value.code=='SEMANTIC_BLOCKED'

def test_analysis_cannot_silently_choose_for_ambiguous_source(tmp_path):
 p=project(tmp_path,'新增备注。');p['sources'][0]['excerpts'][0]['text']=AMBIGUOUS
 value=copy.deepcopy(EXAMPLES['ingest']);value['proposals']=[];value['questions']=[]
 with pytest.raises(Problem) as e:validate_response(value,'ingest',p,p['sources'][0]['excerpts'])
 assert e.value.code=='SEMANTIC_BLOCKED'

def test_review_gets_all_fields_and_actual_narrative_with_semantic_contract(tmp_path):
 p=project(tmp_path,'园区运营人员确认后提交电价。');p['items'][0]['behavior']={'actor':'园区运营人员','flow':'PM人工核对后确认提交。'}
 before=copy.deepcopy(p);messages,_,_=assemble(p,'review','',DEFAULT,tmp_path)
 header=json.loads(messages[0]['content'].split('可信任务头：')[1]);ctx=json.loads(messages[1]['content'][0]['text'])
 assert 'semantic_quality_contract' in header
 assert {'输入内部一致性与反例','运行角色与需求确认角色','既有事实/新增决定/模型建议'}<=set(header['semantic_quality_contract']['review_axes'])
 assert ctx['items'][0]['behavior']==p['items'][0]['behavior'] and p==before


@pytest.mark.parametrize('text',[
 '最多100个Unicode码点，单码点emoji按一个码点计，复合emoji各组成码点分别计数。',
 '最多100个Unicode码点，emoji不按一个码点计，实际逐码点计数。',
 '最多100个扩展字素簇，每个emoji按一个字素簇计。',
 '最多100个Unicode码点，空格与换行分别计数。',
])
def test_clear_counting_rules_are_not_flagged(tmp_path,text):
 p=project(tmp_path,text)
 assert compile_plan(plan(),p,'prd')['result']['document_type']=='prd'

def test_unrelated_review_blocker_does_not_cover_counting_decision(tmp_path):
 p=project(tmp_path,AMBIGUOUS);value=copy.deepcopy(EXAMPLES['review'])
 assert value['result']['assessment']=='needs_changes'
 with pytest.raises(Problem):validate_response(value,'review',p,p['sources'][0]['excerpts'])

def test_actual_counting_review_is_accepted_without_changing_project(tmp_path):
 p=project(tmp_path,AMBIGUOUS);before=copy.deepcopy(p);value=copy.deepcopy(EXAMPLES['review'])
 value['findings'][0].update(message='COUNT:REQ-0001:statement，REQ-0001的Unicode码点与每个emoji一码点冲突；99个A加👍🏽为101码点而100字素簇。',suggested_resolution='请PM选择码点或扩展字素簇计数，同步修订验收。')
 value['result']['required_decisions']=['COUNT:REQ-0001:statement：按Unicode码点还是扩展字素簇计数？']
 assert validate_response(value,'review',p,p['sources'][0]['excerpts'])==value and p==before

def test_ready_review_cannot_pass_counting_conflict(tmp_path):
 p=project(tmp_path,AMBIGUOUS);value=copy.deepcopy(EXAMPLES['review'])
 value['findings']=[];value['result'].update(assessment='ready_for_human_review',required_decisions=[])
 with pytest.raises(Problem):validate_response(value,'review',p,p['sources'][0]['excerpts'])

def test_new_review_policy_invalidates_review_hash_without_mutating_history(tmp_path):
 from app.contracts import review_target
 from app.core import digest,brief_hash
 p=project(tmp_path,CODEPOINTS);before=copy.deepcopy(p)
 legacy=digest({'brief':brief_hash(p),'documents':p['documents'],'ui':p['ui']})
 assert review_target(p)!=legacy and p==before


@pytest.mark.parametrize('field',['statement','behavior.data'])
def test_same_object_other_counting_field_cannot_satisfy_this_conflict(tmp_path,field):
 p=project(tmp_path,'备注可空。');p['items'][0]['behavior']={'data':AMBIGUOUS}
 value=copy.deepcopy(EXAMPLES['review'])
 value['findings'][0].update(message='COUNT:REQ-0001:'+field+' 存在码点/字素簇矛盾。',suggested_resolution='PM选择Unicode码点还是字素簇。')
 value['result']['required_decisions']=['COUNT:REQ-0001:'+field+' 选择码点/字素簇']
 if field=='statement':
  with pytest.raises(Problem):validate_response(value,'review',p,p['sources'][0]['excerpts'])
 else:validate_response(value,'review',p,p['sources'][0]['excerpts'])


@pytest.mark.parametrize('evidence,explanation,dimension',[
 ('服务明确失败时原记录不变，结果未知查询原结果。','失败场景下原记录不变，结果未知查询原结果。','failure_condition'),
 ('所有站点成功才整批成功。','部分站点成功即可整批成功。','success_quantifier'),
 ('其它会话主动刷新读新值，不新增推送。','需在培训或说明中提示刷新。','suggestion_obligation'),
 ('成功备注变更应沿用既有审计。','现有审计已覆盖这一场景。','existing_implementation'),
])
def test_review_cannot_omit_actual_cited_narrative_defect(tmp_path,evidence,explanation,dimension):
 from app.workflow import apply_response
 from app.semantic_quality import current_document_checks
 p=project(tmp_path,evidence);apply_response(p,compile_plan(plan('按来源规范执行。'),p,'prd'))
 block=next(b for s in p['documents']['prd']['content']['sections'] for b in s['blocks'] if b['kind']=='explanation')
 block['text']=explanation;before=copy.deepcopy(p)
 checks=current_document_checks(p);assert len(checks)==1 and checks[0]['dimension']==dimension
 value=copy.deepcopy(EXAMPLES['review']);value['result']['required_decisions']=[]
 with pytest.raises(Problem):validate_response(value,'review',p,p['sources'][0]['excerpts'])
 finding=value['findings'][0];finding.update(message=checks[0]['check_id']+'：'+explanation+' 超出本段引用含义。',related_refs=['REQ-0001'])
 validate_response(value,'review',p,p['sources'][0]['excerpts'])
 assert value['result']['required_decisions']==[] and p==before

@pytest.mark.parametrize('wrong_id',['DOC:another_document:failure_condition','DOC:another_block:failure_condition','DOC:same_block:success_quantifier'])
def test_other_document_block_or_dimension_cannot_cover_narrative_defect(tmp_path,wrong_id):
 from app.workflow import apply_response
 p=project(tmp_path,'服务明确失败时原记录不变，结果未知查询原结果。')
 apply_response(p,compile_plan(plan('按来源规范执行。'),p,'prd'))
 next(b for s in p['documents']['prd']['content']['sections'] for b in s['blocks'] if b['kind']=='explanation')['text']='失败场景下原记录不变。'
 value=copy.deepcopy(EXAMPLES['review']);value['findings'][0]['message']=wrong_id+' 需要纠正失败条件。'
 with pytest.raises(Problem):validate_response(value,'review',p,p['sources'][0]['excerpts'])

def test_unrelated_function_unknown_result_cannot_contaminate_cited_narrative(tmp_path):
 from app.workflow import apply_response
 from app.semantic_quality import current_document_checks
 p=project(tmp_path,'姓名保存失败时原记录不变。')
 extra=dict(p['items'][0],id='REQ-OTHER',statement='电价服务明确失败原记录不变，结果未知查询原结果。')
 p['items'].append(extra)
 apply_response(p,compile_plan(plan('姓名保存失败场景原记录不变。'),p,'prd'))
 assert current_document_checks(p)==[]
 validate_response(copy.deepcopy(EXAMPLES['review']),'review',p,p['sources'][0]['excerpts'])


def test_explicit_runtime_pm_approval_source_is_preserved_without_hardcoded_role_rejection(tmp_path):
 p=project(tmp_path,'运营人员提交前须由业务PM审批，审批通过后运营人员确认提交。')
 p['sources'][0]['excerpts'][0]['text']=p['items'][0]['statement']
 p['items'][0]['behavior']={'actor':'园区运营人员','flow':p['items'][0]['statement']}
 before=copy.deepcopy(p);messages,excerpts,_=assemble(p,'review','',DEFAULT,tmp_path)
 ctx=json.loads(messages[1]['content'][0]['text'])
 assert ctx['items'][0]['behavior']==p['items'][0]['behavior']
 assert excerpts[0]['text']==p['items'][0]['statement']
 validate_response(copy.deepcopy(EXAMPLES['review']),'review',p,excerpts)
 assert p==before


def test_review_output_boundary_separates_diagnostics_and_forbids_role_note(tmp_path):
 p=project(tmp_path,'服务明确失败原记录不变，结果未知查询。');before=copy.deepcopy(p)
 messages,_,_=assemble(p,'review','',DEFAULT,tmp_path)
 header=json.loads(messages[0]['content'].split('可信任务头：')[1])
 boundary=header['response_boundary']
 assert set(boundary['object_keys']['$.result.perspectives[]'])=={'role','considerations'}
 assert not set(boundary['input_only']) & set(boundary['object_keys']['$'])
 assert p==before

def test_ingest_output_boundary_requires_result_without_copying_context(tmp_path):
 p=project(tmp_path,'新增联系人备注。')
 messages,_,_=assemble(p,'ingest','',DEFAULT,tmp_path)
 header=json.loads(messages[0]['content'].split('可信任务头：')[1])
 boundary=header['response_boundary']
 assert set(boundary['object_keys']['$.result'])=={'understanding','material_limits'}
 assert 'result' in boundary['object_keys']['$'] and 'product_context' not in boundary['object_keys']['$']
 assert 'product_context_proposal' in boundary['object_keys']['$']
