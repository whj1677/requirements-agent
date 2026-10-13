"""Synthetic, read-only navigation checks for the existing stage gates."""
import copy

from app.product_flow import BEHAVIOR, INTAKE, SCOPE, blockers, missing, status
from app.store import Store
from tests.helpers import prepared


def project(tmp_path):
    p = prepared(Store(tmp_path))
    p['product_context'] = {key: '合成输入：'+label for key,label in {**INTAKE,**SCOPE}.items()}
    p['product_context']['scope_ids'] = ['REQ-0001']
    p['items'][0]['behavior'] = {key: '合成明确：'+label for key,label in BEHAVIOR.items()}
    return p


def check(p, step):
    before = copy.deepcopy(p)
    rows = blockers(p, step)
    state = status(p)[step-1]
    assert p == before
    assert state['blockers'] == rows
    assert state['missing'] == missing(p,step) == [r['message'] for r in rows]
    assert len(state['missing']) == len(set(state['missing']))
    assert all(set(r) >= {'key','code','title','message','actions'} for r in rows)
    return rows


def by_code(rows,code):
    return next(row for row in rows if row['code'] == code)


def test_behavior_and_acceptance_targets_are_related_and_in_scope(tmp_path):
    p = project(tmp_path)
    req = p['items'][0]
    del req['behavior']['entry']
    del req['behavior']['exceptions']
    req['behavior_review_required'] = True
    req['behavior_review'] = {'content_hash':'obsolete'}
    p['items'][2]['selection_status'] = 'candidate'
    irrelevant = copy.deepcopy(p['items'][2])
    irrelevant.update(id='AC-OTHER',related_refs=['REQ-OTHER'],selection_status='candidate')
    p['items'].append(irrelevant)
    rows = check(p,3)
    assert by_code(rows,'behavior_review_pending')['actions'][0]['item_id'] == req['id']
    gap = by_code(rows,'behavior_fields_missing')
    assert gap['title'] == req['title']
    assert gap['actions'] == [{'kind':'behavior','label':'补全业务行为','item_id':req['id'],
                               'fields':['entry','exceptions']}]
    assert '入口及前置条件' in gap['detail'] and '边界和异常' in gap['detail']
    assert by_code(rows,'acceptance_missing')['actions'][0]['kind'] == 'acceptance'
    assert by_code(rows,'acceptance_missing')['actions'][0]['item_id'] == req['id']
    assert all(r['title'] == req['title'] for r in rows if r['code'] in
               ('behavior_review_pending','behavior_fields_missing','acceptance_missing'))
    p['items'][2]['selection_status'] = 'rejected'
    assert by_code(check(p,3),'acceptance_missing')['actions'][0]['kind'] == 'add_acceptance'


def test_duplicate_question_text_keeps_each_question_target(tmp_path):
    p = project(tmp_path)
    q = p['questions'][0]
    q.update(blocking=True,blocking_stage=3)
    duplicate = copy.deepcopy(q)
    duplicate['id'] = 'Q-0002'
    p['questions'].append(duplicate)
    rows = check(p,3)
    row = by_code(rows,'question_open')
    assert [action['question_id'] for action in row['actions']] == ['Q-0001','Q-0002']
    assert row['message'] == q['question']
    q.update(status='answered',understanding_status='pending')
    assert by_code(check(p,3),'answer_review_pending')['actions'][0]['question_id'] == 'Q-0001'


def test_scope_context_and_document_actions_preserve_messages(tmp_path):
    p = project(tmp_path)
    p['product_context']['users'] = ''
    p['items'][0]['change_type'] = 'unspecified'
    rows = check(p,2)
    assert by_code(rows,'context_field')['actions'][0] == {
        'kind':'context','label':'核对目标使用者','field':'users'}
    assert by_code(rows,'change_type_unknown')['actions'][0]['item_id'] == 'REQ-0001'
    p['product_context']['scope_ids'] = ['REQ-0001','REQ-MISSING']
    rows = check(p,3)
    assert by_code(rows,'scope_unadopted')['actions'][0]['kind'] == 'scope'
    p['documents'].pop('mrd')
    rows = check(p,5)
    assert by_code(rows,'document_missing')['actions'][0]['document'] == 'mrd'
    assert by_code(rows,'document_review_pending')['actions'][0]['document'] == 'prd'


def test_intake_and_legacy_sketch_navigation(tmp_path):
    p = project(tmp_path)
    p['product_context']['product'] = ''
    p['sources'] = []
    rows = check(p,1)
    assert by_code(rows,'context_field')['actions'][0]['field'] == 'product'
    assert by_code(rows,'source_missing')['actions'][0]['kind'] == 'source'
    p['sketch_review'] = {'applicable':False,'reason':''}
    assert by_code(check(p,4),'sketch_reason_missing')['actions'][0]['kind'] == 'sketch'


def test_new_interview_context_requires_human_intent_with_a_direct_field_entry(tmp_path):
    p = project(tmp_path)
    p['product_context'].pop('intent')
    p['product_context_proposal'] = {'intent':'合成储能云目标建议，等待人工确认。'}
    p['interview_context_review'] = {'version':'interview-context-1', 'accepted_fields':['intent']}
    assert 'scope_profile' not in p and 'context_field_states' not in p
    rows = check(p, 2)
    target = next(row for row in rows if row['code'] == 'context_field'
                  and row['actions'][0].get('field') == 'intent')
    assert target['actions'] == [{'kind':'context', 'label':'核对本次改动意图', 'field':'intent'}]
    # A model recommendation is insufficient; only the canonical human value
    # removes the intent gate. No proposal or unrelated field is rewritten.
    proposal = copy.deepcopy(p['product_context_proposal'])
    p['product_context']['intent'] = '产品经理确认的储能云目标。'
    assert not [row for row in check(p, 2) if row['code'] == 'context_field'
                and row['actions'][0].get('field') == 'intent']
    assert p['product_context_proposal'] == proposal


def test_interview_intent_is_bound_to_scope_and_rule_checkpoint_fingerprints(tmp_path):
    from app.product_flow import fingerprint
    p = project(tmp_path)
    p['interview_context_review'] = {'version':'interview-context-1', 'accepted_fields':['intent']}
    scope_before, rules_before = fingerprint(p, 2), fingerprint(p, 3)
    p['product_context']['intent'] = '人工修改后的新目标。'
    assert fingerprint(p, 2) != scope_before
    assert fingerprint(p, 3) != rules_before
    # Audit metadata changes alone do not invalidate a human content checkpoint.
    scope_after, rules_after = fingerprint(p, 2), fingerprint(p, 3)
    p['interview_context_review']['run_id'] = 'RUN-another-audit'
    assert (fingerprint(p, 2), fingerprint(p, 3)) == (scope_after, rules_after)


def test_unmarked_legacy_scope_gate_and_fingerprint_are_unchanged(tmp_path):
    from app.product_flow import fingerprint
    p = project(tmp_path)
    p['product_context'].pop('intent')
    p['product_context_proposal'] = {'intent':'历史整理的建议'}
    assert 'interview_context_review' not in p
    before = fingerprint(p, 2), fingerprint(p, 3)
    assert not [row for row in check(p, 2) if row['code'] == 'context_field'
                and row['actions'][0].get('field') == 'intent']
    p['product_context']['intent'] = '旧合同之外的目标变化'
    assert (fingerprint(p, 2), fingerprint(p, 3)) == before
