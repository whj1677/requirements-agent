"""Responsive workflow and PRD preview checks on isolated synthetic data.

Static files are intercepted into an in-process ASGI app. No listener, model,
production data, or second workbench is used.
"""
import argparse
import asyncio
import copy
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
IMPORT = tempfile.TemporaryDirectory(prefix='ra-prd-responsive-import-')
os.environ['RA_DATA_DIR'] = str(Path(IMPORT.name) / 'data')

import httpx
from playwright.async_api import async_playwright, expect
from app.main import create_app
from app.core import dumps
from tests.browser_navigation import select_project
from tests.check_prewrite_batch_browser import synthetic
from tests.test_runtime import FakeProvider

ORIGIN = 'http://127.0.0.1:8765'
VIEWPORTS = ((1920, 1080), (1366, 768), (1280, 720), (1024, 640), (853, 480), (390, 844))


def ready_fixture(store, snapshot):
    """Copy a frozen synthetic project; only its old diff fixture is changed."""
    project = json.loads(snapshot.read_text('utf-8'))
    project['name'] = '合成UI验收：普通PRD生成前预览'
    old = next(item for item in project['documents']['prd']['item_snapshot']
               if item['kind'] == 'requirement' and '500' in item['statement'])
    old['statement'] = old['statement'].replace('500', '300', 1)
    with store.connect() as db:
        db.execute('INSERT INTO projects VALUES(?,?,?)',
                   (project['id'], project['revision'], dumps(project)))
    for source in project['sources']:
        original = snapshot.parent / 'data' / 'sources' / source['id']
        if original.is_file():
            (store.folder / 'sources').mkdir(exist_ok=True)
            shutil.copyfile(original, store.folder / 'sources' / source['id'])
    return project


async def preview_sizes(page, dialog, output, report, mode):
    for width, height in VIEWPORTS:
        await page.set_viewport_size(dict(width=width, height=height))
        await dialog.locator('.task-approval-body').evaluate('element => { element.scrollTop = 0; }')
        metrics = await dialog.evaluate('''dialog => {
            const rect = element => { const r=element.getBoundingClientRect();
                return {x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width,height:r.height}; };
            const body=dialog.querySelector('.task-approval-body');
            const buttons=[...dialog.querySelectorAll('.task-approval-actions button')].map(button=>{
                const r=button.getBoundingClientRect();
                const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
                return {...rect(button),text:button.textContent,
                    primary:button.classList.contains('primary'),
                    hit:!!hit&&(hit===button||button.contains(hit))};});
            return {viewport:{width:innerWidth,height:innerHeight},dialog:rect(dialog),
                documentWidth:document.documentElement.scrollWidth,
                bodyClientHeight:body.clientHeight,bodyScrollHeight:body.scrollHeight,buttons}; }''')
        metrics.update(label=f'{width}x{height}', mode=mode)
        report['viewports'].append(metrics)
        assert metrics['documentWidth'] <= width + 1, metrics
        assert metrics['dialog']['x'] >= 0 and metrics['dialog']['right'] <= width + 1, metrics
        assert metrics['dialog']['y'] >= 0 and metrics['dialog']['bottom'] <= height + 1, metrics
        assert metrics['bodyClientHeight'] > 0, metrics
        assert len(metrics['buttons']) >= 2 and any(button['primary'] for button in metrics['buttons']), metrics
        for button in metrics['buttons']:
            assert button['y'] >= 0 and button['bottom'] <= height + 1 and button['hit'], metrics
        await page.screenshot(path=str(output / f'{mode}-preview-{width}x{height}.png'))


async def workflow_sizes(page, steps, output, report, mode):
    for width, height in VIEWPORTS:
        await page.set_viewport_size(dict(width=width, height=height))
        await expect(steps.get_by_role('button')).to_have_count(3)
        current = steps.locator('button[aria-current="step"]')
        await expect(current).to_have_count(1)
        await expect(current).to_be_in_viewport(ratio=1)
        for button in await steps.get_by_role('button').all():
            box = await button.bounding_box()
            assert box['x'] >= 0 and box['x'] + box['width'] <= width + 1, (mode, width, box)
        assert await page.evaluate('document.documentElement.scrollWidth') <= width + 1
        await page.screenshot(path=str(output / f'{mode}-workflow-{width}x{height}.png'))
    report['checks'].append(mode + ': three actual workflow entrances fit all requested viewports')


async def core_areas(page, work, steps, output, report):
    for width, height in ((853, 480), (390, 844)):
        await page.set_viewport_size(dict(width=width, height=height))
        label = f'{width}x{height}'
        await steps.get_by_role('button').nth(0).click()
        log = work.get_by_role('log', name='聊天记录', exact=True)
        await log.scroll_into_view_if_needed()
        await expect(log).to_be_visible()
        await page.screenshot(path=str(output / f'chat-history-{label}.png'))
        text = work.get_by_label('你的想法', exact=True)
        await text.fill('仅用于布局核对，暂不发送。')
        send = work.get_by_role('button', name='发送', exact=True)
        await expect(send).to_be_enabled()
        await send.scroll_into_view_if_needed()
        await expect(text).to_be_in_viewport()
        await expect(send).to_be_in_viewport(ratio=1)
        metrics = await send.evaluate('''button => {
            const r=button.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
            const log=document.querySelector('.connected-workspace:has(.intake-chat) .chat-messages');
            return {button:button.textContent,hit:!!hit&&(hit===button||button.contains(hit)),
                logHeight:log.getBoundingClientRect().height,inputHeight:document.querySelector('.intake-chat textarea').getBoundingClientRect().height}; }''')
        assert metrics['hit'], (label, metrics)
        await page.screenshot(path=str(output / f'chat-compose-{label}.png'))
        await text.fill('')
        await steps.get_by_role('button').nth(1).click()
        await work.locator('.refinement-view-switch').get_by_role('button', name='需求列表', exact=True).click()
        table_action = work.locator('.refinement-sheet-footer button.primary')
        await table_action.scroll_into_view_if_needed()
        await expect(table_action).to_be_in_viewport(ratio=1)
        assert await table_action.evaluate('''button => {
            const r=button.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
            return !!hit&&(hit===button||button.contains(hit)); }'''), label
        await page.screenshot(path=str(output / f'requirement-footer-{label}.png'))
        await steps.get_by_role('button').nth(2).click()
        blocker_action = work.locator('.connected-blockers button.primary').first
        await blocker_action.scroll_into_view_if_needed()
        await expect(blocker_action).to_be_in_viewport(ratio=1)
        assert await blocker_action.evaluate('''button => {
            const r=button.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
            return !!hit&&(hit===button||button.contains(hit)); }'''), label
        await page.screenshot(path=str(output / f'delivery-blockers-{label}.png'))
        report['checks'].append(label + ': chat log/input/send, requirement footer and first delivery-blocker action are reachable')


async def run(args):
    output = args.output.resolve()
    build = args.static_build.resolve()
    assert output.is_relative_to(ROOT / 'work') and (build / 'index.html').is_file()
    output.mkdir(parents=True, exist_ok=True)
    report = dict(status='running', fixture='SYNTHETIC_ONLY', model_calls=0,
        listener_started=False, daily_data_access=False, viewports=[], page_errors=[], requests=[], checks=[])
    with tempfile.TemporaryDirectory(prefix='ra-prd-responsive-') as temporary:
        provider = FakeProvider([])
        app = create_app(Path(temporary) / 'data', provider=provider)
        store = app.state.store
        project = synthetic(store)
        ready = ready_fixture(store, args.ready_project.resolve())
        initial = {row['id']: copy.deepcopy(row) for row in (project, ready)}
        report['ordinary_fixture'] = dict(snapshot=str(args.ready_project),
            old_statement_only='500 -> 300 for synthetic UI diff; current requirement remains 500')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                context = await browser.new_context(viewport=dict(width=1366, height=768))

                async def dispatch(route):
                    request, url = route.request, urlsplit(route.request.url)
                    assert url.netloc == '127.0.0.1:8765'
                    if url.path.startswith('/api/'):
                        response = await client.request(request.method, url.path,
                            content=request.post_data_buffer,
                            headers={key: value for key, value in request.headers.items()
                                if key.lower() not in ('host', 'content-length', 'connection', 'accept-encoding')})
                        report['requests'].append(dict(method=request.method, path=url.path, status=response.status_code))
                        await route.fulfill(status=response.status_code, body=response.content,
                            content_type=response.headers.get('content-type', 'application/json'))
                    else:
                        path = (build / (url.path.lstrip('/') or 'index.html')).resolve()
                        assert path.is_relative_to(build) and path.is_file()
                        await route.fulfill(body=path.read_bytes(),
                            content_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream')

                await context.route('**/*', dispatch)
                page = await context.new_page()
                page.set_default_timeout(15000)
                page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
                try:
                    await page.goto(ORIGIN, wait_until='networkidle')
                    for mode, case in (('local', project), ('ordinary', ready)):
                        await page.set_viewport_size(dict(width=1366, height=768))
                        await select_project(page, case['id'])
                        work = page.locator('.connected-workspace:visible')
                        steps = work.get_by_role('navigation', name='需求工作流程', exact=True)
                        await expect(steps.get_by_role('button')).to_have_count(3)
                        labels = await steps.get_by_role('button').all_text_contents()
                        assert '聊' in labels[0] and '草稿' in labels[1] and '文档' in labels[2], labels
                        await expect(steps.locator('button[aria-current="step"]')).to_have_count(1)
                        await expect(work.locator('.workflow-five-steps')).to_have_count(0)
                        await steps.get_by_role('button', name=re.compile(r'^2\s')).click()
                        await expect(steps.get_by_role('button').nth(1)).to_have_attribute('aria-current', 'step')
                        await work.locator('.refinement-view-switch').get_by_role('button', name='成文检查', exact=False).click()
                        if mode == 'local':
                            batch = work.get_by_role('region', name='成文检查确认表', exact=True)
                            await expect(batch).to_be_visible()
                            await batch.get_by_role('button', name='先生成待确认评审稿', exact=True).click()
                        else:
                            await work.get_by_role('region', name='成文检查', exact=True).get_by_role('button', name='生成 PRD', exact=True).click()
                        dialog = page.get_by_role('dialog').filter(has=page.locator('.prd-change-preview'))
                        await expect(dialog).to_be_visible()
                        preview = dialog.locator('.prd-change-preview')
                        for text in ('本轮新增与具体修改', '未确认', '哪些内容被排除', 'AI 建议是什么'):
                            await expect(preview).to_contain_text(text)
                        if mode == 'ordinary':
                            await expect(dialog).to_contain_text(re.compile(r'(调用|使用)\s*AI[\s\S]{0,60}生成|生成[\s\S]{0,60}(调用|使用)\s*AI'))
                            await expect(dialog).not_to_contain_text('不调用 AI，不发送项目资料')
                            await expect(preview.locator('ins')).to_contain_text(['5'])
                            await expect(preview.locator('del')).to_contain_text(['3'])
                        else:
                            await expect(dialog).to_contain_text('不调用 AI，不发送项目资料')
                        assert all(store.get(pid) == before for pid, before in initial.items())
                        assert not provider.received
                        await preview_sizes(page, dialog, output, report, mode)
                        await dialog.get_by_role('button', name='返回继续核对', exact=True).click()
                        await expect(dialog).to_have_count(0)
                        await workflow_sizes(page, steps, output, report, mode)
                        if mode == 'ordinary':
                            await core_areas(page, work, steps, output, report)
                        for index in (0, 1, 2):
                            await steps.get_by_role('button').nth(index).click()
                            await expect(steps.get_by_role('button').nth(index)).to_have_attribute('aria-current', 'step')
                        assert all(store.get(pid) == before for pid, before in initial.items())
                        assert not provider.received
                        report['checks'].append(mode + ': real preview opens, all requested groups visible, cancel preserves project and makes zero model calls')
                    assert not [request for request in report['requests'] if request['path'].endswith('/local-review')]
                    assert any(request['path'].endswith('/local-preview') for request in report['requests'])
                    assert any(request['path'].endswith('/actions/plan') for request in report['requests'])
                    assert not [request for request in report['requests'] if request['method'] == 'POST' and request['path'].endswith('/actions')]
                    assert not report['page_errors']
                    report.update(status='passed', preview_opened_without_generation=True,
                        three_actual_steps_accessible=True, ordinary_preview_before_generation=True,
                        sticky_actions_accessible=True)
                except Exception as error:
                    import traceback
                    report.update(status='failed', error=str(error), traceback=traceback.format_exc())
                    await page.screenshot(path=str(output / 'failure.png'), full_page=True)
                finally:
                    await browser.close()
    report['model_calls'] = len(provider.received)
    (output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
    print(json.dumps({key: value for key, value in report.items() if key not in ('requests', 'traceback')}, ensure_ascii=False))
    assert report['status'] == 'passed'


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--static-build', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ready-project', type=Path,
        default=ROOT / 'work/autointake-prd-20261102/trial/document-revised-project.json')
    asyncio.run(run(parser.parse_args()))
