"""Real browser, real isolated ASGI, no listener/model/daily-data access.

All business values are synthetic. Confirmation below proves a UI/server path;
it is not user business acceptance or a real model quality result.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import sys
import tempfile
import traceback
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ORIGIN = 'http://127.0.0.1:8765'
TITLE = '合成告警派单'
NAME = '合成储能云：详细定义与图文表'


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def seed(store):
    from app.core import now
    from tests.helpers import prepared
    from tests.test_detail_business_draft import sample_detail
    initial = prepared(store)
    with store.edit(initial['id'], initial['revision'], '合成详细定义浏览器前置') as (p, _):
        p.update(name=NAME, ui=None, documents={}, review=None, questions=[], workflow_mode='shared-draft')
        p['product_context'] = dict(product='储能云平台', module='告警派单', intent='验证详细定义编辑与图文表交付',
            users='调度员', change_scope='新增告警派单', scope_ids=['REQ-0001'])
        p['scope_profile'] = dict(scenario='new_module', dependencies='independent')
        p['context_field_states'] = {key: dict(status='known') for key in ('product', 'module', 'intent', 'users', 'change_scope')}
        p['workspace_draft'] = dict(text='合成场景：调度员核对告警级别后派单。', entry_mode='direct', saved_at=now())
        detail = sample_detail(complex_flow=True)
        detail['fields'][0].update(name='告警级别', meaning='本次派单使用的告警等级', data_type='enum',
            source='EMS 上报', constraints='仅允许枚举中的值', enum_values=['一级', '二级'])
        detail['fields'][0].pop('time_basis', None)
        req = p['items'][0]
        req.update(title=TITLE, statement='调度员核对告警级别并按站点派单。', change_type='new', behavior={},
                   detail_contract=copy.deepcopy(detail), detail_required=True)
        p['items'][1].update(title='合成权限规则', statement='调度员仅处理有权限的站点。')
        p['items'][2].update(title='合成派单验收', statement='调度员提交合法告警后按站点显示明确派单结果。')
        candidate = copy.deepcopy(req)
        candidate.update(id='REQ-DETAIL-CANDIDATE', target_item_id=req['id'], action='revise',
                         selection_status='candidate', epistemic_status='proposed')
        candidate['detail_contract']['fields'][0]['meaning'] = 'AI 合成建议：本次派单使用的告警等级'
        p['items'].append(candidate)
    return initial['id']


async def run(args):
    output, build = args.output.resolve(), args.static_build.resolve()
    assert output.is_relative_to(ROOT / 'work') and (build / 'index.html').is_file()
    output.mkdir(parents=True, exist_ok=True)
    report = dict(status='running', checks=[], api_requests=[], page_errors=[], routing_errors=[],
        model_calls=0, listener_started=False, daily_data_access=False, business_acceptance='NOT_RUN',
        model_quality='NOT_RUN', fixture='SYNTHETIC_ONLY', static_build=str(build), served_static={})
    with tempfile.TemporaryDirectory(prefix='ra-detail-browser-') as scratch:
        folder = Path(scratch)
        os.environ['RA_DATA_DIR'] = str(folder / 'import-data')
        import httpx
        from playwright.async_api import async_playwright, expect
        from app.main import create_app
        from app.provider import Provider
        from tests.test_detail_document import compile_fixture

        class NoModelProvider(Provider):
            def __init__(self):
                super().__init__(env_path=folder/'absent.env', credential_dir=folder/'credentials')
                self.calls = 0
            async def request(self, *args, **kwargs):
                self.calls += 1
                raise AssertionError('No model requests allowed')

        provider = NoModelProvider()
        app = create_app(folder/'data', provider=provider)
        store = app.state.store
        pid = seed(store)
        initial = store.get(pid)
        report['project_id'] = pid
        write(output/'project-before.json', initial)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN, timeout=30) as client:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                context = await browser.new_context(viewport=dict(width=1600, height=1000), accept_downloads=True)

                async def dispatch(route):
                    try:
                        request, url = route.request, urlsplit(route.request.url)
                        assert url.netloc == '127.0.0.1:8765', 'External request forbidden'
                        if url.path.startswith('/api/'):
                            assert request.method == 'GET' or url.path.endswith(('/refinement-confirmation', '/workspace-draft')), 'Unexpected write '+url.path
                            headers = {k:v for k,v in request.headers.items() if k.lower() not in ('host','content-length','connection','accept-encoding')}
                            headers['host'] = '127.0.0.1:8765'
                            response = await client.request(request.method, url.path+('?' + url.query if url.query else ''),
                                headers=headers, content=request.post_data_buffer)
                            event = dict(method=request.method, path=url.path, query=url.query, status=response.status_code)
                            if request.post_data:
                                event.update(body=request.post_data_json, response=response.json())
                            report['api_requests'].append(event)
                            await route.fulfill(status=response.status_code, body=response.content,
                                content_type=response.headers.get('content-type','application/json'))
                        else:
                            path=(build/(url.path.lstrip('/') or 'index.html')).resolve()
                            assert path.is_relative_to(build) and path.is_file(), 'Missing static asset '+str(path)
                            report['served_static'][url.path]=hashlib.sha256(path.read_bytes()).hexdigest()
                            await route.fulfill(status=200,body=path.read_bytes(),content_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
                    except BaseException as error:
                        report['routing_errors'].append(str(error))
                        await route.abort()

                await context.route('**/*', dispatch)
                page = await context.new_page()
                page.set_default_timeout(15000)
                page.on('pageerror', lambda error:report['page_errors'].append(str(error)))
                try:
                    async def open_refinement():
                        await page.goto(ORIGIN, wait_until='networkidle')
                        await page.locator('aside.sidebar button[title="'+NAME+'"]').click()
                        work = page.locator('.connected-workspace:visible')
                        direct = work.get_by_role('button', name='2 完善需求', exact=True)
                        if not await direct.is_visible():
                            await work.get_by_role('button', name='直接填写需求', exact=True).click()
                        await direct.click()
                        table = work.get_by_role('table', name='需求确认表', exact=True)
                        await expect(table).to_be_visible()
                        return work, table

                    work, table = await open_refinement()
                    editor = table.locator('[data-detail-requirement="REQ-0001"]')
                    field_card = editor.locator('[data-cell-key="requirement-REQ-0001-detail_contract.fields.F1"]')
                    await editor.locator('[data-detail-section="fields"] > summary').click()
                    await field_card.locator(':scope > summary').click()
                    meaning = editor.get_by_role('textbox', name=TITLE+' 告警级别 业务含义', exact=True)
                    await expect(meaning).to_have_value('AI 合成建议：本次派单使用的告警等级')
                    await meaning.fill('人工核对：上报等级用于派单前查看，不自动决定优先级。')
                    constraints = editor.get_by_role('textbox', name=TITLE+' 告警级别 取值范围与校验', exact=True)
                    await constraints.fill('人工范围：仅一级、二级。')
                    await work.get_by_role('button', name='保存草稿', exact=True).click()
                    assert store.get(pid) == initial, 'Browser draft save changed project or approval'
                    report['checks'].append('候选详细定义实际显示、可修改；保存草稿不写入项目或自动确认')
                    await field_card.scroll_into_view_if_needed()
                    await page.screenshot(path=str(output/'01-detailed-field-editor.png'))

                    work, table = await open_refinement()
                    editor = table.locator('[data-detail-requirement="REQ-0001"]')
                    field_card = editor.locator('[data-cell-key="requirement-REQ-0001-detail_contract.fields.F1"]')
                    await editor.locator('[data-detail-section="fields"] > summary').click()
                    await field_card.locator(':scope > summary').click()
                    meaning = editor.get_by_role('textbox', name=TITLE+' 告警级别 业务含义', exact=True)
                    await expect(meaning).to_have_value('人工核对：上报等级用于派单前查看，不自动决定优先级。')
                    report['checks'].append('刷新并经原工作台重新进入后恢复精确字段草稿')
                    enum = editor.get_by_role('textbox', name=TITLE+' 告警级别 可选值', exact=True)
                    await enum.fill('')
                    confirm = work.locator('.refinement-sheet-footer .primary')
                    before_requests=len(report['api_requests'])
                    await confirm.click()
                    await expect(enum).to_be_focused()
                    assert not [event for event in report['api_requests'][before_requests:] if event['method']!='GET']
                    assert store.get(pid)==initial
                    await page.screenshot(path=str(output/'02-missing-enum-blocked.png'))
                    await enum.fill('一级\n二级')
                    edge = editor.locator('[data-cell-key="requirement-REQ-0001-detail_contract.flow.edges.E3"]')
                    await editor.locator('[data-detail-section="flow"] > summary').click()
                    await edge.locator(':scope > summary').click()
                    condition = edge.get_by_role('textbox', name=TITLE+' E3 条件', exact=True)
                    await condition.fill('')
                    await confirm.click()
                    await expect(condition).to_be_focused()
                    assert store.get(pid)==initial
                    await condition.fill('确认整站建单成功')
                    await work.get_by_role('button', name='保存草稿', exact=True).click()
                    report['checks'].append('缺枚举与判断条件均真实点击确认后定位阻断；不发送确认请求、不改业务数据')

                    with store.edit(pid, store.get(pid)['revision'], '合成并发更新') as (p,_):
                        candidate=next(item for item in p['items'] if item['id']=='REQ-DETAIL-CANDIDATE')
                        candidate['detail_contract']['fields'][0]['constraints']='另一版本：一级、二级、三级。'
                        candidate['detail_contract']['fields'][0]['visibility']='另一版本：有本站查看权限的人员可见'
                    work, table=await open_refinement()
                    editor=table.locator('[data-detail-requirement="REQ-0001"]')
                    conflict=editor.locator('.detail-conflicts')
                    await expect(conflict).to_contain_text('人工范围：仅一级、二级。')
                    await expect(conflict).to_contain_text('另一版本：一级、二级、三级。')
                    await conflict.get_by_role('button',name='保留我的修改',exact=True).click()
                    await work.get_by_role('button',name='保留我的修改，继续核对',exact=True).click()
                    field_card=editor.locator('[data-cell-key="requirement-REQ-0001-detail_contract.fields.F1"]')
                    if await editor.locator('[data-detail-section="fields"]').get_attribute('open') is None:
                        await editor.locator('[data-detail-section="fields"] > summary').click()
                    if await field_card.get_attribute('open') is None:
                        await field_card.locator(':scope > summary').click()
                    await expect(editor.get_by_role('textbox',name=TITLE+' 告警级别 取值范围与校验',exact=True)).to_have_value('人工范围：仅一级、二级。')
                    await expect(editor.get_by_role('textbox',name=TITLE+' 告警级别 可见角色与条件',exact=True)).to_have_value('另一版本：有本站查看权限的人员可见')
                    report['checks'].append('真实并发版本保留人工字段、补入未改属性，并展示冲突两份值后由用户选择')
                    await page.screenshot(path=str(output/'03-concurrent-draft-kept.png'))

                    await work.locator('.refinement-sheet-footer .primary').click()
                    await expect(work.get_by_role('heading',name='确认交付版本',exact=True)).to_be_visible()
                    confirmed=next(event for event in reversed(report['api_requests']) if event['path'].endswith('/refinement-confirmation'))
                    assert confirmed['response']['confirmed'], confirmed
                    saved=store.get(pid)
                    req=next(item for item in saved['items'] if item['id']=='REQ-0001')
                    assert req['detail_contract']['fields'][0]['constraints']=='人工范围：仅一级、二级。'
                    assert req.get('requirement_confirmation'), 'One table submission failed to confirm detailed content'
                    report['checks'].append('一次整表确认真实写入完整详细定义并进入交付；未新增逐字段确认')

                    # Compile the saved same-feature snapshot deterministically, then
                    # invalidate only its requirement review to exercise review export.
                    with store.edit(pid,saved['revision'],'合成未确认文档审核场景',bump=False) as (p,_):
                        next(item for item in p['items'] if item['id']=='REQ-0001').pop('requirement_confirmation',None)
                        compile_fixture(p)
                    await page.reload(wait_until='networkidle')
                    work=page.locator('.connected-workspace:visible')
                    await work.get_by_role('navigation',name='需求工作流程').get_by_role('button',name=re.compile(r'^3\s*确认交付$')).click()
                    await work.get_by_role('button',name='查看与核对正式文档',exact=True).click()
                    reader=page.get_by_role('article',name='PRD 正文',exact=True)
                    await expect(reader).to_be_visible()
                    image=reader.locator('.document-detail-diagram img').first
                    await expect(image).to_be_visible()
                    assert await image.evaluate('(image)=>image.complete&&image.naturalWidth>0&&image.naturalHeight>0')
                    assert await reader.locator('.document-detail-table table').count()>=4
                    await expect(reader).to_contain_text('人工范围：仅一级、二级。')
                    await image.scroll_into_view_if_needed()
                    await page.screenshot(path=str(output/'04-prd-flow-and-tables.png'))
                    await reader.get_by_role('button',name='放大流程图',exact=True).click()
                    dialog=page.get_by_role('dialog',name=re.compile('放大预览$'))
                    await expect(dialog).to_be_visible()
                    await dialog.get_by_role('button',name='放大流程图',exact=True).click()
                    await expect(dialog).to_contain_text('125%')
                    close_button=dialog.get_by_role('button',name='关闭',exact=True)
                    assert await close_button.evaluate('''button => {
                        const rect=button.getBoundingClientRect();
                        const target=document.elementFromPoint(rect.left+rect.width/2,rect.top+rect.height/2);
                        return !!target && (target===button || button.contains(target)) &&
                            !!target.closest('.document-image-dialog');
                    }'''), 'Flow dialog close button is covered by the sticky project header'
                    await page.screenshot(path=str(output/'05-flow-zoom.png'))
                    await close_button.click()
                    await expect(dialog).not_to_be_visible()
                    report['checks'].append('同一功能PRD真实渲染图文表，流程图naturalWidth>0；放大125%且关闭按钮命中对话框、未被项目顶栏遮挡，关闭成功')

                    assert await reader.get_by_role('button',name='DOCX 下载',exact=True).count()==0
                    async with page.expect_download() as event:
                        await reader.get_by_role('button',name='下载评审稿 MD',exact=True).click()
                    download=await event.value
                    await download.save_as(str(output/'synthetic-review.md'))
                    assert '非正式交付' in (output/'synthetic-review.md').read_text('utf-8')
                    artifact=store.get(pid)['documents']['prd']
                    response=await client.get('/api/projects/'+pid+'/documents/prd/docx',params={'document_id':artifact['id']})
                    assert response.status_code==409, response.text
                    report['formal_export_negative']=dict(status=response.status_code,response=response.json())
                    assert any(event['query'].startswith('mode=review') and event['status']==200 for event in report['api_requests'])
                    report['checks'].append('实际点击评审稿下载经ASGI生成含未确认标识的MD；正式导出接口409阻断')
                    assert provider.calls==0 and not report['page_errors'] and not report['routing_errors']
                    write(output/'project-after.json',store.get(pid))
                    report['status']='passed'
                except BaseException:
                    report['status']='failed';report['error']=traceback.format_exc()
                    await page.screenshot(path=str(output/'failure.png'),full_page=True)
                    raise
                finally:
                    report['model_calls']=provider.calls
                    write(output/'verification.json',report)
                    await context.close();await browser.close()
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--static-build',type=Path,default=ROOT/'web/.verify-detail-prd')
    parser.add_argument('--output',type=Path,default=ROOT/'work/detail-prd-20261013/browser')
    args=parser.parse_args()
    result=asyncio.run(run(args))
    print(json.dumps(dict(status=result['status'],checks=len(result['checks']),model_calls=result['model_calls']),ensure_ascii=False))
