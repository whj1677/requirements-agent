"""Offline structural checks, not proof that a model understands business rules."""
import copy
import json
from pathlib import Path

import jsonschema
import pytest

from app.core import Problem, digest
from app.decision_semantics import apply_audit, build_request, proposal_current
from app.decision_review import append_model_proposal, review_question_hash
from app.store import Store
from app.understanding import application_hash, confirm_manual_answer, coverage_complete
from tests.helpers import prepared


def new_question(qid,text):
    return dict(id=qid,topic_key=qid,question=text,why='合成未知',options=[],
                blocking=True,blocking_stage=3,related_refs=['REQ-0001'],
                source_refs=[dict(source_id='SRC-0001',excerpt_id='EX-0001')],
                status='open',answer=None)


def request(staged,before):
    excerpt=copy.deepcopy(staged['sources'][0]['excerpts'][0])
    return build_request(staged,before,[excerpt])


def row(identity,question,status='pending',quote=''):
    return dict(id=identity,question=question,coverage=status,answer_quote=quote,
                related_refs=['REQ-0001'],why='按可独立回答的决定核对')


def test_single_question_mark_can_propose_three_new_decisions(tmp_path):
    before=prepared(Store(tmp_path))
    staged=copy.deepcopy(before)
    staged['questions'].append(new_question('Q-price',
        '版本能否追溯生效，迟到电量如何选价并重算？'))
    plan=request(staged,before)
    assert len(plan['targets'])==1 and plan['targets'][0]['can_apply_split']
    audit=dict(reviews=[dict(question_id='Q-price',decisions=[
        row('tmp-effective','能否追溯生效？'),
        row('tmp-version','迟到电量用哪个版本？'),
        row('tmp-recalc','什么条件下重算？')])])
    jsonschema.validate(audit,plan['schema'])
    result=apply_audit(staged,audit,plan['targets'])
    parent=next(q for q in staged['questions'] if q['id']=='Q-price')
    assert result['split_question_ids']==['Q-price']
    assert len(parent['superseded_by'])==3 and parent['answer'] is None
    children=[q for q in staged['questions'] if q['id'] in parent['superseded_by']]
    assert len({q['decision_group_id'] for q in children})==1
    assert all(q['status']=='open' and q['answer'] is None for q in children)


def test_two_pages_with_one_agreed_shared_period_stay_one_decision(tmp_path):
    before=prepared(Store(tmp_path))
    staged=copy.deepcopy(before)
    staged['questions'].append(new_question('Q-refresh',
        '站点总览和设备详情统一使用哪个刷新周期？'))
    plan=request(staged,before)
    audit=dict(reviews=[dict(question_id='Q-refresh',decisions=[
        row('Q-refresh','站点总览和设备详情统一使用哪个刷新周期？')])])
    result=apply_audit(staged,audit,plan['targets'])
    q=next(q for q in staged['questions'] if q['id']=='Q-refresh')
    assert result['split_question_ids']==[]
    assert q.get('superseded_by') is None and proposal_current(staged,'Q-refresh')


def test_live_compound_question_cannot_shrink_to_answered_subdecision(tmp_path):
    frozen_output=json.loads((Path(__file__).parent/'fixtures'/
        'shared-draft-clarify-semantic-replay.json').read_text('utf-8'))
    before=prepared(Store(tmp_path))
    staged=copy.deepcopy(before)
    q=staged['questions'][0]
    q.update(id=frozen_output['review']['question_id'],
             question=frozen_output['original_question'],
             answer=frozen_output['answer_text'],status='answered',
             decision_coverage='pending',
             related_refs=frozen_output['review']['decisions'][0]['related_refs'])
    for ref in q['related_refs']:
        staged['items'].append(dict(staged['items'][0],id=ref))
    plan=request(staged,copy.deepcopy(staged))
    assert plan['targets'][0]['question_texts'][q['id']]==q['question']
    audit=dict(reviews=[frozen_output['review']])
    with pytest.raises(jsonschema.ValidationError):jsonschema.validate(audit,plan['schema'])
    unchanged=copy.deepcopy(staged)
    with pytest.raises(Problem,match='逐字保留原问题'):
        apply_audit(staged,audit,plan['targets'])
    assert staged==unchanged


def test_conditional_example_in_source_or_history_is_not_current_answer(tmp_path):
    before=prepared(Store(tmp_path))
    staged=copy.deepcopy(before)
    staged['sources'][0]['excerpts'][0]['text']+=' 业务方先只回答“不要自动重算”时，仍需追问选价。'
    q=new_question('Q-recalc','是否自动重算？')
    q['answer_history']=[dict(answer='不要自动重算',answered_at='旧轮次')]
    staged['questions'].append(q)
    plan=request(staged,before)
    assert plan['targets'][0]['answer_hash'] is None
    context=json.loads(plan['messages'][1]['content'])['targets'][0]
    assert context['answer_text'] is None
    assert 'answer_text 是覆盖判定唯一的当前原话' in plan['messages'][0]['content']
    assert '条件示例' in plan['messages'][0]['content']
    invalid=dict(reviews=[dict(question_id='Q-recalc',decisions=[
        row('Q-recalc','是否自动重算？','clear','不要自动重算')])])
    with pytest.raises(jsonschema.ValidationError):jsonschema.validate(invalid,plan['schema'])
    frozen=copy.deepcopy(staged)
    with pytest.raises(Problem,match='无答案却标明确'):
        apply_audit(staged,invalid,plan['targets'])
    assert staged==frozen


def test_partial_old_answer_only_suggests_mapping_and_keeps_history(tmp_path):
    before=prepared(Store(tmp_path))
    staged=copy.deepcopy(before)
    q=staged['questions'][0]
    q.update(question='电价能否追溯生效，历史如何选价并重算？',
             status='answered',answer='新电价只能未来生效。',decision_coverage='pending')
    plan=request(staged,before)
    assert plan['targets'][0]['can_propose_split']
    assert not plan['targets'][0]['can_apply_split']
    original=copy.deepcopy(q)
    audit=dict(reviews=[dict(question_id=q['id'],decisions=[
        row('tmp-effective','能否追溯生效？','clear','新电价只能未来生效。'),
        row('tmp-version','历史数据用哪个版本？'),
        row('tmp-recalc','什么条件下重算？')])])
    result=apply_audit(staged,audit,plan['targets'])
    saved=staged['questions'][0]
    assert result['split_question_ids']==[]
    assert saved['answer']==original['answer'] and saved['status']=='answered'
    assert saved.get('superseded_by') is None
    assert [d['coverage'] for d in saved['decision_review_proposal']['decisions']]==[
        'clear','pending','pending']
    assert proposal_current(staged,q['id'])


def test_wrong_quote_or_missing_batch_target_rejects_without_any_staged_change(tmp_path):
    before=prepared(Store(tmp_path))
    staged=copy.deepcopy(before)
    staged['questions'].append(new_question('Q-a','是否追溯？'))
    staged['questions'].append(new_question('Q-b','如何选价？'))
    staged['questions'][-2].update(status='answered',answer='只允许未来生效。')
    plan=request(staged,before)
    frozen=copy.deepcopy(staged)
    missing=dict(reviews=[dict(question_id='Q-a',decisions=[row('Q-a','是否追溯？','clear','只允许未来生效。')])])
    with pytest.raises(Problem):apply_audit(staged,missing,plan['targets'])
    assert staged==frozen
    invalid=dict(reviews=[dict(question_id='Q-a',decisions=[row('Q-a','是否追溯？','clear','允许追溯。')]),
                          dict(question_id='Q-b',decisions=[row('Q-b','如何选价？')])])
    with pytest.raises(Problem):apply_audit(staged,invalid,plan['targets'])
    assert staged==frozen
    stale=copy.deepcopy(plan['targets']);stale[0]['question_hash']='wrong'
    with pytest.raises(Problem):apply_audit(staged,invalid,stale)
    assert staged==frozen


def test_no_new_or_changed_questions_skips_paid_review(tmp_path):
    before=prepared(Store(tmp_path))
    assert request(copy.deepcopy(before),before) is None


def test_saved_pending_answer_is_a_target_even_if_generation_changes_no_question(tmp_path):
    before=prepared(Store(tmp_path))
    before['questions'][0].update(status='answered',answer='本次只允许未来生效。',
                                  decision_coverage='pending',answered_at='saved-before-run')
    staged=copy.deepcopy(before)
    plan=request(staged,before)
    assert [t['question_id'] for t in plan['targets']]==['Q-0001']


def test_existing_group_requires_every_leaf_and_sent_source_identity(tmp_path):
    before=prepared(Store(tmp_path))
    staged=copy.deepcopy(before)
    for identity,question in [('Q-one','能否追溯？'),('Q-two','如何选价？')]:
        q=new_question(identity,question)
        q['decision_group_id']='QG-price'
        q['decision_group_title']='电价问题'
        staged['questions'].append(q)
    sent=copy.deepcopy(staged['sources'][0]['excerpts'][0])
    forged=copy.deepcopy(sent);forged['source_id']='SRC-other'
    with pytest.raises(Problem):build_request(staged,before,[forged])
    plan=build_request(staged,before,[sent])
    assert len(plan['targets'])==1 and plan['targets'][0]['decision_ids']==['Q-one','Q-two']
    context=json.loads(plan['messages'][1]['content'])['targets'][0]
    assert context['required_decision_ids']==['Q-one','Q-two']
    invented=dict(reviews=[dict(question_id='QG-price',decisions=[
        row('TMP-one','能否追溯？'),row('TMP-two','如何选价？')])])
    with pytest.raises(jsonschema.ValidationError):jsonschema.validate(invented,plan['schema'])
    incomplete=dict(reviews=[dict(question_id='QG-price',decisions=[row('Q-one','能否追溯？')])])
    frozen=copy.deepcopy(staged)
    with pytest.raises(Problem):apply_audit(staged,incomplete,plan['targets'])
    assert staged==frozen
    renamed=dict(reviews=[dict(question_id='QG-price',decisions=[
        row('Q-one','能否追溯生效？'),row('Q-two','如何选价？')])])
    with pytest.raises(jsonschema.ValidationError):jsonschema.validate(renamed,plan['schema'])
    with pytest.raises(Problem,match='逐字保留原问题'):
        apply_audit(staged,renamed,plan['targets'])
    assert staged==frozen
    full=dict(reviews=[dict(question_id='QG-price',decisions=[
        row('Q-one','能否追溯？'),row('Q-two','如何选价？')])])
    jsonschema.validate(full,plan['schema'])
    apply_audit(staged,full,plan['targets'])
    assert proposal_current(staged,'QG-price')
    changed_selection=copy.deepcopy(staged)
    changed_selection['domain_context']={'selected_source_ids':[]}
    assert not proposal_current(changed_selection,'QG-price')
    changed_excerpt=copy.deepcopy(staged)
    changed_excerpt['sources'][0]['excerpts'][0]['text']='另一版来源原文。'
    assert not proposal_current(changed_excerpt,'QG-price')
    changed_version=copy.deepcopy(staged)
    changed_version['sources'][0]['version']=2
    assert not proposal_current(changed_version,'QG-price')


def test_changed_question_after_request_is_stale(tmp_path):
    before=prepared(Store(tmp_path))
    staged=copy.deepcopy(before)
    staged['questions'].append(new_question('Q-new','原问题？'))
    plan=request(staged,before)
    staged['questions'][-1]['question']='用户手改的新问题？'
    snapshot=copy.deepcopy(staged)
    audit=dict(reviews=[dict(question_id='Q-new',decisions=[row('Q-new','原问题？')])])
    with pytest.raises(Problem,match='问题已变化'):
        apply_audit(staged,audit,plan['targets'])
    assert staged==snapshot


def test_changed_sent_excerpt_after_request_is_stale(tmp_path):
    before=prepared(Store(tmp_path))
    staged=copy.deepcopy(before)
    staged['questions'].append(new_question('Q-new','边界如何处理？'))
    plan=request(staged,before)
    staged['sources'][0]['excerpts'][0]['text']='另一次编辑后的来源。'
    audit=dict(reviews=[dict(question_id='Q-new',decisions=[row('Q-new','边界如何处理？')])])
    frozen=copy.deepcopy(staged)
    with pytest.raises(Problem,match='来源或选择已变化'):
        apply_audit(staged,audit,plan['targets'])
    assert staged==frozen


def test_manual_whole_question_confirmation_clears_simple_answer_but_not_current_compound(tmp_path):
    project=prepared(Store(tmp_path))
    q=project['questions'][0]
    q.update(status='answered',answer='相接允许。',decision_coverage='pending')
    target=next(i for i in project['items'] if i['id']=='REQ-0001')
    target['statement']+=' 相接允许。'
    hashes={'REQ-0001':application_hash(target)}
    confirm_manual_answer(project,q,digest(q['answer']),hashes,True,
        answer_implementations={q['id']:[dict(item_id=target['id'],field='statement',quote='相接允许。')]})
    assert coverage_complete(q)
    q['decision_coverage']='pending';q.pop('decision_coverage_review')
    audit=dict(question_id=q['id'],question_hash=review_question_hash(project,q['id']),
        answer_hash=digest(q['answer']),decisions=[
            row('tmp-touch','相接是否允许？','clear','相接允许。'),
            row('tmp-other','另一个独立决定？')])
    assert append_model_proposal(project,audit)
    # A stale source can retire the suggestion, but cannot make this known
    # compound answer safe to confirm as a whole.
    project['sources'][0]['version']=2
    with pytest.raises(Problem,match='多个独立决定'):
        confirm_manual_answer(project,q,digest(q['answer']),hashes,True)
    assert not coverage_complete(q)
