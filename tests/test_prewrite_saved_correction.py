"""Saved business answers and candidate changes need one explicit review."""
import copy
import pytest
from app.core import digest,Problem
from app.decision_review import save_raw_answer
from app.prewrite import normalize_decisions,normalize_quotes,validate_result
from app.prewrite_correction import plan,build,register
from app.store import Store
from tests.helpers import prepared
from tests.test_decision_review import api
from tests.test_prewrite_consistency import review


def scenario(tmp_path):
    store=Store(tmp_path);p=prepared(store)
    with store.edit(p['id'],p['revision'],'合成储能云已有修订') as (p,_):
        q=p['questions'][0];q['related_refs']=['REQ-0001'];save_raw_answer(p,q['id'],'相接边界允许保存。',store)
        original=next(i for i in p['items'] if i['id']=='REQ-0001')
        c=copy.deepcopy(original);c.update(id='REQ-CORRECTION',action='revise',statement='相接边界允许保存。',target_item_id=original['id'],
            selection_status='candidate',answer_refs=[q['id']],answer_versions={q['id']:digest(q['answer'])})
        p['items'].append(c)
        follow=copy.deepcopy(q);follow.update(id='Q-CONFIRM',question='是否采用这份已有修改？',answer=None,status='open')
        p['questions'].append(follow);save_raw_answer(p,follow['id'],'是',store)
        p['prewrite_review']=dict(id='PWR-CORRECTION',status='failed',input_hash='old',message='AI 检查关联失败',findings=[dict(
            id='PW-CORRECTION',status='open',severity='blocker',question_id=follow['id'],related_refs=[original['id']],
            suggested_resolution='采用 REQ-CORRECTION 替换 REQ-0001。')])
    return store,store.get(p['id'])


def test_preview_is_read_only_and_commit_applies_both_saved_answers_atomically(tmp_path):
    store,p=scenario(tmp_path);client=api(store);register(client.app,store)
    root=f'/api/projects/{p["id"]}/prewrite-review'
    response=client.post(root+'/findings/PW-CORRECTION/saved-correction-preview',json={'expected_revision':p['revision']})
    assert response.status_code==200,response.text
    preview=response.json();assert store.get(p['id'])==p
    assert len(preview['payload']['reviews'])==2 and len(preview['payload']['targets'])==1
    body=dict(expected_revision=p['revision'],preview_id=preview['preview_id'],preview_hash=preview['preview_hash'],
        idempotency_key='saved-correction-0001',review_confirmed=True)
    response=client.post(root+'/saved-correction-commit',json=body)
    assert response.status_code==200,response.text
    after=store.get(p['id']);revision=after['revision']
    assert next(i for i in after['items'] if i['id']=='REQ-0001')['statement']=='相接边界允许保存。'
    assert all(q['understanding_status']=='applied' for q in after['questions'])
    assert [q['answer'] for q in after['questions']]==[q['answer'] for q in p['questions']]
    assert after['documents']==p['documents'] and after['sources']==p['sources']
    assert client.post(root+'/saved-correction-commit',json=body).status_code==200
    assert store.get(p['id'])['revision']==revision
    assert len(store.records(p['id'],'prewrite_correction_receipt'))==1


@pytest.mark.parametrize('fault',['answer','source','target','candidate','missing_confirmation','tamper'])
def test_changed_or_unconfirmed_preview_never_writes_partial_changes(tmp_path,fault):
    store,p=scenario(tmp_path);client=api(store);register(client.app,store)
    root=f'/api/projects/{p["id"]}/prewrite-review'
    preview=client.post(root+'/findings/PW-CORRECTION/saved-correction-preview',json={'expected_revision':p['revision']}).json()
    body=dict(expected_revision=p['revision'],preview_id=preview['preview_id'],preview_hash=preview['preview_hash'],
        idempotency_key='saved-correction-0002',review_confirmed=True)
    if fault in ('answer','source','target','candidate'):
        with store.edit(p['id'],p['revision'],'合成并发修改',bump=False) as (current,_):
            if fault=='answer':current['questions'][0]['answer']='另一个决定'
            if fault=='source':current['sources'][0]['excerpts'][0]['text']+='变更'
            if fault=='target':current['items'][0]['statement']='另一份正文'
            if fault=='candidate':current['items'][-1]['statement']='另一份候选'
    elif fault=='missing_confirmation':body['review_confirmed']=False
    else:body['preview_hash']='tampered'
    before=store.get(p['id'])
    response=client.post(root+'/saved-correction-commit',json=body)
    assert response.status_code>=400,response.text
    assert store.get(p['id'])==before and not store.records(p['id'],'prewrite_correction_receipt')


@pytest.mark.parametrize('suffix',['','（保持旧值并标注失效 / 清空显示无数据）'])
def test_exact_required_question_metadata_is_made_blocking_without_rewriting_content(tmp_path,suffix):
    _,p=scenario(tmp_path);value=review(p,blocker=True);f=value['findings'][0]
    f.update(severity='warning',decision_question='断链数据如何展示？');value['result']['required_decisions']=[f['decision_question']+suffix]
    original=copy.deepcopy(value);fixed,changes=normalize_decisions(p,value)
    assert value==original and len(changes)==1
    assert fixed['findings'][0]['severity']=='blocker'
    assert fixed['findings'][0]['message']==f['message'] and fixed['findings'][0]['related_refs']==f['related_refs']
    validate_result(p,fixed)


def test_unknown_question_never_gets_guessed_association_and_context_quotes_resolve(tmp_path):
    _,p=scenario(tmp_path);value=review(p,blocker=True);value['result']['required_decisions']=['完全不同的问题？','另一个未关联问题？']
    fixed,changes=normalize_decisions(p,value)
    assert not changes
    with pytest.raises(Problem):validate_result(p,fixed)
    value['result']['required_decisions']=[];p['product_context']={'current_state':'已保存的现状原文。'}
    value['findings'][0]['evidence_quotes']=[dict(object_id='product_context',path='current_state',quote='已保存的现状原文。')]
    validate_result(p,value)
    value['findings'][0]['evidence_quotes'][0]['quote']='伪造的现状'
    with pytest.raises(Problem):validate_result(p,value)
