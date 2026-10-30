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
    static, output = args.static.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    reports = []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            for width, height, scale in [(1536,734,1.25),(1910,917,1),(1280,720,1),(1366,768,1),(1024,576,1),(390,844,1)]:
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
                if width <= 700:
                    from tests.browser_navigation import select_project
                    await select_project(page,pid)
                else:
                    await page.get_by_role('button',name='合成储能云平台长回复布局核对').click()
                await page.locator('.connected-workspace:visible .chat-messages').wait_for()
                await page.wait_for_timeout(150)
                geometry = await composer_geometry(page)
                minimum = 240 if width >= 1000 and height >= 700 else 180 if width < 700 else 140
                assert geometry['boxes']['.chat-messages']['height'] >= minimum, 'History is squeezed by controls'
                assert geometry['textarea_visible'] and geometry['button_visible'], 'Composer must remain reachable'
                assert geometry['input_style']['fontSize'] >= 16 and geometry['textarea']['height'] >= 80
                await page.screenshot(path=str(output/f'chat-{width}x{height}-{scale}.png'))
                scroll = page.locator('.connected-workspace:visible .chat-messages')
                await scroll.evaluate('e=>e.scrollTop=0')
                first = await scroll.evaluate('e=>({top:e.getBoundingClientRect().top,first:e.firstElementChild.getBoundingClientRect().top,scroll:e.scrollTop})')
                assert first['first'] >= first['top']-1, 'First message is clipped even at scroll start'
                await page.get_by_role('button',name='对标参考产品 展开',exact=True).click()
                expanded = await composer_geometry(page)
                assert expanded['textarea_visible'] and expanded['button_visible'], 'Expanded reference panel hides composer'
                await page.get_by_role('button',name='对标参考产品 收起',exact=True).click()
                reports.append(dict(size=[width,height],scale=scale,geometry=geometry,first_message=first))
                await context.close()
            await browser.close()
    (output/'geometry.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps([dict(size=r['size'],history=r['geometry']['boxes']['.chat-messages']['height'],input=r['geometry']['textarea']['height'],send_visible=r['geometry']['button_visible']) for r in reports]))

parser=argparse.ArgumentParser()
parser.add_argument('--static',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--inspect',action='store_true')
asyncio.run(run(parser.parse_args()))
