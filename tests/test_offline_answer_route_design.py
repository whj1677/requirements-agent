"""Design probe only: isolated public saved-answer route, guarded mock HTTP.

No daily-project writes, provider network, actual usage or PM approval. Minimal
synthetic clauses test the existing state contract, not live content quality.
"""
import copy
import json
import os
import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core import digest
from app.ingest import contract as ingest_contract
from app.main import create_app
from app.product_flow import status
from app.provider import DEFAULT
from tests.helpers import EXAMPLES, prepared
from tests.test_request_cap import mock_transport, public_start

ANSWER = ('synthetic test operator：本期采用结果未知口径，覆盖历史“超时即失败”。'
          '服务明确拒绝时本批不创建且既有资源不变；响应丢失或超时为结果未知，'
          '沿原申请ID查询，状态确认前不重复提交。此为合成测试决定，非真实PM批准。')


@pytest.fixture
def route_case(tmp_path, monkeypatch, request):
    original_connect = socket.socket.connect
    def guarded_connect(sock, address):
        # Windows asyncio uses a loopback socket pair for its self-pipe.
        # Provider traffic is MockTransport; every non-loopback IP is forbidden.
        if sock.family in (socket.AF_INET, socket.AF_INET6) and address[0] not in ('127.0.0.1', '::1'):
            raise AssertionError('Design probe forbids all IP network connections')
        return original_connect(sock, address)
    monkeypatch.setattr(socket.socket, 'connect', guarded_connect)
    app = create_app(tmp_path, access_token='synthetic-design-login', env_path=tmp_path/'absent.env')
    app.state.provider.set_key(DEFAULT, 'synthetic-design-key')
    p = prepared(app.state.store)
    with app.state.store.edit(p['id'], p['revision'], 'isolated synthetic fixture, not restored daily state') as (p, _):
        p['documents'] = {}; p['ui'] = None; p['review'] = None
        p.pop('product_context', None); p.pop('stage_checks', None)
        source = p['sources'][0]
        source['title'] = '合成批量申请原始材料'
        source['excerpts'][0]['text'] = '本期新增同租户每批最多20项云资源申请，任一配额不足整批拒绝不创建。超时口径待核对。'
        p['items'] = p['items'][:2]
        req, rule = p['items']
        req.update(title='合成批量申请', statement='同租户每批最多20项云资源申请；超时口径待核对。',
                   related_refs=['RULE-0001'], selection_status='candidate', change_type='new',
                   behavior={'actor':'租户管理员','result':'任一配额不足整批拒绝不创建。','exceptions':'超时口径待核对。'},
                   behavior_review_required=True)
        rule.update(title='合成结果处理', statement='服务明确拒绝时本批不创建；超时口径待核对。', selection_status='candidate')
        p['questions'][0].update(topic_key='synthetic-timeout', question='本期超时口径采用什么？',
                                related_refs=['REQ-0001','RULE-0001'], blocking=True)
    trace = []
    with TestClient(app) as client:
        csrf = client.post('/api/session/login', json={'key':'synthetic-design-login'}).json()['csrf']
        client.headers.update({'Origin':'http://testserver','X-CSRF-Token':csrf})
        root = '/api/projects/'+p['id']
        def post(path, body):
            response = client.post(root+path, json=body)
            trace.append({'path':path,'request':body,'http':response.status_code,'response':response.json()})
            return response
        yield client, app, root, post
        out = os.environ.get('PM_REQ_DESIGN_EVIDENCE')
        if out:
            with (Path(out)/(request.node.name+'.json')).open('x',encoding='utf-8') as f:
                json.dump({'kind':'isolated mock only','trace':trace,'final':client.get(root).json(),
                    'tasks':client.get(root+'/actions').json(),'runs':client.get(root+'/runs').json()},
                    f,ensure_ascii=False,indent=2)


def save_answer(case, text=ANSWER):
    client, app, root, post = case
    before = client.get(root).json()
    response = post('/answers', {'expected_revision':before['revision'],'answers':{'Q-0001':text}})
    assert response.status_code == 200, response.text
    return before, response.json()


def enable_local_plan(case):
    client, app, root, post = case
    p = client.get(root).json()
    response = post('/product-context', {'expected_revision':p['revision'],
        'values':{'product':'合成园区云平台','module':'云资源申请','intent':'本期同租户批量申请'}})
    assert response.status_code == 200
    p = response.json()
    response = post('/stage-checks/1', {'expected_revision':p['revision'],'expected_hash':status(p)[0]['content_hash']})
    assert response.status_code == 200


def answer_response(p):
    response = copy.deepcopy(EXAMPLES['clarify'])
    response.update(summary='合成答复落实候选，待人工逐项核对。', questions=[], findings=[])
    response['result'] = {'scope_summary':'按已保存合成回答提出修订，不自动采纳。',
                          'outstanding_decisions':[], 'next_focus':'逐项人工核对修订候选。'}
    q = p['questions'][0]
    refs = copy.deepcopy(q['answer_source_refs'])
    req, rule = p['items'][:2]
    response['proposals'] = []
    for old in (req, rule):
        proposal = dict(temp_id='TMP-'+old['id'], action='revise', target_item_id=old['id'],
            kind=old['kind'], title=old['title'], statement=old['statement'].replace('超时口径待核对。',
                '超时为结果未知，沿原申请ID查询，状态确认前不重复提交。'),
            applies_to='to_be', epistemic_status='reported', source_refs=refs,
            related_refs=old['related_refs'], answer_refs=[q['id']])
        if old['kind']=='requirement':
            proposal.update(change_type='new', scope_evidence=[{'state':'change',
                'quote':p['sources'][0]['excerpts'][0]['text'],'source_ref':old['source_refs'][0]}],
                classification_reason='原始材料明确本期新增，修订动作不改变新增性质',
                behavior=dict(old['behavior'],exceptions=ANSWER))
            proposal['source_refs'] = old['source_refs']+refs
        response['proposals'].append(proposal)
    response['used_source_refs'] = list({(r['source_id'],r['excerpt_id']):r
        for i in response['proposals'] for r in i['source_refs']}.values())
    return response


def run_candidate(case, monkeypatch):
    client, app, root, post = case
    enable_local_plan(case)
    output = answer_response(client.get(root).json())
    requests = mock_transport(monkeypatch, [output])
    # Public cap/approval/start. Only local question scope bypasses unfinished scope review.
    p = client.get(root).json()
    body = {'expected_revision':p['revision'],'action':'clarify','document_type':'prd',
        'message':'按已保存合成回答修订该问题直接关联的原需求与规则，不自动采纳。',
        'target':{'kind':'question','id':'Q-0001'},'max_calls':1}
    plan_response = post('/actions/plan',body)
    assert plan_response.status_code==200 and not plan_response.json()['missing'],plan_response.text
    whole_plan = post('/actions/plan',dict(body,target=None))
    assert whole_plan.status_code==200 and whole_plan.json()['missing']
    import uuid, time
    started = post('/actions',dict(body,plan_hash=plan_response.json()['plan_hash'],
                                    idempotency_key=uuid.uuid4().hex,authorize=True))
    assert started.status_code==200,started.text
    for _ in range(350):
        task=client.get(root+'/actions').json()[-1]
        if task['status'] not in ('queued','running'):break
        time.sleep(.01)
    assert len(requests)==1 and task['calls']==1
    assert task['status'] in ('succeeded','partial'),task
    return requests, client.get(root).json()


def test_source_decision_is_not_saved_answer_and_public_plan_requires_checkpoint(route_case,monkeypatch):
    client, app, root, post = route_case
    requests=mock_transport(monkeypatch, [])
    before=client.get(root).json()
    # This is a fixture source, not a system answer or automatic adoption.
    with app.state.store.edit(before['id'],before['revision'],'isolated explicit source decision') as (p,_):
        p['sources'][0]['excerpts'][0]['text'] += ANSWER
    assert client.get(root).json()['questions'][0]['status']=='open'
    before, after=save_answer(route_case)
    assert after['revision']==before['revision']+1 and after['items']==before['items']
    assert after['sources'][:-1]==before['sources']
    q=after['questions'][0]
    assert q['status']=='answered' and q['understanding_status']=='pending' and q['id']=='Q-0001'
    assert q['answer_source_refs'][0]['source_id']==after['sources'][-1]['id']
    assert q['affected_requirements'][0]['version']==before['items'][0]['content_version']
    for target in (None,{'kind':'question','id':q['id']}):
        r=post('/actions/plan',dict(expected_revision=after['revision'],action='clarify',target=target,max_calls=1))
        assert r.status_code==200 and r.json()['missing']
    assert requests==[]


def test_public_saved_answer_to_mock_candidates_and_explicit_each_adoption(route_case,monkeypatch):
    client, app, root, post = route_case
    before, answered=save_answer(route_case)
    requests, candidate_state=run_candidate(route_case,monkeypatch)
    q=candidate_state['questions'][0]
    assert q['understanding_status']=='candidate_ready' and not q['applied_item_ids']
    assert candidate_state['items'][:2]==before['items']
    header=json.loads(requests[0]['messages'][0]['content'].split('可信任务头：',1)[1])
    context=json.loads(requests[0]['messages'][1]['content'][0]['text'])
    assert header['question_action_contract']['answered_decision_ids']==['Q-0001']
    assert header['schema']['$defs']['Question']['properties']['target_question_id']['enum']==[None]
    assert {(r['question_id'],r['target_item_id']) for r in context['stage_focus']['answer_binding_tasks']}=={
        ('Q-0001','REQ-0001'),('Q-0001','RULE-0001')}
    for index,target in enumerate(('REQ-0001','RULE-0001')):
        p=client.get(root).json(); i=next(i for i in p['items'] if i.get('target_item_id')==target)
        assert i['answer_versions']=={'Q-0001':digest(ANSWER)} and i['selection_status']=='candidate'
        if i['kind']=='requirement':
            r=post('/items/'+i['id']+'/behavior',dict(expected_revision=p['revision'],
                values=i['behavior'],change_type='new',review_confirmed=True));assert r.status_code==200
            p=r.json()
        r=post('/items/'+i['id'],dict(expected_revision=p['revision'],selection_status='selected'))
        assert r.status_code==200,r.text
        p=r.json();old=next(i for i in p['items'] if i['id']==target)
        prior=next(x for x in before['items'] if x['id']==target)
        assert old['selection_status']=='selected' and old['content_version']==prior['content_version']+1
        assert next(x for x in p['items'] if x['id']==i['id'])['selection_status']=='deferred'
        assert p['questions'][0]['understanding_status']=='candidate_ready'
        assert not p['questions'][0]['applied_item_ids']
    q=p['questions'][0]
    review=client.get(root+'/questions/'+q['id']+'/application-review').json()
    r=post('/questions/'+q['id']+'/application-review',dict(expected_revision=p['revision'],
        answer_hash=review['answer_hash'],review_confirmed=True,
        clause_hashes={t['item']['id']:t['clause_hash'] for t in review['targets']},
        answer_implementations={q['id']:[dict(item_id=t['item']['id'],field='statement',
                                           quote=t['item']['statement']) for t in review['targets']]}))
    assert r.status_code==200,r.text;p=r.json()
    assert p['questions'][0]['understanding_status']=='applied'
    assert not p.get('active_baseline_id') and not p['documents']


def test_changed_answer_rejects_stale_candidate_and_preserves_previous_answer(route_case,monkeypatch):
    client, app, root, post = route_case
    _, answered=save_answer(route_case)
    _, ready=run_candidate(route_case,monkeypatch)
    old_answer_ref=copy.deepcopy(ready['questions'][0]['answer_source_refs'])
    _, changed=save_answer(route_case, ANSWER+' 合成操作者明确补充：查询结果返回前保持待核对。')
    q=changed['questions'][0]
    assert q['answer_history'][-1]['answer']==ANSWER
    assert q['answer_history'][-1]['answer_source_refs']==old_answer_ref
    assert q['understanding_status']=='pending'
    candidate=next(i for i in changed['items'] if i.get('target_item_id')=='RULE-0001')
    stored_before=app.state.store.get(changed['id'])
    r=post('/items/'+candidate['id'],dict(expected_revision=changed['revision'],selection_status='selected'))
    assert r.status_code==409 and r.json()['code']=='STALE_ANSWER',r.text
    assert app.state.store.get(changed['id'])==stored_before


def test_open_question_empty_split_is_rejected_without_partial_write(route_case,monkeypatch):
    client, app, root, post=route_case
    enable_local_plan(route_case)
    p=client.get(root).json()
    response=ingest_contract()['example']
    response['questions']=[dict(temp_id='QTMP-reask',topic_key='synthetic-timeout',question='本期超时口径采用什么？',
        why='合成错误动作',options=[],blocking=True,related_refs=['REQ-0001','RULE-0001'],source_refs=[],
        target_question_id='Q-0001',decision_points=[])]
    requests=mock_transport(monkeypatch,[response])
    plan, started, task=public_start((client,app,root),1,action='organize')
    assert len(requests)==task['calls']==1 and task['status']=='failed' and task['error']=='REFERENCE_INVALID'
    after=client.get(root).json()
    assert after['items']==p['items'] and after['questions']==p['questions'] and after['revision']==p['revision']
    run=client.get(root+'/runs').json()[-1]
    assert run['repair_count']==0 and not run.get('result_applied',False)
