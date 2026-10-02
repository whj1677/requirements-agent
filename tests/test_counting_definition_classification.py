"""Public boundaries: a declared unit may conflict, but is not a missing unit."""
import copy,json
import pytest

from app.contracts import validate_response
from app.core import Problem
from app.prd import compile_plan
from app.provider import assemble,DEFAULT
from app.semantic_quality import current_counting_checks,readable_document_blocks
from app.workflow import apply_response
from tests.helpers import EXAMPLES
from tests.test_semantic_quality_regressions import project,plan,AMBIGUOUS,CODEPOINTS,GRAPHEMES


@pytest.mark.parametrize('stage',['review','ingest'])
@pytest.mark.parametrize('definition,missing',[(CODEPOINTS,False),(GRAPHEMES,False),('备注最多100字符。',True)])
@pytest.mark.parametrize('claim',['材料未明确计数单位。','计数口径缺失，需要产品决定。','长度按哪种单位计算尚未说明。'])
def test_missing_definition_claim_needs_missing_bound_evidence(tmp_path,stage,definition,missing,claim):
    p=project(tmp_path,definition);p['sources'][0]['excerpts'][0]['text']=definition
    value=copy.deepcopy(EXAMPLES[stage]);value['proposals']=[];value['questions']=[]
    value['findings']=[dict(type='gap',severity='warning',message=claim,related_refs=['REQ-0001'],
        source_refs=[dict(source_id='SRC-0001',excerpt_id='EX-0001')],suggested_resolution='保留待核对，不自动选定。')]
    if stage=='review':value['result']['required_decisions']=[]
    before=copy.deepcopy((p,value))
    if missing:assert validate_response(value,stage,p,p['sources'][0]['excerpts'])==value
    else:
        with pytest.raises(Problem) as error:validate_response(value,stage,p,p['sources'][0]['excerpts'])
        assert error.value.code=='SEMANTIC_BLOCKED'
    assert (p,value)==before


@pytest.mark.parametrize('unit',['Unicode码点','扩展字素簇'])
@pytest.mark.parametrize('missing',[False,True])
def test_document_boundary_definition_and_missing_claim_are_bound_to_actual_block(tmp_path,unit,missing):
    p=project(tmp_path,f'备注按{unit}计数，最多100。')
    text=f'0与100个{unit}通过、101拒绝。' if not missing else '0与100通过、101拒绝。'
    apply_response(p,compile_plan(plan(text),p,'prd'))
    location=next(loc for loc,body in readable_document_blocks(p['documents']['prd']).items() if body==text)
    value=copy.deepcopy(EXAMPLES['review']);value['result']['required_decisions']=[]
    value['findings']=[dict(type='gap',severity='warning',message=f'{location} 只写“{text}”，未明确计数单位。',
        related_refs=['REQ-0001'],source_refs=[],suggested_resolution='在该边界句标出单位。')]
    # An omitted local label is a legitimate document clarity finding, even
    # when its referenced rule has a definition; it cannot claim the rule lacks it.
    if missing:validate_response(value,'review',p,p['sources'][0]['excerpts'])
    else:
        with pytest.raises(Problem):validate_response(value,'review',p,p['sources'][0]['excerpts'])


@pytest.mark.parametrize('claim_missing',[False,True])
def test_conflicting_definition_still_needs_recomputable_witness_and_pm_choice(tmp_path,claim_missing):
    p=project(tmp_path,AMBIGUOUS);p['sources'][0]['excerpts'][0]['text']=AMBIGUOUS
    value=copy.deepcopy(EXAMPLES['review']);check=current_counting_checks(p)[0]
    message=(check['check_id']+'：已经规定Unicode码点，但每个emoji恒计一码点与复合emoji矛盾；'
        '99个A加👍🏽为101码点、100字素簇，两个口径在100上限给出不同结果。')
    if claim_missing:message+=' 材料未明确计数单位。'
    value['findings'][0].update(message=message,suggested_resolution='由PM选择码点或字素簇，修订需求及验收；不代选。')
    value['result']['required_decisions']=[check['check_id']+' 请PM选择Unicode码点还是扩展字素簇。']
    before=copy.deepcopy(p)
    if claim_missing:
        with pytest.raises(Problem):validate_response(value,'review',p,p['sources'][0]['excerpts'])
    else:validate_response(value,'review',p,p['sources'][0]['excerpts'])
    assert p==before


def test_definition_from_unrelated_object_does_not_fill_another_objects_gap(tmp_path):
    p=project(tmp_path,'备注最多100字符。')
    p['items'].append(dict(p['items'][0],id='REQ-OTHER',statement='姓名按Unicode码点计数。',source_refs=[]))
    value=copy.deepcopy(EXAMPLES['review']);value['result']['required_decisions']=[]
    value['findings'][0].update(type='gap',severity='warning',message='备注计数口径未明确。',related_refs=['REQ-0001'],source_refs=[])
    validate_response(value,'review',p,p['sources'][0]['excerpts'])


@pytest.mark.parametrize('definition,missing',[(CODEPOINTS,False),(GRAPHEMES,False),('备注最多100字符。',True)])
def test_question_itself_cannot_assert_an_absent_definition_when_it_is_present(tmp_path,definition,missing):
    p=project(tmp_path,definition);p['sources'][0]['excerpts'][0]['text']=definition
    value=copy.deepcopy(EXAMPLES['ingest']);value['proposals']=[];value['findings']=[]
    from tests.test_semantic_quality_boundaries import question
    value['questions']=[question('QTMP-UNIT','REQ-0001',dict(source_id='SRC-0001',excerpt_id='EX-0001'),
        '计数单位未给出，请选择按Unicode码点还是扩展字素簇计数？')]
    if missing:validate_response(value,'ingest',p,p['sources'][0]['excerpts'])
    else:
        with pytest.raises(Problem):validate_response(value,'ingest',p,p['sources'][0]['excerpts'])


@pytest.mark.parametrize('message',['计数口径不是未明确，而是需要核对实例的一致性。','不能说计数单位未明确，当前已按Unicode码点计数。'])
def test_denial_of_a_missing_definition_is_not_itself_a_gap_assertion(tmp_path,message):
    p=project(tmp_path,CODEPOINTS);value=copy.deepcopy(EXAMPLES['review'])
    value['result']['required_decisions']=[];value['findings'][0].update(message=message,source_refs=[],suggested_resolution='保持当前定义。')
    validate_response(value,'review',p,p['sources'][0]['excerpts'])


@pytest.mark.parametrize('answer,status',[(CODEPOINTS,'defined'),(GRAPHEMES,'defined'),(AMBIGUOUS,'contradictory')])
def test_sourced_human_answers_are_classified_without_becoming_automatically_consistent(tmp_path,answer,status):
    p=project(tmp_path,CODEPOINTS)
    p['questions'][0].update(question='按哪种单位计数？',answer=answer,related_refs=['REQ-0001'],
        answer_source_refs=[dict(source_id='SRC-0001',excerpt_id='EX-0001')])
    before=copy.deepcopy(p);messages,_,_=assemble(p,'review','',DEFAULT,tmp_path)
    context=json.loads(messages[1]['content'][0]['text'])
    facts=[f for f in context['counting_definition_facts'] if f['origin']=='human_answer']
    assert facts[0]['status']==status and facts[0]['definition_present']
    assert facts[0]['source_refs']==p['questions'][0]['answer_source_refs'] and p==before
    if status=='contradictory':
        with pytest.raises(Problem):compile_plan(plan(),p,'prd')
