"""Candidate completeness is information for the model, never human adoption."""
import copy
import json
import pytest
from app.product_flow import BEHAVIOR, missing
from app.provider import assemble, DEFAULT
from app.store import Store
from tests.helpers import prepared


def focus(p, tmp_path):
    messages, _, _ = assemble(p, 'clarify', '', DEFAULT, tmp_path)
    return json.loads(messages[1]['content'][0]['text'])['stage_focus']


def candidate_project(tmp_path):
    p = prepared(Store(tmp_path))
    p['product_context'] = {'scope_ids': ['REQ-0001']}
    for item in p['items']:
        item['selection_status'] = 'candidate'
    p['items'][0]['behavior'] = {k: '材料已明确：' + v for k, v in BEHAVIOR.items() if k != 'permissions'}
    p['items'] = [i for i in p['items'] if i['kind'] != 'acceptance']
    return p


def test_unadopted_candidate_content_gaps_reach_clarify(tmp_path):
    p = candidate_project(tmp_path)
    before = copy.deepcopy(p)
    value = focus(p, tmp_path)
    assert any('REQ-0001' in s and '权限约束' in s for s in value['content_gaps'])
    assert any('REQ-0001' in s and '验收' in s for s in value['content_gaps'])
    assert any('采纳' in s for s in value['adoption_gaps'])
    assert p == before


def test_answer_application_targets_only_scoped_original_requirements(tmp_path):
    p = candidate_project(tmp_path)
    p['questions'][0].update(status='answered', answer='合成预定答案', related_refs=['REQ-0001', 'RULE-0001'])
    value = focus(p, tmp_path)
    assert value['answer_revision_targets'] == [dict(question_id='Q-0001', requirement_ids=['REQ-0001'])]
    assert '新增 acceptance 不填写 answer_refs' in value['instruction']


def test_candidate_acceptance_is_visible_but_does_not_satisfy_human_gate(tmp_path):
    p = candidate_project(tmp_path)
    p['items'].append(dict(id='AC-pending',kind='acceptance',related_refs=['REQ-0001'],selection_status='candidate',applies_to='to_be'))
    value = focus(p, tmp_path)
    assert not any('缺少' in s and '验收' in s for s in value['content_gaps'])
    assert value['requirements'][0]['acceptance_candidate_ids'] == ['AC-pending']
    p['items'][0]['selection_status'] = 'selected'
    assert any('已采纳的关联验收条件' in s for s in missing(p, 3))


def test_applied_answer_is_not_requested_again_but_a_changed_answer_is(tmp_path):
    from app.understanding import record_answer
    p=candidate_project(tmp_path)
    question=p['questions'][0]
    question.update(status='answered',answer='已明确的答案',understanding_status='applied',
                    related_refs=['REQ-0001'],applied_requirement_ids=['REQ-0001'])
    assert focus(p,tmp_path)['answer_revision_targets']==[]
    record_answer(question)
    question['answer']='人工修改后的答案'
    assert focus(p,tmp_path)['answer_revision_targets']==[
        dict(question_id=question['id'],requirement_ids=['REQ-0001'])]


@pytest.mark.parametrize('decision', ['rejected', 'deferred'])
def test_rejected_or_outside_scope_items_do_not_satisfy_candidate_completeness(tmp_path, decision):
    p = candidate_project(tmp_path)
    p['items'].append(dict(id='AC-rejected',kind='acceptance',related_refs=['REQ-0001'],selection_status=decision,applies_to='to_be'))
    p['items'].append(dict(id='REQ-other',kind='requirement',related_refs=[],selection_status='candidate',applies_to='to_be'))
    value = focus(p, tmp_path)
    assert any('缺少' in s and '验收' in s for s in value['content_gaps'])
    assert [r['requirement_id'] for r in value['requirements']] == ['REQ-0001']
