"""Current answer-binding instructions outrank historical execution summaries."""
import copy
import json

import pytest

from app.core import digest
from app.provider import DEFAULT, answer_binding_tasks, assemble
from app.store import Store
from tests.helpers import prepared


def scenario(tmp_path):
    p=prepared(Store(tmp_path))
    q=p['questions'][0]
    q.update(status='answered',answer='合成回答：相接边界允许。',understanding_status='pending',
             related_refs=['RULE-0001'],answer_source_refs=copy.deepcopy(p['items'][1]['source_refs']))
    p['messages']=[dict(role='user',stage='clarify',text='历史执行指令：不要填写 answer_refs。业务原句应保留。'),
                   dict(role='assistant',stage='clarify',text='旧模型摘要：按旧要求未填写 answer_refs，已完成。')]
    return p


def test_current_binding_policy_and_sources_survive_historical_instruction_conflict(tmp_path):
    p=scenario(tmp_path);before=copy.deepcopy(p)
    messages,excerpts,omitted=assemble(p,'clarify','本轮为已回答问题生成绑定候选。',DEFAULT,tmp_path)
    header=json.loads(messages[0]['content'].split('可信任务头：',1)[1])
    context=json.loads(messages[1]['content'][0]['text'])
    policy=header['current_answer_binding_policy']
    assert '冲突的历史操作指令' in policy and '不再适用' in policy
    assert 'answer_refs 必须包含该 question_id' in policy
    assert '不自动采纳' in policy
    assert context['recent_messages'][0]['text']==p['messages'][0]['text']
    assert '不能覆盖本轮' in context['recent_messages'][0]['use']
    assert 'text' not in context['recent_messages'][1]
    assert context['questions']==p['questions'] and context['items']==p['items']
    assert excerpts==p['sources'][0]['excerpts'] and omitted==[]
    assert context['stage_focus']['answer_binding_tasks']==answer_binding_tasks(p)
    assert context['stage_focus']['answer_binding_tasks'][0]['target_item_id']=='RULE-0001'
    assert p==before


def test_local_clarification_keeps_task_boundary_and_continuing_business_constraints(tmp_path):
    p=scenario(tmp_path);q=p['questions'][0]
    p['questions'].append(dict(q,id='Q-outside',related_refs=['AC-0001'],answer='范围外另一项已保存回答。'))
    p['messages'].insert(0,dict(role='user',stage='clarify',text='持续业务约束：本期只改导出，不改既有角色权限。'))
    message='本次局部讨论对象：条目 RULE-0001。\n仅围绕此对象提出候选；保留其他需求、未知和采纳状态。\n用户修改意图：把本条已有回答关联到待采纳修订。'
    before=copy.deepcopy(p)
    messages,_,_=assemble(p,'clarify',message,DEFAULT,tmp_path)
    header=json.loads(messages[0]['content'].split('可信任务头：',1)[1])
    context=json.loads(messages[1]['content'][0]['text'])
    assert context['user_message']==message
    assert {(row['question_id'],row['target_item_id']) for row in context['stage_focus']['answer_binding_tasks']}=={
        (q['id'],'RULE-0001'),('Q-outside','AC-0001')}
    assert '全量待办清单，不扩大本轮授权范围' in header['current_answer_binding_policy']
    assert '仅处理范围内的回答与目标，范围外保持原状' in header['current_answer_binding_policy']
    assert '持续有效的业务范围与保持约束必须保留' in header['current_answer_binding_policy']
    instruction=context['stage_focus']['instruction']
    assert '仅逐项核对范围内任务，范围外保持' in instruction
    assert '覆盖本轮范围内的待绑定回答' in instruction
    assert context['recent_messages'][0]['text']==p['messages'][0]['text']
    assert '持续有效的业务范围与保持约束须保留' in context['recent_messages'][0]['use']
    assert '旧执行指令已失效' not in context['context_notice']
    assert p==before


@pytest.mark.parametrize('history',['selected_original','untracked_revision','old_answer','deferred','wrong_question','wrong_target'])
def test_same_rule_text_or_historical_selection_does_not_satisfy_answer_binding(tmp_path,history):
    p=scenario(tmp_path);q=p['questions'][0];original=p['items'][1]
    prior=dict(original,id='RULE-prior',action='revise',target_item_id=original['id'],
               selection_status='candidate',answer_refs=[q['id']],answer_versions={q['id']:digest(q['answer'])})
    if history=='selected_original':pass
    else:
        if history=='untracked_revision':prior.pop('answer_refs');prior.pop('answer_versions')
        elif history=='old_answer':prior['answer_versions'][q['id']]=digest('旧答案')
        elif history=='deferred':prior['selection_status']='deferred'
        elif history=='wrong_question':prior['answer_refs']=['Q-other']
        else:prior['target_item_id']='RULE-other'
        p['items'].append(prior)
    rows=answer_binding_tasks(p)
    assert len(rows)==1 and rows[0]['target_item_id']==original['id']
    assert rows[0]['reusable_candidate_ids']==[]
    assert rows[0]['answer_source_refs']==q['answer_source_refs']


def test_only_same_question_version_and_target_can_reuse_a_pending_candidate(tmp_path):
    p=scenario(tmp_path);q=p['questions'][0];item=p['items'][1]
    p['items'].append(dict(item,id='RULE-prior',action='revise',target_item_id=item['id'],
        selection_status='candidate',answer_refs=[q['id']],answer_versions={q['id']:digest(q['answer'])}))
    assert answer_binding_tasks(p)[0]['reusable_candidate_ids']==['RULE-prior']
    assert q['understanding_status']=='pending' and item['statement']!=q['answer']


def test_shared_target_keeps_each_answer_pair_and_excludes_applied_pairs(tmp_path):
    p=scenario(tmp_path);q=p['questions'][0]
    q['related_refs'].append('AC-0001')
    p['questions'].append(dict(q,id='Q-other',related_refs=['RULE-0001'],answer='另一项已保存回答。'))
    assert {(row['question_id'],row['target_item_id']) for row in answer_binding_tasks(p)}=={
        (q['id'],'RULE-0001'),(q['id'],'AC-0001'),('Q-other','RULE-0001')}
    q['applied_item_ids']=['RULE-0001']
    assert {(row['question_id'],row['target_item_id']) for row in answer_binding_tasks(p)}=={
        (q['id'],'AC-0001'),('Q-other','RULE-0001')}
    p['questions'][1]['understanding_status']='applied'
    assert [(row['question_id'],row['target_item_id']) for row in answer_binding_tasks(p)]==[(q['id'],'AC-0001')]


@pytest.mark.parametrize('excluded',[{'out_of_scope_reason':'本期明确不处理此事项。'},
                                    {'superseded_by':['Q-child-one','Q-child-two']}])
def test_outside_scope_or_superseded_answer_is_not_a_binding_task(tmp_path,excluded):
    p=scenario(tmp_path);q=p['questions'][0]
    q.update(excluded)
    assert answer_binding_tasks(p)==[]
    messages,_,_=assemble(p,'clarify','核对本期问题。',DEFAULT,tmp_path)
    context=json.loads(messages[1]['content'][0]['text'])
    assert context['stage_focus']['answer_binding_tasks']==[]
    assert context['stage_focus']['answer_revision_targets']==[]
    assert context['stage_focus']['answer_updates_pending']==[]
    assert context['questions']==p['questions']  # Keep the original decision visible.


@pytest.mark.parametrize('invalid',['stale_other_answer','missing_other_question','unanswered_other','wrong_other_target'])
def test_candidate_with_an_invalid_second_binding_cannot_be_reused_for_first_answer(tmp_path,invalid):
    p=scenario(tmp_path);q=p['questions'][0];item=p['items'][1]
    other=dict(q,id='Q-other',answer='另一项原回答。')
    p['questions'].append(other)
    candidate=dict(item,id='RULE-prior',action='revise',target_item_id=item['id'],
                   selection_status='candidate',answer_refs=[q['id'],other['id']],
                   answer_versions={q['id']:digest(q['answer']),other['id']:digest(other['answer'])})
    p['items'].append(candidate)
    assert all(row['reusable_candidate_ids']==['RULE-prior'] for row in answer_binding_tasks(p))
    if invalid=='stale_other_answer':other['answer']='另一项已修改后的回答。'
    elif invalid=='missing_other_question':p['questions'].pop()
    elif invalid=='unanswered_other':other['status']='open'
    else:other['related_refs']=['AC-0001']
    current=next(row for row in answer_binding_tasks(p) if row['question_id']==q['id'])
    assert current['reusable_candidate_ids']==[]
