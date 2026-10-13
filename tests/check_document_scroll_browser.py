"""Document scrolling regression over isolated ASGI, with no recording or model calls.

The browser uses the sole workbench origin without starting a listener. Fixtures
are synthetic; daily data is neither read nor written. Navigation uses visible
controls and scrolling uses mouse wheel, never a scripted scrollTop assignment.
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


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def seed_documents(store):
    from app.core import brief_hash
    from tests.helpers import prepared
    from tests.product_flow_helpers import check_prepared
    p = prepared(store)
    pid = p['id']
    with store.edit(pid, p['revision'], '冻结长文档滚动验收夹具') as (p, _):
        p['name'] = '合成储能云：正式文档滚动验收'
        for kind, artifact in p['documents'].items():
            sections = artifact['content']['sections']
            for index in range(1, 17):
                title = f'{kind.upper()} 云平台业务说明 {index}' if index < 16 else f'{kind.upper()} 云平台业务说明末尾'
                sections.append(dict(section_id=f'{kind.upper()}-SCROLL-{index:02d}', title=title,
                    level=1, parent_section_id=None,
                    blocks=[dict(kind='narrative', ref_ids=[], text=(
                        f'合成滚动验收第 {index} 节第 {n} 段：大型储能云平台的电价资料阅读示例。'
                        '这些文字仅用于检验正文滚动、目录跳转和页面底部操作是否可达，'
                        '不表示真实业务规则或模型分析结果。') * 2) for n in range(1, 5)]))
            artifact['item_snapshot'] = copy.deepcopy(p['items'])
            artifact['draft_revision'] = p['revision']
            artifact['brief_hash'] = brief_hash(p)
    initial = check_prepared(store, pid, through=3)
    return pid, initial


async def visible_in_viewport(locator):
    return await locator.evaluate('''e => {
      const r=e.getBoundingClientRect();
      return r.width>0 && r.height>0 && r.top>=80 && r.bottom<=innerHeight && r.left>=0 && r.right<=innerWidth;
    }''')


async def wheel_to(page, locator, direction, label, report, max_steps=65):
    """Reach a real target through wheel input, recording actual scroll geometry."""
    viewport = page.viewport_size
    await page.mouse.move(viewport['width'] * .76, viewport['height'] * .64)
    for step in range(max_steps):
        if await visible_in_viewport(locator):
            report['checks'].append(f'{label}: wheel reached target after {step} inputs')
            return
        bounds = await locator.bounding_box()
        offset = bounds['y'] + bounds['height'] / 2 - viewport['height'] * .55
        delta = max(-1000, min(1000, offset))
        if abs(delta) < 80:
            delta = direction * 80
        await page.mouse.wheel(0, delta)
        await page.wait_for_timeout(90)
    geometry = await page.evaluate('''() => [...document.querySelectorAll('.project-workspace:not([hidden]),.connected-workspace,.connected-details,.workflow-main,.document-reader')].map(e=>({class:e.className,top:e.getBoundingClientRect().top,height:e.clientHeight,scrollHeight:e.scrollHeight,scrollTop:e.scrollTop,overflow:getComputedStyle(e).overflowY}))''')
    hit = await page.evaluate('''() => {let e=document.elementFromPoint(innerWidth*.76,innerHeight*.64);const result=[];while(e){result.push({tag:e.tagName,class:e.className,overflow:getComputedStyle(e).overflowY,overscroll:getComputedStyle(e).overscrollBehaviorY});e=e.parentElement;}return result;}''')
    raise AssertionError(f'{label}: unreachable with mouse wheel; geometry={geometry}; hit={hit}')


async def inspect_viewport(page, pid, initial, viewport, output, report, expect):
    await page.set_viewport_size(viewport)
    await page.goto(ORIGIN, wait_until='networkidle')
    await page.locator('aside.sidebar button[title="' + initial['name'] + '"]').click()
    workspace = page.locator('.project-workspace:not([hidden])')
    connected = workspace.locator('.connected-workspace')
    await connected.get_by_role('navigation', name='需求工作流程').get_by_role('button', name=re.compile(r'^3\s*确认交付$')).click()
    await expect(connected.get_by_role('heading', name='确认交付版本')).to_be_visible()
    await connected.get_by_role('button', name='查看与核对正式文档', exact=True).click()
    details = workspace.locator('details.connected-details')
    await expect(details).to_have_attribute('open', '')
    report['checks'].append(f'{viewport}: opened formal documents through delivery control')
    prefix = str(viewport['width']) + 'x' + str(viewport['height'])
    await page.screenshot(path=str(output / (prefix + '-01-document-open.png')))
    for kind in ('prd', 'mrd'):
        tab = details.get_by_role('tab', name=kind.upper() + ' 评审稿', exact=True)
        await wheel_to(page, tab, -1, kind.upper() + ' tab reachable', report)
        await tab.click()
        reader = details.get_by_role('article', name=kind.upper() + ' 正文', exact=True)
        await expect(reader).to_be_visible()
        end = reader.get_by_role('heading', name=kind.upper() + ' 云平台业务说明末尾', exact=False)
        await wheel_to(page, end, 1, kind.upper() + ' final section', report)
        await expect(end).to_be_in_viewport()
        await page.screenshot(path=str(output / (prefix + '-' + kind + '-end.png')))
        footer = details.locator('#checkpoint-continue-' + pid + '-4')
        await wheel_to(page, footer, 1, kind.upper() + ' page-end confirmation control', report)
        await expect(footer).to_be_in_viewport()
        await page.screenshot(path=str(output / (prefix + '-' + kind + '-footer.png')))
        toc = details.get_by_role('navigation', name='文档目录')
        await wheel_to(page, toc.locator(':scope > details > summary'), -1, kind.upper() + ' directory', report)
        if await toc.locator('details').get_attribute('open') is None:
            await toc.locator(':scope > details > summary').click()
        await toc.get_by_role('button', name=re.compile(kind.upper() + ' 云平台业务说明末尾')).click()
        await expect(end).to_be_in_viewport()
        report['checks'].append(kind.upper() + ' directory click displays final section')
    formal_confirmation = details.get_by_role('navigation', name='正式文档与确认').get_by_role('button', name='正式确认', exact=True)
    await wheel_to(page, formal_confirmation, -1, 'formal confirmation tab', report)
    await formal_confirmation.click()
    await expect(details.locator('.workflow-page')).to_have_attribute('data-phase', '5')
    formal_documents = details.get_by_role('navigation', name='正式文档与确认').get_by_role('button', name='正式文档', exact=True)
    await wheel_to(page, formal_documents, -1, 'return formal documents tab', report)
    await formal_documents.click()
    await expect(details.locator('.workflow-page')).to_have_attribute('data-phase', '4')
    footer = details.locator('#checkpoint-continue-' + pid + '-4')
    await wheel_to(page, footer, 1, 'phase 5 to 4 restored document footer', report)
    await expect(footer).to_be_in_viewport()
    # Closing then manually reopening must retain a reachable scroll region.
    summary = details.locator(':scope > summary')
    await wheel_to(page, summary, -1, 'formal summary', report)
    await summary.click()
    await expect(details).not_to_have_attribute('open', '')
    await summary.click()
    await expect(details).to_have_attribute('open', '')
    end = details.get_by_role('article', name='MRD 正文', exact=True).get_by_role('heading', name='MRD 云平台业务说明末尾', exact=False)
    await wheel_to(page, end, 1, 'manual expansion MRD final section', report)
    back = details.get_by_role('button', name='修改整体需求', exact=True)
    await wheel_to(page, back, -1, 'return-to-refine control', report)
    await back.click()
    table = connected.get_by_role('table', name='需求确认表', exact=True)
    await expect(table).to_be_visible()
    scroller = connected.locator('.refinement-sheet-scroll')
    confirm = connected.get_by_role('button', name=re.compile(r'^(确认并进入交付|返回交付)$'))
    await expect(confirm).to_be_in_viewport()
    before = await confirm.bounding_box()
    bounds = await scroller.bounding_box()
    await page.mouse.move(bounds['x'] + bounds['width'] * .75, bounds['y'] + min(bounds['height'] * .5, 200))
    await page.mouse.wheel(0, 1600)
    await page.wait_for_timeout(150)
    assert await scroller.evaluate('e=>e.scrollTop') > 0, 'Refinement table lost internal scrolling'
    await expect(confirm).to_be_in_viewport()
    after = await confirm.bounding_box()
    assert abs(before['y'] - after['y']) < 3, 'Refinement confirmation footer moved while table scrolled'
    report['checks'].append(f'{viewport}: returned refinement table scrolls with fixed confirmation footer')
    await page.screenshot(path=str(output / (prefix + '-refinement-scroll.png')))


async def run(args):
    output, build = args.output.resolve(), args.static_build.resolve()
    assert output.is_relative_to(ROOT / 'evidence') and output != ROOT / 'evidence'
    assert not output.exists() and (build / 'index.html').is_file()
    output.mkdir(parents=True)
    report = dict(status='running', fixture='SYNTHETIC_STORAGE_CLOUD_LONG_DOCUMENTS',
        model_calls=0, daily_data_access=False, listener_started=False, video_recorded=False,
        checks=[], api_requests=[], page_errors=[], static_build=str(build), served_static={},
        model_quality='NOT_RUN', business_acceptance='NOT_RUN', formal_confirmation='NOT_RUN')
    with tempfile.TemporaryDirectory(prefix='ra-document-scroll-') as scratch:
        os.environ['RA_DATA_DIR'] = str(Path(scratch) / 'import-data')
        import httpx
        from playwright.async_api import async_playwright, expect
        from app.main import create_app
        from tests.check_refine_workspace_browser import NoModelProvider
        provider = NoModelProvider(Path(scratch))
        app = create_app(Path(scratch) / 'fixture-data', provider=provider)
        store = app.state.store
        pid, initial = seed_documents(store)
        report['project_id'] = pid
        write(output / 'project-before.json', initial)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                context = await browser.new_context(viewport=dict(width=1440, height=900))
                async def dispatch(route):
                    request, url = route.request, urlsplit(route.request.url)
                    assert url.netloc == '127.0.0.1:8765', 'External requests forbidden'
                    if url.path.startswith('/api/'):
                        assert request.method == 'GET', 'Unexpected write: ' + url.path
                        headers = {k: v for k, v in request.headers.items() if k.lower() not in ('host', 'content-length', 'connection', 'accept-encoding')}
                        headers['host'] = '127.0.0.1:8765'
                        response = await client.request('GET', url.path + ('?' + url.query if url.query else ''), headers=headers)
                        report['api_requests'].append(dict(method='GET', path=url.path, status=response.status_code))
                        await route.fulfill(status=response.status_code, body=response.content, content_type=response.headers.get('content-type', 'application/json'))
                    else:
                        path = (build / (url.path.lstrip('/') or 'index.html')).resolve()
                        assert path.is_relative_to(build) and path.is_file(), str(path)
                        report['served_static'][url.path] = hashlib.sha256(path.read_bytes()).hexdigest()
                        await route.fulfill(status=200, body=path.read_bytes(), content_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
                await context.route('**/*', dispatch)
                page = await context.new_page()
                page.set_default_timeout(10000)
                page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
                try:
                    for index, viewport in enumerate((dict(width=1440, height=900), dict(width=1280, height=720), dict(width=900, height=720))):
                        if index:
                            await page.close()
                            page = await context.new_page()
                            page.set_default_timeout(10000)
                            page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
                        await inspect_viewport(page, pid, initial, viewport, output, report, expect)
                    assert store.get(pid) == initial, 'Read-only browser inspection changed synthetic data'
                    assert provider.calls == 0 and not report['page_errors']
                    report['status'] = 'passed'
                    report['fixture_unchanged'] = True
                except Exception as exc:
                    report.update(status='failed', failure=str(exc), traceback=traceback.format_exc())
                    await page.screenshot(path=str(output / 'failure.png'), full_page=True)
                finally:
                    report['model_calls'] = provider.calls
                    write(output / 'result.json', report)
                    await context.close()
                    await browser.close()
    print(json.dumps({key: report.get(key) for key in ('status', 'checks', 'failure', 'model_calls', 'video_recorded')}, ensure_ascii=False))
    if report['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--static-build', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
