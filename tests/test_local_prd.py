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
    from app.document_preview import build_prd_preview
    return dict(expected_revision=p['revision'],idempotency_key='local-prd-test-001',
        review_draft_confirmed=True,preview_hash=build_prd_preview(p)['preview_hash'],**extra)


@pytest.mark.parametrize('unknown', [False, True])
def test_cloud_basic_requirement_uses_confirmed_behavior_without_model(tmp_path, unknown):
    from app.requirement_confirmation import row_fingerprint
    store, p = setup(tmp_path)
    with store.edit(p['id'], p['revision'], '合成无详细定义的云平台需求', bump=False) as (current, _):
        req = current['items'][0]
        req.pop('detail_contract'); req.pop('detail_required')
        req['behavior'] = dict(actor='管理员', entry='在联系人详情点击编辑。',
            data='联系人姓名为文本。', permissions='仅管理员可编辑，只读角色不能编辑。',
            flow='编辑姓名后点击保存。', exceptions='只读角色点击编辑时拒绝操作并保持原姓名。',
            result='保存后显示当前联系人姓名。')
        current['sources'][0]['excerpts'][0]['text'] = '\n'.join(req['behavior'].values())
        if unknown: req['behavior']['exceptions'] = '未知'
        req['requirement_confirmation']['content_hash'] = row_fingerprint(current, req)
        apply_result(current, review(current), 'RUN-FROZEN-BASIC')
    p = store.get(p['id']); before = copy.deepcopy(p)
    result = generate(store, p['id'], body(p)); doc = result['project']['documents']['prd']
    assert doc['model_calls'] == 0 and result['receipt']['model_calls'] == 0
    assert result['project']['items'] == before['items']
    assert result['project']['sources'] == before['sources']
    assert result['project']['prewrite_review'] == before['prewrite_review']
    if unknown:
        from app.prd import cloud_prd_gaps
        assert any('缺少已核对行为' in message for message in cloud_prd_gaps(doc['content'], result['project']))
        assert not export_readiness(result['project'], 'prd')['ready']
    sections = {s['title']: s for s in doc['content']['sections']}
    for title, fields in (('交互逻辑', ('entry', 'exceptions')), ('业务流程说明', ('flow', 'result'))):
        blocks = [b for b in sections[title]['blocks'] if b['kind'] == 'explanation']
        assert len(blocks) == 1
        assert blocks[0]['text'] == '\n'.join(p['items'][0]['behavior'][f] for f in fields)
        assert blocks[0]['evidence_refs']['normative_refs'] == [p['items'][0]['id']]
    if not unknown:
        import asyncio, io, zipfile
        from app.prd import compile_plan, plan_schema
        from app.response_application import apply_response
        from app.exports import document_files
        from app.contracts import validate_response
        flow=dict(nodes=[dict(id='start',label='开始编辑',kind='start'),
            dict(id='edit',label=req['behavior']['flow'],kind='end'),
            dict(id='end',label=req['behavior']['result'],kind='end')],
            edges=[dict(id='e1',**{'from':'start','to':'edit'}),dict(id='e2',condition='用户点击保存后显示已保存的联系人姓名',**{'from':'edit','to':'end'})])
        current=result['project'];iid=current['items'][0]['id']
        plan=dict(plan_version='4',sections=[dict(section_key='function',context_refs=[],
            normative_refs=[i['id'] for i in current['items'] if i['selection_status']=='selected'],discussion_refs=[],explanations=[])],
            flowcharts=[dict(requirement_id=iid,flow=flow)])
        for key,fields in (('interaction',('entry','exceptions')),('business_flow',('flow','result'))):
            plan['sections'].append(dict(section_key=key,context_refs=[],normative_refs=[],discussion_refs=[],
                explanations=[dict(text='\n'.join(req['behavior'][f] for f in fields),
                    evidence_refs=dict(context_refs=[],normative_refs=[iid],business_claim_refs=[]))]))
        wire=plan_schema(current,'prd')
        assert 'flowcharts' in wire['required']
        assert wire['properties']['flowcharts']['minItems']==wire['properties']['flowcharts']['maxItems']==1
        assert wire['properties']['flowcharts']['items']['properties']['requirement_id']['enum']==[iid]
        assert set(wire['properties']['flowcharts']['items']['properties']['flow']['properties']['nodes']['items']['properties'])=={'id','label','kind'}
        assert 'const' in wire['properties']['sections']
        plan['sections'][0]['explanations']=[dict(text='无据模型解释不得进入基础PRD',evidence_refs=dict(context_refs=[],normative_refs=[iid],business_claim_refs=[]))]
        bad=copy.deepcopy(plan);bad['flowcharts'][0]['flow']['edges'][0]['to']='absent'
        with pytest.raises(Problem):compile_plan(bad,current,'prd',include_sketch=False)
        bad=copy.deepcopy(plan);bad['flowcharts'][0]['requirement_id']='ABSENT'
        with pytest.raises(Problem):compile_plan(bad,current,'prd',include_sketch=False)
        response=compile_plan(plan,current,'prd',include_sketch=False)
        assert '无据模型解释' not in str(response)
        generated=next(b for s in response['result']['sections'] for b in s['blocks'] if b['kind']=='flowchart')['flow']
        assert generated['edges']==flow['edges']
        assert [n['label'] for n in generated['nodes']]==[n['label'] for n in flow['nodes']]
        assert generated['nodes'][1]['kind']=='action' and flow['nodes'][1]['kind']=='end'
        validate_response(response,'prd',current,[e for s in current['sources'] for e in s['excerpts']])
        from app.contracts import validate_document
        tampered=copy.deepcopy(response['result'])
        next(b for s in tampered['sections'] for b in s['blocks'] if b['kind']=='flowchart')['source_hash']='0'*64
        with pytest.raises(Problem,match='流程图来源版本已变化'):validate_document(tampered,current,'prd')
        with store.edit(current['id'],current['revision'],'保存正常PRD响应',bump=False) as (draft,_):apply_response(draft,response)
        current=store.get(p['id'])
        saved=generate(store,p['id'],dict(body(current),idempotency_key='local-preserve-flow-001'))['project']
        assert saved['items']==current['items'] and saved['documents']['prd']['model_calls']==0
        charts=[b for s in saved['documents']['prd']['content']['sections'] for b in s['blocks'] if b['kind']=='flowchart']
        assert len(charts)==1 and charts[0]['flow']==generated
        import xml.etree.ElementTree as ET
        reading=reader_document(saved['documents']['prd'])
        nodes=[node for section in reading['sections'] for node in section['reading_nodes']]
        diagram=next(node for node in nodes if node['kind']=='diagram')
        labels=[text.text or '' for text in ET.fromstring(diagram['svg']).iter('{http://www.w3.org/2000/svg}text')]
        assert not any(code in label for label in labels for code in ('start','edit','end','e1','e2'))
        assert any('…' in label for label in labels)
        table=next(node for node in nodes if node['kind']=='table' and node['title']=='流程分支条件')
        assert table['columns']==['起点','终点','条件']
        assert table['rows'][1][-1]==flow['edges'][1]['condition']
        files=asyncio.run(document_files(saved,'prd'))
        assert b'data:image/png;base64,' in files['PRD.md']
        with zipfile.ZipFile(io.BytesIO(files['PRD.docx'])) as package:
            assert any(name.startswith('word/media/') and name.endswith('.png') for name in package.namelist())


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


def test_local_preview_is_read_only_and_stale_preview_cannot_generate(tmp_path):
    from app.local_prd import preview
    store,p=setup(tmp_path);before=copy.deepcopy(p)
    result=preview(store,p['id'],dict(expected_revision=p['revision']))
    assert result['preview_hash']==body(p)['preview_hash']
    assert store.get(p['id'])==before
    request=body(p)
    with store.edit(p['id'],p['revision'],'模拟预览后内容变化',bump=False) as (current,_):
        current['items'][0]['statement']='预览后新增的业务变化。'
    with pytest.raises(Problem) as caught:generate(store,p['id'],request)
    assert caught.value.code=='STALE_PREVIEW'
    assert not store.records(p['id'],'document_artifact')


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
