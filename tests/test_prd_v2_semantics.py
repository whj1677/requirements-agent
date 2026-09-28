"""Synthetic v2 planning boundaries; no model calls or daily project data."""
import copy

import pytest

from app.core import Problem, dumps
from app.document_reader import reader_document
from app.prd import compile_plan, plan_contract
from app.product_flow import INTAKE, SCOPE, checkpoint, status
from app.workflow import apply_response
from tests.test_prd01 import state


def checked_project(tmp_path):
    _,p=state(tmp_path)
    p['product_context']={k:'人工核对：'+label for k,label in {**INTAKE,**SCOPE}.items()}
    p['product_context']['scope_ids']=['REQ-0001']
    for step in (1,2):checkpoint(p,step,status(p)[step-1]['content_hash'])
    assert status(p)[1]['complete']
    return p


def v2(p, *, context=None, normative=None, discussion=None):
    return dict(plan_version='2',sections=[dict(section_key='function',
        context_refs=context if context is not None else plan_contract(p)['context_ref_ids'],
        normative_refs=normative if normative is not None else plan_contract(p)['normative_item_ids'],
        discussion_refs=discussion or [])])


def test_checked_context_and_normative_originals_are_the_only_current_prose(tmp_path):
    p=checked_project(tmp_path);before=copy.deepcopy(p)
    r=compile_plan(v2(p),p,'mrd')
    text=dumps(r)
    assert p==before and r['limitations']==[]
    assert len(r['result']['coverage'])==len(plan_contract(p)['normative_item_ids'])
    for key in plan_contract(p)['context_ref_ids']:
        assert p['product_context'][key] in text
    for item in p['items']:
        if item['id'] in plan_contract(p)['normative_item_ids']:
            assert item['id'] in text
    assert all(s['title']!='补列的已核对事实与规范条款' for s in r['result']['sections'])
    boundary=next(s for s in r['result']['sections'] if s['title']=='文档边界与资料限制')
    assert boundary['blocks'][0]['kind']=='narrative'
    assert boundary['blocks'][0]['text']


def test_stale_or_unchecked_context_cannot_be_referenced(tmp_path):
    p=checked_project(tmp_path)
    key=plan_contract(p)['context_ref_ids'][0]
    p['product_context'][key]+='未经复核的修改'
    assert not status(p)[1]['complete']
    assert plan_contract(p)['context_ref_ids']==[]
    with pytest.raises(Problem) as error:compile_plan(v2(p,context=[key]),p,'prd')
    assert error.value.code=='REFERENCE_INVALID' and key in error.value.message


def test_omission_is_supplemented_and_real_unknowns_survive(tmp_path):
    p=checked_project(tmp_path)
    p['sources'][0].update(parse_status='partial',failure_reason='附图无法辨认；业务含义待核对。')
    p['messages'].append(dict(stage='ingest',created='synthetic',response={'limitations':['过去曾称缺少资料，现已补齐。']}))
    r=compile_plan(v2(p,context=[],normative=['REQ-0001']),p,'prd',omitted=['EX-OMITTED'])
    text=dumps(r)
    assert '补列的已核对事实与规范条款' in text
    assert all(i['id'] in {c['item_id'] for c in r['result']['coverage']}
               for i in p['items'] if i['id'] in plan_contract(p)['normative_item_ids'])
    assert p['questions'][0]['question'] in text
    assert '附图无法辨认' in text and 'EX-OMITTED' in text
    assert '过去曾称缺少资料' in text
    assert '过去曾称缺少资料' not in dumps(r['limitations'])
    artifact=copy.deepcopy(r['result'])
    apply_response(p,r)
    saved_artifact=p['documents']['prd']
    saved_source=saved_artifact['source_snapshot'][0]
    assert saved_source['purpose']==p['sources'][0]['purpose']=='goal'
    view=dumps(reader_document(saved_artifact))
    assert p['questions'][0]['question'] in view
    assert (f'《{saved_source["title"]}》（本期诉求资料）：部分内容未能完整读取；'
            '未读取内容不作为本版产品结论的依据。') in view
    assert '附图无法辨认' not in view and 'EX-OMITTED' not in view
    assert '过去曾称缺少资料' not in view
    assert artifact==r['result']


def test_candidate_rejected_and_deferred_have_distinct_reader_identity(tmp_path):
    _,p=state(tmp_path)
    originals={}
    for selection in ('candidate','rejected','deferred'):
        item=copy.deepcopy(p['items'][0]);item.update(id='REQ-'+selection.upper(),
            selection_status=selection,statement='合成'+selection+'建议。')
        p['items'].append(item);originals[selection]=item
    r=compile_plan(v2(p,context=[],normative=['REQ-0001'],
        discussion=[i['id'] for i in originals.values()]),p,'prd')
    assert all(i['statement'] in dumps(r) for i in originals.values())
    apply_response(p,r)
    view=dumps(reader_document(p['documents']['prd']))
    assert all(i['statement'] in dumps(p['documents']['prd']['content']) for i in originals.values())
    assert all(i['id'] not in view and i['statement'] not in view for i in originals.values())
    assert '附录：条目身份与历史讨论' not in view


def test_selected_normative_cannot_be_downgraded_to_discussion(tmp_path):
    p=checked_project(tmp_path)
    assert 'REQ-0001' in plan_contract(p)['normative_item_ids']
    assert 'REQ-0001' not in plan_contract(p)['discussion_item_ids']
    with pytest.raises(Problem) as error:
        compile_plan(v2(p,discussion=['REQ-0001']),p,'prd')
    assert error.value.code=='REFERENCE_INVALID'


def test_rejected_and_deferred_identical_business_keep_id_and_point_to_stable(tmp_path):
    _,p=state(tmp_path)
    stable=p['items'][0]
    copies=[]
    for selection in ('rejected','deferred'):
        item=copy.deepcopy(stable)
        item.update(id='REQ-'+selection.upper()+'-SAME',selection_status=selection,
                    classification_reason='另一轮的分类说明')
        p['items'].append(item);copies.append(item)
    value=v2(p,context=[],normative=[stable['id']],discussion=[i['id'] for i in copies])
    value['sections'].append(dict(section_key='discussion',context_refs=[],normative_refs=[],
                                  discussion_refs=[i['id'] for i in copies]))
    r=compile_plan(value,p,'prd')
    apply_response(p,r)
    view=reader_document(p['documents']['prd'])
    blocks={i['id']:[b['text'] for s in view['sections'] for b in s['blocks']
            if b['ref_ids']==[i['id']]] for i in copies}
    for item in copies:
        assert blocks[item['id']]==[]
        assert item['id'] in dumps(p['documents']['prd']['content'])
        assert item['statement'] in dumps(p['documents']['prd']['content'])
    assert stable['statement'] in dumps(view)


def test_changed_business_text_behavior_or_classification_has_no_identical_label(tmp_path):
    _,p=state(tmp_path)
    stable=p['items'][0]
    copies=[]
    for suffix,changes in (('TEXT',{'statement':'不同的业务正文。'}),
                           ('BEHAVIOR',{'behavior':{'result':'不同的业务结果。'}}),
                           ('CHANGE',{'change_type':'modified'})):
        item=copy.deepcopy(stable)
        item.update(id='REQ-'+suffix,selection_status='rejected',**changes)
        p['items'].append(item);copies.append(item)
    r=compile_plan(v2(p,context=[],normative=[stable['id']],
        discussion=[i['id'] for i in copies]),p,'prd')
    apply_response(p,r)
    view=reader_document(p['documents']['prd'])
    for item in copies:
        assert item['id'] not in dumps(view)
        if item['statement'] != stable['statement']:
            assert item['statement'] not in dumps(view)
        assert item['id'] in dumps(p['documents']['prd']['content'])
        assert item['statement'] in dumps(p['documents']['prd']['content'])
    assert stable['statement'] in dumps(view)


def test_rejected_acceptance_with_different_classification_does_not_cancel_selected_ac(tmp_path):
    _,p=state(tmp_path)
    stable=next(i for i in p['items'] if i['kind']=='acceptance')
    stable['change_type']='unspecified'
    same=copy.deepcopy(stable)
    same.update(id='AC-REJECTED-SAME',selection_status='rejected',change_type='new',
                classification_reason='另一轮分类说明',scope_evidence=[{'quote':'另一份分类依据'}])
    changed=copy.deepcopy(same)
    changed.update(id='AC-REJECTED-DIFFERENT',statement='不同的验收预期。')
    p['items'] += [same,changed]
    r=compile_plan(v2(p,context=[],normative=[stable['id']],
        discussion=[same['id'],changed['id']]),p,'prd')
    apply_response(p,r)
    view=reader_document(p['documents']['prd'])
    for item in (same,changed):
        assert item['id'] not in dumps(view)
        if item['statement'] != stable['statement']:
            assert item['statement'] not in dumps(view)
        assert item['id'] in dumps(p['documents']['prd']['content'])
        assert item['statement'] in dumps(p['documents']['prd']['content'])
    assert stable['id'] in dumps(view) and stable['statement'] in dumps(view)


def test_discussion_only_plan_places_candidate_in_explicit_appendix(tmp_path):
    _,p=state(tmp_path)
    candidate=copy.deepcopy(p['items'][0]);candidate.update(id='REQ-HISTORY',
        selection_status='rejected',statement='曾讨论但拒绝的独立要求。')
    p['items'].append(candidate)
    plan=dict(plan_version='2',sections=[dict(section_key='discussion',context_refs=[],
        normative_refs=[],discussion_refs=[candidate['id']])])
    result=compile_plan(plan,p,'prd')['result']
    appendix=next(s for s in result['sections'] if s['title']=='附录：条目身份与历史讨论')
    assert [b['ref_ids'] for b in appendix['blocks']][0]==[candidate['id']]
    assert any(b['ref_ids']==['REQ-CANDIDATE'] for b in appendix['blocks'])
    assert all(candidate['id'] not in b['ref_ids'] for s in result['sections'] if s is not appendix
               for b in s['blocks'])
    assert any(c['item_id']=='REQ-0001' for c in result['coverage'])


def test_old_artifact_moves_only_recognized_discussion_not_arbitrary_prose(tmp_path):
    _,p=state(tmp_path)
    candidate=copy.deepcopy(p['items'][0]);candidate.update(id='REQ-HISTORY',
        selection_status='rejected',statement='已拒绝的旧讨论。')
    p['items'].append(candidate)
    result=compile_plan(v2(p,context=[],normative=['REQ-0001'],
        discussion=[candidate['id']]),p,'prd')
    apply_response(p,result)
    artifact=p['documents']['prd']
    sections=artifact['content']['sections']
    appendix=next(s for s in sections if s['title']=='附录：条目身份与历史讨论')
    main=sections[0]
    main['blocks'].extend(appendix['blocks'])
    main['blocks'].append(dict(kind='narrative',ref_ids=[],
        text='管理员不得修改联系人权限；此旧叙述须原样送审。'))
    sections.remove(appendix)
    before=copy.deepcopy(artifact)
    view=reader_document(artifact)
    business=next(s for s in view['sections'] if s['section_id']==main['section_id'])
    assert artifact==before
    assert any('此旧叙述须原样送审' in b['text'] for b in business['blocks'])
    assert not any(b['ref_ids']==[candidate['id']] for b in business['blocks'])
    assert candidate['id'] not in dumps(view) and candidate['statement'] not in dumps(view)
    assert candidate['id'] in dumps(artifact['content'])
    assert candidate['statement'] in dumps(artifact['content'])


def test_answer_section_title_reflects_applied_and_open_questions(tmp_path):
    _,p=state(tmp_path)
    question=p['questions'][0]
    question.update(status='answered',answer='已核对答案。',understanding_status='applied',
                    applied_requirement_ids=['REQ-0001'])
    result=compile_plan(v2(p,context=[],normative=['REQ-0001']),p,'prd')
    apply_response(p,result)
    view=reader_document(p['documents']['prd'])
    assert not any(s['title']=='当前决定' for s in view['sections'])
    assert question['answer'] in dumps(p['documents']['prd']['content'])
    assert question['answer'] not in dumps(view)
    artifact=p['documents']['prd']
    open_question=dict(question,id='Q-OPEN',status='open',answer=None,
                       question='仍需决定的边界？',superseded_by=[])
    artifact['question_snapshot'].append(open_question)
    answer_section=next(s for s in artifact['content']['sections'] if s['title']=='已有回答与未决问题')
    answer_section['blocks'].append(dict(kind='open_question',ref_ids=['Q-OPEN'],
                                           text='仍需决定的边界？'))
    view=reader_document(artifact)
    assert any(s['title']=='未决产品事项' for s in view['sections'])
    assert '仍需决定的边界？' in dumps(view)


@pytest.mark.parametrize('kind', ['mrd', 'prd'])
def test_selected_goal_and_candidate_constraint_keep_their_actual_identity(tmp_path, kind):
    p=checked_project(tmp_path)
    goal=copy.deepcopy(p['items'][0])
    goal.update(id='GOAL-SELECTED',kind='goal',selection_status='selected',
                statement='减少人工整理时间。',source_refs=[],applies_to='reference')
    constraint=copy.deepcopy(goal)
    constraint.update(id='ITEM-CANDIDATE',kind='non_goal',selection_status='candidate',
                      statement=p['product_context']['preserve_scope'])
    p['items'] += [goal,constraint]
    before=copy.deepcopy(p)
    result=compile_plan(v2(p,discussion=[goal['id'],constraint['id']]),p,kind)
    assert p==before
    assert all('不作为本期要求' not in s['title'] for s in result['result']['sections'])
    assert not {goal['id'],constraint['id']} & {c['item_id'] for c in result['result']['coverage']}
    apply_response(p,result)
    view=reader_document(p['documents'][kind])
    assert goal['id'] not in dumps(view) and goal['statement'] not in dumps(view)
    assert constraint['id'] not in dumps(view)
    assert goal['id'] in dumps(p['documents'][kind]['content'])
    assert constraint['id'] in dumps(p['documents'][kind]['content'])
    assert any('保持不变：'+before['product_context']['preserve_scope'] in b.get('text','')
               for s in view['sections'] for b in s['blocks'])
    assert p['items']==before['items']
    assert p['questions'][0]['question'] in dumps(view)


def test_legacy_appendix_heading_is_neutral_without_mutating_old_artifact(tmp_path):
    _,p=state(tmp_path)
    result=compile_plan(v2(p,context=[],normative=['REQ-0001']),p,'prd')
    apply_response(p,result)
    artifact=p['documents']['prd']
    appendix=next(s for s in artifact['content']['sections'] if s['title'].startswith('附录：'))
    appendix['title']='附录：未采纳与历史讨论（不作为本期要求）'
    appendix['blocks'].append(dict(kind='narrative',ref_ids=[],text='旧版补充说明原文，不能丢失。'))
    before=copy.deepcopy(artifact)
    view=reader_document(artifact)
    assert artifact==before
    assert all('不作为本期要求' not in s['title'] for s in view['sections'])
    assert '旧版补充说明原文，不能丢失。' not in dumps(view)
    assert '候选，尚未采纳：方案建议：' not in dumps(view)
    assert '旧版补充说明原文，不能丢失。' in dumps(artifact['content'])
    assert 'REQ-CANDIDATE' in dumps(artifact['content'])
    assert p['items'][-1]['statement'] in dumps(artifact['content'])
