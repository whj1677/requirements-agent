"""Explicit synthetic human checks; never called from the application or live runs."""
from app.product_flow import INTAKE, SCOPE, BEHAVIOR, checkpoint, status, review_document
from app.core import brief_hash
from app.contracts import review_target


def isolate_legacy_engine(app):
    """Keep pre-PRODUCT-01 schema/budget oracles at the engine boundary.

    Their candidate-only drafts intentionally cannot enter the new product path.
    Only these old engine tests suppress stage navigation, never auth/semantic gates.
    test_product_flow_http separately verifies the real endpoints cannot bypass it.
    """
    from unittest.mock import patch
    original_start=app.state.workflow.start
    original_plan=app.state.actions.plan
    def start(*a,**kw):
        with patch('app.workflow.execution_issues',return_value=[]):return original_start(*a,**kw)
    def plan(*a,**kw):
        with patch('app.actions.execution_issues',return_value=[]):return original_plan(*a,**kw)
    app.state.workflow.start=start
    app.state.actions.plan=plan


def check_prepared(store,pid,through=5):
    p=store.get(pid)
    with store.edit(pid,p['revision'],'合成阶段输入准备') as (p,_):
        p['product_context']={k:'合成核对资料：'+v for k,v in {**INTAKE,**SCOPE}.items()}
        p['product_context']['scope_ids']=[i['id'] for i in p['items'] if i['kind']=='requirement' and i['selection_status']=='selected']
        for i in p['items']:
            if i['kind']=='requirement':i['behavior']={k:'测试夹具明确给出的 '+v for k,v in BEHAVIOR.items()}
        p['sketch_review']=dict(applicable=True,changes='夹具功能区域',preserved='夹具外部导航保持',behavior='夹具入口与操作')
    # Refresh deterministic derived fixture snapshots after changing their synthetic input.
    with store.edit(pid,p['revision'],'合成派生对象准备',bump=False) as (p,_):
        if p.get('ui'):p['ui']['brief_hash']=brief_hash(p)
        for d in p['documents'].values():d['brief_hash']=brief_hash(p)
        if p.get('review'):p['review']['target_hash']=review_target(p)
        for n in range(1,through+1):
            if n==5:
                for kind,doc in p['documents'].items():review_document(p,kind,doc['id'])
            checkpoint(p,n,status(p)[n-1]['content_hash'])
    return store.get(pid)


def http_check(client,root,through=3):
    """Exercise current product prerequisites over HTTP with explicit synthetic answers."""
    def post(path,body):
        response=client.post(root+path,json=dict(expected_revision=client.get(root).json()['revision'],**body))
        assert response.status_code==200,response.text
    p=client.get(root).json()
    reqs=[i for i in p['items'] if i['kind']=='requirement' and i['applies_to']=='to_be']
    post('/product-context',dict(values={k:'合成明确输入：'+v for k,v in {**INTAKE,**SCOPE}.items()},scope_ids=[i['id'] for i in reqs]))
    # Explicit synthetic PM decision at scope review; unknown model classification
    # cannot silently count as a completed scope checkpoint.
    if through>=2:
        for i in reqs:
            if i.get('change_type')=='unspecified':post('/items/'+i['id']+'/behavior',dict(values={},change_type='modified'))
    if through>=3:
        for i in reqs:post('/items/'+i['id']+'/behavior',dict(values={k:'合成已给出的 '+v for k,v in BEHAVIOR.items()},change_type='modified'))
    for n in (1,2):
        if n<=through:post('/stage-checks/'+str(n),dict(expected_hash=client.get(root).json()['product_flow'][n-1]['content_hash']))
    if through>=3:
        for i in client.get(root).json()['items']:
            if i['kind'] in ('requirement','rule','acceptance'):post('/items/'+i['id'],dict(selection_status='selected'))
        post('/stage-checks/3',dict(expected_hash=client.get(root).json()['product_flow'][2]['content_hash']))
    return client.get(root).json()
