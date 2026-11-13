"""Optional diagrams must not invalidate a saved, source-backed PRD body."""
import asyncio
import copy
import io

from docx import Document

from app.actions import Actions
from app.contracts import validate_response
from app.document_reader import export_readiness
from app.exports import document_files
from app.local_prd import generate
from app.prd import compile_plan, saved_prd_plan
from app.prewrite import apply_result, input_hash, status
from app.requirement_confirmation import row_fingerprint
from app.response_application import apply_response
from app.workflow import Workflow
from tests.test_local_prd import body, setup
from tests.test_prewrite_consistency import review
from tests.test_runtime import FakeProvider


def basic_project(tmp_path, two_requirements=False):
    store, p = setup(tmp_path)
    with store.edit(p['id'], p['revision'], '隔离的基础需求流程图夹具', bump=False) as (current, _):
        req = current['items'][0]
        req.pop('detail_contract'); req.pop('detail_required')
        req['behavior'] = dict(actor='管理员', entry='在联系人详情点击编辑。',
            data='联系人姓名为文本。', permissions='仅管理员可编辑，只读角色不能编辑。',
            flow='编辑姓名后点击保存。', exceptions='只读角色点击编辑时拒绝操作并保持原姓名。',
            result='保存后显示当前联系人姓名。')
        current['sources'][0]['excerpts'][0]['text'] = '\n'.join(req['behavior'].values())
        requirements = [req]
        if two_requirements:
            other = copy.deepcopy(req)
            other.update(id='REQ-SECOND', title='另一组联系人维护')
            current['items'].append(other)
            requirements.append(other)
        for item in requirements:
            item['requirement_confirmation']['content_hash'] = row_fingerprint(current, item)
        apply_result(current, review(current), 'RUN-FROZEN-BASIC')
    return store, store.get(p['id'])


def diagram(requirement_id):
    return dict(requirement_id=requirement_id, flow=dict(
        nodes=[dict(id='edit', label='编辑姓名', kind='start'),
               dict(id='saved', label='显示姓名', kind='end')],
        edges=[dict(id='saved-edge', **{'from':'edit', 'to':'saved'})]))


def test_scope_change_does_not_reuse_an_excluded_requirements_diagram(tmp_path):
    store, p = basic_project(tmp_path, two_requirements=True)
    plan = saved_prd_plan(p)
    plan['flowcharts'] = [diagram('REQ-0001'), diagram('REQ-SECOND')]
    response = compile_plan(plan, p, 'prd', include_sketch=False)
    validate_response(response, 'prd', p, p['sources'][0]['excerpts'])
    with store.edit(p['id'], p['revision'], '冻结旧文档并缩小本期范围', bump=False) as (current, _):
        apply_response(current, response)
        current['product_context']['scope_ids'] = ['REQ-SECOND']
        apply_result(current, review(current), 'RUN-FROZEN-SCOPE')
    current = store.get(p['id'])
    old_document = copy.deepcopy(current['documents']['prd'])
    old_hash = next(b['source_hash'] for s in old_document['content']['sections']
                    for b in s['blocks'] if b['kind']=='flowchart' and b['ref_ids']==['REQ-0001'])
    assert old_hash == row_fingerprint(current, current['items'][0])
    result = generate(store, p['id'], body(current))
    saved = result['project']['documents']['prd']
    charts = [b for s in saved['content']['sections'] for b in s['blocks'] if b['kind']=='flowchart']
    assert [b['ref_ids'] for b in charts] == [['REQ-SECOND']]
    assert saved['model_calls'] == 0
    assert current['documents']['prd'] == old_document


def test_failed_optional_diagram_keeps_saved_body_check_and_exports(tmp_path):
    async def scenario():
        store, p = basic_project(tmp_path)
        local = generate(store, p['id'], body(p))
        before = copy.deepcopy(local['project'])
        assert export_readiness(before, 'prd')['ready']
        plan = saved_prd_plan(before)
        plan['flowcharts'] = [diagram('REQ-0001')]
        plan['flowcharts'][0]['flow']['edges'][0]['to'] = 'missing-node'
        provider = FakeProvider([plan, plan, plan])
        actions = Actions(store, Workflow(store, provider))
        request = dict(expected_revision=before['revision'], action='document', message='',
                       document_type='prd', max_calls=None)
        preview = actions.plan(p['id'], request)
        assert preview['stages'] == ['prd']
        task = actions.start(p['id'], request, preview['plan_hash'], 'optional-flow-failure-001', True)
        await actions.jobs[task['id']]
        finished = store.get_record(p['id'], task['id'], 'user_task')
        assert finished['status'] == 'failed'
        assert finished['error'] == 'REFERENCE_INVALID'
        assert 1 <= len(provider.received) <= 3
        after = store.get(p['id'])
        for field in ('documents', 'items', 'sources', 'prewrite_review'):
            assert after[field] == before[field]
        assert input_hash(after) == input_hash(before)
        assert status(after)['current'] and status(after)['status'] == 'passed'
        assert export_readiness(after, 'prd') == export_readiness(before, 'prd')
        files = await document_files(after, 'prd')
        markdown = files['PRD.md'].decode('utf-8')
        word = '\n'.join(p.text for p in Document(io.BytesIO(files['PRD.docx'])).paragraphs)
        assert before['items'][0]['statement'] in markdown
        assert before['items'][0]['statement'] in word
        assert not any(b['kind']=='flowchart' for s in after['documents']['prd']['content']['sections'] for b in s['blocks'])
    asyncio.run(scenario())
