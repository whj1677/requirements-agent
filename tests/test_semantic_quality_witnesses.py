"""Counterexamples and binding exceptions, never provider-derived business choices."""
import copy
import pytest
from app.core import Problem
from app.semantic_quality import counting_witness,validate_analysis

BAD='备注最多100个Unicode码点，emoji按一个码点计。'
GOOD='备注按Unicode码点分别计数，复合emoji各组成码点分别计。'
def data():
 ref={'source_id':'SRC-1','excerpt_id':'EX-1'}
 p={'items':[{'id':'REQ-1','statement':BAD,'source_refs':[ref],'selection_status':'selected'}],
 'questions':[{'id':'Q-1','status':'answered','answer':GOOD,'related_refs':['REQ-1'],'source_refs':[ref]}]}
 v={'stage':'clarify','questions':[],'proposals':[{'temp_id':'TMP-1','action':'revise','target_item_id':'REQ-1','statement':GOOD,'answer_refs':['Q-1']}]}
 return p,v,[{'source_id':'SRC-1','id':'EX-1','text':BAD}]

@pytest.mark.parametrize('limit',[1,32,100,250])
def test_concrete_boundary_witness_is_not_tied_to_one_limit(limit):
 w=counting_witness(f'最多{limit}个码点，每个表情符号按1个码点计。')['counterexample']
 assert w['codepoints']==limit+1 and w['extended_grapheme_clusters']==limit
 assert w['sequence_codepoints']==len('👍🏽')==2

def test_source_bound_explicit_pm_answer_allows_revision_but_never_adopts_it():
 p,v,ex=data();before=copy.deepcopy((p,v,ex));validate_analysis(v,p,ex)
 assert (p,v,ex)==before and p['items'][0]['statement']==BAD

@pytest.mark.parametrize('kind',['contradictory_answer','wrong_question','wrong_source','still_bad_proposal'])
def test_answer_with_identity_does_not_waive_unrelated_or_contradictory_input(kind):
 p,v,ex=data()
 if kind=='contradictory_answer':p['questions'][0]['answer']=BAD
 if kind=='wrong_question':v['proposals'][0]['answer_refs']=['Q-OTHER']
 if kind=='wrong_source':p['questions'][0]['source_refs']=[];p['items'][0]['source_refs']=[]
 if kind=='still_bad_proposal':v['proposals'][0]['statement']=BAD
 before=copy.deepcopy(p)
 with pytest.raises(Problem):validate_analysis(v,p,ex)
 assert p==before

def test_open_question_is_reused_instead_of_forcing_duplicate():
 p,v,ex=data();p['questions'][0].update(status='open',answer=None,question='按Unicode码点还是字素簇计？',why='影响验收',blocking=True)
 v['proposals'][0]['statement']=BAD
 validate_analysis(v,p,ex)

def test_answer_repeats_bad_rule_even_with_extra_consistent_sentence():
 text=BAD+' 同时复合emoji每个组成码点分别计数。'
 assert counting_witness(text) is not None


@pytest.mark.parametrize('missing',['wrong_object','wrong_source'])
def test_blocking_counting_question_must_bind_exact_source_and_object(missing):
 p,v,ex=data();p['questions'][0].update(status='open',answer=None,question='按Unicode码点还是字素簇？',blocking=True)
 v['proposals'][0]['statement']=BAD
 if missing=='wrong_object':p['questions'][0]['related_refs']=['REQ-OTHER']
 else:p['questions'][0]['source_refs']=[{'source_id':'SRC-OTHER','excerpt_id':'EX-OTHER'}]
 with pytest.raises(Problem):validate_analysis(v,p,ex)
