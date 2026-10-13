"""Exact, human-reviewed answer locations; no model or daily workspace data."""
import copy

import pytest

from app.answer_implementation import (derive_changed_witnesses, validate_implementations,
    validate_implementation_map)
from app.core import Problem, digest
from app.decision_review import apply_review, build_preview, save_raw_answer
from app.manual_decision_review import build_manual_preview
from app.understanding import (application_hash, applied_targets, confirm_manual_answer,
    invalidate_answer_applications, question_fingerprint)


def project():
    item=dict(id='REQ-1',kind='requirement',title='录入核对',statement='用户核对识别结果后保存。',
              behavior=dict(flow='核对后保存；补录规则另待确认。'),selection_status='selected',
              applies_to='to_be',source_refs=[],related_refs=[])
    question=dict(id='Q-1',question='识别结果是否需要核对？',why='影响保存行为',
                  related_refs=['REQ-1'],source_refs=[],status='answered',answer='需要核对后保存。',
                  understanding_status='pending',blocking=True)
    return dict(id='P-1',revision=1,items=[item],questions=[question],sources=[])


def witnesses(p, field='statement'):
    return [dict(item_id='REQ-1',field=field,quote=p['items'][0]['statement'] if field=='statement'
                 else p['items'][0]['behavior'][field[9:]])]


def confirm(p, rows=None):
    q=p['questions'][0]
    return confirm_manual_answer(p,q,digest(q['answer']),
        {item['id']:application_hash(item) for item in p['items'] if item['id'] in q['related_refs']},True,
        answer_implementations=None if rows is None else {q['id']:rows})


def test_existing_original_can_carry_answer_without_text_change():
    p=project();original=copy.deepcopy(p['items'])
    confirm(p,witnesses(p))
    q=p['questions'][0];record=q['answer_applications']['REQ-1']
    assert p['items']==original and q['understanding_status']=='applied'
    assert record['contract_version']==2 and record['question_hash']==question_fingerprint(q)
    assert record['witnesses']==[dict(field='statement',quote=original[0]['statement'])]
    assert applied_targets(p,q)=={'REQ-1'}


def test_nonempty_answer_and_checkbox_without_location_cannot_mark_applied():
    p=project();before=copy.deepcopy(p)
    with pytest.raises(Problem,match='请选择回答已落实') as error:
        confirm(p)
    assert error.value.code=='ANSWER_IMPLEMENTATION_REQUIRED' and p==before


@pytest.mark.parametrize('row,code',[
    (dict(item_id='REQ-other',field='statement',quote='用户核对识别结果后保存。'),'ANSWER_IMPLEMENTATION_INVALID'),
    (dict(item_id='REQ-1',field='title',quote='录入核对'),'ANSWER_IMPLEMENTATION_INVALID'),
    (dict(item_id='REQ-1',field='statement',quote='未经核对直接保存'),'ANSWER_IMPLEMENTATION_STALE'),
    (dict(item_id='REQ-1',field='statement',quote=' '),'ANSWER_IMPLEMENTATION_INVALID'),
])
def test_foreign_field_or_invented_original_rejected(row,code):
    p=project()
    with pytest.raises(Problem) as error:
        validate_implementations(p,p['questions'][0],[row])
    assert error.value.code==code


def test_all_direct_targets_need_witness_and_acceptance_only_allows_statement():
    p=project();q=p['questions'][0]
    ac=dict(p['items'][0],id='AC-1',kind='acceptance',statement='核对识别结果后保存为记录。')
    p['items'].append(ac);q['related_refs'].append('AC-1')
    with pytest.raises(Problem) as error:
        validate_implementations(p,q,witnesses(p))
    assert error.value.code=='ANSWER_IMPLEMENTATION_REQUIRED'
    with pytest.raises(Problem) as error:
        validate_implementations(p,q,witnesses(p)+[dict(item_id='AC-1',field='behavior.flow',quote=ac['behavior']['flow'])])
    assert error.value.code=='ANSWER_IMPLEMENTATION_INVALID'
    confirm(p,witnesses(p)+[dict(item_id='AC-1',field='statement',quote=ac['statement'])])
    assert applied_targets(p,q)=={'REQ-1','AC-1'}


def test_only_explicit_same_question_unknown_anchor_blocks():
    p=project();q=p['questions'][0]
    # Another decision's unknown in this same field does not invalidate this answer.
    assert validate_implementations(p,q,witnesses(p,'behavior.flow'))
    note='识别结果是否核对，资料未说明。'
    p['items'][0]['behavior']['flow']+='\n'+note
    q['unresolved_locations']=[dict(item_id='REQ-1',field='behavior.flow',quote=note)]
    with pytest.raises(Problem) as error:
        validate_implementations(p,q,witnesses(p))
    assert error.value.code=='ANSWER_IMPLEMENTATION_UNRESOLVED'
    p['items'][0]['behavior']['flow']=p['items'][0]['behavior']['flow'].replace(note,'')
    assert validate_implementations(p,q,witnesses(p))


@pytest.mark.parametrize('change',['answer','question','clause','anchor'])
def test_v2_records_invalidate_with_changed_answer_question_clause_or_anchor(change):
    p=project();confirm(p,witnesses(p));before=copy.deepcopy(p);q=p['questions'][0]
    if change=='answer':q['answer']='不用核对。'
    elif change=='question':q['question']='另一种识别结果是否核对？'
    elif change=='clause':p['items'][0]['statement']='不核对直接保存。'
    else:q['unresolved_locations']=[dict(item_id='REQ-1',field='behavior.flow',quote='补录规则另待确认。')]
    invalidate_answer_applications(p,before)
    assert not applied_targets(p,q) and q['understanding_status']=='pending'


def test_legacy_record_and_fingerprint_are_not_migrated_on_read():
    p=project();q=p['questions'][0]
    q.update(applied_item_ids=['REQ-1'],understanding_status='applied',answer_applications={
        'REQ-1':dict(answer_hash=digest(q['answer']),clause_hash=application_hash(p['items'][0]))})
    before=copy.deepcopy(p)
    expected=digest({key:q.get(key) for key in ('id','topic_key','question','why','options','blocking',
        'blocking_stage','related_refs','source_refs','decision_group_id')})
    assert question_fingerprint(q)==expected and applied_targets(p,q)=={'REQ-1'} and p==before


@pytest.mark.parametrize('stored',[None, [None], [dict(field='statement',quote='x',item_id='REQ-1')]])
def test_malformed_v2_witness_is_invalid_instead_of_crashing_read(stored):
    p=project();confirm(p,witnesses(p));q=p['questions'][0]
    q['answer_applications']['REQ-1']['witnesses']=stored
    assert not applied_targets(p,q)


def decision_payload(p,**extra):
    q=p['questions'][0]
    body=dict(expected_revision=p['revision'],question_id=q['id'],
              coverage=[dict(decision_id=q['id'],status='clear',answer_quote=q['answer'])],
              groups=[dict(id='review',decision_ids=[q['id']],target_item_ids=['REQ-1'],candidate_item_ids=[])])
    body.update(extra);return body


def test_decision_preview_with_unchanged_original_keeps_answer_but_pending():
    p=project();save_raw_answer(p,'Q-1',p['questions'][0]['answer'])
    view=build_preview(p,decision_payload(p));before=copy.deepcopy(p['items'])
    assert view['implementation_pending_ids']==['Q-1']
    assert view['implementation_issues'][0]['code']=='ANSWER_IMPLEMENTATION_REQUIRED'
    result=apply_review(p,view,['review'])
    assert result['implementation_pending_ids']==['Q-1']
    assert p['items']==before and p['questions'][0]['status']=='answered'
    assert p['questions'][0]['understanding_status']=='pending' and not applied_targets(p,p['questions'][0])


def test_explicit_unchanged_preview_and_manual_diff_use_same_final_witness_guard():
    p=project();save_raw_answer(p,'Q-1',p['questions'][0]['answer'])
    view=build_preview(p,decision_payload(p,answer_implementations={'Q-1':witnesses(p)}))
    assert not view['implementation_pending_ids']
    result=apply_review(p,view,['review'])
    assert result['implementation_pending_ids']==[]
    assert p['questions'][0]['understanding_status']=='applied'
    p=project();save_raw_answer(p,'Q-1',p['questions'][0]['answer'])
    text='用户核对识别结果后保存，并展示记录。'
    view=build_manual_preview(p,dict(expected_revision=p['revision'],question_id='Q-1',
                                    edits=[dict(item_id='REQ-1',statement=text)]))
    assert view['answer_implementations']['Q-1']==[dict(item_id='REQ-1',field='statement',quote=text)]
    assert not view['implementation_pending_ids']
    # Without a store, committing an authored edit is forbidden; preview remains read-only.
    assert p['items'][0]['statement']!=text


def test_diff_is_only_visible_nonempty_statement_or_behavior_words():
    p=project();before=p['items'][0];after=dict(copy.deepcopy(before),title='改名')
    assert derive_changed_witnesses(before,after)==[]
    after['behavior']['flow']='核对后保存并可查看新记录。'
    assert derive_changed_witnesses(before,after)==[dict(item_id='REQ-1',field='behavior.flow',quote=after['behavior']['flow'])]
    with pytest.raises(Problem):validate_implementation_map(p,{'Q-other':witnesses(p)})
