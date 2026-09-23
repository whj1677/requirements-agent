"""Normal app-entry browser regressions using only local synthetic fixtures."""
import asyncio
import copy
import sys
import threading
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn
from playwright.async_api import async_playwright

from app.core import ROOT
from app.main import create_app
from app.workflow import apply_response
from tests.helpers import prepared


async def main(case):
    evidence = ROOT/'evidence/runtime/review-fixes'
    evidence.mkdir(parents=True, exist_ok=True)
    app = create_app(evidence/('db-'+uuid.uuid4().hex), access_token='offline-review-token', env_path=evidence/'.env')
    store = app.state.store
    p = prepared(store)
    if case == 'r1':
        a = copy.deepcopy(p['documents']['prd'])
        a['id'], a['content']['title'] = 'DOC-A', 'PRD A 专属正文'
        b = copy.deepcopy(a)
        b['id'], b['content']['title'] = 'DOC-B', 'PRD B 专属正文'
        with store.edit(p['id'], p['revision'], '同底稿两份文档', bump=False) as (state, db):
            store.record(p['id'], 'document_artifact', a, db=db)
            store.record(p['id'], 'document_artifact', b, db=db)
            state['documents']['prd'] = b
    else:
        base = p['ui']['spec']
        with store.edit(p['id'], p['revision'], '两个合成布局候选', bump=False) as (state, _):
            for label in ('拒绝的候选布局', '已采纳的新布局'):
                spec = copy.deepcopy(base)
                spec['pages'][0]['regions'][0]['components'][0]['label'] = label
                apply_response(state, dict(stage='ui', result={'spec':spec}, proposals=[], questions=[], summary='合成候选', limitations=[]))
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=8877, log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(.1)
    assert server.started
    if case == 'r3':
        store.record(p['id'], 'run', dict(stage='ui', status='running', created='synthetic-poll'))
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page = await browser.new_page(viewport={'width':1440,'height':900})
                await page.goto('http://127.0.0.1:8877')
                await page.get_by_label('本机访问令牌').fill('offline-review-token')
                await page.get_by_role('button', name='进入工作台').click()
                await page.get_by_role('button', name='合成工程验证项目').first.click()
                if case == 'r1':
                    await page.get_by_role('button', name='需求文档', exact=True).click()
                    await page.get_by_label('文档版本').select_option(index=1)
                    await page.get_by_role('heading', name='PRD A 专属正文').first.wait_for()
                    assert await page.get_by_role('link', name='DOCX 下载').count() == 0, 'historical A incorrectly offers current B download'
                    await page.get_by_label('文档版本').select_option('')
                    await page.get_by_role('heading', name='PRD B 专属正文').first.wait_for()
                    assert await page.get_by_role('link', name='DOCX 下载').count() == 1
                    async with page.expect_download() as pending:
                        await page.get_by_role('link', name='MD 下载').click()
                    download = await pending.value
                    target = evidence/'r1-current-b.md'
                    await download.save_as(str(target))
                    content = target.read_text('utf-8')
                    assert 'PRD B 专属正文' in content and 'PRD A 专属正文' not in content
                    await page.screenshot(path=str(evidence/'r1-document.png'), full_page=True)
                else:
                    await page.get_by_role('button', name='原型画布', exact=True).click()
                    frame = page.frame_locator('iframe[title="低保真页面预览"]')
                    await frame.get_by_text('时段列表', exact=False).first.wait_for()
                    await frame.locator('body').evaluate('node => { window.__reviewMarker = 17; }')
                    await page.wait_for_timeout(2600)
                    assert await frame.locator('body').evaluate('node => window.__reviewMarker') == 17, 'ordinary poll reloaded unchanged canvas'
                    await page.get_by_role('button', name='保留旧方案').first.click()
                    await page.get_by_text('拒绝的候选布局').first.wait_for(state='detached')
                    assert await frame.locator('body').evaluate('node => window.__reviewMarker') == 17, 'reject reloaded active canvas'
                    await page.get_by_role('button', name='采纳候选并建立新草稿').click()
                    await frame.get_by_text('已采纳的新布局', exact=False).first.wait_for(timeout=5000)
                    assert await frame.locator('body').evaluate('node => window.__reviewMarker') is None
                    await page.screenshot(path=str(evidence/'r3-activated.png'), full_page=True)
                print(case.upper()+' browser regression PASS')
            finally:
                await browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1]))
