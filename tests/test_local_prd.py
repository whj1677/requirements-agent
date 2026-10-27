import copy
import pytest

from app.core import Problem
from app.local_prd import generate
from app.prewrite import apply_result, input_hash, status
from app.document_reader import export_readiness, reader_document
from app.store import Store
from tests.test_detail_document import project_fixture
from tests.test_prewrite_consistency import review


def setup(tmp_path, pending=False):
    p=project_fixture(tmp_path);p['workflow_mode']='shared-draft'
    apply_result(p,review(p,blocker=pending),'RUN-FROZEN')
    store=Store(tmp_path)
    with store.edit(p['id'],store.get(p['id'])['revision'],'隔离的本机成文夹具',bump=False) as (current,_):current.update(p)
    return store,store.get(p['id'])


def body(p,**extra):
    return dict(expected_revision=p['revision'],idempotency_key='local-prd-test-001',review_draft_confirmed=True,**extra)


@pytest.mark.parametrize('pending',[False,True])
def test_local_review_keeps_exact_content_and_pending_decisions(tmp_path,pending):
    store,p=setup(tmp_path,pending);before=copy.deepcopy(p)
    result=generate(store,p['id'],body(p));after=result['project'];doc=after['documents']['prd']
    assert doc['model_calls']==0 and doc['assembly_origin']=='saved_content'
    for field in ('items','questions','product_context','sources','prewrite_review','stage_checks','grants'):
        assert after.get(field)==before.get(field)
    assert '联系人姓名' in str(reader_document(doc)) and '1 至 30 个字符' in str(reader_document(doc))
    assert doc['prewrite_snapshot']['blocking_count']==status(before)['blocking_count']
    if pending:
        assert not export_readiness(after,'prd')['ready']
        assert status(after)['status']=='needs_confirmation'
    repeated=generate(store,p['id'],body(p));assert repeated['repeated']
    assert repeated['artifact_id']==result['artifact_id']
    assert store.get(p['id'])['revision']==after['revision']
    assert len(store.records(p['id'],'document_artifact'))==1
    task=store.records(p['id'],'user_task')[-1]
    assert task['status']=='succeeded' and task['calls']==0 and task['document_artifact_id']==doc['id']


@pytest.mark.parametrize('fault',['stale','failed','missing','hard','unconfirmed','revision','busy','reference'])
def test_local_generation_does_not_bypass_guards_or_partially_write(tmp_path,fault):
    store,p=setup(tmp_path,True);request=body(p)
    with store.edit(p['id'],p['revision'],'冻结拒绝路径',bump=False) as (current,db):
        if fault=='stale':current['product_context']['intent']='本期已改变'
        if fault=='failed':current['prewrite_review']['status']='failed'
        if fault=='missing':current.pop('prewrite_review')
        if fault=='hard':current['prewrite_review']['findings'][0]['hard_block']=True
        if fault=='busy':store.record(p['id'],'user_task',dict(status='running'),db=db)
        if fault=='reference':
            current['items'][0]['source_refs']=[dict(source_id='ABSENT',excerpt_id='ABSENT')]
            current['prewrite_review']['input_hash']=input_hash(current)
    if fault=='unconfirmed':request['review_draft_confirmed']=False
    if fault=='revision':request['expected_revision']-=1
    before=store.get(p['id'])
    with pytest.raises(Problem):generate(store,p['id'],request)
    assert store.get(p['id'])==before
    assert not store.records(p['id'],'document_artifact') and not store.records(p['id'],'local_prd_receipt')


def test_idempotency_key_cannot_silently_refer_to_newer_content(tmp_path):
    store,p=setup(tmp_path);generate(store,p['id'],body(p))
    request=body(store.get(p['id']))
    with pytest.raises(Problem,match='生成编号'):generate(store,p['id'],request)


def test_model_document_task_can_start_after_local_review_and_legacy_task(tmp_path):
    import asyncio
    from app.actions import Actions
    from app.workflow import Workflow
    from tests.test_runtime import FakeProvider
    async def run():
        store,p=setup(tmp_path)
        # Older installed versions already wrote a local task without this key.
        store.record(p['id'],'user_task',dict(action='document',status='succeeded',label='旧本机成文',calls=0))
        local=generate(store,p['id'],body(p));p=local['project']
        plan=dict(plan_version='4',sections=[dict(section_key='function',context_refs=[],
            normative_refs=[i['id'] for i in p['items'] if i['selection_status']=='selected'],discussion_refs=[],explanations=[])])
        provider=FakeProvider([plan]);actions=Actions(store,Workflow(store,provider))
        request=dict(expected_revision=p['revision'],action='document',message='',document_type='prd',max_calls=None)
        preview=actions.plan(p['id'],request)
        task=actions.start(p['id'],request,preview['plan_hash'],'model-after-local-001',True)
        await actions.jobs[task['id']]
        saved=store.get_record(p['id'],task['id'],'user_task')
        assert saved['status']=='succeeded',saved
        assert len(provider.received)==1
        assert store.get(p['id'])['documents']['prd']['base_document_id']==local['artifact_id']
    asyncio.run(run())
