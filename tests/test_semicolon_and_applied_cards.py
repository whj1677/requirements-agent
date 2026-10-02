"""Two bounded offline gaps; full validation and explicit public adoption."""
import copy
import pytest
from app.contracts import validate_response
from app.core import Problem, digest
from app.generation_context import condition_layers
from app.requirements import behavior_fingerprint, require_behavior_review
from app.result_conditions import facts, issues
from app.understanding import application_hash, confirm_manual_answer
from tests.test_result_conditions import case
from tests.test_answer_application_recovery import answered_project
from tests.test_offline_answer_route_design import route_case, save_answer, run_candidate, ANSWER

# Frozen independent 15-case oracle; never dependent on ignored local files.
CASES=[
    ('original_prohibition','响应丢失或超时为结果未知，不断言创建、未创建或既有资源未变',False),
    ('cannot_assert_parallel','响应丢失或超时为结果未知，不能据此断言已创建或未创建资源',False),
    ('should_not_assert','结果未知，不应断言本批已创建资源',False),
    ('prohibition_then_query','结果未知，不断言创建；仅允许沿原申请ID查询',False),
    ('new_clause_affirmative_comma','结果未知，不断言创建，资源已创建',True),
    ('new_clause_affirmative_semicolon','结果未知，不断言创建；本批已创建资源',True),
    ('double_negation_not_cannot','结果未知，并非不能断言已创建资源',True),
    ('double_negation_not_cannot_no_effect','结果未知，不是不能认定本批未创建资源',True),
    ('double_negation_cannot_not','结果未知，不能不认定本批已创建资源',True),
    ('new_attempt_prohibited','超时后结果未知，禁止创建新申请',False),
    ('new_attempt_vs_original_effect','超时后结果未知，禁止创建新申请，原申请未创建资源',True),
    ('explicit_unknown_no_effect','响应丢失或超时为结果未知，本批未创建资源',True),
    ('explicit_unknown_effect','结果未知，本批已创建资源',True),
    ('prohibition_synonym','结果未知，不得认定本批未创建资源',False),
    ('prohibition_does_not_prove','结果未知，不意味着资源已创建',False),
]


@pytest.mark.parametrize('name,text,blocked',CASES,ids=[c[0] for c in CASES])
def test_fixed_oracle_through_complete_validation_and_review(name,text,blocked):
    p,v,ex=case(text)
    item=copy.deepcopy(v['proposals'][0]);item['behavior_review_required']=True
    item['behavior_review']={'content_hash':behavior_fingerprint(item)}
    before=copy.deepcopy((p,v,ex))
    if blocked:
        with pytest.raises(Problem) as error:validate_response(v,'ingest',p,ex)
        assert error.value.code=='SEMANTIC_BLOCKED' and not error.value.model_repair_allowed
        with pytest.raises(Problem) as reviewed:require_behavior_review(item)
        assert reviewed.value.code=='SEMANTIC_BLOCKED'
    else:
        assert validate_response(v,'ingest',p,ex)==v
        require_behavior_review(item)
    assert (p,v,ex)==before


@pytest.mark.parametrize('text,blocked',[
    ('超时结果未知，不断言创建;原申请已创建资源',True),
    ('原申请结果未知；禁止再次创建新申请；原申请未创建资源',True),
    ('结果未知；不断言创建；既有资源不变',True),
    ('结果未知；不能不认定原申请未创建资源',True),
    ('结果未知；禁止创建新申请；先查询原申请ID',False),
    ('结果未知；不得断言创建、未创建或既有资源未变',False),
    ('结果未知；服务明确拒绝时本批不创建且既有资源不变',False),
    ('结果未知；系统受理成功且处理成功的分支，配额满足则创建资源',False),
    ('结果未知；系统尚未受理成功，配额满足则创建资源',True),
    ('资源申请结果未知；工单处理成功，配额满足则创建资源',True),
    ('资源申请结果未知；正常成功分支，工单已创建',False),
    ('结果未知；查询确认成功；本批资源已创建',False),
    ('结果未知；未查询确认成功；本批资源已创建',True),
    ('资源申请结果未知；查询确认工单成功；本批资源已创建',True),
])
def test_semicolon_preserves_unknown_not_inference_or_other_branch(text,blocked):
    p,v,ex=case(text)
    if blocked:
        with pytest.raises(Problem) as error:validate_response(v,'ingest',p,ex)
        assert error.value.code=='SEMANTIC_BLOCKED'
    else:validate_response(v,'ingest',p,ex)


def test_semicolon_evidence_keeps_exact_condition_and_assertion_owners():
    refs=[dict(source_id='SRC-original',excerpt_id='EX-original')]
    parsed=facts('资源申请结果未知，不断言创建；本批已创建资源',
                 location='$.proposals[0].behavior.result',object_id='TMP-original',source_refs=refs)
    found=issues(parsed)
    assert found[0]['reason']=='unknown_result_asserted'
    assert found[0]['quote']=='本批已创建资源'
    assert found[0]['evidence'][0]['quote']=='资源申请结果未知，不断言创建'
    assert all(f['location']=='$.proposals[0].behavior.result' and f['object_id']=='TMP-original'
               and f['source_refs']==refs for f in [found[0],*found[0]['evidence']])


def tasks(p):return condition_layers({'facts':[]},p)['local_result_tasks']


@pytest.mark.parametrize('state',['valid','pending_label_valid_hash','answer_changed','clause_changed','new_target','superseded','out_of_scope','legacy_valid'])
def test_effective_application_hashes_determine_result_cards(tmp_path,state):
    p,q=answered_project(tmp_path);target=p['items'][1]
    confirm_manual_answer(p,q,digest(q['answer']),{target['id']:application_hash(target)},True)
    if state=='pending_label_valid_hash':q['understanding_status']='pending'
    elif state=='answer_changed':q['answer']+=' 合成测试改答，需重新落实。'
    elif state=='clause_changed':target['statement']+=' 条款已变更。'
    elif state=='new_target':q['related_refs'].append('AC-0001')
    elif state=='superseded':q['superseded_by']=['Q-new']
    elif state=='out_of_scope':q['out_of_scope_reason']='明确不在本期'
    elif state=='legacy_valid':q.pop('answer_applications')
    before=copy.deepcopy(p);cards=tasks(p)
    expected=['RULE-0001'] if state in ('answer_changed','clause_changed') else ['AC-0001'] if state=='new_target' else []
    assert [c['target_item_id'] for c in cards]==expected
    assert all(c['question_ids']==['Q-0001'] for c in cards) and p==before


def test_public_pending_partial_applied_changed_answer_and_stale_adoption(route_case,monkeypatch):
    client,app,root,post=route_case
    _,p=save_answer(route_case)
    assert [c['target_item_id'] for c in tasks(p)]==['REQ-0001','RULE-0001']
    _,p=run_candidate(route_case,monkeypatch)
    assert p['questions'][0]['understanding_status']=='candidate_ready' and len(tasks(p))==2
    candidates={i['target_item_id']:i['id'] for i in p['items'] if i.get('target_item_id')}
    for target in ('REQ-0001','RULE-0001'):
        candidate=next(i for i in p['items'] if i['id']==candidates[target])
        if candidate['kind']=='requirement':
            r=post('/items/'+candidate['id']+'/behavior',dict(expected_revision=p['revision'],
                values=candidate['behavior'],change_type='new',review_confirmed=True))
            assert r.status_code==200;p=r.json()
        r=post('/items/'+candidate['id'],dict(expected_revision=p['revision'],selection_status='selected'))
        assert r.status_code==200,r.text;p=r.json()
        assert [c['target_item_id'] for c in tasks(p)]==(['RULE-0001'] if target=='REQ-0001' else [])
    assert p['questions'][0]['understanding_status']=='applied'
    assert set(p['questions'][0]['answer_applications'])=={'REQ-0001','RULE-0001'}
    assert not p.get('active_baseline_id') and not p['documents']
    old_source=copy.deepcopy(p['questions'][0]['answer_source_refs'])
    _,p=save_answer(route_case,ANSWER+' 合成操作者补充：查询结果返回前仍待核对。')
    assert p['questions'][0]['understanding_status']=='pending' and len(tasks(p))==2
    assert p['questions'][0]['answer_history'][-1]['answer_source_refs']==old_source
    before=app.state.store.get(p['id'])
    r=post('/items/'+candidates['RULE-0001'],dict(expected_revision=p['revision'],selection_status='selected'))
    assert r.status_code==409 and r.json()['code']=='STALE_ANSWER'
    assert app.state.store.get(p['id'])==before and len(tasks(p))==2
