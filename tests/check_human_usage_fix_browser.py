"""Isolated human-usage checks on the sole workbench origin.

Synthetic fixtures and an in-process ASGI app only: no listener, daily data,
or network model calls. Clipboard events are synthetic; when not cancelled,
Playwright inserts text with Chromium's keyboard input to model the browser's
default insertion, rather than treating dispatchEvent as a native paste.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import mimetypes
import os
import re
from pathlib import Path
import sys
import tempfile
import traceback
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
IMPORT_DIR = tempfile.TemporaryDirectory(prefix='ra-human-usage-import-')
os.environ['RA_DATA_DIR'] = str(Path(IMPORT_DIR.name) / 'data')
os.environ['RA_ACCESS_TOKEN'] = 'off'

import httpx
from docx import Document
from playwright.async_api import async_playwright, expect
from app.main import create_app
from tests.browser_navigation import select_project
from tests.check_chat_composer_browser import OfflineInterview, composer_geometry
from tests.check_detail_prd_browser import seed

ORIGIN = 'http://127.0.0.1:8765'
VIEWPORTS = ((846, 620), (1366, 768), (390, 844))


def document_bytes():
    doc = Document()
    doc.add_paragraph('合成储能资料：只查看已保存场站。')
    output = io.BytesIO()
    doc.save(output)
    return output.getvalue()


async def mixed_paste(page, textarea, filename, mime, text):
    """Dispatch mixed clipboard content, then simulate native default if allowed."""
    await textarea.fill('前待换后')
    await textarea.focus()
    await textarea.evaluate('e => e.setSelectionRange(1, 3)')
    cancelled = await textarea.evaluate('''(element, value) => {
        const data = new DataTransfer();
        data.setData('text/plain', value.text);
        data.items.add(new File(['synthetic'], value.filename, {type:value.mime}));
        const event = new ClipboardEvent('paste', {clipboardData:data, bubbles:true, cancelable:true});
        element.dispatchEvent(event);
        return event.defaultPrevented;
    }''', dict(filename=filename, mime=mime, text=text))
    if not cancelled:
        await page.keyboard.insert_text(text)
    await expect(textarea).to_have_value('前' + text + '后')
    return cancelled


async def run(args):
    build, output = args.static_build.resolve(), args.output.resolve()
    assert (build / 'index.html').is_file(), f'No built index.html: {build}'
    assert output != ROOT and (output.is_relative_to(ROOT / 'work') or output.is_relative_to(ROOT / 'evidence'))
    assert not output.exists(), f'Use a new output directory: {output}'
    output.mkdir(parents=True)
    report = dict(status='running', static_build=str(build), fixture='SYNTHETIC_ONLY',
                  listener_started=False, daily_data_access=False, model_calls=0,
                  checks=[], failures=[], requests=[], page_errors=[], routing_errors=[])
    with tempfile.TemporaryDirectory(prefix='ra-human-usage-') as scratch:
        folder = Path(scratch)
        provider = OfflineInterview(folder)
        app = create_app(folder / 'data', provider=provider, env_path=folder / 'absent.env')
        store = app.state.store
        pid = seed(store)
        with store.edit(pid, store.get(pid)['revision'], '合成人本使用回归') as (project, _):
            project['name'] = '合成储能云人本回归'
        other = store.create('合成切换项目')['id']

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN, timeout=30) as client:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                for width, height in VIEWPORTS:
                    label = f'{width}x{height}'
                    context = await browser.new_context(viewport=dict(width=width, height=height))

                    async def dispatch(route):
                        request = route.request
                        url = urlsplit(request.url)
                        try:
                            if url.scheme != 'http' or url.netloc != '127.0.0.1:8765':
                                report['routing_errors'].append('Blocked external URL: ' + request.url)
                                await route.abort()
                                return
                            if url.path.startswith('/api/'):
                                # Do not allow AI task creation, even against the synthetic provider.
                                if request.method not in ('GET', 'HEAD') and ('/tasks' in url.path or '/runs' in url.path):
                                    report['routing_errors'].append('Blocked model task: ' + request.method + ' ' + url.path)
                                    await route.abort()
                                    return
                                headers = {k: v for k, v in request.headers.items() if k.lower() not in
                                           ('host', 'content-length', 'connection', 'accept-encoding')}
                                headers['host'] = '127.0.0.1:8765'
                                target = url.path + ('?' + url.query if url.query else '')
                                response = await client.request(request.method, target, content=request.post_data_buffer, headers=headers)
                                report['requests'].append(dict(viewport=label, method=request.method, path=url.path, status=response.status_code))
                                await route.fulfill(status=response.status_code, body=response.content,
                                    content_type=response.headers.get('content-type', 'application/json'))
                            elif url.path == '/' or url.path.startswith('/assets/'):
                                target = (build / (url.path.lstrip('/') or 'index.html')).resolve()
                                if not target.is_relative_to(build) or not target.is_file():
                                    raise AssertionError('Missing static asset: ' + url.path)
                                await route.fulfill(status=200, body=target.read_bytes(),
                                    content_type=mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
                            else:
                                report['routing_errors'].append('Blocked unknown path: ' + url.path)
                                await route.abort()
                        except Exception as exc:
                            report['routing_errors'].append(f'{type(exc).__name__}: {exc}')
                            await route.abort()

                    await context.route('**/*', dispatch)
                    page = await context.new_page()
                    page.set_default_timeout(10000)
                    page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
                    try:
                        await page.goto(ORIGIN + '/', wait_until='networkidle')
                        await select_project(page, pid)
                        work = page.locator('.connected-workspace:visible')
                        textarea = work.get_by_role('textbox', name='你的想法', exact=True)
                        await expect(textarea).to_be_visible()
                        await textarea.click()
                        await textarea.fill('合成草稿，待发送。')
                        geometry = await composer_geometry(page)
                        assert geometry['textarea_visible'] and geometry['button_visible'], geometry
                        report['checks'].append(dict(viewport=label, check='input and send visible and hit-testable', geometry=geometry))

                        cancelled = await mixed_paste(page, textarea, '混合资料.txt', 'text/plain', '粘贴补充')
                        await expect(work.locator('.chat-pasted-image')).to_have_count(1)
                        report['checks'].append(dict(viewport=label, check='mixed clipboard text replaces selection and accepted file appears', default_prevented=cancelled))
                        await work.locator('.chat-pasted-image button').click()
                        cancelled = await mixed_paste(page, textarea, '拒绝程序.exe', 'application/octet-stream', '拒绝附件仍保留文字')
                        await expect(work.locator('.chat-pasted-image')).to_have_count(0)
                        await expect(work).to_contain_text('拒绝程序.exe')
                        report['checks'].append(dict(viewport=label, check='rejected file does not swallow mixed clipboard text', default_prevented=cancelled))
                        await page.screenshot(path=str(output / (label + '-clipboard.png')))
                        await textarea.fill('合成草稿，待发送。')

                        # One picker selection must retain both files and both must be read after save.
                        picker = work.get_by_label('选择本轮聊天附件')
                        assert await picker.get_attribute('multiple') is not None
                        names = {f'场站说明-{label}.txt', f'派单说明-{label}.docx'}
                        await picker.set_input_files([
                            dict(name=f'场站说明-{label}.txt', mimeType='text/plain', buffer='合成场站说明：只读查看。'.encode('utf-8')),
                            dict(name=f'派单说明-{label}.docx', mimeType='application/vnd.openxmlformats-officedocument.wordprocessingml.document', buffer=document_bytes()),
                        ])
                        await expect(work.locator('.chat-pasted-image')).to_have_count(2)

                        # Test navigation before upload: both files remain truly unsent and unsaved.
                        before_attachment_ids = {s['id'] for s in store.get(pid)['sources']}
                        await work.get_by_role('button', name='继续核对已有草稿', exact=True).click()
                        await expect(work.get_by_role('table', name='需求确认表', exact=True)).to_be_visible()
                        await work.get_by_role('navigation', name='需求工作流程').get_by_role('button', name=re.compile(r'^1\s')).click()
                        await expect(page.get_by_role('dialog', name='未保存的修改')).not_to_be_visible()
                        await expect(textarea).to_be_visible()
                        await expect(textarea).to_have_value('合成草稿，待发送。')
                        await expect(work.locator('.chat-pasted-image')).to_have_count(2)
                        assert {s['id'] for s in store.get(pid)['sources']} == before_attachment_ids, 'Opening the table silently uploaded attachments'
                        await work.get_by_role('button', name='让 AI 更新草稿', exact=True).click()
                        await expect(work).to_contain_text('未发送')
                        assert provider.calls == 0
                        report['checks'].append(dict(viewport=label, check='existing draft view and return preserves unsaved text/files; generation remains protected'))
                        await page.screenshot(path=str(output / (label + '-draft-preserved.png')))

                        # Saving on project switch, then reloading, must preserve the exact pending draft.
                        await select_project(page, other)
                        leave = page.get_by_role('dialog', name='未保存的修改')
                        if await leave.is_visible():
                            await leave.get_by_role('button', name='保存后离开').click()
                            await expect(leave).not_to_be_visible()
                        await expect(page.get_by_role('heading', name='合成切换项目', exact=True)).to_be_visible()
                        sources = [s for s in store.get(pid)['sources'] if s.get('origin') == 'interview_attachment' and s.get('title') in names]
                        assert len(sources) == 2 and all(s.get('parse_status') in ('read', 'partial') and s.get('excerpts') for s in sources), sources
                        assert {s['title'] for s in sources} == names, sources
                        assert provider.calls == 0
                        report['checks'].append(dict(viewport=label, check='saving unsent draft reads both files without sending or authorizing an AI task'))
                        await page.reload(wait_until='networkidle')
                        await select_project(page, pid)
                        work = page.locator('.connected-workspace:visible')
                        textarea = work.get_by_role('textbox', name='你的想法', exact=True)
                        await expect(textarea).to_have_value('合成草稿，待发送。')
                        await expect(work.locator('.chat-pasted-image')).to_have_count(2)
                        assert provider.calls == 0
                        report['checks'].append(dict(viewport=label, check='saved pending text and files restored after switch and reload'))

                    except Exception as exc:
                        report['failures'].append(dict(viewport=label, error=str(exc), traceback=traceback.format_exc()))
                        await page.screenshot(path=str(output / (label + '-failure.png')), full_page=True)
                    finally:
                        await context.close()
                await browser.close()
        report['model_calls'] = provider.calls
    report['status'] = 'passed' if not (report['failures'] or report['page_errors'] or report['routing_errors'] or report['model_calls']) else 'failed'
    (output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    summary_failures = [dict(viewport=row['viewport'], error=row['error'].split('\n')[0]) for row in report['failures']]
    print(json.dumps(dict(status=report['status'], checks=len(report['checks']), failures=summary_failures,
                          page_errors=report['page_errors'], routing_errors=report['routing_errors'], model_calls=report['model_calls']), ensure_ascii=False))
    if report['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--static-build', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
