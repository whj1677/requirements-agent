"""A saved QG sentence may bind a candidate; only human review may apply it."""
import copy
import json

import pytest

from app.core import Problem, digest
from app.decision_review import apply_review, build_preview, save_raw_answer
from app.provider import DEFAULT, answer_binding_tasks, assemble
from app.store import Store
from app.understanding import (accept_answer_update, annotate_candidates,
                               current_decision_answer, validate)
from tests.helpers import prepared


def group_project(tmp_path):
    store=Store(tmp_path)
    project=prepared(store)
    project['workflow_mode']='shared-draft'
    original=project['questions'][0]
    project['questions']=[dict(original,id='Q-boundary',question='相接边界是否允许？',
        related_refs=['RULE-0001'],decision_group_id='QG-boundary',
        decision_group_title='相接和例外处理',decision_coverage='pending'),
        dict(original,id='Q-exception',question='例外如何处理？',
        related_refs=['RULE-0001'],decision_group_id='QG-boundary',
        decision_group_title='相接和例外处理',decision_coverage='pending')]
    row=save_raw_answer(project,'QG-boundary','相接边界允许。',store)
    return store,project,row


def revision(project,row):
    return dict(id='C-boundary',temp_id='TMP-boundary',kind='rule',action='revise',
        target_item_id='RULE-0001',title='相接边界规则修订',
        statement='相接边界允许。',applies_to='to_be',epistemic_status='reported',
        source_refs=copy.deepcopy(row['source_refs']),related_refs=['REQ-0001'],
        answer_refs=['Q-boundary'],selection_status='candidate')


def preview(project,row,candidate):
    return build_preview(project,dict(expected_revision=project['revision'],
        question_id='QG-boundary',answer_id=row['id'],coverage=[
            dict(decision_id='Q-boundary',status='clear',answer_quote='相接边界允许。'),
            dict(decision_id='Q-exception',status='pending',answer_quote='')],
        groups=[dict(id='G-boundary',title='核对相接规则',
            decision_ids=['Q-boundary'],candidate_item_ids=[candidate['id']],
            target_item_ids=['RULE-0001'],depends_on=[])]))


def test_saved_qg_answer_creates_bindable_candidate_but_leaves_stay_open(tmp_path):
    _,project,row=group_project(tmp_path)
    leaf=project['questions'][0]
    assert leaf['status']=='open' and leaf['answer'] is None
    assert current_decision_answer(project,leaf) is row
    tasks=answer_binding_tasks(project)
    assert {task['question_id'] for task in tasks}=={'Q-boundary','Q-exception'}
    assert all(task['binding_mode']=='saved_group_sentence_unreviewed'
               and not task['requires_candidate'] for task in tasks)
    assert all(task['answer_id']==row['id'] and task['answer_version']==row['answer_hash']
               for task in tasks)
    messages,_,_=assemble(project,'clarify','只核对相接边界决定。',DEFAULT,tmp_path)
    context=json.loads(messages[1]['content'][0]['text'])
    assert context['decision_answers'][0]['id']==row['id']
    assert {task['question_id'] for task in context['stage_focus']['answer_binding_tasks']}=={
        'Q-boundary','Q-exception'}
    assert any(target['question_id']=='Q-boundary'
               for target in context['stage_focus']['answer_revision_targets'])
    candidate=revision(project,row)
    excerpt=next(e for s in project['sources'] if s['id']==row['source_refs'][0]['source_id']
                 for e in s['excerpts'] if e['id']==row['source_refs'][0]['excerpt_id'])
    validate(dict(stage='clarify',proposals=[candidate],questions=[]),project,[excerpt])
    annotate_candidates(project,[candidate])
    assert candidate['decision_answer_bindings']=={
        'Q-boundary':dict(answer_id=row['id'],answer_hash=row['answer_hash'])}
    assert candidate['answer_versions']=={}
    project['items'].append(candidate)
    assert answer_binding_tasks(project)[0]['reusable_candidate_ids']==['C-boundary']
    with pytest.raises(Problem,match='人工核对明确覆盖'):
        accept_answer_update(project,candidate)
    view=preview(project,row,candidate)
    assert [d['coverage'] for d in view['decisions']]==['clear','pending']
    assert project['questions'][0]['status']=='open' and project['questions'][1]['status']=='open'
    result=apply_review(project,view,['G-boundary'])
    assert result['applied_group_ids']==['G-boundary']
    assert project['questions'][0]['status']=='answered'
    assert project['questions'][0]['answer']=='相接边界允许。'
    assert project['questions'][1]['status']=='open' and project['questions'][1]['answer'] is None
    assert candidate['selection_status']=='deferred'


def test_qg_candidate_rejects_old_answer_version_or_changed_group_sentence(tmp_path):
    _,project,row=group_project(tmp_path)
    candidate=revision(project,row)
    candidate['answer_versions']={'Q-boundary':digest(None)}
    project['items'].append(candidate)
    with pytest.raises(Problem,match='未绑定当前已保存原话'):
        preview(project,row,candidate)
    annotate_candidates(project,[candidate])
    assert preview(project,row,candidate)['groups'][0]['candidate_item_ids']==['C-boundary']
    newer=save_raw_answer(project,'QG-boundary','相接边界不允许。')
    assert newer['id']!=row['id']
    assert answer_binding_tasks(project)[0]['reusable_candidate_ids']==[]
    with pytest.raises(Problem):
        preview(project,newer,candidate)
    with pytest.raises(Problem):
        accept_answer_update(project,candidate)


def test_qg_binding_requires_all_leaves_on_same_current_record(tmp_path):
    _,project,row=group_project(tmp_path)
    project['questions'][1]['decision_answer_id']='DA-not-current'
    assert current_decision_answer(project,project['questions'][0]) is None
    assert answer_binding_tasks(project)==[]
    candidate=revision(project,row)
    with pytest.raises(Problem,match='没有当前有效的已保存回答'):
        validate(dict(stage='clarify',proposals=[candidate],questions=[]),project,[])
