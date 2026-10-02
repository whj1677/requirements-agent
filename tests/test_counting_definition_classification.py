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


def adoption_review(p,stage='review',claim='本期计数口径未确定，需PM确认当前来源中的方案是否已经采纳。'):
    value=copy.deepcopy(EXAMPLES[stage]);value['proposals']=[];value['questions']=[]
    value['findings']=[dict(type='gap',severity='warning',message=claim,related_refs=['REQ-0001'],
        source_refs=[dict(source_id='SRC-0001',excerpt_id='EX-0001')],suggested_resolution='由PM确认；审查不替PM决定。')]
    if stage=='review':value['result']['required_decisions']=[]
    return value


@pytest.mark.parametrize('stage',['review','ingest'])
@pytest.mark.parametrize('unit',['Unicode码点','扩展字素簇'])
@pytest.mark.parametrize('case,accepted,decision',[
    ('pending',True,'pending'),('confirmed',False,'confirmed'),
    ('proposal_then_confirmed',False,'confirmed'),('confirmation_unproven',True,'pending')])
def test_source_adoption_boundary_separates_proposed_units_and_human_decisions(tmp_path,stage,unit,case,accepted,decision):
    from app.semantic_quality import counting_definition
    proposal=f'方案建议：备注可以按{unit}计数；该方案需产品经理确认后才采纳。'
    sources=dict(pending=proposal,confirmed=f'产品经理已确认：备注按{unit}计数，最多100。',
        proposal_then_confirmed=proposal+f'产品经理已确认：本期采用{unit}计数。',
        confirmation_unproven=proposal+'没有证据表明PM已确认采纳该方案。')
    p=project(tmp_path,'备注最多100字符。');p['sources'][0]['excerpts'][0]['text']=sources[case]
    value=adoption_review(p,stage);before=copy.deepcopy((p,value))
    if accepted:validate_response(value,stage,p,p['sources'][0]['excerpts'])
    else:
        with pytest.raises(Problem) as error:validate_response(value,stage,p,p['sources'][0]['excerpts'])
        assert error.value.code=='SEMANTIC_BLOCKED'
    classification=counting_definition(sources[case])
    assert classification['definition_present'] and classification['decision_status']==decision
    assert classification['status']==('undecided' if accepted else 'defined')
    assert (p,value)==before


@pytest.mark.parametrize('unit',['Unicode码点','扩展字素簇'])
def test_adoption_confirmation_can_reference_the_same_preceding_candidate(tmp_path,unit):
    p=project(tmp_path,'备注最多100字符。')
    p['sources'][0]['excerpts'][0]['text']=f'候选方案：备注按{unit}计数；该方案尚未采纳。PM已确认采纳上述方案。'
    with pytest.raises(Problem) as error:validate_response(adoption_review(p),'review',p,p['sources'][0]['excerpts'])
    assert error.value.code=='SEMANTIC_BLOCKED'


@pytest.mark.parametrize('unit',['Unicode码点','扩展字素簇'])
def test_pending_source_adoption_does_not_erase_an_existing_current_rule(tmp_path,unit):
    p=project(tmp_path,f'备注按{unit}计数，最多100。')
    p['sources'][0]['excerpts'][0]['text']=f'建议备注按{unit}计数；需PM确认后采纳。'
    with pytest.raises(Problem):validate_response(adoption_review(p),'review',p,p['sources'][0]['excerpts'])


@pytest.mark.parametrize('unit',['Unicode码点','扩展字素簇'])
def test_sourced_human_adoption_answer_keeps_its_authority_and_bindings(tmp_path,unit):
    from app.semantic_quality import counting_definition_facts
    p=project(tmp_path,'备注最多100字符。')
    p['sources'][0]['excerpts'][0]['text']=f'候选：备注可以按{unit}计数；需PM确认。'
    answer=f'产品经理已确认：本期备注按{unit}计数，最多100。'
    p['questions'][0].update(question='采用哪种计数口径？',answer=answer,related_refs=['REQ-0001'],
        answer_source_refs=[dict(source_id='SRC-0001',excerpt_id='EX-0001')])
    before=copy.deepcopy(p)
    with pytest.raises(Problem):validate_response(adoption_review(p),'review',p,p['sources'][0]['excerpts'])
    fact=next(f for f in counting_definition_facts(p) if f['origin']=='human_answer')
    assert fact['decision_status']=='confirmed' and fact['quote']==answer
    assert fact['location']==p['questions'][0]['id']+'.answer'
    assert fact['source_refs']==p['questions'][0]['answer_source_refs'] and 'REQ-0001' in fact['related_refs']
    assert p==before


@pytest.mark.parametrize('unit',['Unicode码点','扩展字素簇'])
def test_unadopted_candidate_is_still_a_present_unit_when_source_is_claimed_missing(tmp_path,unit):
    p=project(tmp_path,'备注最多100字符。')
    p['sources'][0]['excerpts'][0]['text']=f'方案建议：备注可以按{unit}计数；需PM确认后采纳。'
    value=adoption_review(p,claim='材料未提供任何计数单位。')
    with pytest.raises(Problem):validate_response(value,'review',p,p['sources'][0]['excerpts'])


def test_adoption_confirmation_does_not_remove_a_sourced_counting_contradiction(tmp_path):
    from app.semantic_quality import counting_definition_facts
    p=project(tmp_path,CODEPOINTS)
    p['questions'][0].update(question='本期采用哪种计数口径？',answer='产品经理已确认：'+AMBIGUOUS,
        related_refs=['REQ-0001'],answer_source_refs=[dict(source_id='SRC-0001',excerpt_id='EX-0001')])
    before=copy.deepcopy(p)
    fact=next(f for f in counting_definition_facts(p) if f['origin']=='human_answer')
    assert fact['decision_status']=='confirmed' and fact['status']=='contradictory'
    with pytest.raises(Problem):compile_plan(plan(),p,'prd')
    checks=current_counting_checks(p)
    assert any(c.get('counterexample',{}).get('codepoints')==101 and
        c['counterexample']['extended_grapheme_clusters']==100 and c['decision'] for c in checks)
    assert p==before


def test_unrelated_human_confirmation_does_not_adopt_a_counting_candidate(tmp_path):
    p=project(tmp_path,'备注最多100字符。')
    p['sources'][0]['excerpts'][0]['text']='候选方案：备注可以按Unicode码点计数；需PM确认后采纳。PM已确认采用登录方案。'
    validate_response(adoption_review(p),'review',p,p['sources'][0]['excerpts'])


@pytest.mark.parametrize('confirmation',['PM未确认采纳该方案。','PM已确认暂不采纳该方案。'])
def test_adoption_confirmation_requires_affirmative_adoption(tmp_path,confirmation):
    from app.semantic_quality import counting_definition
    p=project(tmp_path,'备注最多100字符。')
    source='候选方案：备注可以按Unicode码点计数；该方案需PM确认后采纳。'+confirmation
    p['sources'][0]['excerpts'][0]['text']=source
    validate_response(adoption_review(p),'review',p,p['sources'][0]['excerpts'])
    assert counting_definition(source)['decision_status']=='pending'
