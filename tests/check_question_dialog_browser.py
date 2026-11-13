"""Question dialog against the built workbench and isolated ASGI data; no listener/model."""
import argparse
import asyncio
import mimetypes
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
IMPORT_DATA = tempfile.TemporaryDirectory(prefix='ra-question-import-')
os.environ['RA_DATA_DIR'] = IMPORT_DATA.name
os.environ['RA_ACCESS_TOKEN'] = 'off'

import httpx
from playwright.async_api import async_playwright, expect
from app.main import create_app
from app.provider import Provider
from tests.helpers import prepared
from tests.browser_navigation import select_project

ORIGIN = 'http://127.0.0.1:8765'


async def run(build, output):
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ra-question-dialog-') as scratch:
        folder = Path(scratch)
        class NoModel(Provider):
            async def request(self, *args, **kwargs):
                raise AssertionError('No model calls allowed')
        app = create_app(folder/'data', provider=NoModel(env_path=folder/'absent.env', credential_dir=folder/'credentials'))
        store = app.state.store
        initial = prepared(store)
        pid = initial['id']
        original_question = dict(initial['questions'][0])
        other = store.create('隔离的其他项目')['id']
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client, async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            for width, height in ((1366,768),(846,620),(390,844)):
                with store.edit(pid, store.get(pid)['revision'], 'synthetic dialog fixture') as (p,_):
                    p['name'] = 'BMS 末端降功提问示例 '+str(width)
                    p['questions'] = [dict(original_question)]
                    q = p['questions'][0]
                    q.update(question='末端降功解除后，电流如何恢复？', why='决定恢复过程是否出现电流突变。', options=['每个控制周期最多增加 1 A','直接恢复至当前允许值'])
                    p['questions'].append(dict(q, id='Q-0002', topic_key='temperature', question='还需要补充哪些温度边界？', options=[]))
                    p['questions'].append(dict(q, id='Q-HISTORY', topic_key='old', question='历史旧问题', superseded_by='Q-0001'))
                    p['questions'].append(dict(q, id='Q-SAVED', topic_key='saved', question='已回答待核对的问题', status='answered',answer='已保存原话',decision_coverage='pending'))
                context = await browser.new_context(viewport=dict(width=width,height=height))
                errors, writes = [], []
                fail_next = False
                async def dispatch(route):
                    nonlocal fail_next
                    req, url = route.request, urlsplit(route.request.url)
                    try:
                        assert url.netloc == '127.0.0.1:8765'
                        if url.path.startswith('/api/'):
                            assert req.method in ('GET','HEAD') or url.path.endswith('/answers'), 'Unexpected mutation '+url.path
                            if url.path.endswith('/answers'):
                                writes.append(req.post_data_json)
                                if fail_next:
                                    fail_next = False
                                    await route.fulfill(status=500,json={'detail':'模拟保存失败'})
                                    return
                                await asyncio.sleep(.15)
                            headers = {k:v for k,v in req.headers.items() if k.lower() not in ('host','content-length','connection','accept-encoding')}
                            headers['host'] = '127.0.0.1:8765'
                            result = await client.request(req.method,url.path+('?' +url.query if url.query else ''),content=req.post_data_buffer,headers=headers)
                            await route.fulfill(status=result.status_code,body=result.content,content_type=result.headers.get('content-type','application/json'))
                        else:
                            target = (build/(url.path.lstrip('/') or 'index.html')).resolve()
                            assert target.is_relative_to(build) and target.is_file()
                            await route.fulfill(body=target.read_bytes(),content_type=mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
                    except Exception as error:
                        errors.append(str(error));await route.abort()
                await context.route('**/*',dispatch)
                page = await context.new_page()
                page.on('pageerror',lambda error: errors.append(str(error)))
                await page.goto(ORIGIN,wait_until='networkidle')
                await select_project(page,pid)
                entry = page.locator('.connected-workspace:visible .question-dialog-entry')
                await expect(entry).to_contain_text('2 个问题待回答')
                await expect(page.locator('.question-dialog')).to_have_count(0)
                await entry.get_by_role('button',name='逐题回答').click()
                dialog = page.get_by_role('dialog')
                await expect(dialog.get_by_role('button',name='提交并继续')).to_be_disabled()
                await dialog.get_by_role('radio').first.check()
                await dialog.get_by_role('textbox').fill('仅用于虚构示例；每 100 ms 执行一次。')
                await dialog.get_by_role('button',name='关闭提问，保留输入').click()
                await select_project(page,other)
                await expect(page.locator('.question-dialog')).to_have_count(0)
                await select_project(page,pid)
                await page.locator('.connected-workspace:visible').get_by_role('button',name='逐题回答',exact=True).click()
                await expect(dialog.get_by_role('textbox')).to_have_value('仅用于虚构示例；每 100 ms 执行一次。')
                await page.reload(wait_until='networkidle')
                await select_project(page,pid)
                await page.locator('.connected-workspace:visible').get_by_role('button',name='逐题回答',exact=True).click()
                await expect(dialog.get_by_role('radio').first).to_be_checked()
                await expect(dialog.get_by_role('textbox')).to_have_value('仅用于虚构示例；每 100 ms 执行一次。')
                box = await dialog.bounding_box()
                assert box['x']>=0 and box['y']>=0 and box['x']+box['width']<=width+1 and box['y']+box['height']<=height+1
                assert await dialog.evaluate('e=>e.scrollWidth<=e.clientWidth')
                await page.screenshot(path=str(output/f'question-{width}.png'))
                fail_next = True
                await dialog.get_by_role('button',name='提交并继续').click()
                await expect(dialog.get_by_role('alert')).to_contain_text('输入已保留')
                await expect(dialog.get_by_role('radio').first).to_be_checked()
                await dialog.get_by_role('button',name='提交并继续').click()
                await expect(dialog.get_by_role('heading')).to_have_text('还需要补充哪些温度边界？')
                saved = store.get(pid)['questions'][0]
                assert saved['answer']=='每个控制周期最多增加 1 A\n补充：仅用于虚构示例；每 100 ms 执行一次。'
                assert saved['status']=='answered'
                await expect(dialog.get_by_role('radio')).to_have_count(0)
                await dialog.get_by_role('textbox').fill('示例只验证末端电压，温度保护沿用已有规则。')
                await dialog.get_by_role('button',name='提交回答',exact=True).click()
                await expect(dialog.get_by_role('heading')).to_have_text('回答完成，继续核对需求')
                assert len(writes)==3
                assert store.get(pid)['questions'][1]['answer']=='示例只验证末端电压，温度保护沿用已有规则。'
                assert store.get(pid)['questions'][2]['status']=='open'
                await expect(page.locator('.connected-workspace:visible .question-dialog-entry')).to_have_count(0)
                with store.edit(pid,store.get(pid)['revision'],'new synthetic followup') as (p,_):
                    p['questions'].append(dict(original_question,id='Q-FOLLOWUP',topic_key='followup',question='新增追问：恢复斜率是否需要标定？'))
                await dialog.get_by_role('button',name='继续核对草稿').click()
                await expect(dialog.get_by_role('heading')).to_have_text('新增追问：恢复斜率是否需要标定？')
                await page.keyboard.press('Escape')
                await expect(page.locator('.question-dialog')).to_have_count(0)
                assert not errors, errors
                print(f'PASS {width}x{height}: persistence, isolation, failure/retry, sequential save, history exclusion, geometry, continuation, new-question popup, Escape',flush=True)
                await context.close()
            await browser.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--static-build',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    asyncio.run(run(args.static_build.resolve(),args.output.resolve()))
