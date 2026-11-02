import argparse
import asyncio
import json
import mimetypes
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
scratch = tempfile.TemporaryDirectory(prefix='ra-chat-layout-')
os.environ['RA_DATA_DIR'] = str(Path(scratch.name) / 'isolated-default')
os.environ['RA_ACCESS_TOKEN'] = 'off'
from tests.check_chat_composer_browser import OfflineInterview, seed, composer_geometry, ORIGIN
from app.main import create_app
import httpx
from playwright.async_api import async_playwright

async def run(args):
    folder = Path(scratch.name)
    app = create_app(folder / 'data', provider=OfflineInterview(folder), env_path=folder/'absent.env')
    pid = seed(app.state.store, '合成储能云平台长回复布局核对')
    draft_pid = seed(app.state.store, '合成储能云平台已有草稿布局核对')
    with app.state.store.edit(draft_pid, app.state.store.get(draft_pid)['revision'], '合成已有需求草稿') as (project, _):
        project['items'].append(dict(id='REQ-LAYOUT', kind='requirement', title='场站运行数据查看',
            statement='运维人员查看当前有权限场站的已保存运行数据。', applies_to='to_be',
            selection_status='candidate', epistemic_status='reported', source_refs=[], related_refs=[], revision=1))
    static, output = args.static.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    reports, failures, page_errors = [], [], []
    cases = [(1536,734,1.25,False),(1910,917,1,False),(1280,720,1,False),
             (1366,768,1,False),(1024,576,1,False),(390,844,1,False)]
    # OS scaling changes both the device pixel ratio and the available CSS viewport.
    # The screenshot is 1908x1002 physical pixels: 1272x668 CSS pixels at 150%.
    cases += [(round(1908/scale),round(1002/scale),scale,draft)
              for scale in (1,1.25,1.5,1.75,2) for draft in (False,True)]
    cases += [(910,450,1.5,True),(390,667,1,True)]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            for width, height, scale, has_draft in cases:
                context = await browser.new_context(viewport=dict(width=width,height=height),device_scale_factor=scale)
                async def route(request_route):
                    req = request_route.request
                    path = urlsplit(req.url).path
                    if not req.url.startswith(ORIGIN):
                        raise AssertionError('External request forbidden')
                    if path.startswith('/api/'):
                        response = await client.request(req.method, path, content=req.post_data_buffer,
                            headers={k:v for k,v in req.headers.items() if k.lower() not in ('host','content-length','connection','accept-encoding')})
                        await request_route.fulfill(status=response.status_code,
                            content_type=response.headers.get('content-type','application/json'),body=response.content)
                    else:
                        target=(static / (path.lstrip('/') or 'index.html')).resolve()
                        if target.is_relative_to(static) and target.is_file():
                            await request_route.fulfill(body=target.read_bytes(),content_type=mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
                        else:
                            await request_route.abort()
                await context.route('**/*',route)
                page = await context.new_page()
                page.on('pageerror', lambda error: page_errors.append(str(error)))
                page.set_default_timeout(8000)
                await page.goto(ORIGIN)
                await page.wait_for_timeout(500)
                if args.inspect:
                    print((await page.locator('body').inner_text())[:1200])
                    await page.screenshot(path=str(output/'loaded.png'))
                    return
                if await page.get_by_label('本机访问令牌').count():
                    await page.get_by_label('本机访问令牌').fill('off')
                    await page.get_by_role('button',name='进入工作台').click()
                from tests.browser_navigation import select_project
                await select_project(page,draft_pid if has_draft else pid)
                await page.locator('.connected-workspace:visible .chat-messages').wait_for()
                await page.wait_for_timeout(150)
                geometry = await composer_geometry(page)
                minimum = 240 if width >= 1000 and height >= 700 else 180 if height >= 600 else 120
                case = f'{width}x{height}-{scale}-draft{int(has_draft)}'
                checks = dict(history=geometry['boxes']['.chat-messages']['height'] >= minimum,
                    input_visible=geometry['textarea_visible'],send_visible=geometry['button_visible'],
                    readable_input=geometry['input_style']['fontSize'] >= 16 and geometry['textarea']['height'] >= 80)
                if has_draft:
                    assert await page.locator('.assistant-resume:visible').count() == 1
                await page.screenshot(path=str(output/f'chat-{case}.png'))
                scroll = page.locator('.connected-workspace:visible .chat-messages')
                await scroll.evaluate('e=>e.scrollTop=0')
                first = await scroll.evaluate('e=>({top:e.getBoundingClientRect().top,first:e.firstElementChild.getBoundingClientRect().top,scroll:e.scrollTop})')
                checks['first_message'] = first['first'] >= first['top']-1
                if not args.observe:
                    await page.get_by_role('button',name='对标参考产品 展开',exact=True).click()
                    expanded = await composer_geometry(page)
                    checks['expanded_composer'] = expanded['textarea_visible'] and expanded['button_visible']
                    await page.get_by_role('button',name='对标参考产品 收起',exact=True).click()
                failures.extend(f'{case}: {key}' for key,value in checks.items() if not value)
                reports.append(dict(size=[width,height],scale=scale,has_draft=has_draft,
                    checks=checks,geometry=geometry,first_message=first))
                await context.close()
            await browser.close()
    (output/'geometry.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2),'utf-8')
    (output/'result.json').write_text(json.dumps(dict(failures=failures,page_errors=page_errors),indent=2),'utf-8')
    print(json.dumps([dict(size=r['size'],history=r['geometry']['boxes']['.chat-messages']['height'],input=r['geometry']['textarea']['height'],send_visible=r['geometry']['button_visible']) for r in reports]))
    if not args.observe:
        assert not failures and not page_errors, (failures,page_errors)

parser=argparse.ArgumentParser()
parser.add_argument('--static',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--inspect',action='store_true')
parser.add_argument('--observe',action='store_true',help='Record failing baseline geometry without stopping at the first case')
asyncio.run(run(parser.parse_args()))
