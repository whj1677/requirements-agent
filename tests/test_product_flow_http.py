"""Current product entrypoints; no gate mocks, synthetic loopback model only."""
from tests.test_ui02_actions import case, plan, start, finished
from tests.product_flow_helpers import http_check


def test_normal_and_diagnostic_entrypoints_enforce_same_steps(case):
    c,app,model,root=case
    for action,stage in [('explore','brainstorm'),('prototype','ui'),('document','prd'),('review','review')]:
        body,preview=plan(c,root,action)
        assert preview['missing']
        assert start(c,root,body,preview).status_code==409
        p=c.get(root).json()
        response=c.post(root+'/runs',json=dict(expected_revision=p['revision'],stage=stage))
        assert response.status_code==409 and response.json()['code']=='STAGE_BLOCKED'
    assert model['requests']==[]
    body,preview=plan(c,root)
    assert start(c,root,body,preview).status_code==200
    assert finished(c,root)['status']=='succeeded'
    p=c.get(root).json();before=p['items']
    assert all(i['content_version']==1 for i in before)
    assert not any(s['complete'] for s in p['product_flow'])
    body,preview=plan(c,root,'document','')
    count=len(model['requests'])
    assert start(c,root,body,preview).status_code==409
    assert len(model['requests'])==count and c.get(root).json()['items']==before
    http_check(c,root,through=2)
    p=c.get(root).json()
    assert [s['complete'] for s in p['product_flow'][:3]]==[True,True,False]
    assert any(p['questions'][0]['question'] in m for m in p['product_flow'][2]['missing'])
    assert c.post(root+'/stage-checks/3',json=dict(expected_revision=p['revision'],expected_hash=p['product_flow'][2]['content_hash'])).status_code==409
    assert not app.state.store.records(p['id'],'confirmation')


def test_answer_reports_impacted_requirement_without_rewriting_it(case):
    c,app,model,root=case
    body,preview=plan(c,root)
    assert start(c,root,body,preview).status_code==200
    finished(c,root)
    p=c.get(root).json();q=p['questions'][0]
    r=c.post(root+'/questions/'+q['id'],json=dict(expected_revision=p['revision'],answer='合成预定回答：本期保留既有重复姓名行为，不新增去重。'))
    assert r.status_code==200
    after=c.get(root).json();answer=after['questions'][0]
    assert after['items']==p['items']
    assert answer['affected_requirements'][0]['id']==p['items'][0]['id']
    assert answer['answer_source_refs'] and answer['answer_effect'].startswith('待核对')
    old=p['product_flow'][0]['content_hash']
    assert c.post(root+'/product-context',json=dict(expected_revision=after['revision'],values={'intent':'改变本期目的'})).status_code==200
    current=c.get(root).json()
    assert c.post(root+'/stage-checks/1',json=dict(expected_revision=current['revision'],expected_hash=old)).json()['code']=='STALE_REVISION'


def test_correcting_candidate_title_and_change_type_preserves_identity_and_selection(case):
    c,app,model,root=case
    body,preview=plan(c,root)
    assert start(c,root,body,preview).status_code==200
    finished(c,root)
    p=c.get(root).json();item=p['items'][0]
    before=dict(item)
    response=c.post(root+'/items/'+item['id'],json=dict(expected_revision=p['revision'],
        selection_status='candidate',title='联系人选填备注',change_type='new',statement=item['statement']))
    assert response.status_code==200
    current=next(i for i in response.json()['items'] if i['id']==item['id'])
    assert current['title']=='联系人选填备注' and current['change_type']=='new'
    assert current['content_version']==before['content_version']+1
    assert current['selection_status']=='candidate' and current['source_refs']==before['source_refs']
    assert not app.state.store.records(p['id'],'confirmation')
