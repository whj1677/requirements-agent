"""Material grouping, screenshot reference and original preview/download UI.

Only synthetic data, an isolated temporary HTTP app and a short-lived browser.
"""
import argparse
import asyncio
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ISOLATED = Path(tempfile.mkdtemp(prefix='ra-material-library-'))
os.environ['RA_DATA_DIR'] = str(ISOLATED / 'module-data')
import app.config
app.config.ROOT = ISOLATED
from app.main import create_app
from docx import Document
from docx.shared import Inches
from fastapi.testclient import TestClient
from PIL import Image
from playwright.async_api import async_playwright, expect
from starlette.staticfiles import StaticFiles
import uvicorn


async def run(dist, evidence):
    evidence.mkdir(parents=True, exist_ok=True)
    result = {'status': 'running', 'cases': [], 'page_errors': [], 'model_calls': 0}
    class NoModel:
        async def request(self, *args, **kwargs):
            result['model_calls'] += 1
            raise AssertionError('Material browsing must not call a model')
        def key_status(self, config):
            return {'key_configured': False, 'key_source': 'none', 'bound_origin': 'synthetic'}
    app = create_app(ISOLATED / 'case', access_token='synthetic-token', provider=NoModel(), env_path=ISOLATED / 'absent.env')
    app.router.routes[:] = [r for r in app.router.routes if getattr(r, 'name', '') != 'web']
    app.mount('/', StaticFiles(directory=dist, html=True), name='web')
    png = io.BytesIO(); Image.new('RGB', (500, 280), '#265b43').save(png, format='PNG')
    document = Document(); document.add_paragraph('合成现状：已有联系人列表。此原文必须可以在附件中核对。')
    document.add_picture(io.BytesIO(png.getvalue()), width=Inches(4))
    raw = io.BytesIO(); document.save(raw)
    thread = server = sock = None
    try:
        with TestClient(app) as client:
            token = client.post('/api/session/login', json={'key': 'synthetic-token'}).json()['csrf']
            client.headers.update({'Origin': 'http://testserver', 'X-CSRF-Token': token})
            project = client.post('/api/projects', json={'name': '材料分类与预览验收'}).json()
            root = '/api/projects/' + project['id']
            goal = client.post(root + '/sources/text', json={'expected_revision': project['revision'], 'title': '本期目标', 'text': '合成目标：减少查找时间。', 'purpose': 'goal'}).json()
            revision = client.get(root).json()['revision']
            response = client.post(root + '/sources/file', data={'expected_revision': revision, 'purpose': 'current'}, files={'file': ('现状说明.docx', raw.getvalue())})
            assert response.status_code == 200, response.text
            word = response.json()
            sock = socket.socket(); sock.bind(('127.0.0.1', 0))
            server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
            thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True); thread.start()
            for _ in range(100):
                if server.started: break
                await asyncio.sleep(.05)
            assert server.started
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                page = await browser.new_page(viewport={'width': 1440, 'height': 1000})
                page.on('pageerror', lambda error: result['page_errors'].append(str(error)))
                await page.goto(f'http://127.0.0.1:{sock.getsockname()[1]}/')
                await page.get_by_label('本机访问令牌').fill('synthetic-token')
                await page.get_by_role('button', name='进入工作台').click()
                await page.get_by_role('button', name='材料分类与预览验收', exact=True).click()
                await expect(page.locator('.intake-material-group').filter(has_text='目标与诉求')).to_contain_text('本期目标')
                await expect(page.locator('.intake-material-group').filter(has_text='现状资料')).to_contain_text('现状说明.docx')
                assert await page.locator('.intake-material-group .material-file').count() == 2
                result['cases'].append('intake separates goals/current and nests embedded images')
                await page.get_by_role('button', name='项目资料 ·', exact=False).click()
                drawer = page.get_by_role('dialog', name='项目资料', exact=True)
                async with page.expect_file_chooser() as chooser:
                    await drawer.get_by_role('button', name='导入墨刀原型截图', exact=True).click()
                await (await chooser.value).set_files({'name': '墨刀-联系人.png', 'mimeType': 'image/png', 'buffer': png.getvalue()})
                await expect(drawer.get_by_role('button', name='查看内容：墨刀-联系人.png', exact=True)).to_be_visible()
                screenshot = next(s for s in client.get(root).json()['sources'] if s['title'] == '墨刀-联系人.png')
                assert screenshot['purpose'] == 'reference' and screenshot['parse_status'] == 'awaiting_vision'
                await expect(drawer.locator('.source-category').filter(has=page.get_by_role('heading', name='设计参考', exact=False))).to_contain_text('墨刀-联系人.png')
                result['cases'].append('Modao screenshot imports as reference without claiming visual understanding')
                await drawer.get_by_role('button', name='查看内容：现状说明.docx', exact=True).click()
                preview = page.get_by_role('dialog', name='附件内容', exact=True)
                await expect(preview).to_contain_text('此原文必须可以在附件中核对')
                await expect(preview.get_by_role('heading', name='文档内图片 · 1')).to_be_visible()
                async with page.expect_download() as download_info:
                    await preview.get_by_role('button', name='下载原始附件', exact=True).click()
                download = await download_info.value
                await download.save_as(evidence / 'original.docx')
                assert (evidence / 'original.docx').read_bytes() == raw.getvalue()
                await page.screenshot(path=str(evidence / 'word-preview.png'))
                result['cases'].append('Word extracted content and embedded image preview; original download byte-identical')
                await preview.get_by_role('button', name='关闭附件').click()
                # Reclassify a parent and verify the evidence identity and child purpose.
                card = drawer.locator('.material-card').filter(has=page.get_by_role('button', name='查看内容：现状说明.docx', exact=True))
                await card.get_by_text('分类与读取操作', exact=True).click()
                await card.get_by_label('资料分类：现状说明.docx', exact=True).select_option('goal')
                await expect(drawer.locator('.source-category').filter(has=page.get_by_role('heading', name='目标与诉求', exact=False))).to_contain_text('现状说明.docx')
                current = client.get(root).json()
                parent = next(s for s in current['sources'] if s['id'] == word['id'])
                assert parent['purpose'] == 'goal' and parent['sha256'] == word['sha256'] and parent['excerpts'] == word['excerpts']
                assert all(s['purpose'] == 'goal' for s in current['sources'] if s.get('container_source_id') == word['id'])
                result['cases'].append('manual reclassification synchronizes children and preserves evidence')
                await page.screenshot(path=str(evidence / 'library-desktop.png'))
                await drawer.get_by_role('button', name='查看内容：墨刀-联系人.png', exact=True).click()
                await expect(preview.get_by_role('img', name='墨刀-联系人.png', exact=True)).to_be_visible()
                assert await preview.get_by_role('img', name='墨刀-联系人.png', exact=True).evaluate('(img)=>img.naturalWidth') == 500
                await preview.get_by_role('button', name='查看原图尺寸').click()
                await expect(preview.get_by_role('button', name='适应窗口')).to_be_visible()
                result['cases'].append('screenshot opens and supports original-size viewing')
                await preview.get_by_role('button', name='关闭附件').click()
                await page.set_viewport_size({'width': 390, 'height': 844})
                await page.screenshot(path=str(evidence / 'library-mobile.png'))
                await drawer.get_by_role('button', name='关闭项目资料').click()
                # First-screen attachment itself opens the corresponding content.
                await page.get_by_role('button', name='墨刀-联系人.png', exact=False).click()
                await expect(preview.get_by_role('img', name='墨刀-联系人.png', exact=True)).to_be_visible()
                result['cases'].append('intake attachment click opens the corresponding preview on narrow screen')
                assert not result['page_errors'] and result['model_calls'] == 0
                await browser.close()
                result['status'] = 'passed'
    except Exception as error:
        result['status'] = 'failed'; result['error'] = str(error)
        raise
    finally:
        if server: server.should_exit = True
        if thread: await asyncio.to_thread(thread.join, 5)
        if sock: sock.close()
        result['listener_closed'] = not thread or not thread.is_alive()
        (evidence / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--dist', type=Path, required=True); parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args(); asyncio.run(run(args.dist.resolve(), args.evidence.resolve()))
