"""UI handoff contract tests; isolated fixtures, no paid model requests."""
import copy
from fastapi.testclient import TestClient
from app.main import create_app
from app.product_flow import review_document, status, checkpoint
from app.core import digest
from tests.helpers import prepared
from tests.product_flow_helpers import check_prepared


def client(tmp_path):
 app=create_app(tmp_path,access_token='offline-ui-test',env_path=tmp_path/'.env')
 c=TestClient(app);c.headers['Origin']='http://testserver'
 c.headers['X-CSRF-Token']=c.post('/api/session/login',json={'key':'offline-ui-test'}).json()['csrf']
 return c,app


def test_single_intake_preserves_raw_input_no_selection_or_model(tmp_path):
 c,app=client(tmp_path)
 p=c.post('/api/projects',json={'name':'联系人补充说明'}).json();root='/api/projects/'+p['id']
 r=c.post(root+'/intake',json={'expected_revision':0,'text':'在已有联系人页面增加一个选填备注。其他规则还不知道。'})
 assert r.status_code==200
 p=c.get(root).json()
 assert p['product_flow'][0]['complete'] and p['product_flow'][1]['available']
 assert not p['product_flow'][1]['complete'] and p['items']==[] and p['questions']==[]
 assert p['sources'][0]['excerpts'][0]['text']=='在已有联系人页面增加一个选填备注。其他规则还不知道。'
 assert not app.state.store.records(p['id'],'run') and not app.state.store.records(p['id'],'confirmation')
 assert 'product_context' not in p
 assert c.post(root+'/intake',json={'expected_revision':0,'text':'旧版本编辑'}).status_code==409


def test_manual_candidate_and_atomic_answers_preserve_identity_and_unknown(tmp_path):
 c,app=client(tmp_path);p=prepared(app.state.store);root='/api/projects/'+p['id']
 before=copy.deepcopy(p['items'])
 r=c.post(root+'/items',json={'expected_revision':p['revision'],'title':'新增说明','statement':'用户提出新增说明，未决定是否纳入本期。'})
 assert r.status_code==200
 p=c.get(root).json();item=p['items'][-1]
 assert item['selection_status']=='candidate' and item['content_version']==1 and item['id'].startswith('REQ-') and item['source_refs']
 r=c.post(root+'/answers',json={'expected_revision':p['revision'],'answers':{'Q-0001':'边界暂按已有材料明确规则','missing':'无效'}})
 assert r.status_code==400 and c.get(root).json()['questions'][0]['status']=='open'
 r=c.post(root+'/answers',json={'expected_revision':p['revision'],'answers':{'Q-0001':'只有本问题的预先准备回答'}})
 assert r.status_code==200
 p=c.get(root).json();assert p['items'][:-1]==before and p['items'][-1]==item
 assert p['questions'][0]['answer_source_refs'] and not p['active_baseline_id']


def test_document_reviews_bind_artifact_not_draft_revision(tmp_path):
 c,app=client(tmp_path);p=prepared(app.state.store)
 p=check_prepared(app.state.store,p['id'],through=4);root='/api/projects/'+p['id']
 assert not status(p)[4]['complete']
 for kind in ('mrd','prd'):
  p=c.get(root).json()
  assert c.post(root+'/documents/'+kind+'/review',json={'expected_revision':p['revision'],'document_id':p['documents'][kind]['id']}).status_code==200
 p=c.get(root).json();assert all(x['reviewed'] for x in p['document_review_status'].values())
 assert c.post(root+'/stage-checks/5',json={'expected_revision':p['revision'],'expected_hash':p['product_flow'][4]['content_hash']}).status_code==200
 p=c.get(root).json();assert p['product_flow'][4]['complete'] and not p['active_baseline_id']
 with app.state.store.edit(p['id'],p['revision'],'fixture second document') as (draft,_):
  draft['documents']['prd']['id']='DOC-NEW-SAME-REVISION'
 p=c.get(root).json()
 assert p['document_review_status']['mrd']['reviewed'] and not p['document_review_status']['prd']['reviewed']
 assert not p['product_flow'][4]['complete']
 assert c.post(root+'/documents/prd/review',json={'expected_revision':p['revision'],'document_id':'DOC-prd'}).status_code==409


def test_target_resolution_uses_actual_object_and_budget_unchanged(tmp_path):
 from app.actions import target_context
 from app.core import Problem
 import pytest
 c,app=client(tmp_path);p=prepared(app.state.store)
 before=copy.deepcopy(p)
 assert 'REQ-0001' in target_context(p,{'kind':'item','id':'REQ-0001'})
 with pytest.raises(Problem):target_context(p,{'kind':'item','id':'Q-0001'})
 with pytest.raises(Problem):target_context(p,{'kind':'ui','id':'old'})
 assert p['ui']['spec']['title'] in target_context(p,{'kind':'ui','id':digest(p['ui']['spec'])})
 assert p==before and not app.state.store.records(p['id'],'run')

def test_review_requires_current_document_and_never_confirms(tmp_path):
 c,app=client(tmp_path);p=prepared(app.state.store);root='/api/projects/'+p['id']
 # An available document alone cannot bypass earlier human checkpoints.
 assert c.post(root+'/documents/prd/review',json={'expected_revision':p['revision'],'document_id':p['documents']['prd']['id']}).status_code==409
 p=check_prepared(app.state.store,p['id'],through=4)
 with app.state.store.edit(p['id'],p['revision'],'fixture stale UI document') as (draft,_):
  draft['stale_document_kinds']=['prd']
 p=c.get(root).json()
 assert c.post(root+'/documents/prd/review',json={'expected_revision':p['revision'],'document_id':p['documents']['prd']['id']}).status_code==409
 assert not p['document_review_status']['prd']['reviewed']
 assert not app.state.store.records(p['id'],'confirmation')


def test_legacy_combined_review_remains_historical_not_fabricated(tmp_path):
 c,app=client(tmp_path);p=prepared(app.state.store);p=check_prepared(app.state.store,p['id'],through=5)
 with app.state.store.edit(p['id'],p['revision'],'legacy fixture',bump=False) as (draft,_):
  draft['stage_checks']['5'].pop('contract_version');draft.pop('document_reviews')
 p=c.get('/api/projects/'+p['id']).json()
 assert p['product_flow'][4]['complete']
 assert not any(x['reviewed'] for x in p['document_review_status'].values())
 # A new artifact breaks the legacy fingerprint; it cannot inherit review.
 with app.state.store.edit(p['id'],p['revision'],'different artifact') as (draft,_):
  draft['documents']['prd']['id']='DOC-other'
 p=c.get('/api/projects/'+p['id']).json()
 assert not p['product_flow'][4]['complete']


def test_contextual_plan_identity_is_bound_and_invalid_object_rejected(tmp_path):
 c,app=client(tmp_path);p=prepared(app.state.store);p=check_prepared(app.state.store,p['id'],through=3);root='/api/projects/'+p['id']
 body=dict(expected_revision=p['revision'],action='clarify',message='只讨论这个问题',document_type='prd',max_calls=2,target={'kind':'question','id':'Q-0001'})
 r=c.post(root+'/actions/plan',json=body);assert r.status_code==200
 assert '相接边界如何处理' in r.json()['target_label'] and r.json()['max_calls']==2
 original=r.json()['plan_hash']
 body['target']={'kind':'item','id':'REQ-0001'}
 assert c.post(root+'/actions/plan',json=body).json()['plan_hash']!=original
 body['target']={'kind':'item','id':'Q-0001'}
 assert c.post(root+'/actions/plan',json=body).status_code==409
 assert app.state.store.get(p['id'])==p and not app.state.store.records(p['id'],'run')


def test_answer_batch_stale_revision_preserves_all_answers(tmp_path):
 c,app=client(tmp_path);p=prepared(app.state.store);root='/api/projects/'+p['id'];snapshot=copy.deepcopy(p['questions'])
 r=c.post(root+'/answers',json={'expected_revision':p['revision']-1,'answers':{'Q-0001':'不应保存'}})
 assert r.status_code==409 and c.get(root).json()['questions']==snapshot
 assert not c.get(root).json()['active_baseline_id']
