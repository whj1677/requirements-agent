"""Current navigation and identity reuse keep synthetic historical decisions intact."""
import copy

import pytest

from app.product_flow import question_view
from app.understanding import question_ids


def question(**changes):
    return dict(dict(id='Q-saved',topic_key='permission',question='谁可以导出？',why='权限决定',
        related_refs=['REQ-1'],source_refs=[dict(source_id='S1',excerpt_id='E1')],
        status='answered',answer='仅站点管理员',blocking=True,options=[],understanding_status='pending'),**changes)


def test_projection_preserves_history_and_pending_implementation():
    rows=[question(),question(id='Q-applied',understanding_status='applied'),
          question(id='Q-open',status='open',answer=None),
          question(id='Q-partial',decision_coverage='pending'),
          question(id='Q-history',prewrite_resolution={'basis':'reviewed_dispute'})]
    p=dict(questions=rows);before=copy.deepcopy(p)
    assert question_view(p)==dict(open_ids=['Q-open','Q-partial'],answered_ids=['Q-saved','Q-applied'],
        history_ids=['Q-history'],answer_pending_ids=['Q-saved'],answer_applied_ids=['Q-applied'],
        open_groups=[dict(representative_id='Q-open',question_ids=['Q-open','Q-partial'],related_refs=['REQ-1'])],
        duplicate_groups=[])
    assert p==before


@pytest.mark.parametrize('change',[
    {'related_refs':['REQ-2']}, {'related_refs':['REQ-1','REQ-2']},
    {'source_refs':[dict(source_id='S2',excerpt_id='E1')]},
    {'source_refs':[dict(source_id='S1',excerpt_id='E2')]},
    {'source_refs':[dict(source_id='S1',excerpt_id='E1',version=2)]},
    {'question':'谁可以删除？'},
])
def test_similar_questions_do_not_consume_other_scope_source_or_decision(change):
    old=question(status='open',answer=None);candidate=question(temp_id='QTMP-new',**change)
    assert question_ids(dict(questions=[old]),[candidate],{})['QTMP-new']!='Q-saved'


def test_exact_current_answer_reuses_identity_but_not_resolved_history():
    old=question();candidate=question(temp_id='QTMP-new');before=copy.deepcopy(old)
    assert question_ids(dict(questions=[old]),[candidate],{})['QTMP-new']=='Q-saved'
    assert old==before
    old['prewrite_resolution']={'basis':'reviewed_dispute'}
    assert question_ids(dict(questions=[old]),[candidate],{})['QTMP-new']!='Q-saved'


def test_related_group_does_not_claim_paraphrases_or_new_sources_are_duplicates():
    old=question(status='open',answer=None)
    exact=dict(old,id='Q-exact')
    broader=dict(old,id='Q-broader',question='谁可以导出，谁可以删除？')
    changed=dict(old,id='Q-source',source_refs=[dict(source_id='S2',excerpt_id='E1')])
    foreign=dict(old,id='Q-foreign',related_refs=['REQ-2'])
    p=dict(questions=[old,exact,broader,changed,foreign]);before=copy.deepcopy(p)
    view=question_view(p)
    assert view['open_groups']==[
        dict(representative_id='Q-saved',question_ids=['Q-saved','Q-exact','Q-broader','Q-source'],related_refs=['REQ-1']),
        dict(representative_id='Q-foreign',question_ids=['Q-foreign'],related_refs=['REQ-2'])]
    assert view['duplicate_groups']==[dict(representative_id='Q-saved',question_ids=['Q-saved','Q-exact'])]
    assert view['open_ids']==[q['id'] for q in p['questions']] and p==before
