"""Built-UI + real ASGI ingestion/actions, with isolated synthetic model/data.

Ephemeral loopback test listener only; no daily data or external model calls.
Run with .venv Python; --dist must point to the candidate Vite build.
"""
import argparse
import asyncio
import io
import json
import mimetypes
import os
from pathlib import Path
import random
import sys
import tempfile
import socket
import threading
import uvicorn
from starlette.staticfiles import StaticFiles
import time
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
isolated = Path(tempfile.mkdtemp(prefix='ra-intake-browser-'))
os.environ['RA_DATA_DIR'] = str(isolated / 'data')
os.environ.pop('RA_DEEPSEEK_API_KEY', None)
import app.config
app.config.ROOT = isolated
import app.main as main
from docx import Document
from docx.shared import Inches
from fastapi.testclient import TestClient
from PIL import Image
from playwright.async_api import async_playwright, expect
from tests.ui02_fixture import model_server


async def run(dist, evidence):
    evidence.mkdir(parents=True, exist_ok=True)
    checks, errors = [], []
    server = thread = sock = None
    original_save = main.save_source

    def slow_save(*args, **kwargs):
        time.sleep(5)  # Controlled parser latency to inspect real XHR/reading UI.
        return original_save(*args, **kwargs)

    main.save_source = slow_save
    doc = Document()
    doc.add_paragraph('合成验收：联系人列表增加选填备注；只读角色不能修改。')
    for n in range(4):
        image = io.BytesIO()
        Image.frombytes('RGB', (480, 320), random.Random(n).randbytes(480 * 320 * 3)).save(image, format='PNG')
        doc.add_picture(io.BytesIO(image.getvalue()), width=Inches(4))
    file = io.BytesIO(); doc.save(file)
    (evidence / 'synthetic-intake.docx').write_bytes(file.getvalue())
    try:
        with model_server() as model:
            model['delay'] = .8
            application = main.create_app(isolated / 'case', access_token='off', env_path=isolated / 'absent.env')
            with TestClient(application) as client:
                csrf = client.post('/api/session/login', json={'key': 'off'}).json()['csrf']
                client.headers.update({'Origin': 'http://testserver', 'X-CSRF-Token': csrf})
                for slot in ('model', 'vision'):
                    assert client.put('/api/models/' + slot, json=model['config']).status_code == 200
                    assert client.put('/api/models/' + slot + '/key', json={'key': 'synthetic-local-key'}).status_code == 200
                project = client.post('/api/projects', json={'name': '合成导入与分析验收'}).json()
                root = '/api/projects/' + project['id']
                application.router.routes[:] = [r for r in application.router.routes if not (getattr(r, 'path', None) == '' and getattr(r, 'name', None) == 'web')]
                application.mount('/', StaticFiles(directory=dist, html=True), name='isolated-web')
                sock = socket.socket(); sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
                server = uvicorn.Server(uvicorn.Config(application, log_level='error', access_log=False))
                thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True); thread.start()
                for _ in range(100):
                    if server.started: break
                    await asyncio.sleep(.05)
                assert server.started, 'isolated test server did not start'
                async with async_playwright() as pw:
                    browser = await pw.chromium.launch(headless=True)
                    page = await browser.new_page(viewport={'width': 1440, 'height': 1000})
                    page.on('pageerror', lambda e: errors.append(str(e)))

                    await page.goto(f'http://127.0.0.1:{port}/')
                    await page.get_by_label('本机访问令牌').fill('off')
                    await page.get_by_role('button', name='进入工作台').click()
                    await page.get_by_role('button', name='合成导入与分析验收', exact=True).click()
                    await page.get_by_role('button', name='项目资料 · 0', exact=True).click()
                    drawer = page.get_by_role('dialog', name='项目资料', exact=True)
                    await page.screenshot(path=str(evidence / 'import-desktop.png'))
                    await drawer.locator('input[type=file]').set_input_files({
                        'name': 'synthetic-intake.docx', 'mimeType': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                        'buffer': file.getvalue()})
                    await expect(drawer.get_by_role('progressbar', name='文档读取进度')).to_be_visible()
                    await expect(drawer.get_by_role('button', name='关闭项目资料')).to_be_disabled()
                    await page.keyboard.press('Escape')
                    await expect(drawer).to_be_visible()
                    await page.screenshot(path=str(evidence / 'import-reading.png'))
                    checks.append('real XHR upload enters visible reading progress; close/Escape guarded')
                    await expect(drawer.get_by_role('button', name='关闭项目资料')).to_be_enabled(timeout=15000)
                    p = client.get(root).json()
                    assert len([s for s in p['sources'] if s.get('image_mime')]) == 4
                    assert any('选填备注' in e['text'] for s in p['sources'] for e in s['excerpts'])
                    assert not model['requests']
                    checks.append('Word text and 4 embedded images extracted; import makes no model requests')
                    await page.screenshot(path=str(evidence / 'import-result.png'))
                    await drawer.locator('input[type=file]').set_input_files({'name':'second.txt','mimeType':'text/plain','buffer':'合成追加说明：未知规则必须澄清。'.encode()})
                    await expect(drawer.get_by_role('progressbar', name='文档读取进度')).to_be_visible()
                    await expect(drawer.get_by_role('button', name='关闭项目资料')).to_be_enabled(timeout=15000)
                    assert len(client.get(root).json()['sources']) == 6
                    checks.append('selecting a second file directly after completion imports it once')
                    await drawer.get_by_role('button', name='关闭项目资料').click()
                    await page.get_by_role('button', name='✦ 分析现状与诉求 →', exact=True).click()
                    plan = page.get_by_role('dialog', name='核对本次任务')
                    await expect(plan).to_be_visible()
                    assert not model['requests']
                    await plan.get_by_role('button', name='授权本次范围并运行').click()
                    await expect(page.get_by_role('progressbar', name='分析步骤进度')).to_be_visible()
                    await expect(page.get_by_role('button', name='正在分析现状与诉求…')).to_be_disabled()
                    await page.screenshot(path=str(evidence / 'analysis-running.png'))
                    checks.append('explicit authorization before model; running status by analyze button; duplicate start disabled')
                    await expect(page.get_by_role('button', name='查看分析结果', exact=True)).to_be_visible(timeout=30000)
                    task = client.get(root + '/actions').json()[-1]
                    assert task['status'] in ('succeeded', 'partial') and task['completed_steps'] == 3, task
                    assert task['calls'] == 3 and len(model['requests']) == 3
                    assert any(m['stage'] == 'ingest' and m['role'] == 'assistant' for m in client.get(root).json()['messages'])
                    await page.screenshot(path=str(evidence / 'analysis-complete.png'))
                    checks.append('large-image Word -> 2 vision batches -> ingest -> visible result, 3 synthetic requests')
                    await page.get_by_role('button', name='查看分析结果', exact=True).click()
                    await expect(page.get_by_role('heading', name='核对现状、价值与改动范围', exact=True)).to_be_visible()
                    checks.append('result button navigates to actual scope analysis')
                    # A genuine provider failure must remain visible after budgets are removed.
                    model['fail_next'] = True
                    for s in client.get(root).json()['sources']:
                        if s.get('image_mime'):
                            with application.state.store.edit(project['id'], client.get(root).json()['revision'], 'Synthetic failure observation') as (draft, _):
                                source = next(x for x in draft['sources'] if x['id'] == s['id'])
                                source.pop('vision_run_id', None)
                                source['parse_status'] = 'awaiting_vision'
                            break
                    await page.reload()
                    await page.get_by_role('button', name='合成导入与分析验收', exact=True).click()
                    await page.get_by_role('button', name='提供现状与诉求', exact=False).click()
                    await page.get_by_role('button', name='✦ 分析现状与诉求 →', exact=True).click()
                    await page.get_by_role('dialog', name='核对本次任务').get_by_role('button', name='运行本次任务', exact=True).click()
                    await expect(page.get_by_text('本次已发出 1 次模型请求。', exact=False)).to_be_visible(timeout=15000)
                    status = page.locator('.step-check-0 .task-status')
                    await expect(status).to_contain_text('HTTP 401')
                    assert await status.locator('details').get_attribute('open') is None
                    assert len(model['requests']) == 4
                    checks.append('actual provider failure visible beside button with details closed; inputs preserved')
                    await page.screenshot(path=str(evidence / 'analysis-paused.png'))
                    await page.set_viewport_size({'width': 390, 'height': 844})
                    await page.get_by_role('button', name='项目资料 ·', exact=False).click()
                    await page.screenshot(path=str(evidence / 'import-mobile.png'))
                    assert not errors, errors
                    await browser.close()
    finally:
        main.save_source = original_save
        if server: server.should_exit = True
        if thread: await asyncio.to_thread(thread.join, 5)
        if sock: sock.close()
        (evidence / 'browser-checks.json').write_text(json.dumps(dict(
            mode='built UI + actual isolated loopback HTTP app/model; ephemeral listener closed; no daily data',
            checks=checks, page_errors=errors, isolated_path=str(isolated)), ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({'checks': len(checks), 'page_errors': errors}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dist', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.dist.resolve(), args.evidence.resolve()))
