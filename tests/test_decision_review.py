"""Isolated decision-review regressions; all answers and rules are synthetic."""
import copy

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.core import Problem
from app.decision_review import (append_model_proposal, build_preview,
                                 review_question_hash)
from app.decision_routes import register
from app.store import Store
from app.understanding import coverage_complete, question_ids, save_questions
from app.core import digest
from tests.helpers import prepared


def setup_group(store):
    project=prepared(store)
    with store.edit(project['id'],project['revision'],'合成三决定') as (draft,_):
        old=draft['questions'].pop()
        for index, question in enumerate(('电价能否追溯生效？','迟到电量用哪个电价版本？','何时重算迟到电量？')):
            draft['questions'].append(dict(old,id='Q-part-'+str(index),question=question,
                decision_group_id='QG-price',decision_group_title='电价追溯、选价和重算',
                decision_coverage='pending',status='open',answer=None))
    return store.get(project['id'])


def api(store):
    app=FastAPI()
    @app.exception_handler(Problem)
    async def problem(_,exc):
        return JSONResponse(status_code=exc.status,content={'code':exc.code,'message':exc.message})
    register(app,store)
    return TestClient(app)


def answer_and_preview(client, project, groups=None):
    pid=project['id']
    response=client.post(f'/api/projects/{pid}/decision-review/answer',json=dict(
        expected_revision=project['revision'],question_id='QG-price',answer='新电价只能未来生效。'))
    assert response.status_code==200,response.text
    saved=response.json()
    project=saved['project']
    coverage=[dict(decision_id='Q-part-'+str(index),status='clear' if index==0 else 'pending',
                   answer_quote='新电价只能未来生效。' if index==0 else '') for index in range(3)]
    response=client.post(f'/api/projects/{pid}/decision-review',json=dict(
        expected_revision=project['revision'],question_id='QG-price',answer_id=saved['answer_id'],
        coverage=coverage,groups=groups or []))
    assert response.status_code==200,response.text
    return project,response.json()


def commit(client,project,preview,selected=None,key='review-key-0001'):
    return client.post(f'/api/projects/{project["id"]}/decision-review/commit',json=dict(
        expected_revision=project['revision'],preview_id=preview['preview_id'],
        preview_hash=preview['preview_hash'],selected_group_ids=selected or [],
        idempotency_key=key,review_confirmed=True))


def test_parent_group_never_aliases_first_child():
    parent=dict(temp_id='QTMP-parent',topic_key='price',question='三项决定',
        decision_points=[dict(temp_id='QTMP-a',topic_key='price-a',question='是否追溯？',
                              related_refs=[],blocking=True,options=[]),
                         dict(temp_id='QTMP-b',topic_key='price-b',question='如何选价？',
                              related_refs=[],blocking=True,options=[])])
    mapping=question_ids(dict(questions=[]),[parent],{})
    assert 'QTMP-parent' not in mapping
    assert mapping['QTMP-a'] != mapping['QTMP-b']


def test_flattened_children_share_stable_group_without_parent_answer_record():
    project=dict(questions=[])
    parent=dict(temp_id='QTMP-parent',question='电价决定组',decision_points=[
        dict(temp_id='Q-a',question='能否追溯？',related_refs=['REQ-a'],blocking=True),
        dict(temp_id='Q-b',question='历史如何选价？',related_refs=['REQ-a'],blocking=True)])
    save_questions(project,[parent])
    assert len(project['questions'])==2
    gid=project['questions'][0]['decision_group_id']
    assert gid.startswith('QG-') and project['questions'][1]['decision_group_id']==gid
    assert all(q['id']!='QTMP-parent' for q in project['questions'])
    save_questions(project,[parent])
    assert len(project['questions'])==2
    assert all(q['decision_group_id']==gid for q in project['questions'])


def test_partial_answer_only_covers_one_child_and_repeat_returns_receipt(tmp_path):
    store=Store(tmp_path)
    project=setup_group(store)
    with api(store) as client:
        project,preview=answer_and_preview(client,project)
        assert [d['coverage'] for d in preview['decisions']]==['clear','pending','pending']
        first=commit(client,project,preview)
        assert first.status_code==200,first.text
        result=first.json()
        q={q['id']:q for q in result['project']['questions']}
        assert q['Q-part-0']['status']=='answered'
        assert coverage_complete(q['Q-part-0'])
        assert all(q['Q-part-'+str(i)]['status']=='open' for i in (1,2))
        assert result['receipt']['pending_decision_ids']==['Q-part-1','Q-part-2']
        repeat=commit(client,project,preview)
        assert repeat.status_code==200 and repeat.json()['repeated'] is True
        assert repeat.json()['receipt']['id']==result['receipt']['id']


def test_new_group_raw_answer_reopens_prior_child_without_answering_siblings(tmp_path):
    store=Store(tmp_path)
    project=setup_group(store)
    with api(store) as client:
        project,preview=answer_and_preview(client,project)
        committed=commit(client,project,preview)
        assert committed.status_code==200
        current=committed.json()['project']
        assert next(q for q in current['questions'] if q['id']=='Q-part-0')['status']=='answered'
        second=client.post(f'/api/projects/{project["id"]}/decision-review/answer',json=dict(
            expected_revision=current['revision'],question_id='QG-price',
            answer='下一轮新原话，仍未说明历史选价。'))
        assert second.status_code==200,second.text
    questions={q['id']:q for q in second.json()['project']['questions']}
    assert all(questions['Q-part-'+str(index)]['status']=='open' for index in range(3))
    assert questions['Q-part-0']['answer'] is None
    assert questions['Q-part-0']['answer_history'][-1]['answer']=='新电价只能未来生效。'
    assert all(q['decision_coverage']=='pending' for q in questions.values())


def test_second_target_conflict_writes_nothing_and_stale_preview_rejected(tmp_path):
    store=Store(tmp_path)
    project=setup_group(store)
    groups=[dict(id='G-both',title='权限和验收一起核对',decision_ids=['Q-part-0'],
                 candidate_item_ids=[],target_item_ids=['REQ-0001','RULE-0001'],depends_on=[])]
    with api(store) as client:
        project,preview=answer_and_preview(client,project,groups)
        with store.edit(project['id'],project['revision'],'同时编辑第二目标',bump=False) as (draft,_):
            next(i for i in draft['items'] if i['id']=='RULE-0001')['statement']='另一个编辑者的最新规则。'
        before=store.get(project['id'])
        failed=commit(client,project,preview,['G-both'])
        assert failed.status_code==409 and failed.json()['code']=='STALE_CONTENT'
        assert store.get(project['id'])==before
        assert not store.records(project['id'],'decision_receipt')
        with store.edit(project['id'],before['revision'],'再修改项目') as (draft,_):
            draft['name']='版本已前进'
        stale=commit(client,project,preview,['G-both'],key='review-key-0002')
        assert stale.status_code==409 and stale.json()['code']=='STALE_REVISION'


def test_group_preview_rejects_leaf_change_at_same_revision(tmp_path):
    store=Store(tmp_path)
    project=setup_group(store)
    with api(store) as client:
        project,preview=answer_and_preview(client,project)
        assert preview['snapshot']['question_hash'] is None
        assert preview['snapshot']['question_group_hash']
        with store.edit(project['id'],project['revision'],'并发修改子决定',bump=False) as (draft,_):
            next(q for q in draft['questions'] if q['id']=='Q-part-1')['question']='迟到电量改用哪个版本？'
        current=store.get(project['id'])
        failed=commit(client,project,preview)
        assert failed.status_code==409 and failed.json()['code']=='STALE_QUESTION'
        assert store.get(project['id'])==current
        assert not store.records(project['id'],'decision_receipt')


def test_preexisting_preview_without_group_hash_remains_committable(tmp_path):
    store=Store(tmp_path)
    project=setup_group(store)
    with api(store) as client:
        project,preview=answer_and_preview(client,project)
        old=copy.deepcopy(preview)
        old.pop('preview_id')
        old['snapshot'].pop('question_group_hash')
        old['preview_hash']=digest({key:value for key,value in old.items()
                                    if key!='preview_hash'})
        old['preview_id']=store.record(project['id'],'decision_preview',old)
        result=commit(client,project,old,key='old-preview-compat')
        assert result.status_code==200,result.text
        repeated=commit(client,project,old,key='old-preview-compat')
        assert repeated.status_code==200 and repeated.json()['repeated'] is True


def test_unselected_independent_group_remains_pending(tmp_path):
    store=Store(tmp_path)
    project=setup_group(store)
    groups=[dict(id='G-one',title='需求',decision_ids=['Q-part-0'],
                 candidate_item_ids=[],target_item_ids=['REQ-0001'],depends_on=[]),
            dict(id='G-two',title='规则',decision_ids=['Q-part-0'],
                 candidate_item_ids=[],target_item_ids=['RULE-0001'],depends_on=[])]
    with api(store) as client:
        project,preview=answer_and_preview(client,project,groups)
        result=commit(client,project,preview,['G-one'])
        assert result.status_code==200,result.text
        body=result.json()
    q=next(q for q in body['project']['questions'] if q['id']=='Q-part-0')
    assert q['decision_coverage']=='clear' and q['understanding_status']=='pending'
    assert q['decision_pending_group_ids']==['G-two']
    assert body['receipt']['pending_group_ids']==['G-two']


def test_legacy_answer_requires_explicit_mapping_and_keeps_original(tmp_path):
    store=Store(tmp_path)
    project=prepared(store)
    with store.edit(project['id'],project['revision'],'合成旧复合题') as (draft,_):
        q=draft['questions'][0]
        q.update(question='边界是否相接，历史如何选价？',status='answered',
                 answer='边界相接允许。历史选价待定。',answered_at='2026-01-01')
    project=store.get(project['id'])
    original=copy.deepcopy(project['questions'][0])
    legacy=[dict(question='边界是否相接？',related_refs=['REQ-0001'],status='clear',
                 answer_quote='边界相接允许。'),
            dict(question='历史如何选价？',related_refs=['REQ-0001'],status='pending',
                 answer_quote='')]
    preview=build_preview(project,dict(expected_revision=project['revision'],
        question_id='Q-0001',legacy_decisions=legacy,groups=[]))
    assert project['questions'][0]==original  # Preview does not migrate history.
    with api(store) as client:
        preview_id=store.record(project['id'],'decision_preview',preview)
        result=client.post(f'/api/projects/{project["id"]}/decision-review/commit',json=dict(
            expected_revision=project['revision'],preview_id=preview_id,
            preview_hash=preview['preview_hash'],selected_group_ids=[],
            idempotency_key='legacy-key-0001',review_confirmed=True))
        assert result.status_code==200,result.text
    saved=store.get(project['id'])
    old=next(q for q in saved['questions'] if q['id']=='Q-0001')
    assert old['answer']==original['answer'] and old['answered_at']==original['answered_at']
    assert len(old['superseded_by'])==2
    children=[q for q in saved['questions'] if q['id'] in old['superseded_by']]
    assert sorted(q['status'] for q in children)==['answered','open']
    assert next(q for q in children if q['status']=='answered')['answer']=='边界相接允许。'


def test_legacy_mapping_creates_group_answer_identity_without_changing_old_record(tmp_path):
    from app.decision_semantics import build_request
    from app.decision_review import apply_review
    from app.understanding import current_decision_answer

    project=prepared(Store(tmp_path))
    project['workflow_mode']='shared-draft'
    parent=project['questions'][0]
    parent.update(question='边界是否相接，历史如何选价？',status='answered',
                  answer='边界相接允许。历史选价待定。')
    old_answer=dict(id='DA-old',question_id=parent['id'],decision_ids=[parent['id']],
                    text=parent['answer'],answer_hash=digest(parent['answer']),
                    source_refs=copy.deepcopy(project['items'][0]['source_refs']))
    project['decision_answers']=[old_answer]
    parent['decision_answer_id']=old_answer['id']
    before=copy.deepcopy(project)
    legacy=[dict(question='边界是否相接？',related_refs=['REQ-0001'],status='clear',
                 answer_quote='边界相接允许。'),
            dict(question='历史如何选价？',related_refs=['REQ-0001'],status='pending',
                 answer_quote='')]
    view=build_preview(project,dict(expected_revision=project['revision'],
        question_id=parent['id'],answer_id=old_answer['id'],
        legacy_decisions=legacy,groups=[]))
    apply_review(project,view,[])
    assert project['decision_answers'][0]==old_answer
    group_id=project['questions'][1]['decision_group_id']
    leaves=[q for q in project['questions'] if q.get('decision_group_id')==group_id]
    assert len(leaves)==2
    mapped=project['decision_answers'][1]
    assert mapped['question_id']==group_id and mapped['decision_ids']==[q['id'] for q in leaves]
    assert mapped['text']==old_answer['text'] and mapped['answer_hash']==old_answer['answer_hash']
    assert mapped['mapped_from_answer_id']==old_answer['id']
    assert all(q['decision_answer_id']==mapped['id']
               and current_decision_answer(project,q)==mapped for q in leaves)
    assert sorted(q['status'] for q in leaves)==['answered','open']
    sent=copy.deepcopy(project['sources'][0]['excerpts'][0])
    plan=build_request(project,before,[sent])
    assert any(target['question_id']==group_id for target in plan['targets'])


def test_saved_raw_sentence_is_not_reviewed_coverage_and_model_suggestion_is_not_approval(tmp_path):
    store=Store(tmp_path)
    project=prepared(store)
    question=project['questions'][0]
    with api(store) as client:
        saved=client.post(f'/api/projects/{project["id"]}/decision-review/answer',json=dict(
            expected_revision=project['revision'],question_id=question['id'],
            answer='目前只能未来生效。')).json()
    p=saved['project'];q=p['questions'][0]
    assert q['status']=='answered' and not coverage_complete(q)
    assert q['answer_source_refs'] and q['decision_answer_id']==saved['answer_id']
    proposal=dict(question_id=q['id'],answer_id=saved['answer_id'],
        question_hash=review_question_hash(p,q['id']),answer_hash=digest(q['answer']),
        decisions=[dict(id=q['id'],question=q['question'],coverage='clear',
                        answer_quote='目前只能未来生效。',related_refs=q['related_refs'],why='合成建议')])
    assert append_model_proposal(p,proposal) is True
    assert not coverage_complete(p['questions'][0])
    stale=copy.deepcopy(proposal);stale['answer_hash']='wrong'
    assert append_model_proposal(p,stale) is False
    assert p['questions'][0]['decision_review_proposal']['answer_hash']==digest(q['answer'])


def test_legacy_compound_model_suggestion_blocks_automatic_coverage_without_migration(tmp_path):
    store=Store(tmp_path)
    project=prepared(store)
    with store.edit(project['id'],project['revision'],'合成旧回答') as (draft,_):
        draft['questions'][0].update(status='answered',answer='只允许未来生效。选价待定。')
    project=store.get(project['id'])
    q=project['questions'][0]
    assert coverage_complete(q)  # Compatibility until a current, specific audit exists.
    proposal=dict(question_id=q['id'],question_hash=review_question_hash(project,q['id']),
        answer_hash=digest(q['answer']),decisions=[
            dict(id='proposed-price',question='能否追溯？',coverage='clear',
                 answer_quote='只允许未来生效。'),
            dict(id='proposed-history',question='如何选价？',coverage='pending',answer_quote='')])
    assert append_model_proposal(project,proposal)
    assert not coverage_complete(q)
    assert len(project['questions'])==1 and q.get('superseded_by') is None
