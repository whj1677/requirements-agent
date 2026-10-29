"""Whole-table preparation stays a draft until explicit human confirmation."""
import asyncio
import copy
import json
import pytest
from app.actions import Actions
from app.business_draft import assemble
from app.core import Problem
from app.provider import DEFAULT
from app.refinement_completion import validate
from app.store import Store
from app.workflow import Workflow
from tests.check_prewrite_batch_browser import BatchProvider,synthetic


def test_one_business_response_covers_all_points_and_preserves_saved_answers(tmp_path):
    async def run():
        store=Store(tmp_path);before=synthetic(store);provider=BatchProvider(tmp_path);provider.project=lambda:store.get(before['id'])
        actions=Actions(store,Workflow(store,provider))
        body=dict(expected_revision=before['revision'],action='organize',message='整批起草全部检查修改。',document_type='prd',draft_request={'mode':'prewrite'})
        plan=actions.plan(before['id'],body);assert plan['stages']==['ingest']
        task=actions.start(before['id'],body,plan['plan_hash'],'whole-check-draft-0001',True)
        await actions.jobs[task['id']]
        task=store.get_record(before['id'],task['id'],'user_task');after=store.get(before['id'])
        assert task['status'] in ('succeeded','partial'),task
        assert len(provider.requests)==1 and task['calls']==1
        assert after['questions']==before['questions'] and after['documents']==before['documents']
        assert after['items'][:len(before['items'])]==before['items']
        completion=after['refinement_completion'];assert completion['mode']=='prewrite' and completion['applied_revision']==after['revision']
        assert completion['complete'] and set(completion['covered_requirement_ids'])==set(before['product_context']['scope_ids'])
        assert before['questions'][0]['id'] not in completion['answers']
        assert before['questions'][1]['id'] in completion['answers']
    asyncio.run(run())


def test_prewrite_prompt_has_all_saved_answers_and_no_unrelated_new_requirements(tmp_path):
    p=synthetic(Store(tmp_path));before=copy.deepcopy(p)
    messages,_,_=assemble(p,'ingest','整批起草',DEFAULT,tmp_path,draft_request={'mode':'prewrite'})
    header=json.loads(messages[0]['content'].split('可信任务头：')[1]);context=json.loads(messages[1]['content'][0]['text'])
    assert len(context['prewrite_confirmation_points'])==2
    assert context['saved_business_answers'][0]['answer']==p['questions'][0]['answer']
    assert header['schema']['properties']['requirements']['items']['properties']['row_key']['enum']==header['required_row_keys']
    assert header['required_summary_fields']==[]
    assert header['schema']['properties']['requirements']['items']['properties']['questions']['maxItems']==0
    assert p==before


def test_empty_batch_rejected_without_task_or_request(tmp_path):
    store=Store(tmp_path);p=store.create('没有检查项')
    with pytest.raises(Problem):validate(p,{'mode':'prewrite'})
    assert not store.records(p['id'],'user_task') and not store.records(p['id'],'run')


def test_review_recommendation_remains_a_draft_not_a_human_answer(tmp_path):
    from app.prewrite import apply_result,status
    from tests.test_prewrite_consistency import review
    from tests.test_detail_document import project_fixture
    from app.contracts import VALIDATOR
    p=project_fixture(tmp_path);before=copy.deepcopy(p)
    response=review(p,blocker=True)
    response['findings'][0]['recommended_answer']='建议结果未知时先查询原请求，再决定是否再次提交。'
    VALIDATOR.validate(response)
    report=apply_result(p,response,'RUN-recommendation')
    finding=report['findings'][0];q=next(q for q in p['questions'] if q['id']==finding['question_id'])
    assert finding['recommended_answer']==response['findings'][0]['recommended_answer']
    assert q['answer'] is None and q['status']=='open'
    assert p['items']==before['items'] and p['documents']==before['documents']
    assert status(p)['status']=='needs_confirmation'


def test_explicit_decision_does_not_bind_another_finding_on_a_shared_clause(tmp_path):
    from app.prewrite import apply_result
    from tests.test_prewrite_consistency import review
    from tests.test_detail_document import project_fixture
    p=project_fixture(tmp_path);response=review(p,blocker=True)
    decision='REQ-0001 的异常重试需要哪些前置条件？'
    first=response['findings'][0];first.update(message='权限口径需要落实已保存回答。',suggested_resolution='权限沿用已保存角色规则。')
    second=copy.deepcopy(first);second.update(message='重试前置条件尚不明确。',decision_question=decision,suggested_resolution='建议先查询原请求。')
    response['findings'].append(second);response['result']['required_decisions']=[decision]
    report=apply_result(p,response,'RUN-shared-clause')
    a,b=report['findings'];qa=next(q for q in p['questions'] if q['id']==a['question_id']);qb=next(q for q in p['questions'] if q['id']==b['question_id'])
    assert qa['id']!=qb['id'] and qa['question']!=decision and qb['question']==decision


def test_prewrite_draft_cannot_claim_complete_with_missing_enum_values(tmp_path):
    from app.business_draft import adapt
    from tests.test_business_draft import setup,draft_row,packet
    from tests.test_detail_business_draft import sample_detail
    p,local,_,_,excerpts,_=setup(tmp_path)
    row=draft_row();row['detail_contract']=sample_detail();row['detail_contract']['fields'][0]['data_type']='enum'
    value,report=adapt(packet(row),p,excerpts,'clarify',local,draft_request={'mode':'prewrite'})
    assert report['accepted'] and value['proposals'],'usable draft text must be retained'
    assert not report['complete'] and any(i['code']=='DETAIL_ENUM_MISSING' for i in report['field_rejected'])
