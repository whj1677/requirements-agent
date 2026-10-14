"""Actionable review corrections and exact evidence identity, offline fixtures."""
import copy

import pytest

from app.prewrite import apply_result, formal_issues, status
from tests.test_detail_document import project_fixture
from tests.test_prewrite_consistency import isolated_store, review, run_action


def warning_review(project, *, assessment='needs_changes'):
    response=review(project,assessment=assessment)
    req=project['items'][0]
    response['findings']=[dict(type='contradiction',severity='warning',
        related_refs=[req['id']],source_refs=[],message='等待状态与重复操作说明需核对。',
        suggested_resolution='请核对等待期间是否仍可重复操作，并同步文字和流程。',
        evidence_quotes=[dict(object_id=req['id'],path='detail_contract.interactions.I-save.pending',
                             quote=req['detail_contract']['interactions'][0]['pending']),
                         dict(object_id=req['id'],path='detail_contract.interactions.I-save.repeat',
                             quote=req['detail_contract']['interactions'][0]['repeat'])])]
    return response


def test_negative_warning_review_has_editable_correction_without_auto_acceptance(tmp_path):
    project=project_fixture(tmp_path)
    before=copy.deepcopy(project)
    report=apply_result(project,warning_review(project),'RUN-warning-correction')
    assert report['status']=='needs_confirmation' and report['blocking_count']==1
    assert len(report['findings'])==1 and report['findings'][0]['id']!='PW-assessment'
    card=report['findings'][0]
    assert card['severity']=='warning' and card['decision_kind']=='correction'
    assert card['requires_confirmation'] and card['blocks_document']
    question=next(q for q in project['questions'] if q['id']==card['question_id'])
    assert question['related_refs']==['REQ-0001']
    assert question['status']=='open' and question['answer'] is None
    assert '核对是否' in question['question']
    assert project['items']==before['items'] and project['documents']==before['documents']
    assert formal_issues(project)
    question.update(status='answered',answer='先保留原逻辑，需要进一步核对。')
    apply_result(project,review(project),'RUN-ready-without-application')
    assert status(project)['status']=='needs_confirmation'


def test_ready_warning_is_still_advisory(tmp_path):
    project=project_fixture(tmp_path)
    report=apply_result(project,warning_review(project,assessment='ready_for_human_review'),'RUN-advisory')
    assert report['status']=='passed' and project['questions']==[]
    assert not report['findings'][0].get('blocks_document')


def test_unlocatable_negative_warning_keeps_unknown_assessment_blocked(tmp_path):
    project=project_fixture(tmp_path)
    response=warning_review(project)
    response['findings'][0].update(related_refs=[],evidence_quotes=[])
    report=apply_result(project,response,'RUN-unlocatable')
    assert report['status']=='needs_confirmation'
    assert any(f['id']=='PW-assessment' for f in report['findings'])
    assert project['questions']==[]


def test_exact_evidence_reuses_card_and_pending_question_across_rephrasing(tmp_path):
    project=project_fixture(tmp_path)
    response=warning_review(project)
    first=apply_result(project,response,'RUN-initial')
    response['findings'][0].update(message='请对齐同一按钮的等待与重复操作约束。',
        suggested_resolution='请再次核对待处理时的重复点击行为。',topic_key='model-reworded-topic')
    response['findings'][0]['evidence_quotes'].reverse()
    second=apply_result(project,response,'RUN-rephrased')
    assert len(second['findings'])==len(project['questions'])==1
    assert second['findings'][0]['id']==first['findings'][0]['id']
    assert second['findings'][0]['question_id']==first['findings'][0]['question_id']
    assert second['findings'][0]['message']==response['findings'][0]['message']
    assert second['status']=='needs_confirmation'


def test_exact_evidence_duplicates_with_different_prose_stay_one_card(tmp_path):
    project=project_fixture(tmp_path)
    response=warning_review(project)
    duplicate=copy.deepcopy(response['findings'][0])
    duplicate['message']='同一证据的另一种叙述。'
    duplicate['evidence_quotes'].reverse()
    response['findings'].append(duplicate)
    report=apply_result(project,response,'RUN-duplicate-prose')
    assert len(report['findings'])==len(project['questions'])==1


@pytest.mark.parametrize('dimension',['type','quote','path','extra_quote','source','scope'])
def test_different_evidence_never_inherits_card_identity(tmp_path,dimension):
    project=project_fixture(tmp_path)
    response=warning_review(project)
    first=apply_result(project,response,'RUN-old-evidence')
    finding=response['findings'][0]
    if dimension=='type':finding['type']='other'
    elif dimension=='quote':finding['evidence_quotes'][0]['quote']='禁用保存按钮'
    elif dimension=='path':
        finding['evidence_quotes'][0].update(path='detail_contract.interactions.I-save.repeat',quote='同一请求只执行一次')
    elif dimension=='extra_quote':
        finding['evidence_quotes'].append(dict(object_id='REQ-0001',path='statement',quote=project['items'][0]['statement']))
    elif dimension=='source':finding['source_refs']=copy.deepcopy(project['items'][0]['source_refs'])
    else:finding['related_refs'].append(project['items'][1]['id'])
    second=apply_result(project,response,'RUN-new-evidence')
    assert len(second['findings'])==2
    assert any(f['id']!=first['findings'][0]['id'] and f['status']=='open' for f in second['findings'])


def test_ready_downgrade_cannot_drop_a_pending_warning_correction(tmp_path):
    project=project_fixture(tmp_path)
    response=warning_review(project)
    first=apply_result(project,response,'RUN-correction')
    response['result']['assessment']='ready_for_human_review'
    response['findings'][0]['severity']='info'
    report=apply_result(project,response,'RUN-downgrade')
    assert report['status']=='needs_confirmation'
    assert report['findings'][0]['question_id']==first['findings'][0]['question_id']
    assert report['findings'][0]['severity']=='info'


def test_changed_answered_question_does_not_inherit_a_prior_answer(tmp_path):
    project=project_fixture(tmp_path)
    response=warning_review(project)
    first=apply_result(project,response,'RUN-before-answer')
    project['questions'][0].update(status='answered',answer='等待期间禁用。')
    response['findings'][0].update(decision_question='是否允许等待结束后重复提交原请求？')
    second=apply_result(project,response,'RUN-changed-question')
    card=second['findings'][0]
    assert card['id']==first['findings'][0]['id']
    assert card['question_id']!=first['findings'][0]['question_id']
    question=next(q for q in project['questions'] if q['id']==card['question_id'])
    assert question['answer'] is None and question['status']=='open'


@pytest.mark.parametrize('persisted_response',[False,True])
def test_legacy_card_type_is_recovered_only_from_its_saved_response(tmp_path,persisted_response):
    from app.prewrite import _legacy_finding_id
    project=project_fixture(tmp_path)
    response=warning_review(project,assessment='ready_for_human_review')
    old=apply_result(project,response,'RUN-legacy')
    card=old['findings'][0]
    card['id']=_legacy_finding_id(response['findings'][0])
    card.pop('finding_type');card.pop('evidence_identity')
    prior_response=copy.deepcopy(response)
    response['findings'][0]['message']='仅修改文字而保留确切证据。'
    report=apply_result(project,response,'RUN-migrated',previous_response=prior_response if persisted_response else None)
    active=[f for f in report['findings'] if f['status']=='open']
    assert len(active)==1
    assert (active[0]['id']==card['id']) is persisted_response
    assert active[0]['finding_type']==response['findings'][0]['type']


@pytest.mark.parametrize('result_applied',[False,True])
def test_workflow_legacy_identity_reads_only_a_previously_applied_response(tmp_path,result_applied):
    from app.prewrite import _legacy_finding_id
    store,project=isolated_store(tmp_path)
    response=warning_review(project,assessment='ready_for_human_review')
    prior_id=store.record(project['id'],'run',dict(stage='review',status='succeeded',
        result_applied=result_applied,response=copy.deepcopy(response)))
    with store.edit(project['id'],project['revision'],'合成旧版审查',bump=False) as (p,_):
        old=apply_result(p,response,prior_id)
        card=old['findings'][0]
        card['id']=_legacy_finding_id(response['findings'][0])
        card.pop('finding_type');card.pop('evidence_identity')
        old_id=card['id']
    project=store.get(project['id'])
    response['findings'][0]['message']='同一确切证据的新表述。'
    task,provider,_=run_action(store,project,[response],action='prewrite')
    assert task['status']=='succeeded',task
    assert task['calls']==len(provider.received)==1
    saved=store.get(project['id'])
    assert saved['prewrite_history'][-1]==project['prewrite_review']
    assert saved['prewrite_history'][-1]['status']!='running'
    active=[f for f in saved['prewrite_review']['findings'] if f['status']=='open']
    assert len(active)==1
    assert (active[0]['id']==old_id) is result_applied


@pytest.mark.parametrize('changed',['answer','application'])
def test_same_evidence_downgrade_reopens_a_resolution_with_expired_application(tmp_path,changed):
    from app.answer_implementation import implementation_records
    from app.decision_review import save_raw_answer
    project=project_fixture(tmp_path)
    response=warning_review(project)
    first=apply_result(project,response,'RUN-warning-found')
    question=project['questions'][0]
    req=project['items'][0]
    req['statement']+='等待期间禁用保存。'
    question.update(status='answered',answer='等待期间禁用保存。',understanding_status='applied',
        applied_item_ids=[req['id']],answer_implementation_contract=2)
    question['answer_applications']=implementation_records(project,question,
        {req['id']:[dict(field='statement',quote='等待期间禁用保存。')]},method='manual_review')
    resolved=apply_result(project,review(project),'RUN-resolved')
    assert resolved['status']=='passed' and question['prewrite_resolution']
    if changed=='answer':save_raw_answer(project,question['id'],'等待期间允许再次保存。')
    else:req['statement']='等待期间允许再次保存。'
    response['result']['assessment']='ready_for_human_review'
    response['findings'][0]['severity']='info'
    result=apply_result(project,response,'RUN-downgraded')
    assert result['status']=='needs_confirmation'
    card=next(f for f in result['findings'] if f['id']==first['findings'][0]['id'])
    assert card['question_id']==question['id'] and card['blocks_document']
    assert 'prewrite_resolution' not in question


def test_first_workflow_check_does_not_invent_a_running_history_snapshot(tmp_path):
    store,project=isolated_store(tmp_path)
    task,_,_=run_action(store,project,[review(project)],action='prewrite')
    assert task['status']=='succeeded',task
    first=store.get(project['id'])
    assert first.get('prewrite_history',[])==[]
    task,_,_=run_action(store,first,[review(first)],action='prewrite')
    assert task['status']=='succeeded',task
    second=store.get(project['id'])
    assert second['prewrite_history']==[first['prewrite_review']]
