"""Offline normal-entry check for direction and explicit item acceptance."""
import asyncio
import json
import socket
import sys
import threading
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn
from playwright.async_api import async_playwright

from app.core import ROOT
from tests.test_pilot01_fix import setup


async def main():
    folder = ROOT / 'evidence/runtime/pilot-01-fix' / ('db-' + uuid.uuid4().hex)
    client, store, pid = setup(folder)
    app = client.app
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(.1)
    assert server.started
    evidence = ROOT / 'evidence/runtime/pilot-01-fix'
    requests = []
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page = await browser.new_page(viewport={'width': 1440, 'height': 900})
                page.on('request', lambda request: requests.append(request.url) if request.method == 'POST' and request.url.endswith('/runs') else None)
                await page.goto(f'http://127.0.0.1:{port}')
                await page.get_by_label('本机访问令牌').fill('test-token')
                await page.get_by_role('button', name='进入工作台').click()
                await page.get_by_role('button', name='离线方案测试').first.click()
                await page.get_by_role('heading', name='离线方案测试').wait_for()
                await page.get_by_role('tab', name='方案对比').click()
                first = page.get_by_role('article').filter(has=page.get_by_role('heading', name='方向 1'))
                await first.get_by_role('button', name='按此方向继续讨论').click()
                await page.get_by_role('dialog', name='核对方案操作').get_by_role('button', name='确认提交').click()
                await page.get_by_text('讨论方向：本期已选').first.wait_for()
                assert [x['selection_status'] for x in store.get(pid)['items']] == ['candidate', 'candidate']
                assert store.get(pid)['questions'][0]['status'] == 'open'
                await first.get_by_role('button', name='采纳指定条目').click()
                dialog = page.get_by_role('dialog', name='核对方案操作')
                assert await dialog.get_by_role('button', name='确认提交').is_disabled()
                await dialog.get_by_role('checkbox').first.check()
                assert await dialog.get_by_text('合成原文 1').is_visible()
                assert await dialog.get_by_text('合成来源原文').first.is_visible()
                await page.screenshot(path=str(evidence / 'explicit-item-review.png'), full_page=True)
                await dialog.get_by_role('button', name='确认提交').click()
                await dialog.wait_for(state='hidden')
                assert [x['selection_status'] for x in store.get(pid)['items']] == ['selected', 'candidate']
                await page.screenshot(path=str(evidence / 'direction-and-partial-acceptance.png'), full_page=True)
                assert not requests
                print(json.dumps({'result':'PASS','project_id':pid,'screenshot':str(evidence / 'direction-and-partial-acceptance.png'),'model_run_posts':len(requests)}, ensure_ascii=False))
            finally:
                await browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


if __name__ == '__main__':
    asyncio.run(main())
