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
    view=dumps(reader_document(p['documents']['prd']))
    assert p['questions'][0]['question'] in view and '附图无法辨认' in view
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
    assert '独立条目尚未采纳（不改变已核对的产品上下文）：' in view
    assert '已拒绝：' in view
    assert '已暂缓：' in view
    assert '待纳入本期' not in view


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
    appendix=next(s for s in view['sections'] if s['title']=='附录：未采纳与历史讨论（不作为本期要求）')
    blocks={i['id']:[b['text'] for s in view['sections'] for b in s['blocks']
            if b['ref_ids']==[i['id']]] for i in copies}
    for item in copies:
        expected='本草稿已拒绝' if item['selection_status']=='rejected' else '本草稿已暂缓'
        assert all(t.startswith(item['id']+'｜') for t in blocks[item['id']])
        assert any(item['statement'] in t and expected in t and
                   '规范原文与 '+stable['id']+' 相同，以该已采纳条目为准' in t
                   for t in blocks[item['id']])
        assert len(blocks[item['id']])==1
        assert any(b['ref_ids']==[item['id']] for b in appendix['blocks'])
        assert not any(b['ref_ids']==[item['id']] for s in view['sections'] if s is not appendix
                       for b in s['blocks'])


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
    blocks={b['ref_ids'][0]:b['text'] for s in view['sections'] for b in s['blocks']
            if len(b['ref_ids'])==1 and b['ref_ids'][0] in {i['id'] for i in copies}}
    for item in copies:
        assert blocks[item['id']].startswith(item['id']+'｜已拒绝：')
        assert item['statement'] in blocks[item['id']]
        assert '规范原文与 ' not in blocks[item['id']]


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
    blocks={b['ref_ids'][0]:b['text'] for s in view['sections'] for b in s['blocks']
            if len(b['ref_ids'])==1 and b['ref_ids'][0] in (same['id'],changed['id'])}
    assert blocks[same['id']].startswith(same['id']+'｜已拒绝：')
    assert ('验收预期原文与已采纳 '+stable['id']+' 相同；本独立草稿已拒绝，'
            '不撤销 '+stable['id']+' 的验收要求；两条分类记录不同（new/unspecified）') in blocks[same['id']]
    assert changed['statement'] in blocks[changed['id']]
    assert '验收预期原文与已采纳' not in blocks[changed['id']]


def test_discussion_only_plan_places_candidate_in_explicit_appendix(tmp_path):
    _,p=state(tmp_path)
    candidate=copy.deepcopy(p['items'][0]);candidate.update(id='REQ-HISTORY',
        selection_status='rejected',statement='曾讨论但拒绝的独立要求。')
    p['items'].append(candidate)
    plan=dict(plan_version='2',sections=[dict(section_key='discussion',context_refs=[],
        normative_refs=[],discussion_refs=[candidate['id']])])
    result=compile_plan(plan,p,'prd')['result']
    appendix=next(s for s in result['sections'] if s['title']=='附录：未采纳与历史讨论（不作为本期要求）')
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
    appendix=next(s for s in sections if s['title']=='附录：未采纳与历史讨论（不作为本期要求）')
    main=sections[0]
    main['blocks'].extend(appendix['blocks'])
    main['blocks'].append(dict(kind='narrative',ref_ids=[],
        text='管理员不得修改联系人权限；此旧叙述须原样送审。'))
    sections.remove(appendix)
    before=copy.deepcopy(artifact)
    view=reader_document(artifact)
    business=next(s for s in view['sections'] if s['section_id']==main['section_id'])
    discussion=next(s for s in view['sections'] if s['title']=='附录：未采纳与历史讨论（不作为本期要求）')
    assert artifact==before
    assert any('此旧叙述须原样送审' in b['text'] for b in business['blocks'])
    assert not any(b['ref_ids']==[candidate['id']] for b in business['blocks'])
    assert any(b['ref_ids']==[candidate['id']] and candidate['statement'] in b['text']
               for b in discussion['blocks'])


def test_answer_section_title_reflects_applied_and_open_questions(tmp_path):
    _,p=state(tmp_path)
    question=p['questions'][0]
    question.update(status='answered',answer='已核对答案。',understanding_status='applied',
                    applied_requirement_ids=['REQ-0001'])
    result=compile_plan(v2(p,context=[],normative=['REQ-0001']),p,'prd')
    apply_response(p,result)
    view=reader_document(p['documents']['prd'])
    assert any(s['title']=='当前决定' for s in view['sections'])
    artifact=p['documents']['prd']
    artifact['question_snapshot'].append(dict(question,id='Q-OPEN',status='open',answer=None,
                                               question='仍需决定的边界？',superseded_by=[]))
    view=reader_document(artifact)
    assert any(s['title']=='当前决定与未决事项' for s in view['sections'])
