"""Offline browser check for keyboard and document attachments on the formal origin.

All /api calls use an isolated create_app; static bytes come from --static-build.
The provider returns synthetic interview JSON and has no network request path.
"""

from tests.browser_navigation import select_project

import argparse
import asyncio
import base64
import io
import json
import mimetypes
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from docx import Document
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.check_chat_composer_browser import OfflineInterview, composer_geometry, wait_assistant
from app.main import create_app


ORIGIN = 'http://127.0.0.1:8765'


class RecordingInterview(OfflineInterview):
    def __init__(self, folder):
        super().__init__(folder)
        self.sent_contexts = []

    async def request(self, config, messages, evidence=None):
        self.sent_contexts.append(json.loads(messages[1]['content'][0]['text']))
        return await super().request(config, messages, evidence=evidence)


def docx_bytes():
    doc = Document()
    doc.add_paragraph('合成联系人备注文档：权限规则待确定。')
    output = io.BytesIO()
    doc.save(output)
    return output.getvalue()


def attachment_sources(store, pid):
    return [source for source in store.get(pid)['sources'] if source.get('origin') == 'interview_attachment']


async def assert_composer_visible(page, size, label='发送'):
    await page.wait_for_timeout(150)
    geometry = await composer_geometry(page, label)
    assert geometry['textarea_visible'] and geometry['button_visible'], (size, label, geometry)
    return geometry


async def wait_chat_ready(page):
    await page.wait_for_function('''() => {
      const root=document.querySelector('.project-workspace:not([hidden])');
      const textarea=root?.querySelector('.connected-chat textarea[aria-label="你的想法"]');
      const send=[...(root?.querySelectorAll('.connected-chat button')||[])].find(button=>button.textContent.trim()==='发送');
      return textarea && !textarea.disabled && send;
    }''', timeout=10000)


async def open_chat(page, pid, name, width):
    await page.goto(ORIGIN + '/', wait_until='networkidle')
    if width <= 700:
        await select_project(page,pid)
    else:
        await page.get_by_role('button', name=name).click()
    await page.locator('.connected-workspace:visible').get_by_role('button', name='和助手聊需求').click()
    await page.locator('.connected-workspace:visible textarea[aria-label="你的想法"]').wait_for()


async def clipboard_or_drop(page, encoded, filename, mime, kind):
    target = page.locator('.connected-workspace:visible textarea[aria-label="你的想法"]' if kind == 'paste'
                          else '.connected-workspace:visible .chat-input-zone')
    await target.evaluate('''(element, value) => {
      const raw=atob(value.encoded), bytes=new Uint8Array(raw.length);
      for(let i=0;i<raw.length;i++)bytes[i]=raw.charCodeAt(i);
      const data=new DataTransfer();
      data.items.add(new File([bytes],value.filename,{type:value.mime}));
      const event=value.kind==='paste'
        ? new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true})
        : new DragEvent('drop',{dataTransfer:data,bubbles:true,cancelable:true});
      element.dispatchEvent(event);
    }''', {'encoded': encoded, 'filename': filename, 'mime': mime, 'kind': kind})


async def run(args):
    build = args.static_build.resolve()
    assert (build / 'index.html').is_file(), f'missing static build: {build}'
    args.output.mkdir(parents=True, exist_ok=True)
    report = {'origin': ORIGIN, 'static_build': str(build), 'checks': [], 'provider_calls': 0,
              'provider_stages': [], 'page_errors': []}
    previous_data_dir = os.environ.get('RA_DATA_DIR')
    provider = None
    try:
        with tempfile.TemporaryDirectory(prefix='ra-chat-doc-browser-') as scratch:
            folder = Path(scratch)
            os.environ['RA_DATA_DIR'] = str(folder / 'data')
            provider = RecordingInterview(folder)
            app = create_app(folder / 'data', provider=provider)
            store = app.state.store
            desktop = store.create('键盘与文件选择隔离项目')
            mobile = store.create('拖放粘贴与恢复隔离项目')

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN, timeout=30) as client:
                async with async_playwright() as playwright:
                    browser = await playwright.chromium.launch(headless=True)

                    async def dispatch(route):
                        request = route.request
                        parsed = urlsplit(request.url)
                        if parsed.scheme != 'http' or parsed.netloc != '127.0.0.1:8765':
                            await route.abort()
                            return
                        path = parsed.path
                        if path.startswith('/api/'):
                            headers = {key: value for key, value in request.headers.items()
                                       if key.lower() not in ('host', 'content-length', 'connection', 'accept-encoding')}
                            headers['host'] = '127.0.0.1:8765'
                            response = await client.request(request.method, path, content=request.post_data_buffer,
                                                            headers=headers)
                            await route.fulfill(status=response.status_code,
                                                headers={'content-type': response.headers.get('content-type', 'application/json')},
                                                body=response.content)
                        elif path == '/' or path.startswith('/assets/'):
                            target = (build / (path.lstrip('/') or 'index.html')).resolve()
                            assert target.is_relative_to(build) and target.is_file(), f'missing static asset: {path}'
                            await route.fulfill(status=200,
                                                content_type=mimetypes.guess_type(target.name)[0] or 'application/octet-stream',
                                                body=target.read_bytes())
                        else:
                            await route.abort()

                    context = await browser.new_context(viewport={'width': 1280, 'height': 720})
                    await context.route('**/*', dispatch)
                    page = await context.new_page()
                    page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
                    await open_chat(page, desktop['id'], desktop['name'], 1280)
                    await assert_composer_visible(page, (1280, 720))
                    textarea = page.locator('.connected-workspace:visible textarea[aria-label="你的想法"]')
                    await textarea.fill('第一轮纯文字讨论')
                    await textarea.press('Shift+Enter')
                    assert '\n' in await textarea.input_value() and provider.calls == 0, 'Shift+Enter sent instead of newline'
                    await textarea.press('Enter')
                    await wait_assistant(page, 1)
                    await wait_chat_ready(page)
                    assert provider.calls == 1 and provider.stages == ['interview']
                    await textarea.fill('输入法候选尚未确认')
                    for composing, key_code in [(True, 13), (False, 229)]:
                        await textarea.evaluate('''(element, flags) => {
                          const event=new KeyboardEvent('keydown',{key:'Enter',code:'Enter',bubbles:true,
                            cancelable:true,isComposing:flags.composing,keyCode:flags.keyCode});
                          Object.defineProperty(event,'keyCode',{get:()=>flags.keyCode});
                          element.dispatchEvent(event);
                        }''', {'composing': composing, 'keyCode': key_code})
                    await page.wait_for_timeout(200)
                    assert provider.calls == 1 and await textarea.input_value() == '输入法候选尚未确认', 'composition Enter submitted'
                    await textarea.press('Enter')
                    await wait_assistant(page, 2)
                    await wait_chat_ready(page)
                    assert provider.calls == 2
                    await page.screenshot(path=str(args.output / 'desktop-keyboard.png'))
                    report['checks'].append({'viewport': [1280, 720], 'keyboard': 'Enter sent once; Shift+Enter and composition/229 did not send'})

                    txt = '合成文字附件：联系人备注为选填。'.encode('utf-8')
                    doc = docx_bytes()
                    file_input = page.get_by_label('选择本轮聊天附件')
                    await file_input.set_input_files([
                        {'name': 'chat-notes.txt', 'mimeType': 'text/plain', 'buffer': txt},
                        {'name': 'chat-rules.docx', 'mimeType': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'buffer': doc},
                    ])
                    await page.locator('.connected-chat .chat-pasted-image').first.wait_for()
                    assert await page.locator('.connected-chat .chat-pasted-image').count() == 2
                    assert provider.calls == 2 and not attachment_sources(store, desktop['id'])
                    await textarea.fill('请结合这两份文件讨论联系人备注。')
                    await assert_composer_visible(page, (1280, 720))
                    await page.get_by_role('button', name='发送', exact=True).click()
                    dialog = page.get_by_role('dialog', name='核对本次任务')
                    await dialog.wait_for()
                    sources = attachment_sources(store, desktop['id'])
                    assert len(sources) == 2 and provider.calls == 2
                    await dialog.locator('.task-source-scope > summary').click()
                    await dialog.get_by_text('查看来源编号',exact=True).click()
                    plan_text = await dialog.inner_text()
                    assert all(source['id'] in plan_text for source in sources)
                    await page.screenshot(path=str(args.output / 'desktop-document-plan.png'))
                    await dialog.get_by_role('button', name='返回保留输入').click()
                    assert await page.locator('.connected-chat .chat-pasted-image').count() == 2
                    assert await textarea.input_value() == '请结合这两份文件讨论联系人备注。'
                    await page.get_by_role('button', name='发送', exact=True).click()
                    dialog = page.get_by_role('dialog', name='核对本次任务')
                    await dialog.wait_for()
                    assert len(attachment_sources(store, desktop['id'])) == 2 and provider.calls == 2
                    assert '分析待处理图片' not in await dialog.inner_text()
                    await dialog.get_by_role('button', name='授权本次范围并运行').click()
                    await wait_assistant(page, 3)
                    await wait_chat_ready(page)
                    assert provider.calls == 3 and provider.stages[-1] == 'interview'
                    project = store.get(desktop['id'])
                    user = [message for message in project['messages'] if message.get('role') == 'user' and message.get('stage') == 'interview'][-1]
                    assert set(user['attachment_source_ids']) == {source['id'] for source in sources}
                    sent = provider.sent_contexts[-1]['excerpts']
                    assert {source['id'] for source in sources} <= {excerpt['source_id'] for excerpt in sent}
                    history = page.locator('.connected-chat .chat-history-attachments')
                    assert 'chat-notes.txt' in await history.last.inner_text() and 'chat-rules.docx' in await history.last.inner_text()
                    assert await history.last.locator('img').count() == 0, 'document was presented as an image'
                    assert await textarea.input_value() == '' and await page.locator('.connected-chat .chat-pasted-image').count() == 0
                    await textarea.fill('再追问一个纯文字问题。')
                    await textarea.press('Enter')
                    await wait_assistant(page, 4)
                    await wait_chat_ready(page)
                    assert provider.calls == 4 and provider.stages[-1] == 'interview'
                    await page.screenshot(path=str(args.output / 'desktop-documents-sent.png'))
                    report['checks'].append({'viewport': [1280, 720], 'file_input': 'two documents uploaded once; cancelled plan retained input; confirmed interview and text follow-up'})
                    await context.close()

                    context = await browser.new_context(viewport={'width': 390, 'height': 844})
                    await context.route('**/*', dispatch)
                    page = await context.new_page()
                    page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
                    await open_chat(page, mobile['id'], mobile['name'], 390)
                    await assert_composer_visible(page, (390, 844))
                    txt64 = base64.b64encode(b'# Synthetic markdown dragged into chat.').decode('ascii')
                    doc64 = base64.b64encode(doc).decode('ascii')
                    await clipboard_or_drop(page, txt64, 'dragged.md', 'text/markdown', 'drop')
                    await clipboard_or_drop(page, doc64, 'pasted.docx',
                                            'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'paste')
                    assert await page.locator('.connected-chat .chat-pasted-image').count() == 2
                    assert provider.calls == 4 and not attachment_sources(store, mobile['id'])
                    await assert_composer_visible(page, (390, 844))
                    await page.screenshot(path=str(args.output / 'mobile-documents-pending.png'))
                    await page.locator('.connected-workspace:visible .connected-steps button').nth(1).click()
                    await page.get_by_text('聊天框还有未发送的文字或附件。请先发送，或移除本轮输入，再进入完善需求。', exact=True).wait_for()
                    assert len(attachment_sources(store, mobile['id'])) == 0 and provider.calls == 4
                    assert await page.locator('.connected-chat .chat-pasted-image').count() == 2
                    # Entering refinement deliberately keeps unsent input in
                    # the assistant. Project switching exercises save-on-leave.
                    await select_project(page,desktop['id'])
                    leave = page.get_by_role('dialog', name='未保存的修改')
                    await leave.wait_for()
                    await leave.get_by_role('button', name='保存后离开').click()
                    await leave.wait_for(state='hidden')
                    assert len(attachment_sources(store, mobile['id'])) == 2 and provider.calls == 4
                    await page.reload(wait_until='networkidle')
                    await select_project(page,mobile['id'])
                    await page.locator('.connected-workspace:visible .connected-steps').get_by_role('button', name='提供信息').click()
                    await page.locator('.connected-workspace:visible').get_by_role('button', name='和助手聊需求').click()
                    await page.locator('.connected-chat .chat-pasted-image').first.wait_for()
                    assert await page.locator('.connected-chat .chat-pasted-image').count() == 2
                    assert 'dragged.md' in await page.locator('.connected-chat .chat-pasted-images').inner_text()
                    assert 'pasted.docx' in await page.locator('.connected-chat .chat-pasted-images').inner_text()
                    await assert_composer_visible(page, (390, 844))
                    await page.screenshot(path=str(args.output / 'mobile-documents-restored.png'))
                    await page.get_by_role('button', name='发送', exact=True).click()
                    dialog = page.get_by_role('dialog', name='核对本次任务')
                    await dialog.wait_for()
                    assert len(attachment_sources(store, mobile['id'])) == 2 and provider.calls == 4
                    await dialog.get_by_role('button', name='授权本次范围并运行').click()
                    await wait_assistant(page, 1)
                    await wait_chat_ready(page)
                    assert provider.calls == 5 and provider.stages[-1] == 'interview'
                    history = page.locator('.connected-chat .chat-history-attachments')
                    assert 'dragged.md' in await history.last.inner_text() and 'pasted.docx' in await history.last.inner_text()
                    assert await history.last.locator('img').count() == 0
                    textarea = page.locator('.connected-workspace:visible textarea[aria-label="你的想法"]')
                    await textarea.fill('移动端继续讨论。')
                    await textarea.press('Enter')
                    await wait_assistant(page, 2)
                    await wait_chat_ready(page)
                    assert provider.calls == 6 and provider.stages == ['interview'] * 6
                    await page.screenshot(path=str(args.output / 'mobile-documents-sent.png'))
                    report['checks'].append({'viewport': [390, 844], 'drop_paste': 'documents stayed local through save/reload; confirmed interview and Enter follow-up'})
                    report['provider_calls'] = provider.calls
                    report['provider_stages'] = provider.stages
                    await browser.close()
    except Exception as error:
        report['error'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        if previous_data_dir is None:
            os.environ.pop('RA_DATA_DIR', None)
        else:
            os.environ['RA_DATA_DIR'] = previous_data_dir
        if provider is not None:
            report['provider_calls'] = provider.calls
            report['provider_stages'] = provider.stages
        (args.output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    assert report['provider_calls'] == 6 and not report['page_errors'], report
    print(json.dumps({'provider_calls': report['provider_calls'], 'provider_stages': report['provider_stages'],
                      'viewports': [row['viewport'] for row in report['checks']]}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--static-build', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
