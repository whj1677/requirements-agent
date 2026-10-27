"""Explicit draft suggestions stay separate from human answers and review."""
import asyncio
import copy
import json
import pytest
from app.actions import Actions
from app.business_draft import adapt,assemble
from app.core import Problem
from app.provider import DEFAULT
from app.workflow import Workflow
from tests.test_business_draft import packet,draft_row
from tests.test_business_draft_workflow import BusinessProvider
from tests.test_requirement_recommendation import project


def suggestion(text='建议沿用已授权站点范围，未授权站点不展示。',basis='recommendation',sources=None):
    return dict(text=text,basis=basis,sources=sources or [])


def test_question_draft_is_keyed_to_existing_id_and_never_changes_answers_or_items(tmp_path):
    async def run():
        store,before=project(tmp_path);qid=before['questions'][0]['id']
        raw=packet();raw['completion']={'answers':{'Q1':suggestion()}}
        provider=BusinessProvider([raw]);workflow=Workflow(store,provider);actions=Actions(store,workflow)
        body=dict(expected_revision=before['revision'],action='organize',message='',document_type='prd',
            draft_request={'mode':'question','question_id':qid},max_calls=None)
        plan=actions.plan(before['id'],body);assert plan['label']=='为这道问题起草回答'
        task=actions.start(before['id'],body,plan['plan_hash'],'completion-question-0001',True)
        await actions.jobs[task['id']]
        after=store.get(before['id']);run=store.records(before['id'],'run')[-1]
        assert run['result_applied'] and run['calls']==1,run
        assert after['questions']==before['questions'] and after['items']==before['items']
        assert after['documents']==before['documents'] and after.get('product_context')==before.get('product_context')
        assert after['refinement_completion']['answers'][qid]['text']=='建议：'+raw['completion']['answers']['Q1']['text']
        header=json.loads(provider.received[0][0]['content'].split('可信任务头：')[1])
        assert header['schema']['properties']['requirements']['maxItems']==0
        assert header['schema']['properties']['completion']['properties']['answers']['required']==['Q1']
        assert len(store.get_record(before['id'],task['id'],'user_task')['retry_input']['draft_request'])==2
    asyncio.run(run())


@pytest.mark.parametrize('bad',['missing','unknown_key','bad_source','unknown'])
def test_missing_or_invalid_answers_remain_visible_diagnostics_not_human_decisions(tmp_path,bad):
    store,p=project(tmp_path);request={'mode':'question','question_id':p['questions'][0]['id']}
    messages,excerpts,_=assemble(p,'ingest','',DEFAULT,tmp_path,draft_request=request)
    raw=packet();raw['completion']={'answers':{'Q1':suggestion()}}
    if bad=='missing':raw['completion']['answers']={}
    if bad=='unknown_key':raw['completion']['answers']={'Q404':suggestion()}
    if bad=='bad_source':raw['completion']['answers']['Q1']=suggestion(basis='fact',sources=[{'source':'S404','locator':'L1'}])
    if bad=='unknown':raw['completion']['answers']['Q1']=suggestion(basis='unknown')
    before=copy.deepcopy(p);_,report=adapt(raw,p,excerpts,'ingest',draft_request=request)
    assert report['completion']['missing_answer_ids']==[p['questions'][0]['id']]
    assert report['complete'] is False and not report['completion']['answers']
    assert p==before and store.get(p['id'])==p


def test_question_request_cannot_smuggle_whole_table_changes(tmp_path):
    _,p=project(tmp_path);request={'mode':'question','question_id':p['questions'][0]['id']}
    _,excerpts,_=assemble(p,'ingest','',DEFAULT,tmp_path,draft_request=request)
    with pytest.raises(Problem):adapt(packet(draft_row()),p,excerpts,'ingest',draft_request=request)


def test_invalid_question_rejected_before_provider_call(tmp_path):
    store,p=project(tmp_path);provider=BusinessProvider([]);actions=Actions(store,Workflow(store,provider))
    with pytest.raises(Problem):actions.plan(p['id'],dict(expected_revision=p['revision'],action='organize',message='',document_type='prd',draft_request={'mode':'question','question_id':'foreign-question'}))
    assert not provider.received


def test_prewrite_drafter_gets_the_displayed_recommendations_even_without_typing(tmp_path):
    _,p=project(tmp_path);qid=p['questions'][0]['id'];before=copy.deepcopy(p)
    p['prewrite_review']=dict(findings=[dict(id='PW-FROZEN',status='open',question_id=qid,
        message='验收与已保存决定冲突',suggested_resolution='改写旧验收',recommended_answer='本期只查询结果未知的派单。',
        quotes=[],related_refs=[p['items'][0]['id']],severity='blocker')])
    expected=copy.deepcopy(p)
    messages,_,_=assemble(p,'ingest','',DEFAULT,tmp_path,draft_request={'mode':'prewrite'})
    header=json.loads(messages[0]['content'].split('可信任务头：')[1]);context=json.loads(messages[1]['content'][0]['text'])
    assert context['prewrite_confirmation_points'][0]['recommended_answer']=='本期只查询结果未知的派单。'
    assert 'target_ref' in header['prewrite_drafting_rules'] and 'basis' in header['prewrite_drafting_rules']
    assert p==expected and p['questions']==before['questions']
